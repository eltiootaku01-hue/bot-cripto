from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from threading import Barrier

import pytest

from bot_obrero.execution import EvidenceBundle, EvidenceRecord, ExecutionOrchestrator, OrderIntent, ExchangeAdapter
from bot_obrero.financial_admission import FinancialAdmissionBoundary, FinancialAdmissionStatus
from bot_obrero.murphy import GuardState, MurphyGuard
from bot_obrero.persistent_ledger import SQLiteIdempotencyLedger
from bot_obrero.reservation import ReservationState, ReservationTransitionEvidence, SQLiteReservationStore
from bot_obrero.reservation_execution_bridge import (
    ExecutionBridgeStatus,
    PreparedExecutionIntent,
    ReservationExecutionBridge,
)

from test_hueso_05d_final_admission import make_request

UTC = timezone.utc
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
REQUIRED = (
    "reconciliation_match", "clock_valid", "stream_ready", "resources_healthy",
    "permissions_safe", "configuration_valid", "protection_safe",
)


class FakeAdapter(ExchangeAdapter):
    def __init__(self, error=None):
        self.error = error
        self.submits = 0
        self.orders = []

    def _submit(self, order):
        self.submits += 1
        self.orders.append(order)
        if self.error:
            raise self.error
        return {"returned": True}

    def _cancel(self, order):
        return None

    def snapshot(self):
        return None


def make_evidence(intent_id: str, correlation_id: str, *, false_kind: str | None = None):
    return EvidenceBundle(
        tuple(
            EvidenceRecord(
                kind=kind,
                value=False if kind == false_kind else True,
                source="test-evidence-observer",
                observed_at=NOW,
                decision_at=NOW,
                intent_id=intent_id,
                correlation_id=correlation_id,
                expires_at=NOW + timedelta(minutes=5),
            )
            for kind in REQUIRED
        ),
        correlation_id,
    )


def admit(path):
    store = SQLiteReservationStore(path)
    request = make_request()
    result = FinancialAdmissionBoundary(store).admit(request)
    assert result.status is FinancialAdmissionStatus.ADMITTED
    assert result.reservation is not None
    return store, result.reservation.reservation_id, request


def setup_orchestrator(path, *, adapter=None, store_path=None, ledger_path=None, clock=None):
    reservation_path = store_path or (path / "reservations.sqlite")
    store, reservation_id, request = admit(reservation_path)
    bridge = ReservationExecutionBridge(store)
    prepared_result = bridge.prepare(reservation_id)
    assert prepared_result.status is ExecutionBridgeStatus.PREPARED
    assert prepared_result.intent is not None
    prepared = prepared_result.intent
    ledger = SQLiteIdempotencyLedger(ledger_path or (path / "ledger.sqlite"))
    active_adapter = adapter or FakeAdapter()
    guard = MurphyGuard()
    orchestrator = ExecutionOrchestrator(
        adapter=active_adapter,
        ledger=ledger,
        murphy_guard=guard,
        reservation_bridge=bridge,
        clock=clock or (lambda: NOW + timedelta(seconds=1)),
    )
    payload = bridge.verify_prepared(prepared).payload
    evidence = make_evidence(prepared.client_order_id, payload["correlation_id"])
    return store, reservation_id, request, bridge, prepared, ledger, active_adapter, guard, orchestrator, evidence


def test_01_admission_persists_economic_snapshot_with_reservation_transition_and_auth_binding(tmp_path):
    store = SQLiteReservationStore(tmp_path / "reservation.sqlite")
    request = make_request()
    result = FinancialAdmissionBoundary(store).admit(request)
    assert result.status is FinancialAdmissionStatus.ADMITTED
    reservation = result.reservation
    snapshot = store.get_trade_terms_snapshot(reservation.reservation_id)
    binding = store.get_authorization_binding(reservation.reservation_id)
    assert snapshot is not None and binding is not None
    assert snapshot.terms_hash == hashlib.sha256(snapshot.canonical_json.encode("utf-8")).hexdigest()
    terms = snapshot.terms
    assert terms["proposal_id"] == request.proposal.proposal_id
    assert terms["signal_id"] == request.proposal.signal_id
    assert terms["symbol"] == request.context.instrument.symbol
    assert terms["instrument"]["instrument_id"] == request.context.instrument.instrument_id
    assert terms["side"] == request.proposal.side.value
    assert terms["requested_quantity"] == "2"
    assert terms["risk_decision_id"] == request.risk_decision.risk_decision_id
    assert terms["authorization"]["semantic_fingerprint"] == binding.semantic_fingerprint
    assert terms["resource_kind"] == reservation.resource_kind.value
    assert isinstance(Decimal(terms["reserved_amount"]), Decimal)
    assert store._connection.execute(
        "SELECT COUNT(*) FROM reservation_transitions WHERE reservation_id=?",
        (reservation.reservation_id,),
    ).fetchone()[0] == 1
    store.close()


def test_02_snapshot_write_failure_rolls_back_reservation_transition_and_binding(tmp_path, monkeypatch):
    store = SQLiteReservationStore(tmp_path / "reservation.sqlite")
    request = make_request()

    def fail_snapshot(**kwargs):
        raise OSError("injected snapshot failure")

    monkeypatch.setattr(store, "_insert_trade_terms_snapshot", fail_snapshot)
    result = FinancialAdmissionBoundary(store).admit(request)
    assert result.status is FinancialAdmissionStatus.REJECTED
    assert result.reservation is None
    for table in (
        "reservations", "reservation_transitions",
        "reservation_authorization_bindings", "reservation_trade_terms_snapshots",
    ):
        assert store._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    store.close()


def test_03_preparation_identity_survives_reopening_reservation_store(tmp_path):
    path = tmp_path / "reservation.sqlite"
    store, reservation_id, _ = admit(path)
    bridge = ReservationExecutionBridge(store)
    first = bridge.prepare(reservation_id)
    assert first.status is ExecutionBridgeStatus.PREPARED
    stable_identity = first.intent
    store.close()

    reopened = SQLiteReservationStore(path)
    second = ReservationExecutionBridge(reopened).prepare(reservation_id)
    assert second.status is ExecutionBridgeStatus.ALREADY_PREPARED
    assert second.intent == stable_identity
    reopened.close()


def test_04_missing_reservation_is_rejected(tmp_path):
    store = SQLiteReservationStore(tmp_path / "reservation.sqlite")
    result = ReservationExecutionBridge(store).prepare("missing-reservation")
    assert result.status is ExecutionBridgeStatus.REJECTED
    assert result.intent is None
    store.close()


@pytest.mark.parametrize(
    "target",
    [ReservationState.UNKNOWN, ReservationState.RELEASED, ReservationState.CONSUMED, ReservationState.PARTIALLY_CONSUMED],
)
def test_05_non_active_reservation_cannot_create_new_entry_binding(tmp_path, target):
    store, reservation_id, _ = admit(tmp_path / "reservation.sqlite")
    reservation = store.get(reservation_id)
    occurred = NOW + timedelta(seconds=1)
    if target is ReservationState.UNKNOWN:
        store.mark_unknown(reservation_id, evidence=ReservationTransitionEvidence("TEST_UNKNOWN", "evidence", occurred))
    elif target is ReservationState.RELEASED:
        store.release(reservation_id, evidence=ReservationTransitionEvidence("TEST_RELEASED", "evidence", occurred))
    elif target is ReservationState.CONSUMED:
        store.consume(reservation_id, reservation.reserved_amount, evidence=ReservationTransitionEvidence("TEST_CONSUMED", "evidence", occurred))
    else:
        store.consume(reservation_id, reservation.reserved_amount / Decimal("2"), evidence=ReservationTransitionEvidence("TEST_PARTIAL", "evidence", occurred))
    result = ReservationExecutionBridge(store).prepare(reservation_id)
    assert result.status is ExecutionBridgeStatus.REJECTED
    assert result.intent is None
    store.close()


def test_06_missing_authorization_binding_blocks_preparation(tmp_path):
    store, reservation_id, _ = admit(tmp_path / "reservation.sqlite")
    store._connection.execute(
        "DELETE FROM reservation_authorization_bindings WHERE reservation_id=?", (reservation_id,)
    )
    store._connection.commit()
    result = ReservationExecutionBridge(store).prepare(reservation_id)
    assert result.status is ExecutionBridgeStatus.BLOCKED
    assert result.intent is None
    store.close()


def test_07_snapshot_corruption_without_matching_digest_blocks_preparation(tmp_path):
    store, reservation_id, _ = admit(tmp_path / "reservation.sqlite")
    store._connection.execute(
        "UPDATE reservation_trade_terms_snapshots SET canonical_json=? WHERE reservation_id=?",
        ('{"schema":"tampered"}', reservation_id),
    )
    store._connection.commit()
    result = ReservationExecutionBridge(store).prepare(reservation_id)
    assert result.status is ExecutionBridgeStatus.BLOCKED
    assert result.intent is None
    store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "different-account"),
        ("side", "SELL"),
        ("order_type", "LIMIT"),
    ],
)
def test_08_cross_record_term_contradictions_block_preparation(tmp_path, field, value):
    store, reservation_id, _ = admit(tmp_path / "reservation.sqlite")
    row = store._connection.execute(
        "SELECT canonical_json FROM reservation_trade_terms_snapshots WHERE reservation_id=?",
        (reservation_id,),
    ).fetchone()
    terms = json.loads(row[0])
    terms[field] = value
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # Recomputing an unkeyed digest does not override the cross-record economic checks.
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    store._connection.execute(
        "UPDATE reservation_trade_terms_snapshots SET canonical_json=?, terms_hash=? WHERE reservation_id=?",
        (canonical, digest, reservation_id),
    )
    store._connection.commit()
    result = ReservationExecutionBridge(store).prepare(reservation_id)
    assert result.status is ExecutionBridgeStatus.BLOCKED
    assert result.intent is None
    store.close()


def test_09_economic_values_are_canonical_decimal_strings_and_never_float(tmp_path):
    store, reservation_id, _, bridge, prepared, *_ = setup_orchestrator(tmp_path)
    binding = bridge.verify_prepared(prepared)
    snapshot = store.get_trade_terms_snapshot(reservation_id)
    terms = snapshot.terms
    assert type(terms["requested_quantity"]) is str
    assert type(terms["reserved_amount"]) is str
    assert type(binding.payload["quantity"]) is str
    assert Decimal(binding.payload["quantity"]) == Decimal(terms["requested_quantity"])
    source = Path("bot_obrero/reservation_execution_bridge.py").read_text(encoding="utf-8")
    assert "float(" not in source
    store.close()


def test_10_prepared_identity_reuse_with_modified_hash_is_conflict(tmp_path):
    store, reservation_id, _, bridge, prepared, *_ = setup_orchestrator(tmp_path)
    altered = PreparedExecutionIntent(prepared.reservation_id, prepared.client_order_id, "0" * 64)
    with pytest.raises(Exception):
        bridge.verify_prepared(altered)
    assert bridge.prepare(reservation_id).status is ExecutionBridgeStatus.ALREADY_PREPARED
    store.close()


def test_11_concurrent_preparation_converges_to_one_stable_identity(tmp_path):
    path = tmp_path / "reservation.sqlite"
    store, reservation_id, _ = admit(path)
    store.close()
    barrier = Barrier(2)

    def worker():
        own_store = SQLiteReservationStore(path)
        own_bridge = ReservationExecutionBridge(own_store)
        barrier.wait()
        result = own_bridge.prepare(reservation_id)
        own_store.close()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert {r.status for r in results} == {
        ExecutionBridgeStatus.PREPARED, ExecutionBridgeStatus.ALREADY_PREPARED
    }
    assert len({r.intent for r in results}) == 1
    check = SQLiteReservationStore(path)
    assert check._connection.execute(
        "SELECT COUNT(*) FROM reservation_execution_bindings WHERE reservation_id=?",
        (reservation_id,),
    ).fetchone()[0] == 1
    check.close()


def test_12_generic_order_intent_cannot_reach_adapter(tmp_path):
    store, reservation_id, _, bridge, prepared, ledger, adapter, _, orch, evidence = setup_orchestrator(tmp_path)
    payload = bridge.verify_prepared(prepared).payload
    generic = OrderIntent(prepared.client_order_id, payload, payload["correlation_id"])
    result = orch.execute(generic, evidence)
    assert not result.allowed and result.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0
    assert ledger.get(prepared.client_order_id) is None
    store.close()
    ledger.close()


def test_13_pre_submission_persistence_failure_makes_zero_adapter_calls(tmp_path, monkeypatch):
    store, reservation_id, _, bridge, prepared, ledger, adapter, _, orch, evidence = setup_orchestrator(tmp_path)
    monkeypatch.setattr(bridge, "begin_submission", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("sqlite failure")))
    result = orch.execute(prepared, evidence)
    assert not result.allowed and result.reason == "BLOCKED"
    assert adapter.submits == 0
    assert ledger.get(prepared.client_order_id) is None
    assert bridge.get_binding(reservation_id).state == "PREPARED"
    store.close()
    ledger.close()


def test_14_ledger_failure_before_adapter_is_blocked_and_not_ambiguous(tmp_path, monkeypatch):
    store, reservation_id, _, bridge, prepared, ledger, adapter, guard, orch, evidence = setup_orchestrator(tmp_path)
    monkeypatch.setattr(ledger, "register", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("ledger unavailable")))
    result = orch.execute(prepared, evidence)
    assert not result.allowed and result.reason == "BLOCKED"
    assert adapter.submits == 0
    assert bridge.get_binding(reservation_id).state == "BLOCKED"
    assert store.get(reservation_id).state is ReservationState.ACTIVE
    assert guard.state is GuardState.FROZEN
    store.close()
    ledger.close()


def test_15_timeout_persists_unknown_protects_reservation_and_never_retries(tmp_path):
    store, reservation_id, _, bridge, prepared, ledger, adapter, guard, orch, evidence = setup_orchestrator(
        tmp_path, adapter=FakeAdapter(TimeoutError("ambiguous"))
    )
    result = orch.execute(prepared, evidence)
    assert not result.allowed and result.reason == "UNKNOWN"
    assert adapter.submits == 1
    assert store.get(reservation_id).state is ReservationState.UNKNOWN
    assert bridge.get_binding(reservation_id).state == "UNKNOWN"
    assert ledger.get(prepared.client_order_id).result == "ORDER_RESULT_UNKNOWN"
    assert guard.state is GuardState.FROZEN
    repeat = orch.execute(prepared, evidence)
    assert not repeat.allowed
    assert adapter.submits == 1
    store.close()
    ledger.close()


def test_16_crash_left_submission_marker_recovers_as_unknown_and_repairs_ledger(tmp_path):
    reservation_path = tmp_path / "reservation.sqlite"
    store, reservation_id, _ = admit(reservation_path)
    bridge = ReservationExecutionBridge(store)
    prepared = bridge.prepare(reservation_id).intent
    payload = bridge.verify_prepared(prepared).payload
    bridge.begin_submission(prepared, occurred_at=NOW + timedelta(seconds=1))
    store.close()

    reopened = SQLiteReservationStore(reservation_path)
    restarted_bridge = ReservationExecutionBridge(reopened)
    repaired_ledger = SQLiteIdempotencyLedger(tmp_path / "repaired-ledger.sqlite")
    restarted = ExecutionOrchestrator(
        adapter=FakeAdapter(),
        ledger=repaired_ledger,
        murphy_guard=MurphyGuard(),
        reservation_bridge=restarted_bridge,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    result = restarted.recover_projection(reservation_id)
    assert not result.allowed and result.reason == "UNKNOWN"
    assert reopened.get(reservation_id).state is ReservationState.UNKNOWN
    assert restarted_bridge.get_binding(reservation_id).state == "UNKNOWN"
    assert repaired_ledger.get(prepared.client_order_id).result == "ORDER_RESULT_UNKNOWN"
    assert repaired_ledger.get(prepared.client_order_id).intent_hash == repaired_ledger._intent_hash(payload)
    assert restarted.recover_projection(reservation_id).reason == "UNKNOWN"
    reopened.close()
    repaired_ledger.close()


def test_17_successful_local_submission_can_repair_missing_ledger_projection(tmp_path):
    store, reservation_id, _, bridge, prepared, ledger, adapter, _, orch, evidence = setup_orchestrator(tmp_path)
    result = orch.execute(prepared, evidence)
    assert result.allowed and result.reason == "SUBMITTED"
    assert adapter.submits == 1
    assert bridge.get_binding(reservation_id).state == "SUBMITTED"
    ledger.close()

    repaired_ledger = SQLiteIdempotencyLedger(tmp_path / "empty-ledger.sqlite")
    restarted = ExecutionOrchestrator(
        adapter=FakeAdapter(),
        ledger=repaired_ledger,
        murphy_guard=MurphyGuard(),
        reservation_bridge=bridge,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    repair = restarted.recover_projection(reservation_id)
    assert not repair.allowed and repair.reason == "PROJECTION_REPAIRED"
    assert repaired_ledger.get(prepared.client_order_id).result == "SUBMITTED"
    assert adapter.submits == 1
    store.close()
    repaired_ledger.close()


def test_18_invalid_readiness_evidence_does_not_use_favorable_defaults(tmp_path):
    store, _, _, bridge, prepared, ledger, adapter, guard, orch, _ = setup_orchestrator(tmp_path)
    payload = bridge.verify_prepared(prepared).payload
    bad = make_evidence(prepared.client_order_id, payload["correlation_id"], false_kind="protection_safe")
    result = orch.execute(prepared, bad)
    assert not result.allowed and result.reason == "FAIL_CLOSED"
    assert adapter.submits == 0
    assert guard.state is GuardState.FROZEN
    store.close()
    ledger.close()


def test_19_local_recovery_does_not_claim_exchange_reconciliation():
    source = Path("bot_obrero/reservation_execution_bridge.py").read_text(encoding="utf-8")
    assert "does not contact or reconcile an exchange" in source
    assert "recover_ambiguous" in source


def test_20_changed_valid_terms_after_preparation_return_typed_conflict(tmp_path):
    store, reservation_id, _ = admit(tmp_path / "reservation.sqlite")
    bridge = ReservationExecutionBridge(store)
    prepared = bridge.prepare(reservation_id)
    assert prepared.status is ExecutionBridgeStatus.PREPARED

    row = store._connection.execute(
        "SELECT canonical_json FROM reservation_trade_terms_snapshots WHERE reservation_id=?",
        (reservation_id,),
    ).fetchone()
    terms = json.loads(row[0])
    terms["strategy_identity"] = "strategy.changed-after-prepare"
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    store._connection.execute(
        "UPDATE reservation_trade_terms_snapshots SET canonical_json=?, terms_hash=? WHERE reservation_id=?",
        (canonical, digest, reservation_id),
    )
    store._connection.commit()

    result = bridge.prepare(reservation_id)
    assert result.status is ExecutionBridgeStatus.CONFLICT
    assert result.intent is None
    store.close()


def test_21_authorization_fingerprint_disagreement_blocks_preparation(tmp_path):
    store, reservation_id, _ = admit(tmp_path / "reservation.sqlite")
    store._connection.execute(
        "UPDATE reservation_authorization_bindings SET semantic_fingerprint=? WHERE reservation_id=?",
        ("risk-authorization-semantic-v1:" + "0" * 64, reservation_id),
    )
    store._connection.commit()

    result = ReservationExecutionBridge(store).prepare(reservation_id)
    assert result.status is ExecutionBridgeStatus.BLOCKED
    assert result.intent is None
    store.close()


def test_22_preparation_and_repetition_do_not_mutate_canonical_snapshot(tmp_path):
    store, reservation_id, request = admit(tmp_path / "reservation.sqlite")
    before = store.get_trade_terms_snapshot(reservation_id)
    original_proposal = (
        request.proposal.proposal_id,
        request.proposal.signal_id,
        request.proposal.symbol,
        request.proposal.requested_quantity,
        request.proposal.requested_price,
        request.proposal.max_quote_spend,
        request.proposal.correlation_id,
    )
    bridge = ReservationExecutionBridge(store)
    first = bridge.prepare(reservation_id)
    second = bridge.prepare(reservation_id)
    after = store.get_trade_terms_snapshot(reservation_id)

    assert first.status is ExecutionBridgeStatus.PREPARED
    assert second.status is ExecutionBridgeStatus.ALREADY_PREPARED
    assert first.intent == second.intent
    assert before.canonical_json == after.canonical_json
    assert before.terms_hash == after.terms_hash
    assert original_proposal == (
        request.proposal.proposal_id,
        request.proposal.signal_id,
        request.proposal.symbol,
        request.proposal.requested_quantity,
        request.proposal.requested_price,
        request.proposal.max_quote_spend,
        request.proposal.correlation_id,
    )
    store.close()


def test_23_post_adapter_persistence_failure_recovers_as_unknown_without_resend(tmp_path, monkeypatch):
    reservation_path = tmp_path / "reservations.sqlite"
    store, reservation_id, _, bridge, prepared, ledger, adapter, guard, orch, evidence = setup_orchestrator(
        tmp_path, store_path=reservation_path
    )
    original_finish = bridge.finish_submission

    def fail_after_external_return(prepared_intent, *, outcome, occurred_at, error=None):
        if outcome == "SUBMITTED":
            raise OSError("injected result persistence failure after adapter return")
        return original_finish(
            prepared_intent, outcome=outcome, occurred_at=occurred_at, error=error
        )

    monkeypatch.setattr(bridge, "finish_submission", fail_after_external_return)
    result = orch.execute(prepared, evidence)
    assert not result.allowed and result.reason == "UNKNOWN"
    assert adapter.submits == 1
    assert bridge.get_binding(reservation_id).state == "SUBMISSION_STARTED"
    assert store.get(reservation_id).state is ReservationState.ACTIVE
    assert ledger.get(prepared.client_order_id).result == "ORDER_RESULT_UNKNOWN"
    store.close()
    ledger.close()

    reopened = SQLiteReservationStore(reservation_path)
    restarted_bridge = ReservationExecutionBridge(reopened)
    recovery_ledger = SQLiteIdempotencyLedger(tmp_path / "recovery-ledger.sqlite")
    recovery_adapter = FakeAdapter()
    restarted = ExecutionOrchestrator(
        adapter=recovery_adapter,
        ledger=recovery_ledger,
        murphy_guard=MurphyGuard(),
        reservation_bridge=restarted_bridge,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    recovery = restarted.recover_projection(reservation_id)
    assert not recovery.allowed and recovery.reason == "UNKNOWN"
    assert reopened.get(reservation_id).state is ReservationState.UNKNOWN
    assert restarted_bridge.get_binding(reservation_id).state == "UNKNOWN"
    assert recovery_ledger.get(prepared.client_order_id).result == "ORDER_RESULT_UNKNOWN"
    assert adapter.submits == 1
    assert recovery_adapter.submits == 0
    reopened.close()
    recovery_ledger.close()
