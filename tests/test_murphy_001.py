from datetime import datetime, timezone, timedelta
from decimal import Decimal

from bot_obrero.clock import ClockService
from bot_obrero.infrastructure import StreamHealth, classify_http_failure, InfrastructureError
from bot_obrero.murphy import MurphyGuard, GuardState
from bot_obrero.order_lifecycle import LifecycleOrder, LifecycleStatus
from bot_obrero.orders import IdempotencyLedger
from bot_obrero.protection import PositionProtection
from bot_obrero.reconciliation import Snapshot, ReconciliationState, reconcile

T = datetime(2026, 1, 1, tzinfo=timezone.utc)

def test_murphy_001_compound_failure_freezes_and_never_duplicates():
    guard = MurphyGuard()
    order = LifecycleOrder("entry-001", Decimal("1"))
    order.acknowledge("exchange-001")
    order.apply_fill(Decimal("0.4"), Decimal("100"))
    assert order.status is LifecycleStatus.PARTIALLY_FILLED

    stream = StreamHealth(T, 10, 5, last_sequence=8)
    assert stream.stale(16)

    assert classify_http_failure(None, timeout=True) is InfrastructureError.TIMEOUT
    clock = ClockService(2).snapshot(T + timedelta(seconds=5), T, 100)
    assert not clock.valid

    order.request_cancel()
    order.cancel_unknown()
    assert order.status is LifecycleStatus.CANCEL_UNKNOWN

    restarted_guard = MurphyGuard()
    local = Snapshot(Decimal("0.4"), frozenset({"exchange-001"}), ("fill-001",), (("USDT","1000"),))
    remote = Snapshot(Decimal("1.0"), frozenset({"exchange-001"}), ("fill-001",), (("USDT","1000"),))
    assert reconcile(local, remote) is ReconciliationState.MISMATCH
    assert restarted_guard.state is GuardState.FROZEN
    assert not restarted_guard.allow_new_entry()

    ledger = IdempotencyLedger()
    assert ledger.register("entry-001")
    assert not ledger.register("entry-001")

    protection = PositionProtection()
    protection.submit()
    protection.unknown()
    assert not protection.safe()

    assert not guard.allow_new_entry()
    assert not restarted_guard.allow_new_entry()

def test_murphy_001_recovers_only_after_confirmed_state():
    guard = MurphyGuard()
    local = Snapshot(Decimal("0.4"), frozenset({"exchange-001"}), ("fill-001",), (("USDT","1000"),))
    remote = Snapshot(Decimal("0.4"), frozenset({"exchange-001"}), ("fill-001",), (("USDT","1000"),))
    assert reconcile(local, remote) is ReconciliationState.MATCH
    guard.recover()
    guard.ready()
    assert guard.allow_new_entry()
