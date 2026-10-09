from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance, Signal
from bot_obrero.reservation import (
    NON_TERMINAL_STATES,
    Reservation,
    ReservationContractError,
    ReservationReadSet,
    ReservationResourceKind,
    ReservationState,
    ReservationTransitionEvidence,
    SQLiteReservationStore,
)
from bot_obrero.risk_contracts import Completeness, RiskDecision, RiskDecisionOutcome
from bot_obrero.trade_proposal import (
    PricePolicy,
    TradeOrderType,
    TradeProposal,
    TradeSide,
    build_trade_proposal,
)

UTC = timezone.utc
BASE = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)


def make_signal() -> Signal:
    return Signal(
        symbol="BTC/USDT",
        hypothesis_id="h-02e1",
        direction="BUY",
        generated_at=BASE,
        decision_timestamp=BASE,
        provenance=Provenance("reservation-read-set-test", ArtifactNature.DERIVED),
    )


def make_proposal() -> TradeProposal:
    return build_trade_proposal(
        signal=make_signal(),
        side=TradeSide.BUY,
        requested_quantity=Decimal("1"),
        requested_price=None,
        max_quote_spend=Decimal("100"),
        price_policy=PricePolicy.MARKET_REFERENCE,
        order_type=TradeOrderType.MARKET,
        strategy_identity="strategy.test",
        strategy_version="1.0.0",
    )


def make_risk_decision(
    proposal: TradeProposal,
    *,
    risk_decision_id: str,
) -> RiskDecision:
    return RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id=risk_decision_id,
        outcome=RiskDecisionOutcome.APPROVED,
        reason="approved for reservation read-set test",
        decision_timestamp=BASE,
        risk_evidence=(),
    )


def make_reservation(
    store: SQLiteReservationStore,
    *,
    reservation_id: str,
    account_id: str = "account-A",
    amount: Decimal = Decimal("100"),
    resource_kind: ReservationResourceKind = ReservationResourceKind.QUOTE,
    asset: str = "USDT",
    created_at: datetime = BASE,
) -> Reservation:
    proposal = make_proposal()
    decision = make_risk_decision(
        proposal,
        risk_decision_id=f"risk-{reservation_id}",
    )
    reservation = Reservation.from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=decision,
        account_id=account_id,
        resource_kind=resource_kind,
        asset=asset,
        reserved_amount=amount,
        created_at=created_at,
        reservation_id=reservation_id,
    )
    return store.create(
        reservation,
        evidence=ReservationTransitionEvidence(
            kind="RESERVATION_CREATED",
            reference_id=proposal.proposal_id,
            occurred_at=created_at,
        ),
    )


def make_store(tmp_path: Path) -> SQLiteReservationStore:
    return SQLiteReservationStore(tmp_path / "reservations.sqlite3")


def test_empty_account_returns_complete_empty_read_set(tmp_path):
    store = make_store(tmp_path)

    read_set = store.read_set_for_account("account-A")

    assert read_set.reservations == ()
    assert read_set.reservation_count == 0
    assert read_set.relevant_reservations == ()
    assert read_set.relevant_reservation_count == 0
    assert read_set.completeness is Completeness.COMPLETE
    store.close()


def test_mixed_lifecycle_contains_all_five_and_marks_three_relevant(tmp_path):
    store = make_store(tmp_path)

    active = make_reservation(store, reservation_id="r1")
    partial = make_reservation(store, reservation_id="r2")
    unknown = make_reservation(store, reservation_id="r3")
    consumed = make_reservation(store, reservation_id="r4")
    released = make_reservation(store, reservation_id="r5")

    partial = store.consume(
        partial.reservation_id,
        Decimal("40"),
        evidence=ReservationTransitionEvidence(
            kind="FILL", reference_id="fill-r2", occurred_at=BASE + timedelta(seconds=1)
        ),
    )
    unknown = store.mark_unknown(
        unknown.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="VENUE", reference_id="timeout-r3", occurred_at=BASE + timedelta(seconds=2)
        ),
    )
    consumed = store.consume(
        consumed.reservation_id,
        Decimal("100"),
        evidence=ReservationTransitionEvidence(
            kind="FILL", reference_id="fill-r4", occurred_at=BASE + timedelta(seconds=3)
        ),
    )
    released = store.release(
        released.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="ORDER", reference_id="release-r5", occurred_at=BASE + timedelta(seconds=4)
        ),
    )

    read_set = store.read_set_for_account("account-A")

    assert {item.reservation_id for item in read_set.reservations} == {"r1", "r2", "r3", "r4", "r5"}
    assert read_set.reservation_count == 5
    assert {item.reservation_id for item in read_set.relevant_reservations} == {"r1", "r2", "r3"}
    assert read_set.relevant_reservation_count == 3
    assert active.state is ReservationState.ACTIVE
    assert partial.state is ReservationState.PARTIALLY_CONSUMED
    assert unknown.state is ReservationState.UNKNOWN
    assert consumed.state is ReservationState.CONSUMED
    assert released.state is ReservationState.RELEASED
    store.close()


def test_relevant_resources_remain_separate_by_resource_kind_and_asset(tmp_path):
    store = make_store(tmp_path)

    quote = make_reservation(
        store,
        reservation_id="quote-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        amount=Decimal("250"),
    )
    base = make_reservation(
        store,
        reservation_id="base-01",
        resource_kind=ReservationResourceKind.BASE,
        asset="BTC",
        amount=Decimal("0.5"),
    )

    read_set = store.read_set_for_account("account-A")
    relevant = {item.reservation_id: item for item in read_set.relevant_reservations}

    assert relevant["quote-01"].resource_kind is ReservationResourceKind.QUOTE
    assert relevant["quote-01"].asset == "USDT"
    assert relevant["quote-01"].protected_capacity == Decimal("250")
    assert relevant["base-01"].resource_kind is ReservationResourceKind.BASE
    assert relevant["base-01"].asset == "BTC"
    assert relevant["base-01"].protected_capacity == Decimal("0.5")
    assert quote.protected_capacity == Decimal("250")
    assert base.protected_capacity == Decimal("0.5")
    store.close()


def test_account_isolation_is_enforced_by_store_query(tmp_path):
    store = make_store(tmp_path)

    make_reservation(store, reservation_id="r1", account_id="account-A")
    make_reservation(store, reservation_id="r2", account_id="account-B")

    a = store.read_set_for_account("account-A")
    b = store.read_set_for_account("account-B")

    assert [item.reservation_id for item in a.reservations] == ["r1"]
    assert [item.reservation_id for item in b.reservations] == ["r2"]
    store.close()


def test_proposal_multiplicity_retains_released_and_active_for_same_proposal(tmp_path):
    store = make_store(tmp_path)

    proposal = make_proposal()
    first_decision = make_risk_decision(proposal, risk_decision_id="risk-p1")
    first = Reservation.from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=first_decision,
        account_id="account-A",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        created_at=BASE,
        reservation_id="r1",
    )
    store.create(
        first,
        evidence=ReservationTransitionEvidence(
            kind="RESERVATION_CREATED",
            reference_id=proposal.proposal_id,
            occurred_at=BASE,
        ),
    )
    store.release(
        first.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="ORDER", reference_id="release-r1", occurred_at=BASE + timedelta(seconds=1)
        ),
    )

    second_decision = make_risk_decision(proposal, risk_decision_id="risk-p1-2")
    second = Reservation.from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=second_decision,
        account_id="account-A",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("50"),
        created_at=BASE + timedelta(seconds=2),
        reservation_id="r2",
    )
    store.create(
        second,
        evidence=ReservationTransitionEvidence(
            kind="RESERVATION_CREATED",
            reference_id=proposal.proposal_id,
            occurred_at=BASE + timedelta(seconds=2),
        ),
    )

    read_set = store.read_set_for_account("account-A")

    assert [item.reservation_id for item in read_set.reservations] == ["r1", "r2"]
    assert [item.reservation_id for item in read_set.relevant_reservations] == ["r2"]
    store.close()


def test_unknown_remains_relevant_and_protected_capacity_is_preserved(tmp_path):
    store = make_store(tmp_path)

    reservation = make_reservation(store, reservation_id="r1", amount=Decimal("60"))
    unknown = store.mark_unknown(
        reservation.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="VENUE", reference_id="timeout-r1", occurred_at=BASE + timedelta(seconds=1)
        ),
    )

    read_set = store.read_set_for_account("account-A")

    assert unknown.state is ReservationState.UNKNOWN
    assert unknown.protected_capacity == Decimal("60")
    assert read_set.relevant_reservations[0].reservation_id == "r1"
    assert read_set.relevant_reservations[0].protected_capacity == Decimal("60")
    store.close()


def test_terminal_states_remain_in_base_read_set_but_not_relevance(tmp_path):
    store = make_store(tmp_path)

    consumed = make_reservation(store, reservation_id="r1")
    released = make_reservation(store, reservation_id="r2")
    store.consume(
        consumed.reservation_id,
        Decimal("100"),
        evidence=ReservationTransitionEvidence(
            kind="FILL", reference_id="fill-r1", occurred_at=BASE + timedelta(seconds=1)
        ),
    )
    store.release(
        released.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="ORDER", reference_id="release-r2", occurred_at=BASE + timedelta(seconds=2)
        ),
    )

    read_set = store.read_set_for_account("account-A")

    assert {item.reservation_id for item in read_set.reservations} == {"r1", "r2"}
    assert read_set.relevant_reservations == ()
    store.close()


def test_account_mismatch_is_not_silently_accepted(tmp_path):
    store = make_store(tmp_path)
    make_reservation(store, reservation_id="r1", account_id="account-A")

    original_from_row = store._from_row

    def inconsistent_from_row(row):
        return replace(original_from_row(row), account_id="account-B")

    store._from_row = inconsistent_from_row

    with pytest.raises(ReservationContractError, match="INCONSISTENT READ SET"):
        store.read_set_for_account("account-A")

    store.close()


def test_deterministic_order_uses_created_at_then_reservation_id(tmp_path):
    store = make_store(tmp_path)
    make_reservation(
        store,
        reservation_id="b",
        created_at=BASE,
    )
    make_reservation(
        store,
        reservation_id="a",
        created_at=BASE,
    )

    read_set = store.read_set_for_account("account-A")

    assert [item.reservation_id for item in read_set.reservations] == ["a", "b"]
    store.close()


def test_read_timestamp_is_present_and_timezone_aware(tmp_path):
    store = make_store(tmp_path)

    read_set = store.read_set_for_account("account-A")

    assert isinstance(read_set.read_at, datetime)
    assert read_set.read_at.tzinfo is not None
    store.close()


def test_completeness_contract_can_represent_all_states():
    for completeness in (
        Completeness.COMPLETE,
        Completeness.PARTIAL,
        Completeness.UNKNOWN,
    ):
        read_set = ReservationReadSet(
            account_id="account-A",
            reservations=(),
            read_at=BASE,
            completeness=completeness,
        )
        assert read_set.completeness is completeness


def test_read_set_rejects_foreign_account_reservations():
    reservation = Reservation(
        reservation_id="r1",
        account_id="account-B",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        consumed_amount=Decimal("0"),
        remaining_amount=Decimal("100"),
        state=ReservationState.ACTIVE,
        proposal_id="proposal-r1",
        risk_decision_id="risk-r1",
        correlation_id="corr-r1",
        client_order_id=None,
        exchange_order_id=None,
        created_at=BASE,
        updated_at=BASE,
    )

    with pytest.raises(ReservationContractError, match="INCONSISTENT READ SET"):
        ReservationReadSet(
            account_id="account-A",
            reservations=(reservation,),
            read_at=BASE,
            completeness=Completeness.COMPLETE,
        )


def test_read_set_is_ephemeral_and_no_new_sqlite_table_is_created(tmp_path):
    store = make_store(tmp_path)
    make_reservation(store, reservation_id="r1")

    store.read_set_for_account("account-A")

    table_names = {
        row[0]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }

    assert table_names == {
        "reservations",
        "reservation_transitions",
        "reservation_authorization_bindings",
    }
    store.close()


def test_read_set_exposes_only_the_canonical_account_api():
    source = Path("bot_obrero/reservation.py").read_text(encoding="utf-8")

    assert "def read_set_for_account(" in source
    assert "def list_for_account(" not in source
    assert "def all_reservations(" not in source


def test_no_new_effective_capacity_or_temporal_surface_is_added():
    source = Path("bot_obrero/reservation.py").read_text(encoding="utf-8")

    assert "EffectiveCapacity" not in source
    assert "available_at" not in source
    assert "atomic snapshot" not in source.lower()
