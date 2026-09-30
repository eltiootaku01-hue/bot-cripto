from datetime import datetime,timezone,timedelta
from bot_obrero.clock import ClockCheck
from bot_obrero.intrabar import OHLC,IntrabarState,classify,require_unambiguous
from bot_obrero.murphy import MurphyGuard,TradingMode,GuardState,ProtectionState,execution_policy
from bot_obrero.orders import IdempotencyLedger,Order,OrderStatus
from bot_obrero.ownership import AccountPosition,OwnershipBook
from bot_obrero.protection import PositionProtection
from bot_obrero.reconciliation import Snapshot,ReconciliationState,reconcile
from bot_obrero.temporal import EvidenceTimestamp
from bot_obrero.unknown_execution import AmbiguousExecutionRecovery,OrderEvidence,classify_result
T=datetime(2026,1,1,tzinfo=timezone.utc)
def test_lookahead_rejected():
    try: EvidenceTimestamp(T,T+timedelta(seconds=1),T)
    except ValueError: return
    assert False
def test_partial_fill():
    o=Order("c1",1.0); o.apply_fill(.37,100)
    assert o.filled_qty==.37 and o.remaining_qty==.63 and o.status is OrderStatus.PARTIALLY_FILLED
def test_idempotency_after_lost_response():
    l=IdempotencyLedger(); assert l.register("c1"); l.mark_result("c1",OrderStatus.FILLED); assert not l.register("c1")
def test_ambiguous_order_freezes():
    g=MurphyGuard(); r=AmbiguousExecutionRecovery(g).recover(OrderEvidence("c1"))
    assert r.value=="ORDER_RESULT_UNKNOWN" and g.state is GuardState.FROZEN
def test_rest_websocket_conflict():
    a=Snapshot(1.0,frozenset({"o1"}),("f1",)); b=Snapshot(.5,frozenset({"o1"}),("f1",))
    assert reconcile(a,b) is ReconciliationState.STATE_CONFLICT
def test_protection_unknown_is_unsafe():
    p=PositionProtection(); p.submit(); p.unknown(); assert p.state is ProtectionState.PROTECTION_UNKNOWN and not p.safe()
def test_account_vs_strategy_exposure():
    a=AccountPosition("BTC",1.0); b=OwnershipBook({}); b.set_exposure("A","BTC",.4); b.set_exposure("B","BTC",.6)
    assert a.quantity==1 and b.exposure("A","BTC")==.4 and b.exposure("B","BTC")==.6
def test_execution_policy_is_contextual():
    assert execution_policy(TradingMode.NORMAL).allow_new_entries
    assert not execution_policy(TradingMode.DEGRADED).allow_new_entries
    assert execution_policy(TradingMode.EMERGENCY).priority=="REDUCE_EXPOSURE"
    assert not execution_policy(TradingMode.UNKNOWN).allow_new_entries
def test_clock_offset():
    c=ClockCheck(T,T+timedelta(seconds=3),2); assert not c.healthy
def test_intrabar_ambiguity():
    s=classify(OHLC(100,110,90,105),95,108)
    assert s is IntrabarState.AMBIGUOUS
    try: require_unambiguous(s)
    except ValueError: return
    assert False
def test_murphy_001_compound_failure():
    g=MurphyGuard(); o=Order("c1",1); o.apply_fill(.37,100); g.freeze()
    assert o.remaining_qty==.63
    assert reconcile(Snapshot(.37),None) is ReconciliationState.UNKNOWN_STATE
    assert not ClockCheck(T,T+timedelta(seconds=5),2).healthy
    assert classify_result(OrderEvidence("c1")).value=="ORDER_RESULT_UNKNOWN"
    p=PositionProtection(); p.submit(); p.unknown(); assert not p.safe()
    l=IdempotencyLedger(); assert l.register("c1") and not l.register("c1")
    assert g.state is GuardState.FROZEN and not g.allow_new_entry()
