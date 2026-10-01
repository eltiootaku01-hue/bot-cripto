from datetime import datetime, timezone, timedelta
from decimal import Decimal

from bot_obrero.account import AccountConfiguration, StrategyAssumptions, ConfigurationMismatch, validate_configuration
from bot_obrero.constraints import OrderConstraints, normalize_order, validate_order
from bot_obrero.order_lifecycle import LifecycleOrder, LifecycleStatus
from bot_obrero.reconciliation import Snapshot, ReconciliationState, reconcile, reconcile_sources
from bot_obrero.infrastructure import (
    StreamHealth, RetryPolicy, ReconnectGate, InfrastructureError,
    classify_http_failure, retryable,
)
from bot_obrero.clock import ClockService
from bot_obrero.data import Candle, BacktestAssumptions
from bot_obrero.health import ResourceState, state_from_ratio
from bot_obrero.ownership import OwnershipBook, OwnershipState
from bot_obrero.llm_policy import parse_non_authoritative

T = datetime(2026, 1, 1, tzinfo=timezone.utc)

def test_o1_checks_side_flags_permissions_and_symbol():
    account = AccountConfiguration(
        "a", "HEDGE", "CROSS", Decimal("3"),
        frozenset({"REDUCE_ONLY", "POST_ONLY"}),
        {"BTCUSDT": {}},
        frozenset({"READ", "TRADE"}),
        frozenset({"BUY", "SELL"}),
    )
    strategy = StrategyAssumptions(
        "HEDGE", "CROSS", Decimal("3"),
        frozenset({"REDUCE_ONLY", "POST_ONLY"}),
        "BUY", frozenset({"READ", "TRADE"}), "BTCUSDT",
    )
    assert validate_configuration(strategy, account)
    bad = StrategyAssumptions("HEDGE", "CROSS", Decimal("3"), frozenset({"REDUCE_ONLY", "POST_ONLY", "X"}))
    try:
        validate_configuration(bad, account)
    except ConfigurationMismatch:
        pass
    else:
        assert False

def test_o2_normalization_uses_decimal_and_rejects_invalid_constraints():
    c = OrderConstraints(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), min_notional=Decimal("10"))
    p, q = normalize_order("100.009", "0.1239", c)
    assert p == Decimal("100.00") and q == Decimal("0.123")
    assert validate_order("BTCUSDT", p, q, p*q, c)
    assert not validate_order("BTCUSDT", p, Decimal("0.1234"), p*Decimal("0.1234"), c)

def test_o3_cancel_response_loss_stays_unknown():
    o = LifecycleOrder("c1", Decimal("1"))
    o.acknowledge("e1")
    o.apply_fill(Decimal("0.4"), Decimal("100"))
    o.request_cancel()
    assert o.status is LifecycleStatus.PENDING_CANCEL
    o.cancel_unknown()
    assert o.status is LifecycleStatus.CANCEL_UNKNOWN

def test_e3_distinguishes_match_mismatch_incomplete_unknown():
    a = Snapshot(1, frozenset({"o1"}), ("f1",), (("USDT","100"),))
    b = Snapshot(1, frozenset({"o1"}), ("f1",), (("USDT","100"),))
    c = Snapshot(1, frozenset({"o2"}), ("f1",), (("USDT","100"),))
    d = Snapshot(1, frozenset({"o1"}), ("f1",), (("USDT","100"),), complete=False)
    assert reconcile(a,b) is ReconciliationState.MATCH
    assert reconcile(a,c) is ReconciliationState.MISMATCH
    assert reconcile(a,d) is ReconciliationState.INCOMPLETE
    assert reconcile(a,None) is ReconciliationState.UNKNOWN
    assert reconcile_sources(a,b) is ReconciliationState.MATCH

def test_multibot_unknown_or_conflicting_ownership_freezes():
    book = OwnershipBook({}, account_id="acct", instance_id="i1")
    assert book.resolve("BTC") == (OwnershipState.UNKNOWN, None)
    assert book.register_owner("BTC", "strategy-A") is OwnershipState.OWNED
    assert book.register_owner("BTC", "strategy-B") is OwnershipState.CONFLICT

def test_n1_classification_bounded_retry_reconnect_and_clock():
    assert classify_http_failure(429) is InfrastructureError.RATE_LIMIT
    assert classify_http_failure(504) is InfrastructureError.SERVER_ERROR
    assert classify_http_failure(None, timeout=True) is InfrastructureError.TIMEOUT
    assert not retryable(InfrastructureError.AUTHENTICATION_FAILURE)
    p = RetryPolicy(3, 0.5, 0)
    assert p.can_retry(2) and not p.can_retry(3) and p.delay(2) == 2.0
    s = StreamHealth(T, 10, 5, last_sequence=4)
    assert s.stale(16) and not s.accepts_event(4) and s.accepts_event(5)
    gate = ReconnectGate()
    assert not gate.ready()
    gate = ReconnectGate(snapshot_confirmed=True)
    assert gate.ready()
    assert not ClockService(2).snapshot(T + timedelta(seconds=3), T, 10).valid
    assert ClockService(5).snapshot(T + timedelta(seconds=3), T, 10).valid

def test_d3_requires_temporal_availability_and_execution_assumptions():
    candle = Candle(T, T + timedelta(minutes=1), 100, True, T + timedelta(seconds=5))
    assert candle.available_at(T + timedelta(seconds=5))
    assert not candle.available_at(T)
    model = BacktestAssumptions("slip","spread","fees","funding","latency","partial","depth",True,True,True)
    assert model.validate()

def test_s1_resource_ratio_is_fail_closed_without_measurement_integration():
    assert state_from_ratio(0.99, 0.95) is ResourceState.CRITICAL
    assert state_from_ratio(0.90, 0.95) is ResourceState.DEGRADED

def test_llm_is_advisory_and_invalid_output_is_rejected():
    assert not parse_non_authoritative("{").accepted
    assert parse_non_authoritative('{"signal":"BUY"}').accepted
