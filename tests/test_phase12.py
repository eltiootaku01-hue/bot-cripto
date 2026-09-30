from decimal import Decimal
from datetime import datetime,timezone
from bot_obrero.account import AccountConfiguration,StrategyAssumptions,ConfigurationMismatch,validate_configuration
from bot_obrero.constraints import OrderConstraints,validate_order,quantize_down
from bot_obrero.order_lifecycle import LifecycleOrder,LifecycleStatus
from bot_obrero.infrastructure import StreamHealth,RetryPolicy
from bot_obrero.health import ResourceHealth,ResourceState,trading_allowed
from bot_obrero.data import Candle,LiquidityModel
from bot_obrero.permissions import ApiPermissions

def test_configuration_mismatch_blocks():
    a=AccountConfiguration("a","ONE_WAY","CROSS",Decimal("5"),frozenset({"REDUCE_ONLY"}))
    s=StrategyAssumptions("HEDGE","CROSS",Decimal("5"),frozenset())
    try: validate_configuration(s,a)
    except ConfigurationMismatch: return
    assert False

def test_decimal_constraints():
    c=OrderConstraints(Decimal("0.01"),Decimal("0.001"),Decimal("0.01"),min_notional=Decimal("10"))
    assert quantize_down("0.1234",c.step_size)==Decimal("0.123")
    assert validate_order("BTC",Decimal("100.00"),Decimal("0.100"),Decimal("10"),c)
    assert not validate_order("BTC",Decimal("100.00"),Decimal("0.1005"),Decimal("10.05"),c)
    assert not validate_order("BTC",Decimal("100.00"),Decimal("0.01"),Decimal("1"),c)

def test_cancel_unknown():
    o=LifecycleOrder("c1",1,0,1,status=LifecycleStatus.PENDING_CANCEL); o.cancel_unknown()
    assert o.status is LifecycleStatus.CANCEL_UNKNOWN

def test_stale_websocket():
    s=StreamHealth(datetime.now(timezone.utc),100,5)
    assert s.stale(106)

def test_retry_bounded():
    p=RetryPolicy(3)
    assert p.can_retry(0) and p.can_retry(2) and not p.can_retry(3)

def test_incomplete_candle():
    try: Candle(datetime.now(timezone.utc),datetime.now(timezone.utc),1,False).require_closed()
    except ValueError: return
    assert False

def test_orderbook_limit():
    try: LiquidityModel(1,False).validate(1)
    except ValueError as e: assert str(e)=="EXECUTION_MODEL_LIMITED"; return
    assert False

def test_resource_health_fail_closed():
    h=ResourceHealth(ResourceState.HEALTHY,ResourceState.HEALTHY,ResourceState.HEALTHY,ResourceState.HEALTHY,ResourceState.DEGRADED,ResourceState.HEALTHY)
    assert not trading_allowed(h)

def test_permissions():
    assert ApiPermissions(True,True,False).safe_for_trading()
    assert not ApiPermissions(True,True,True).safe_for_trading()
