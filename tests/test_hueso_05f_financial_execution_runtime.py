from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Lock

import pytest

from bot_obrero.execution import EvidenceBundle, EvidenceRecord, ExecutionOrchestrator, ExchangeAdapter
from bot_obrero.financial_admission import FinancialAdmissionBoundary
from bot_obrero.murphy import MurphyGuard
from bot_obrero.persistent_ledger import SQLiteIdempotencyLedger
from bot_obrero.reservation import ReservationState, SQLiteReservationStore
from bot_obrero.reservation_execution_bridge import ReservationExecutionBridge
from bot_obrero.risk_contracts import RiskDecisionOutcome
from bot_obrero.risk_engine import RiskEngine
from bot_obrero.risk_limit_applicability import RiskLimitApplicabilityResolver
from bot_obrero.financial_execution_runtime import (
    FinancialExecutionRuntime,
    FinancialExecutionRuntimeStatus as Status,
)
from test_hueso_02h2_financial_admission import (
    ACCOUNT,
    BASE,
    make_context,
    make_policy,
    make_proposal,
    make_request,
    make_state,
)

REQUIRED = (
    "reconciliation_match",
    "clock_valid",
    "stream_ready",
    "resources_healthy",
    "permissions_safe",
    "configuration_valid",
    "protection_safe",
)


class FakeAdapter(ExchangeAdapter):
    def __init__(self, error=None):
        self.error = error
        self.orders = []
        self._lock = Lock()

    @property
    def submits(self):
        with self._lock:
            return len(self.orders)

    def _submit(self, order):
        with self._lock:
            self.orders.append(order)
        if self.error is not None:
            raise self.error
        return {"local_fake_ack": True}

    def _cancel(self, order):
        return None

    def snapshot(self):
        return None


def evidence_provider(runtime, *, invalid_kind=None, wrong_order=False, raises=False, missing_kind=None, stale=False, future=False, wrong_correlation=False, duplicate_kind=None):
    def provide(prepared):
        if raises:
            raise RuntimeError("evidence source unavailable")
        binding = runtime.reservation_bridge.verify_prepared(prepared)
        correlation_id = binding.payload["correlation_id"]
        intent_id = "different-client-order" if wrong_order else prepared.client_order_id
        record_correlation = "different-correlation" if wrong_correlation else correlation_id
        observed_at = BASE + timedelta(minutes=2) if future else BASE
        expires_at = BASE + timedelta(seconds=30) if stale else BASE + timedelta(minutes=5)
        kinds = tuple(kind for kind in REQUIRED if kind != missing_kind)
        records_list = [
            EvidenceRecord(
                kind=kind,
                value=False if kind == invalid_kind else True,
                source="explicit-test-observer",
                observed_at=observed_at,
                decision_at=BASE,
                intent_id=intent_id,
                correlation_id=record_correlation,
                expires_at=expires_at,
            )
            for kind in kinds
        ]
        if duplicate_kind is not None:
            records_list.append(next(record for record in records_list if record.kind == duplicate_kind))
        return EvidenceBundle(tuple(records_list), record_correlation)
    return provide


def compose(path: Path, *, adapter=None, clock=None):
    store = SQLiteReservationStore(path / "reservations.sqlite")
    bridge = ReservationExecutionBridge(store)
    ledger = SQLiteIdempotencyLedger(path / "ledger.sqlite")
    active_adapter = adapter or FakeAdapter()
    orchestrator = ExecutionOrchestrator(
        adapter=active_adapter,
        ledger=ledger,
        murphy_guard=MurphyGuard(),
        reservation_bridge=bridge,
        clock=clock or (lambda: BASE + timedelta(minutes=1)),
    )
    runtime = FinancialExecutionRuntime(
        store=store,
        risk_engine=RiskEngine(),
        risk_limit_resolver=RiskLimitApplicabilityResolver(),
        admission_boundary=FinancialAdmissionBoundary(store),
        reservation_bridge=bridge,
        execution_orchestrator=orchestrator,
    )
    return runtime, store, bridge, ledger, active_adapter


def close_all(store, ledger):
    ledger.close()
    store.close()


def canonical_inputs(proposal_id="proposal-runtime"):
    proposal = make_proposal(proposal_id=proposal_id)
    state = make_state()
    context = make_context(proposal, state)
    policy = make_policy()
    return proposal, state, context, policy


def start(runtime, proposal, state, context, policy, provider=None):
    return runtime.start(
        proposal=proposal,
        context=context,
        policy=policy,
        canonical_account_state=state,
        account_id=state.account_id,
        created_at=BASE + timedelta(seconds=30),
        evidence_provider=provider or evidence_provider(runtime),
    )


def test_happy_path_uses_real_risk_admission_bridge_and_orchestrator(tmp_path):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs()
    result = start(runtime, proposal, state, context, policy)

    assert result.status is Status.SUBMITTED
    assert result.proposal_id == proposal.proposal_id
    assert result.reservation_id
    assert result.execution_result is not None and result.execution_result.reason == "SUBMITTED"
    reservations = store.list_for_proposal(proposal.proposal_id)
    assert len(reservations) == 1
    reservation = store.get(result.reservation_id)
    assert reservation is not None and reservation.client_order_id
    assert store.get_trade_terms_snapshot(result.reservation_id) is not None
    assert store.get_authorization_binding(result.reservation_id) is not None
    binding = bridge.get_binding(result.reservation_id)
    assert binding is not None and binding.state == "SUBMITTED"
    assert adapter.submits == 1
    assert ledger.get(reservation.client_order_id).result == "SUBMITTED"
    # The adapter receives the persisted canonical intent, not caller economics.
    assert adapter.orders[0].client_order_id == reservation.client_order_id
    close_all(store, ledger)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (RiskDecisionOutcome.REJECTED, Status.RISK_REJECTED),
        (RiskDecisionOutcome.UNKNOWN, Status.RISK_UNKNOWN),
    ],
)
def test_non_approved_risk_never_admits_or_sends(tmp_path, monkeypatch, outcome, expected):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-risk-" + outcome.value)
    original = runtime.risk_engine.evaluate

    def decision_with_outcome(**kwargs):
        decision = original(**kwargs)
        from dataclasses import replace
        return replace(decision, outcome=outcome, reason="TEST_NON_APPROVED")

    monkeypatch.setattr(runtime.risk_engine, "evaluate", decision_with_outcome)
    result = start(runtime, proposal, state, context, policy)
    assert result.status is expected
    assert store.list_for_proposal(proposal.proposal_id) == ()
    assert adapter.submits == 0
    close_all(store, ledger)


def test_context_account_mismatch_fails_before_risk(tmp_path, monkeypatch):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-mismatch")
    monkeypatch.setattr(
        runtime.risk_engine, "evaluate",
        lambda **kwargs: pytest.fail("RiskEngine must not run for mismatched canonical inputs"),
    )
    other_state = make_state(account_id="other-account")
    result = start(runtime, proposal, other_state, context, policy)
    assert result.status is Status.RISK_UNKNOWN
    assert store.list_for_proposal(proposal.proposal_id) == ()
    assert adapter.submits == 0
    close_all(store, ledger)


@pytest.mark.parametrize("bad_provider", ["missing", "false", "wrong_order", "raises", "missing_kind", "stale", "future", "wrong_correlation", "duplicate_kind"])
def test_invalid_evidence_preserves_prepared_and_never_sends(tmp_path, bad_provider):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-evidence-" + bad_provider)
    if bad_provider == "missing":
        provider = lambda prepared: None
    elif bad_provider == "false":
        provider = evidence_provider(runtime, invalid_kind="permissions_safe")
    elif bad_provider == "wrong_order":
        provider = evidence_provider(runtime, wrong_order=True)
    elif bad_provider == "missing_kind":
        provider = evidence_provider(runtime, missing_kind="stream_ready")
    elif bad_provider == "stale":
        provider = evidence_provider(runtime, stale=True)
    elif bad_provider == "future":
        provider = evidence_provider(runtime, future=True)
    elif bad_provider == "wrong_correlation":
        provider = evidence_provider(runtime, wrong_correlation=True)
    elif bad_provider == "duplicate_kind":
        provider = evidence_provider(runtime, duplicate_kind="clock_valid")
    else:
        provider = evidence_provider(runtime, raises=True)
    result = start(runtime, proposal, state, context, policy, provider)
    assert result.status is Status.READINESS_BLOCKED
    assert result.reservation_id
    binding = bridge.get_binding(result.reservation_id)
    assert binding is not None and binding.state == "PREPARED"
    assert store.get(result.reservation_id).state is ReservationState.ACTIVE
    assert adapter.submits == 0
    assert ledger.get(binding.client_order_id) is None
    close_all(store, ledger)


def test_existing_proposal_is_detected_before_risk_and_requires_explicit_resume(tmp_path, monkeypatch):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-existing")
    request = make_request(proposal=proposal, account_id=state.account_id, state=state)
    admitted = runtime.admission_boundary.admit(request)
    assert admitted.reservation is not None
    monkeypatch.setattr(
        runtime.risk_engine, "evaluate",
        lambda **kwargs: pytest.fail("Existing proposal must be detected before risk evaluation"),
    )
    result = start(runtime, proposal, state, context, policy)
    assert result.status is Status.RESUME_REQUIRED
    assert result.reservation_id == admitted.reservation.reservation_id
    assert adapter.submits == 0
    close_all(store, ledger)


def test_resume_reuses_persisted_binding_without_risk_or_admission(tmp_path, monkeypatch):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-resume")
    request = make_request(proposal=proposal, account_id=state.account_id, state=state)
    admitted = runtime.admission_boundary.admit(request)
    reservation_id = admitted.reservation.reservation_id
    prepared_before_restart = bridge.prepare(reservation_id)
    assert prepared_before_restart.status.value == "PREPARED"
    # Simulate process restart: close both SQLite handles and compose new objects.
    close_all(store, ledger)
    runtime2, store2, bridge2, ledger2, adapter2 = compose(tmp_path)
    monkeypatch.setattr(runtime2.risk_engine, "evaluate", lambda **kwargs: pytest.fail("resume must not evaluate risk"))
    monkeypatch.setattr(runtime2.admission_boundary, "admit", lambda request: pytest.fail("resume must not re-admit"))
    result = runtime2.resume(reservation_id, evidence_provider=evidence_provider(runtime2))
    assert result.status is Status.SUBMITTED
    assert result.reservation_id == reservation_id
    assert adapter2.submits == 1
    assert bridge2.get_binding(reservation_id).state == "SUBMITTED"
    close_all(store2, ledger2)


def test_submitted_reservation_resume_never_sends_twice(tmp_path):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-no-resend")
    result1 = start(runtime, proposal, state, context, policy)
    assert result1.status is Status.SUBMITTED
    result2 = runtime.resume(result1.reservation_id, evidence_provider=evidence_provider(runtime))
    assert result2.status is Status.ALREADY_SUBMITTED
    assert adapter.submits == 1
    close_all(store, ledger)


def test_missing_reservation_is_not_inferred_or_recreated(tmp_path):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    result = runtime.resume("reservation-does-not-exist", evidence_provider=evidence_provider(runtime))
    assert result.status is Status.RESERVATION_NOT_FOUND
    assert adapter.submits == 0
    close_all(store, ledger)


def test_constructor_rejects_split_persistence_composition(tmp_path):
    store1 = SQLiteReservationStore(tmp_path / "one.sqlite")
    store2 = SQLiteReservationStore(tmp_path / "two.sqlite")
    bridge = ReservationExecutionBridge(store1)
    ledger = SQLiteIdempotencyLedger(tmp_path / "ledger.sqlite")
    orchestrator = ExecutionOrchestrator(
        adapter=FakeAdapter(), ledger=ledger, murphy_guard=MurphyGuard(),
        reservation_bridge=bridge, clock=lambda: BASE + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="RUNTIME_COMPONENTS_MUST_SHARE_RESERVATION_STORE"):
        FinancialExecutionRuntime(
            store=store1, risk_engine=RiskEngine(),
            risk_limit_resolver=RiskLimitApplicabilityResolver(),
            admission_boundary=FinancialAdmissionBoundary(store2),
            reservation_bridge=bridge, execution_orchestrator=orchestrator,
        )
    close_all(store1, ledger)
    store2.close()


def test_adapter_ambiguous_exception_is_unknown_and_resume_does_not_resend(tmp_path):
    adapter = FakeAdapter(TimeoutError("ambiguous timeout"))
    runtime, store, bridge, ledger, _ = compose(tmp_path, adapter=adapter)
    proposal, state, context, policy = canonical_inputs("proposal-ambiguous")
    result = start(runtime, proposal, state, context, policy)
    assert result.status is Status.UNKNOWN
    assert adapter.submits == 1
    binding = bridge.get_binding(result.reservation_id)
    assert binding is not None and binding.state == "UNKNOWN"
    resumed = runtime.resume(result.reservation_id, evidence_provider=evidence_provider(runtime))
    assert resumed.status is Status.UNKNOWN
    assert adapter.submits == 1
    close_all(store, ledger)


def test_two_concurrent_starts_never_send_twice_for_same_proposal(tmp_path):
    proposal, state, context, policy = canonical_inputs("proposal-concurrent")
    adapter = FakeAdapter()
    db_path = tmp_path / "reservations.sqlite"
    ledger_path = tmp_path / "ledger.sqlite"

    def worker():
        store = SQLiteReservationStore(db_path)
        bridge = ReservationExecutionBridge(store)
        ledger = SQLiteIdempotencyLedger(ledger_path)
        orchestrator = ExecutionOrchestrator(
            adapter=adapter, ledger=ledger, murphy_guard=MurphyGuard(),
            reservation_bridge=bridge, clock=lambda: BASE + timedelta(seconds=1),
        )
        runtime = FinancialExecutionRuntime(
            store=store, risk_engine=RiskEngine(),
            risk_limit_resolver=RiskLimitApplicabilityResolver(),
            admission_boundary=FinancialAdmissionBoundary(store),
            reservation_bridge=bridge, execution_orchestrator=orchestrator,
        )
        try:
            return start(runtime, proposal, state, context, policy)
        finally:
            close_all(store, ledger)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert adapter.submits <= 1
    store = SQLiteReservationStore(db_path)
    reservations = store.list_for_proposal(proposal.proposal_id)
    assert len([r for r in reservations if r.state in {ReservationState.ACTIVE, ReservationState.PARTIALLY_CONSUMED, ReservationState.UNKNOWN}]) <= 1
    assert any(result.status is Status.SUBMITTED for result in results) or any(
        result.status is Status.RESUME_REQUIRED for result in results
    ) or any(result.status in {Status.ADMISSION_BUSY, Status.ADMISSION_REJECTED, Status.UNKNOWN} for result in results)
    store.close()


def test_crash_left_submission_marker_is_recovered_locally_without_resend(tmp_path):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-crash-marker")
    request = make_request(proposal=proposal, account_id=state.account_id, state=state)
    admitted = runtime.admission_boundary.admit(request)
    reservation_id = admitted.reservation.reservation_id
    prepared = bridge.prepare(reservation_id)
    assert prepared.intent is not None
    bridge.begin_submission(prepared.intent, occurred_at=BASE + timedelta(minutes=1))

    result = runtime.resume(reservation_id, evidence_provider=evidence_provider(runtime))
    assert result.status is Status.UNKNOWN
    binding = bridge.get_binding(reservation_id)
    assert binding is not None and binding.state == "UNKNOWN"
    assert adapter.submits == 0
    assert ledger.get(binding.client_order_id).result == "ORDER_RESULT_UNKNOWN"
    close_all(store, ledger)


def test_submitted_ledger_projection_is_repaired_without_second_send(tmp_path):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-ledger-repair")
    result = start(runtime, proposal, state, context, policy)
    assert result.status is Status.SUBMITTED
    binding = bridge.get_binding(result.reservation_id)
    assert binding is not None and binding.state == "SUBMITTED"
    ledger._connection.execute(
        "DELETE FROM idempotency_ledger WHERE client_order_id=?",
        (binding.client_order_id,),
    )
    ledger._connection.commit()
    assert ledger.get(binding.client_order_id) is None

    resumed = runtime.resume(result.reservation_id, evidence_provider=evidence_provider(runtime))
    assert resumed.status is Status.ALREADY_SUBMITTED
    assert ledger.get(binding.client_order_id).result == "SUBMITTED"
    assert adapter.submits == 1
    close_all(store, ledger)


@pytest.mark.parametrize("missing", ["snapshot", "authorization", "execution_binding"])
def test_resume_blocks_missing_persisted_integrity_record(tmp_path, missing):
    runtime, store, bridge, ledger, adapter = compose(tmp_path)
    proposal, state, context, policy = canonical_inputs("proposal-missing-" + missing)
    request = make_request(proposal=proposal, account_id=state.account_id, state=state)
    admitted = runtime.admission_boundary.admit(request)
    reservation_id = admitted.reservation.reservation_id
    if missing == "snapshot":
        store._connection.execute(
            "DELETE FROM reservation_trade_terms_snapshots WHERE reservation_id=?",
            (reservation_id,),
        )
    elif missing == "authorization":
        store._connection.execute(
            "DELETE FROM reservation_authorization_bindings WHERE reservation_id=?",
            (reservation_id,),
        )
    else:
        assert bridge.get_binding(reservation_id) is None
    store._connection.commit()

    result = runtime.resume(reservation_id, evidence_provider=evidence_provider(runtime))
    assert result.status is Status.BLOCKED
    assert adapter.submits == 0
    close_all(store, ledger)
