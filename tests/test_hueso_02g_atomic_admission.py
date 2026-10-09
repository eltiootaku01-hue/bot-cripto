from __future__ import annotations

import multiprocessing as mp
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from bot_obrero.financial_admission import FinancialAdmissionBoundary, FinancialAdmissionStatus
import test_hueso_05d_final_admission as admission_fixtures
from bot_obrero.analysis_contracts import ArtifactNature, Provenance, Signal
from bot_obrero.effective_capacity import (
    EffectiveCapacityStatus,
    calculate_effective_capacity,
)
from bot_obrero.reservation import (
    ReservationAdmissionBusy,
    ReservationAdmissionRejected,
    ReservationConflict,
    ReservationResourceKind,
    ReservationSchemaConflict,
    ReservationState,
    ReservationTransitionEvidence,
    SQLiteReservationStore,
)
from bot_obrero.risk_contracts import (
    BalanceSnapshot,
    CanonicalAccountState,
    Completeness,
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidenceRef,
)
from bot_obrero.trade_proposal import (
    PricePolicy,
    TradeOrderType,
    TradeProposal,
    TradeSide,
    build_trade_proposal,
)

UTC = timezone.utc
BASE = datetime(2026, 10, 4, 14, 0, tzinfo=UTC)
ACCOUNT = "account-g1"
ASSET = "USDT"
RESOURCE = ReservationResourceKind.QUOTE
DB_NAME = "reservations.sqlite3"


def make_signal() -> Signal:
    return Signal(
        symbol="BTC/USDT",
        hypothesis_id="hueso-02-g1",
        direction="BUY",
        generated_at=BASE,
        decision_timestamp=BASE,
        provenance=Provenance("hueso-02-g1-test", ArtifactNature.DERIVED),
    )


def make_proposal(
    *,
    proposal_id: str | None = None,
    correlation_id: str | None = None,
) -> TradeProposal:
    proposal = build_trade_proposal(
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
    if proposal_id is not None:
        object.__setattr__(proposal, "proposal_id", proposal_id)
    if correlation_id is not None:
        object.__setattr__(proposal, "correlation_id", correlation_id)
    return proposal


def make_risk_decision(
    proposal: TradeProposal,
    *,
    outcome: RiskDecisionOutcome = RiskDecisionOutcome.APPROVED,
    risk_decision_id: str | None = None,
) -> RiskDecision:
    decision_id = risk_decision_id or f"risk-{proposal.proposal_id}"
    return RiskDecision(
        risk_decision_id=decision_id,
        proposal_id=proposal.proposal_id,
        signal_id=proposal.signal_id,
        outcome=outcome,
        reason="G1 reservation admission test",
        decision_timestamp=BASE,
        risk_evidence=(
            RiskEvidenceRef(
                kind="G1_TEST",
                reference_id=decision_id,
                as_of=BASE,
            ),
        ),
        correlation_id=proposal.correlation_id,
        evaluation_context_id=f"g1-context-{proposal.proposal_id}",
        policy_id="g1-test-policy",
        policy_version="1.0.0",
    )


def make_state(
    *,
    available: str = "100",
    account_id: str = ACCOUNT,
    completeness: Completeness = Completeness.COMPLETE,
) -> CanonicalAccountState:
    amount = Decimal(available)
    return CanonicalAccountState(
        account_state_id=f"state-{account_id}-{available}",
        account_id=account_id,
        as_of=BASE,
        balances=(
            BalanceSnapshot(
                asset=ASSET,
                total=amount,
                available=amount,
                locked=Decimal("0"),
            ),
        ),
        completeness=completeness,
        provenance=Provenance("canonical-account-test", ArtifactNature.OBSERVED),
    )


def make_store(tmp_path: Path) -> SQLiteReservationStore:
    return SQLiteReservationStore(tmp_path / DB_NAME)


def evidence(proposal: TradeProposal, *, seconds: int = 0) -> ReservationTransitionEvidence:
    return ReservationTransitionEvidence(
        kind="RESERVATION_CREATED",
        reference_id=proposal.proposal_id,
        occurred_at=BASE,
    )


def make_admission_request_for_amount(
    proposal: TradeProposal,
    amount: str,
    *,
    state: CanonicalAccountState | None = None,
    decision_suffix: str | None = None,
):
    amount_decimal = Decimal(amount)
    economic_proposal = replace(proposal, max_quote_spend=amount_decimal)
    # Preserve semantic identity when replacing only financial terms in a fixture.
    object.__setattr__(economic_proposal, "proposal_id", proposal.proposal_id)
    object.__setattr__(economic_proposal, "signal_id", proposal.signal_id)
    object.__setattr__(economic_proposal, "correlation_id", proposal.correlation_id)
    resolved_state = make_state() if state is None else state
    if decision_suffix is None:
        return admission_fixtures.make_request(
            proposal=economic_proposal,
            state=resolved_state,
            amount=amount_decimal,
        )
    _, _, context, policy, decision, _ = admission_fixtures.make_bundle(
        proposal=economic_proposal,
        state=resolved_state,
    )
    decision = replace(
        decision,
        risk_decision_id=f"{decision.risk_decision_id}-{decision_suffix}",
    )
    return admission_fixtures.make_request(
        proposal=economic_proposal,
        state=resolved_state,
        context=context,
        policy=policy,
        decision=decision,
        amount=amount_decimal,
    )


def admit(
    store: SQLiteReservationStore,
    *,
    amount: str,
    proposal: TradeProposal | None = None,
    state: CanonicalAccountState | None = None,
    reservation_id: str | None = None,
    decision_suffix: str | None = None,
):
    proposal = make_proposal() if proposal is None else proposal
    request = make_admission_request_for_amount(
        proposal,
        amount,
        state=state,
        decision_suffix=decision_suffix,
    )
    result = FinancialAdmissionBoundary(store).admit(request)
    if result.status in {
        FinancialAdmissionStatus.ADMITTED,
        FinancialAdmissionStatus.ALREADY_ADMITTED,
    }:
        assert result.reservation is not None
        return result.reservation
    if result.status is FinancialAdmissionStatus.BUSY:
        raise ReservationAdmissionBusy(result.reason)
    if result.reason in {"RESERVATION_CONFLICT", "IDEMPOTENCY_CONTEXT_CONFLICT"}:
        raise ReservationConflict(result.reason)
    raise ReservationAdmissionRejected(result.reason)


def test_partial_unique_index_exists_and_matches_non_terminal_states(tmp_path):
    store = make_store(tmp_path)
    row = store._connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        ("ux_reservations_non_terminal_proposal",),
    ).fetchone()
    assert row is not None
    sql = " ".join(row[0].split())
    assert "CREATE UNIQUE INDEX ux_reservations_non_terminal_proposal" in sql
    assert "WHERE state IN ('ACTIVE', 'PARTIALLY_CONSUMED', 'UNKNOWN')" in sql
    store.close()


def test_sqlite_runtime_supports_partial_index():
    assert sqlite3.sqlite_version_info >= (3, 8, 0)


def test_duplicate_non_terminal_data_blocks_schema_upgrade(tmp_path):
    path = tmp_path / DB_NAME
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE reservations (
            reservation_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            resource_kind TEXT NOT NULL,
            asset TEXT NOT NULL,
            reserved_amount TEXT NOT NULL,
            consumed_amount TEXT NOT NULL,
            remaining_amount TEXT NOT NULL,
            state TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            risk_decision_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            client_order_id TEXT,
            exchange_order_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE reservation_transitions (
            transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            evidence_reference_id TEXT NOT NULL,
            consumed_delta TEXT NOT NULL,
            FOREIGN KEY(reservation_id) REFERENCES reservations(reservation_id)
        );
        INSERT INTO reservations VALUES
        ('r1','account-g1','QUOTE','USDT','10','0','10','ACTIVE','proposal-dup','risk-1','corr-1',NULL,NULL,'2026-10-04T14:00:00+00:00','2026-10-04T14:00:00+00:00'),
        ('r2','account-g1','QUOTE','USDT','20','0','20','UNKNOWN','proposal-dup','risk-2','corr-2',NULL,NULL,'2026-10-04T14:00:00+00:00','2026-10-04T14:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(ReservationSchemaConflict, match="multiplicity"):
        SQLiteReservationStore(path)


def test_admission_uses_begin_immediate_and_commits(tmp_path):
    store = make_store(tmp_path)
    traces = []
    store._connection.set_trace_callback(traces.append)

    admit(store, amount="10", reservation_id="trace-10")

    assert any(statement.startswith("BEGIN IMMEDIATE") for statement in traces)
    assert "COMMIT" in traces
    store.close()


def test_store_controls_creation_evidence_timestamp(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal(proposal_id="store-owned-evidence")
    request = make_admission_request_for_amount(proposal, "10", state=make_state())

    reservation = store.admit(request=request)
    transitions = store.transitions(reservation.reservation_id)

    assert len(transitions) == 1
    assert transitions[0].occurred_at == request.created_at
    assert transitions[0].evidence_kind == "RESERVATION_CREATED"
    assert transitions[0].evidence_reference_id == proposal.proposal_id
    store.close()


def test_admit_is_atomic_happy_path_and_creates_transition(tmp_path):
    store = make_store(tmp_path)

    reservation = admit(store, amount="30", reservation_id="admit-30")

    assert reservation.state is ReservationState.ACTIVE
    assert reservation.protected_capacity == Decimal("30")
    assert store.get(reservation.reservation_id) == reservation
    transitions = store.transitions(reservation.reservation_id)
    assert len(transitions) == 1
    assert transitions[0].from_state is None
    assert transitions[0].to_state is ReservationState.ACTIVE
    assert transitions[0].consumed_delta == Decimal("0")

    read_set = store.read_set_for_account(ACCOUNT)
    effective = calculate_effective_capacity(
        make_state(),
        read_set,
        resource_kind=RESOURCE,
        asset=ASSET,
    )
    assert effective.effective_available == Decimal("70")
    store.close()


@pytest.mark.parametrize(
    "amount",
    [Decimal("0"), Decimal("-1")],
)
def test_admit_rejects_tampered_nonpositive_amount(tmp_path, amount):
    store = make_store(tmp_path)
    request = make_admission_request_for_amount(make_proposal(), "10", state=make_state())
    object.__setattr__(request, "approved_reserved_amount", amount)

    with pytest.raises(ReservationAdmissionRejected, match="canonical semantic validation"):
        store.admit(request=request)

    assert store.read_set_for_account(ACCOUNT).reservations == ()
    store.close()


def test_admit_rejects_tampered_incomplete_canonical_snapshot(tmp_path):
    store = make_store(tmp_path)
    for completeness in (Completeness.PARTIAL, Completeness.UNKNOWN):
        request = make_admission_request_for_amount(make_proposal(), "10", state=make_state())
        object.__setattr__(
            request,
            "canonical_account_state",
            make_state(completeness=completeness),
        )
        with pytest.raises(ReservationAdmissionRejected, match="canonical semantic validation"):
            store.admit(request=request)

    assert store.read_set_for_account(ACCOUNT).reservations == ()
    store.close()


def test_admit_exact_capacity_boundary_is_allowed(tmp_path):
    store = make_store(tmp_path)
    first = admit(store, amount="60", reservation_id="first-60")
    assert first.protected_capacity == Decimal("60")

    second = admit(store, amount="40", reservation_id="second-40")
    assert second.protected_capacity == Decimal("40")

    effective = calculate_effective_capacity(
        make_state(),
        store.read_set_for_account(ACCOUNT),
        resource_kind=RESOURCE,
        asset=ASSET,
    )
    assert effective.effective_available == Decimal("0")
    assert effective.status is EffectiveCapacityStatus.AVAILABLE
    store.close()


def test_admit_rejects_capacity_overage(tmp_path):
    store = make_store(tmp_path)
    admit(store, amount="60", reservation_id="first-60")
    proposal = make_proposal()

    with pytest.raises(ReservationAdmissionRejected, match="exceeds"):
        admit(store, amount="41", proposal=proposal, state=make_state())

    read_set = store.read_set_for_account(ACCOUNT)
    assert sum((item.protected_capacity for item in read_set.relevant_reservations), Decimal("0")) == Decimal("60")
    store.close()


def test_unknown_reservation_remains_protected(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal(proposal_id="unknown-proposal")
    admitted = admit(store, amount="70", proposal=proposal, reservation_id="unknown-70")
    unknown = store.mark_unknown(
        admitted.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="VENUE_TIMEOUT",
            reference_id="unknown-70",
            occurred_at=store.get(admitted.reservation_id).updated_at + timedelta(seconds=1),
        ),
    )

    assert unknown.state is ReservationState.UNKNOWN
    assert unknown.protected_capacity == Decimal("70")

    other = make_proposal(proposal_id="other-proposal")
    with pytest.raises(ReservationAdmissionRejected, match="exceeds"):
        admit(store, amount="31", proposal=other, state=make_state())
    store.close()


def test_existing_overcommit_is_not_clamped(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal(proposal_id="legacy-overcommit")
    reservation = __import__("bot_obrero.reservation", fromlist=["Reservation"]).Reservation.from_trade_proposal_and_risk_decision(
        proposal=proposal,
        risk_decision=make_risk_decision(proposal),
        account_id=ACCOUNT,
        resource_kind=RESOURCE,
        asset=ASSET,
        reserved_amount=Decimal("120"),
        created_at=BASE,
        reservation_id="legacy-120",
    )
    store._insert_unbound_fixture(reservation, evidence=evidence(proposal))

    with pytest.raises(ReservationAdmissionRejected, match="OVERCOMMITTED"):
        admit(store, amount="1", reservation_id="new-1")

    effective = calculate_effective_capacity(
        make_state(),
        store.read_set_for_account(ACCOUNT),
        resource_kind=RESOURCE,
        asset=ASSET,
    )
    assert effective.effective_available == Decimal("-20")
    assert effective.status is EffectiveCapacityStatus.OVERCOMMITTED
    store.close()


def test_same_proposal_can_be_reused_only_after_terminal_state(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal(proposal_id="reusable-proposal")
    first = admit(store, amount="30", proposal=proposal, reservation_id="first")
    store.release(
        first.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="ORDER_RELEASE",
            reference_id="first-release",
            occurred_at=first.updated_at + timedelta(seconds=1),
        ),
    )

    second = admit(
        store,
        amount="20",
        proposal=proposal,
        reservation_id="second",
        decision_suffix="terminal-retry",
    )
    assert second.proposal_id == proposal.proposal_id
    assert {
        item.state for item in store.list_for_proposal(proposal.proposal_id)
    } == {ReservationState.RELEASED, ReservationState.ACTIVE}
    store.close()


def test_same_nonterminal_admission_repeats_idempotently(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal(proposal_id="duplicate-proposal")
    first = admit(store, amount="20", proposal=proposal, reservation_id="first")
    second = admit(store, amount="20", proposal=proposal, reservation_id="second")

    assert second.reservation_id == first.reservation_id
    assert len(store.list_for_proposal(proposal.proposal_id)) == 1
    store.close()


def test_risk_rejection_rolls_back_and_does_not_persist(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal()
    request = make_admission_request_for_amount(proposal, "10", state=make_state())
    object.__setattr__(
        request,
        "risk_decision",
        make_risk_decision(proposal, outcome=RiskDecisionOutcome.REJECTED),
    )

    with pytest.raises(ReservationAdmissionRejected, match="canonical semantic validation"):
        store.admit(request=request)

    assert store.read_set_for_account(ACCOUNT).reservations == ()
    store.close()


def test_artificial_failure_after_reservation_insert_rolls_back_everything(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    proposal = make_proposal(proposal_id="rollback-proposal")
    request = make_admission_request_for_amount(proposal, "25", state=make_state())

    original = store._insert_transition

    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("ARTIFICIAL_G1_FAILURE")

    monkeypatch.setattr(store, "_insert_transition", fail_after_insert)

    result = FinancialAdmissionBoundary(store).admit(request)
    assert result.status is FinancialAdmissionStatus.REJECTED
    assert store.list_for_proposal(proposal.proposal_id) == ()
    assert store._connection.execute("SELECT COUNT(*) FROM reservations").fetchone() == (0,)
    assert store._connection.execute("SELECT COUNT(*) FROM reservation_transitions").fetchone() == (0,)
    assert store._connection.execute("SELECT COUNT(*) FROM reservation_authorization_bindings").fetchone() == (0,)
    store.close()


def test_sqlite_busy_fails_closed(tmp_path):
    holder = SQLiteReservationStore(tmp_path / DB_NAME)
    contender = SQLiteReservationStore(tmp_path / DB_NAME)
    contender._connection.execute("PRAGMA busy_timeout=0")

    holder._connection.execute("BEGIN IMMEDIATE")
    request = make_admission_request_for_amount(make_proposal(), "10", state=make_state())

    with pytest.raises(ReservationAdmissionBusy, match="busy"):
        contender.admit(request=request)

    holder._connection.rollback()
    contender.close()
    holder.close()


def _cross_process_worker(
    path: str,
    amount: str,
    proposal_id: str,
    correlation_id: str,
    barrier,
    results,
) -> None:
    store = None
    try:
        store = SQLiteReservationStore(path)
        barrier.wait(timeout=15)
        proposal = make_proposal(
            proposal_id=proposal_id,
            correlation_id=correlation_id,
        )
        request = make_admission_request_for_amount(proposal, amount, state=make_state())
        result = FinancialAdmissionBoundary(store).admit(request)
        if result.status in {
            FinancialAdmissionStatus.ADMITTED,
            FinancialAdmissionStatus.ALREADY_ADMITTED,
        }:
            assert result.reservation is not None
            results.put(("accepted", amount, result.reservation.reservation_id))
        else:
            results.put(("rejected", amount, result.status.value, result.reason))
    except Exception as exc:
        results.put(("rejected", amount, type(exc).__name__, str(exc)))
    finally:
        if store is not None:
            store.close()


def run_cross_process_race(tmp_path: Path, left_amount: str, right_amount: str, *, same_proposal: bool = False):
    path = str(tmp_path / DB_NAME)
    seed = SQLiteReservationStore(path)
    seed.close()

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    results = ctx.Queue()
    proposal_id = "shared-proposal" if same_proposal else None

    processes = [
        ctx.Process(
            target=_cross_process_worker,
            args=(
                path,
                left_amount,
                proposal_id or f"proposal-{left_amount}",
                "shared-correlation" if same_proposal else f"corr-{left_amount}",
                barrier,
                results,
            ),
        ),
        ctx.Process(
            target=_cross_process_worker,
            args=(
                path,
                right_amount,
                proposal_id or f"proposal-{right_amount}",
                "shared-correlation" if same_proposal else f"corr-{right_amount}",
                barrier,
                results,
            ),
        ),
    ]

    for process in processes:
        process.start()

    outcomes = [results.get(timeout=30) for _ in processes]

    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    return outcomes, path


def assert_no_overcommit(path: str) -> tuple[Decimal, tuple]:
    store = SQLiteReservationStore(path)
    read_set = store.read_set_for_account(ACCOUNT)
    effective = calculate_effective_capacity(
        make_state(),
        read_set,
        resource_kind=RESOURCE,
        asset=ASSET,
    )
    protected = sum(
        (item.protected_capacity for item in read_set.relevant_reservations),
        Decimal("0"),
    )
    rows = read_set.reservation_count, tuple(
        (item.reservation_id, item.state, item.protected_capacity)
        for item in read_set.relevant_reservations
    )
    assert protected <= Decimal("100")
    assert effective.effective_available >= Decimal("0")
    store.close()
    return protected, rows


def test_cross_process_70_50_accepts_exactly_one_and_never_120(tmp_path):
    outcomes, path = run_cross_process_race(tmp_path, "70", "50")

    accepted = [item for item in outcomes if item[0] == "accepted"]
    rejected = [item for item in outcomes if item[0] == "rejected"]

    assert len(accepted) == 1
    assert len(rejected) == 1
    protected, _ = assert_no_overcommit(path)
    assert protected in {Decimal("50"), Decimal("70")}


def test_cross_process_60_40_can_accept_both_and_reaches_100(tmp_path):
    outcomes, path = run_cross_process_race(tmp_path, "60", "40")

    assert sum(1 for item in outcomes if item[0] == "accepted") == 2
    assert sum(1 for item in outcomes if item[0] == "rejected") == 0
    protected, _ = assert_no_overcommit(path)
    assert protected == Decimal("100")


def test_cross_process_60_41_rejects_one_and_never_101(tmp_path):
    outcomes, path = run_cross_process_race(tmp_path, "60", "41")

    assert sum(1 for item in outcomes if item[0] == "accepted") == 1
    assert sum(1 for item in outcomes if item[0] == "rejected") == 1
    protected, _ = assert_no_overcommit(path)
    assert protected in {Decimal("60"), Decimal("41")}


def test_cross_process_same_proposal_creates_exactly_one_non_terminal_reservation(tmp_path):
    outcomes, path = run_cross_process_race(
        tmp_path,
        "60",
        "40",
        same_proposal=True,
    )

    accepted = [item for item in outcomes if item[0] == "accepted"]
    rejected = [item for item in outcomes if item[0] == "rejected"]

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert (
        "ReservationConflict" in rejected[0][2]
        or "reservation" in rejected[0][3].lower()
        or "IDEMPOTENCY_CONTEXT_CONFLICT" in rejected[0][3]
    )

    store = SQLiteReservationStore(path)
    reservations = store.list_for_proposal("shared-proposal")
    assert len(reservations) == 1
    assert reservations[0].state in {
        ReservationState.ACTIVE,
        ReservationState.PARTIALLY_CONSUMED,
        ReservationState.UNKNOWN,
    }
    store.close()


def test_cross_process_results_are_explicit_about_fail_closed_rejection(tmp_path):
    outcomes, _ = run_cross_process_race(tmp_path, "70", "50")
    rejected = [item for item in outcomes if item[0] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0][2] in {"REJECTED", "BUSY"}


def test_admission_preserves_decimal_amounts_and_terminal_zero_protection(tmp_path):
    store = make_store(tmp_path)
    proposal = make_proposal(proposal_id="decimal-proposal")
    reservation = admit(store, amount="12.345", proposal=proposal)

    assert type(reservation.reserved_amount) is Decimal
    assert type(reservation.remaining_amount) is Decimal

    released = store.release(
        reservation.reservation_id,
        evidence=ReservationTransitionEvidence(
            kind="RELEASE",
            reference_id="release-decimal",
            occurred_at=reservation.updated_at + timedelta(seconds=1),
        ),
    )
    assert released.protected_capacity == Decimal("0")

    effective = calculate_effective_capacity(
        make_state(),
        store.read_set_for_account(ACCOUNT),
        resource_kind=RESOURCE,
        asset=ASSET,
    )
    assert effective.effective_available == Decimal("100")
    store.close()
