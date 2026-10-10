from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from bot_obrero.execution import EvidenceBundle, EvidenceRecord, ExchangeAdapter, ExecutionBoundary, ExecutionOrchestrator, OrderIntent, ReadinessInputs
from bot_obrero.murphy import GuardState, MurphyGuard, ProtectionState
from bot_obrero.order_lifecycle import LifecycleOrder, LifecycleStatus
from bot_obrero.persistent_ledger import IdempotencyConflict, SQLiteIdempotencyLedger
from bot_obrero.protection import PositionProtection

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
REQUIRED = ("reconciliation_match", "clock_valid", "stream_ready", "resources_healthy", "permissions_safe", "configuration_valid", "protection_safe")


class FakeAdapter(ExchangeAdapter):
    def __init__(self, error=None):
        self.error, self.submits, self.cancels = error, 0, 0

    def _submit(self, order):
        self.submits += 1
        if self.error:
            raise self.error

    def _cancel(self, order):
        self.cancels += 1

    def snapshot(self):
        return None


def evidence(intent_id="order-1", correlation_id="corr-1", *, expires=None, reconciliation=True):
    return EvidenceBundle(
        tuple(
            EvidenceRecord(
                kind,
                reconciliation if kind == "reconciliation_match" else True,
                "simulated-observer",
                T,
                T,
                intent_id,
                correlation_id,
                expires_at=expires,
            )
            for kind in REQUIRED
        ),
        correlation_id,
    )


def intent(correlation_id="corr-1"):
    return OrderIntent("order-1", {"symbol": "BTCUSDT", "quantity": "1"}, correlation_id)


def build(tmp_path, adapter=None, guard=None):
    ledger = SQLiteIdempotencyLedger(tmp_path / "ledger.sqlite")
    return (
        ExecutionOrchestrator(
            adapter=adapter or FakeAdapter(),
            ledger=ledger,
            murphy_guard=guard or MurphyGuard(),
            clock=lambda: T + timedelta(seconds=1),
        ),
        ledger,
    )


def test_01_generic_intent_is_rejected_without_persisted_reservation_binding(tmp_path):
    adapter = FakeAdapter()
    orch, ledger = build(tmp_path, adapter=adapter)
    result = orch.execute(intent(), evidence())
    assert not result.allowed and result.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0
    assert ledger.get("order-1") is None

def test_02_readiness_false_blocks(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    assert not orch.execute(intent(), evidence(reconciliation=False)).allowed
    assert adapter.submits == 0


def test_03_evidence_absent_blocks(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    records = tuple(record for record in evidence().records if record.kind != "clock_valid")
    assert not orch.execute(intent(), EvidenceBundle(records, "corr-1")).allowed
    assert adapter.submits == 0


def test_04_stale_evidence_blocks(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    assert not orch.execute(intent(), evidence(expires=T + timedelta(seconds=0.5))).allowed
    assert adapter.submits == 0


def test_05_murphy_not_ready_blocks_when_provenance_cannot_authorize(tmp_path):
    adapter = FakeAdapter()
    guard = MurphyGuard()
    orch, _ = build(tmp_path, adapter=adapter, guard=guard)
    result = orch.execute(intent(), evidence(intent_id="other-order"))
    assert not result.allowed and guard.state is GuardState.FROZEN and adapter.submits == 0


def test_06_generic_duplicate_intents_never_reach_ledger_or_adapter(tmp_path):
    adapter = FakeAdapter()
    orch, ledger = build(tmp_path, adapter=adapter)
    first = orch.execute(intent(), evidence())
    second = orch.execute(intent(), evidence())
    assert first.reason == second.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0
    assert ledger.get("order-1") is None

def test_07_generic_client_order_id_reuse_cannot_reach_ledger(tmp_path):
    adapter = FakeAdapter()
    orch, ledger = build(tmp_path, adapter=adapter)
    first = orch.execute(intent(), evidence())
    conflict = OrderIntent("order-1", {"symbol": "BTCUSDT", "quantity": "2"}, "corr-1")
    second = orch.execute(conflict, evidence())
    assert first.reason == second.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0
    assert ledger.get("order-1") is None

def test_08_generic_timeout_intent_is_rejected_before_adapter_invocation(tmp_path):
    adapter = FakeAdapter(TimeoutError())
    orch, ledger = build(tmp_path, adapter=adapter)
    result = orch.execute(intent(), evidence())
    assert not result.allowed and result.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0
    assert ledger.get("order-1") is None

def test_09_protection_requires_valid_evidence():
    protection = PositionProtection()
    protection.submit()
    with pytest.raises(TypeError):
        protection.confirm()
    wrong = EvidenceRecord("clock_valid", True, "simulated-observer", T, T, "order-1", "corr-1")
    with pytest.raises(ValueError, match="PROTECTION_CONFIRMATION_REQUIRES_VALID_EVIDENCE"):
        protection.confirm(wrong, now=T + timedelta(seconds=1))
    assert protection.state is ProtectionState.PROTECTION_UNKNOWN
    valid = EvidenceRecord(
        "protection_safe",
        True,
        "venue-verifier",
        T,
        T,
        "order-1",
        "corr-1",
        expires_at=T + timedelta(seconds=10),
    )
    protection.confirm(valid, now=T + timedelta(seconds=1))
    assert protection.safe()


def test_10_duplicate_fill_does_not_mutate_twice(tmp_path):
    orch, ledger = build(tmp_path)
    order = LifecycleOrder("order-1", Decimal("1"))
    order.acknowledge("venue-1")
    assert orch.apply_external_fill(order, "fill-1", Decimal("0.4"), Decimal("100"))
    assert not orch.apply_external_fill(order, "fill-1", Decimal("0.4"), Decimal("100"))
    assert order.filled_qty == Decimal("0.4")
    ledger.close()


def test_11_restart_does_not_restore_generic_order_send_path(tmp_path):
    path = tmp_path / "ledger.sqlite"
    adapter = FakeAdapter()
    ledger = SQLiteIdempotencyLedger(path)
    orch = ExecutionOrchestrator(
        adapter=adapter, ledger=ledger, murphy_guard=MurphyGuard(),
        clock=lambda: T + timedelta(seconds=1),
    )
    result = orch.execute(intent(), evidence())
    assert result.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0
    assert ledger.get("order-1") is None
    ledger.close()

    restarted_ledger = SQLiteIdempotencyLedger(path)
    restarted = ExecutionOrchestrator(
        adapter=FakeAdapter(), ledger=restarted_ledger, murphy_guard=MurphyGuard(),
        clock=lambda: T + timedelta(seconds=2),
    )
    assert restarted_ledger.get("order-1") is None
    assert restarted.execute(intent(), evidence()).reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    restarted_ledger.close()

def test_12_bypass_is_rejected_by_public_adapter_interface():
    adapter = FakeAdapter()
    with pytest.raises(PermissionError, match="EXECUTION_BOUNDARY_REQUIRED"):
        adapter.submit(intent())
    with pytest.raises(PermissionError, match="EXECUTION_BOUNDARY_REQUIRED"):
        adapter.cancel(intent())
    with pytest.raises(TypeError, match="BOUNDARY_CONTROLLED"):
        class UnauthorizedAdapter(ExchangeAdapter):
            def submit(self, order):
                return None

            def _cancel(self, order):
                return None

            def snapshot(self):
                return None


def test_13_generic_intent_cannot_create_ambiguous_execution_state(tmp_path):
    path = tmp_path / "ledger.sqlite"
    ledger = SQLiteIdempotencyLedger(path)
    adapter = FakeAdapter(TimeoutError())
    first = ExecutionOrchestrator(
        adapter=adapter, ledger=ledger, murphy_guard=MurphyGuard(),
        clock=lambda: T + timedelta(seconds=1),
    )
    result = first.execute(intent(), evidence())
    assert result.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0
    assert ledger.get("order-1") is None
    ledger.close()

    restarted_ledger = SQLiteIdempotencyLedger(path)
    restarted = ExecutionOrchestrator(
        adapter=FakeAdapter(), ledger=restarted_ledger, murphy_guard=MurphyGuard(),
        clock=lambda: T + timedelta(seconds=2),
    )
    stale_uncertain = evidence(expires=T + timedelta(seconds=0.5), reconciliation=False)
    assert restarted.execute(intent(), stale_uncertain).reason == "FAIL_CLOSED"
    assert restarted_ledger.get("order-1") is None
    restarted_ledger.close()

    order = LifecycleOrder(
        "terminal", Decimal("1"), Decimal("1"), Decimal("0"),
        status=LifecycleStatus.FILLED,
    )
    before = order.filled_qty
    with pytest.raises(ValueError):
        order.apply_fill("0.1", "100")
    assert order.filled_qty == before

def test_14_naked_readiness_inputs_cannot_cross_execution_boundary(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    naked = ReadinessInputs(True, True, True, True, True, True, True)
    result = orch.execute(intent(), naked)
    assert not result.allowed and result.reason == "FAIL_CLOSED"
    assert adapter.submits == 0


def test_15_matching_generic_correlation_does_not_replace_reservation_binding(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    result = orch.execute(intent("corr-1"), evidence(correlation_id="corr-1"))
    assert result.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0

def test_16_mismatched_intent_and_bundle_correlation_fails_closed(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    result = orch.execute(intent("corr-intent"), evidence(correlation_id="corr-bundle"))
    assert not result.allowed and result.reason == "FAIL_CLOSED"
    assert adapter.submits == 0


def test_17_internally_consistent_records_cannot_authorize_wrong_bundle_context(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    bundle = evidence(correlation_id="corr-bundle")
    result = orch.execute(intent("corr-intent"), bundle)
    assert all(record.correlation_id == bundle.correlation_id for record in bundle.records)
    assert not result.allowed and result.reason == "FAIL_CLOSED"
    assert adapter.submits == 0


def test_18_complete_evidence_bundle_cannot_authorize_generic_intent(tmp_path):
    adapter = FakeAdapter()
    orch, _ = build(tmp_path, adapter=adapter)
    bundle = evidence(correlation_id="corr-1")
    assert len(bundle.records) == len(REQUIRED)
    assert all(record.correlation_id == bundle.correlation_id for record in bundle.records)
    result = orch.execute(intent("corr-1"), bundle)
    assert result.reason == "RESERVATION_EXECUTION_BINDING_REQUIRED"
    assert adapter.submits == 0

def test_19_protection_state_is_read_only_through_public_api():
    protection = PositionProtection()
    with pytest.raises(AttributeError):
        protection.state = ProtectionState.PROTECTION_CONFIRMED
    assert not protection.safe()
    protection.submit()
    assert protection.state is ProtectionState.PROTECTION_PENDING


def test_20_direct_execution_boundary_cannot_submit_generic_intent(tmp_path):
    adapter = FakeAdapter()
    boundary = ExecutionBoundary(adapter)

    with pytest.raises(PermissionError, match="PERSISTED_RESERVATION_SUBMISSION_AUTHORITY_REQUIRED"):
        boundary.submit(intent())
    assert adapter.submits == 0

    orchestrator, ledger = build(tmp_path, adapter=adapter)
    with pytest.raises(PermissionError, match="PERSISTED_RESERVATION_SUBMISSION_AUTHORITY_REQUIRED"):
        orchestrator.boundary.submit(intent())
    assert adapter.submits == 0
    assert ledger.get("order-1") is None
    ledger.close()
