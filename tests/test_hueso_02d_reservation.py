from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, Provenance, Signal
from bot_obrero.reservation import (
    InvalidReservationTransition,
    NON_TERMINAL_STATES,
    Reservation,
    ReservationConflict,
    ReservationContractError,
    ReservationResourceKind,
    ReservationState,
    ReservationTransitionEvidence,
    SQLiteReservationStore,
    TERMINAL_STATES,
)
from bot_obrero.risk_contracts import RiskDecision, RiskDecisionOutcome
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
        hypothesis_id="h-02d",
        direction="BUY",
        generated_at=BASE,
        decision_timestamp=BASE,
        provenance=Provenance("reservation-test", ArtifactNature.DERIVED),
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
    outcome: RiskDecisionOutcome = RiskDecisionOutcome.APPROVED,
    risk_decision_id: str = "risk-02d",
) -> RiskDecision:
    return RiskDecision.from_trade_proposal(
        proposal=proposal,
        risk_decision_id=risk_decision_id,
        outcome=outcome,
        reason="approved for reservation contract test",
        decision_timestamp=BASE,
        risk_evidence=(),
    )


def make_reservation(
    *,
    proposal: TradeProposal | None = None,
    risk_decision: RiskDecision | None = None,
    amount: Decimal = Decimal("100"),
    reservation_id: str = "reservation-01",
    resource_kind: ReservationResourceKind = ReservationResourceKind.QUOTE,
    asset: str = "USDT",
) -> Reservation:
    source = make_proposal() if proposal is None else proposal
    decision = (
        make_risk_decision(source, risk_decision_id="risk-02d")
        if risk_decision is None
        else risk_decision
    )
    return Reservation.from_trade_proposal_and_risk_decision(
        proposal=source,
        risk_decision=decision,
        account_id="account-01",
        resource_kind=resource_kind,
        asset=asset,
        reserved_amount=amount,
        created_at=BASE,
        reservation_id=reservation_id,
    )


def evidence(
    kind: str,
    reference_id: str,
    seconds: int = 0,
) -> ReservationTransitionEvidence:
    return ReservationTransitionEvidence(
        kind=kind,
        reference_id=reference_id,
        occurred_at=BASE + timedelta(seconds=seconds),
    )


def make_store(tmp_path: Path) -> SQLiteReservationStore:
    return SQLiteReservationStore(tmp_path / "reservations.sqlite3")


def test_identity_is_unique_and_separate_from_other_ids():
    proposal = make_proposal()
    decision = make_risk_decision(proposal)
    first = make_reservation(
        proposal=proposal,
        risk_decision=decision,
        reservation_id="reservation-a",
    )
    second = make_reservation(
        proposal=proposal,
        risk_decision=decision,
        reservation_id="reservation-b",
    )

    assert first.reservation_id != second.reservation_id
    assert first.reservation_id not in {
        first.proposal_id,
        first.risk_decision_id,
        first.correlation_id,
    }


def test_identity_is_immutable():
    reservation = make_reservation()

    with pytest.raises(FrozenInstanceError):
        reservation.reservation_id = "changed"
    with pytest.raises(FrozenInstanceError):
        reservation.proposal_id = "changed"
    with pytest.raises(FrozenInstanceError):
        reservation.state = ReservationState.RELEASED


def test_resource_kind_is_exactly_spot_v1():
    assert {item.value for item in ReservationResourceKind} == {"QUOTE", "BASE"}


def test_amounts_are_decimal_and_invariants_hold():
    reservation = make_reservation(amount=Decimal("100.25"))

    assert type(reservation.reserved_amount) is Decimal
    assert type(reservation.consumed_amount) is Decimal
    assert type(reservation.remaining_amount) is Decimal
    assert reservation.consumed_amount == Decimal("0")
    assert reservation.remaining_amount == Decimal("100.25")
    assert reservation.protected_capacity == Decimal("100.25")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("reserved_amount", 1),
        ("reserved_amount", 1.0),
        ("consumed_amount", 1),
        ("remaining_amount", 1.0),
    ],
)
def test_amounts_reject_non_decimal_values(field_name, value):
    proposal = make_proposal()
    decision = make_risk_decision(proposal)
    kwargs = dict(
        reservation_id="reservation-invalid",
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        consumed_amount=Decimal("0"),
        remaining_amount=Decimal("100"),
        state=ReservationState.ACTIVE,
        proposal_id=proposal.proposal_id,
        risk_decision_id=decision.risk_decision_id,
        correlation_id=proposal.correlation_id,
        client_order_id=None,
        exchange_order_id=None,
        created_at=BASE,
        updated_at=BASE,
    )
    kwargs[field_name] = value

    with pytest.raises(ReservationContractError, match="Decimal"):
        Reservation(**kwargs)


@pytest.mark.parametrize("amount", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_negative_or_non_finite_reserved_amount_rejected(amount):
    with pytest.raises(ReservationContractError):
        make_reservation(amount=amount)


def test_risk_admission_requires_approved_decision():
    proposal = make_proposal()
    for outcome in (RiskDecisionOutcome.REJECTED, RiskDecisionOutcome.UNKNOWN):
        decision = make_risk_decision(proposal, outcome=outcome)
        with pytest.raises(ReservationContractError, match="APPROVED"):
            make_reservation(proposal=proposal, risk_decision=decision)


def test_proposal_and_risk_decision_identity_must_match():
    first = make_proposal()
    second = make_proposal()
    mismatched = make_risk_decision(second)

    with pytest.raises(ReservationContractError, match="proposal_id"):
        make_reservation(proposal=first, risk_decision=mismatched)


def test_correlation_is_inherited_and_not_regenerated():
    proposal = make_proposal()
    decision = make_risk_decision(proposal)
    reservation = make_reservation(proposal=proposal, risk_decision=decision)

    assert reservation.correlation_id == proposal.correlation_id
    assert reservation.correlation_id == decision.correlation_id


def test_order_ids_may_be_unresolved_at_birth():
    reservation = make_reservation()
    assert reservation.client_order_id is None
    assert reservation.exchange_order_id is None


def test_timestamps_must_be_timezone_aware():
    proposal = make_proposal()
    decision = make_risk_decision(proposal)
    with pytest.raises(ReservationContractError, match="timezone-aware"):
        Reservation.from_trade_proposal_and_risk_decision(
            proposal=proposal,
            risk_decision=decision,
            account_id="account-01",
            resource_kind=ReservationResourceKind.QUOTE,
            asset="USDT",
            reserved_amount=Decimal("100"),
            created_at=datetime(2026, 10, 4, 10, 0),
        )


def test_state_set_is_exact():
    assert {item.value for item in ReservationState} == {
        "ACTIVE",
        "PARTIALLY_CONSUMED",
        "CONSUMED",
        "RELEASED",
        "UNKNOWN",
    }
    assert NON_TERMINAL_STATES == {
        ReservationState.ACTIVE,
        ReservationState.PARTIALLY_CONSUMED,
        ReservationState.UNKNOWN,
    }
    assert TERMINAL_STATES == {
        ReservationState.CONSUMED,
        ReservationState.RELEASED,
    }


def test_active_to_partial_to_consumed():
    reservation = make_reservation(amount=Decimal("100"))
    partial, first = reservation.consume(
        Decimal("40"),
        evidence=evidence("FILL", "fill-40", seconds=1),
    )

    assert partial.state is ReservationState.PARTIALLY_CONSUMED
    assert partial.consumed_amount == Decimal("40")
    assert partial.remaining_amount == Decimal("60")
    assert first.from_state is ReservationState.ACTIVE
    assert first.to_state is ReservationState.PARTIALLY_CONSUMED

    consumed, second = partial.consume(
        Decimal("60"),
        evidence=evidence("FILL", "fill-100", seconds=2),
    )
    assert consumed.state is ReservationState.CONSUMED
    assert consumed.consumed_amount == Decimal("100")
    assert consumed.remaining_amount == Decimal("0")
    assert consumed.protected_capacity == Decimal("0")
    assert second.to_state is ReservationState.CONSUMED


def test_release_from_active_and_partial():
    active = make_reservation(amount=Decimal("100"))
    released, transition = active.release(
        evidence=evidence("ORDER", "release-active", seconds=1)
    )
    assert released.state is ReservationState.RELEASED
    assert released.remaining_amount == Decimal("100")
    assert released.protected_capacity == Decimal("0")
    assert transition.to_state is ReservationState.RELEASED

    partial, _ = active.consume(
        Decimal("40"),
        evidence=evidence("FILL", "fill-40", seconds=1),
    )
    released_partial, _ = partial.release(
        evidence=evidence("ORDER", "release-partial", seconds=2)
    )
    assert released_partial.state is ReservationState.RELEASED
    assert released_partial.remaining_amount == Decimal("60")
    assert released_partial.protected_capacity == Decimal("0")


def test_unknown_does_not_release_protected_capacity():
    reservation = make_reservation(amount=Decimal("100"))
    unknown, transition = reservation.mark_unknown(
        evidence=evidence("VENUE", "timeout-01", seconds=1)
    )

    assert unknown.state is ReservationState.UNKNOWN
    assert unknown.protected_capacity == Decimal("100")
    assert transition.from_state is ReservationState.ACTIVE

    still_unknown, _ = unknown.mark_unknown(
        evidence=evidence("VENUE", "timeout-02", seconds=2)
    )
    assert still_unknown.state is ReservationState.UNKNOWN
    assert still_unknown.protected_capacity == Decimal("100")


def test_unknown_partial_can_still_consume():
    reservation = make_reservation(amount=Decimal("100"))
    unknown, _ = reservation.consume(
        Decimal("40"),
        evidence=evidence("FILL", "fill-40", seconds=1),
    )
    unknown, _ = unknown.mark_unknown(
        evidence=evidence("VENUE", "timeout-01", seconds=2),
    )
    consumed, _ = unknown.consume(
        Decimal("60"),
        evidence=evidence("FILL", "fill-100", seconds=3),
    )

    assert consumed.state is ReservationState.CONSUMED
    assert consumed.protected_capacity == Decimal("0")


@pytest.mark.parametrize(
    ("start_state", "operation"),
    [
        (ReservationState.CONSUMED, "consume"),
        (ReservationState.CONSUMED, "release"),
        (ReservationState.CONSUMED, "unknown"),
        (ReservationState.RELEASED, "consume"),
        (ReservationState.RELEASED, "release"),
        (ReservationState.RELEASED, "unknown"),
    ],
)
def test_terminal_states_cannot_be_revived(start_state, operation):
    reservation = make_reservation(amount=Decimal("10"))
    if start_state is ReservationState.CONSUMED:
        reservation, _ = reservation.consume(
            Decimal("10"),
            evidence=evidence("FILL", "fill-10", seconds=1),
        )
    else:
        reservation, _ = reservation.release(
            evidence=evidence("ORDER", "release", seconds=1)
        )

    with pytest.raises(InvalidReservationTransition):
        if operation == "consume":
            reservation.consume(
                Decimal("1"),
                evidence=evidence("FILL", "late-fill", seconds=2),
            )
        elif operation == "release":
            reservation.release(
                evidence=evidence("ORDER", "late-release", seconds=2)
            )
        else:
            reservation.mark_unknown(
                evidence=evidence("VENUE", "late-unknown", seconds=2)
            )


def test_partial_consumption_rejects_zero_overage_and_backward_evidence():
    reservation = make_reservation(amount=Decimal("10"))
    with pytest.raises(InvalidReservationTransition):
        reservation.consume(
            Decimal("0"),
            evidence=evidence("FILL", "zero", seconds=1),
        )
    with pytest.raises(InvalidReservationTransition):
        reservation.consume(
            Decimal("11"),
            evidence=evidence("FILL", "too-much", seconds=1),
        )
    with pytest.raises(InvalidReservationTransition):
        reservation.release(
            evidence=evidence("ORDER", "backward", seconds=-1),
        )


def test_direct_state_transition_methods_do_not_mutate_original():
    reservation = make_reservation(amount=Decimal("100"))
    updated, _ = reservation.consume(
        Decimal("40"),
        evidence=evidence("FILL", "fill-40", seconds=1),
    )
    assert reservation.state is ReservationState.ACTIVE
    assert reservation.consumed_amount == Decimal("0")
    assert updated.state is ReservationState.PARTIALLY_CONSUMED


def test_persistence_survives_reload_and_preserves_transition_evidence(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    decision = make_risk_decision(proposal)
    reservation = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=decision,
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        created_at=BASE,
        evidence=evidence("RISK", decision.risk_decision_id),
    )
    store.consume(
        reservation.reservation_id,
        Decimal("40"),
        evidence=evidence("FILL", "fill-40", seconds=1),
    )
    store.mark_unknown(
        reservation.reservation_id,
        evidence=evidence("TIMEOUT", "timeout-01", seconds=2),
    )
    store.close()

    reloaded_store = make_store(tmp_path)
    reloaded = reloaded_store.get(reservation.reservation_id)

    assert reloaded is not None
    assert reloaded.reservation_id == reservation.reservation_id
    assert reloaded.proposal_id == proposal.proposal_id
    assert reloaded.risk_decision_id == decision.risk_decision_id
    assert reloaded.correlation_id == proposal.correlation_id
    assert reloaded.state is ReservationState.UNKNOWN
    assert reloaded.reserved_amount == Decimal("100")
    assert reloaded.consumed_amount == Decimal("40")
    assert reloaded.remaining_amount == Decimal("60")
    assert reloaded.protected_capacity == Decimal("60")

    transitions = reloaded_store.transitions(reservation.reservation_id)
    assert len(transitions) == 3
    assert [item.to_state for item in transitions] == [
        ReservationState.ACTIVE,
        ReservationState.PARTIALLY_CONSUMED,
        ReservationState.UNKNOWN,
    ]
    assert transitions[1].evidence_reference_id == "fill-40"
    assert transitions[2].evidence_kind == "TIMEOUT"
    reloaded_store.close()


def test_proposal_multiplicity_rejects_two_non_terminal_reservations(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    decision = make_risk_decision(proposal, risk_decision_id="risk-1")
    first = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=decision,
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        created_at=BASE,
    )

    with pytest.raises(ReservationConflict):
        store.create_from_trade_proposal_and_risk_decision(
            proposal=proposal,
            risk_decision=make_risk_decision(
                proposal, risk_decision_id="risk-2"
            ),
            account_id="account-01",
            resource_kind=ReservationResourceKind.QUOTE,
            asset="USDT",
            reserved_amount=Decimal("50"),
            created_at=BASE + timedelta(seconds=1),
        )

    assert store.get(first.reservation_id) is not None
    store.close()


def test_proposal_multiplicity_allows_new_active_after_release(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    first = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=make_risk_decision(proposal, risk_decision_id="risk-1"),
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        created_at=BASE,
    )
    store.release(
        first.reservation_id,
        evidence=evidence("ORDER", "release", seconds=1),
    )

    second = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=make_risk_decision(proposal, risk_decision_id="risk-2"),
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("50"),
        created_at=BASE + timedelta(seconds=2),
    )
    assert second.state is ReservationState.ACTIVE
    assert [item.state for item in store.list_for_proposal(proposal.proposal_id)] == [
        ReservationState.RELEASED,
        ReservationState.ACTIVE,
    ]
    store.close()


def test_proposal_multiplicity_allows_new_active_after_consumed(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    first = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=make_risk_decision(proposal, risk_decision_id="risk-1"),
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        created_at=BASE,
    )
    store.consume(
        first.reservation_id,
        Decimal("100"),
        evidence=evidence("FILL", "fill-100", seconds=1),
    )

    second = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=make_risk_decision(proposal, risk_decision_id="risk-2"),
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("50"),
        created_at=BASE + timedelta(seconds=2),
    )
    assert second.state is ReservationState.ACTIVE
    store.close()


def test_proposal_multiplicity_blocks_unknown_reservation(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    first = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=make_risk_decision(proposal, risk_decision_id="risk-1"),
        account_id="account-01",
        resource_kind=ReservationResourceKind.QUOTE,
        asset="USDT",
        reserved_amount=Decimal("100"),
        created_at=BASE,
    )
    store.mark_unknown(
        first.reservation_id,
        evidence=evidence("VENUE", "timeout", seconds=1),
    )

    with pytest.raises(ReservationConflict):
        store.create_from_trade_proposal_and_risk_decision(
            proposal=proposal,
            risk_decision=make_risk_decision(proposal, risk_decision_id="risk-2"),
            account_id="account-01",
            resource_kind=ReservationResourceKind.QUOTE,
            asset="USDT",
            reserved_amount=Decimal("25"),
            created_at=BASE + timedelta(seconds=2),
        )
    store.close()


def test_persistence_is_separate_from_idempotency_ledger():
    source = Path("bot_obrero/reservation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = []
    referenced_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append((node.module or "").lower())
        elif isinstance(node, ast.Name):
            referenced_names.append(node.id.lower())
        elif isinstance(node, ast.Attribute):
            referenced_names.append(node.attr.lower())

    assert "persistent_ledger" not in imported_modules
    assert "sqliteidempotencyledger" not in referenced_names


def test_provider_and_execution_neutrality():
    source = Path("bot_obrero/reservation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    float_calls = []
    float_literals = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append((node.module or "").lower())
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "float":
                float_calls.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            float_literals.append(node.lineno)

    forbidden = (
        "binance",
        "websocket",
        "execution",
        "reconciliation",
        "strategy",
        "availability",
    )
    assert all(
        not any(fragment in module for fragment in forbidden)
        for module in imported
    )
    assert float_calls == []
    assert float_literals == []


def test_no_changes_are_required_in_out_of_scope_modules():
    for path in (
        "bot_obrero/risk_contracts.py",
        "bot_obrero/trade_proposal.py",
        "bot_obrero/execution.py",
        "bot_obrero/persistent_ledger.py",
    ):
        assert Path(path).exists()


def test_store_creates_separate_tables(tmp_path):
    store = make_store(tmp_path)
    names = {
        row[0]
        for row in store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "reservations" in names
    assert "reservation_transitions" in names
    assert "reservation_authorization_bindings" in names
    assert "idempotency_ledger" not in names
    store.close()


def test_store_rejects_duplicate_reservation_id(tmp_path):
    store = make_store(tmp_path)
    reservation = make_reservation()
    store.create(
        reservation,
        evidence=evidence("RISK", reservation.risk_decision_id),
    )
    with pytest.raises(ReservationContractError):
        store.create(
            reservation,
            evidence=evidence("RISK", reservation.risk_decision_id),
        )
    store.close()


def test_store_protected_capacity_terminal_states_is_zero(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    reservation = store.create_from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=make_risk_decision(proposal),
        account_id="account-01",
        resource_kind=ReservationResourceKind.BASE,
        asset="BTC",
        reserved_amount=Decimal("2"),
        created_at=BASE,
    )
    released = store.release(
        reservation.reservation_id,
        evidence=evidence("ORDER", "release", seconds=1),
    )
    assert released.protected_capacity == Decimal("0")
    store.close()


def test_suite_does_not_modify_existing_contract_files():
    assert Path("bot_obrero/risk_contracts.py").read_text(encoding="utf-8")
    assert Path("bot_obrero/trade_proposal.py").read_text(encoding="utf-8")
    assert Path("bot_obrero/execution.py").read_text(encoding="utf-8")
    assert Path("bot_obrero/persistent_ledger.py").read_text(encoding="utf-8")
