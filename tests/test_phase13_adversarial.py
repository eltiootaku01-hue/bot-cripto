import pytest
from decimal import Decimal
from bot_obrero.execution import ReadinessGate,ReadinessInputs,RiskGuard
from bot_obrero.murphy import MurphyGuard,GuardState,OrderResult
from bot_obrero.reconciliation import Snapshot,reconcile,ReconciliationState
from bot_obrero.order_lifecycle import LifecycleOrder
from bot_obrero.unknown_execution import OrderEvidence,classify_result,retry_allowed

G=ReadinessInputs(True,True,True,True,True,True,True)

def test_ready_requires_all_evidence():
    g=MurphyGuard()
    with pytest.raises(ValueError): g.ready()
    assert g.state is GuardState.FROZEN
    for field in G.__dict__:
        d=G.__dict__.copy(); d[field]=False
        assert not ReadinessGate().evaluate(ReadinessInputs(**d)).allowed

def test_risk_guard_cannot_bypass_gate():
    g=MurphyGuard(); r=RiskGuard(g)
    assert not r.authorize_new_entry(ReadinessInputs(True,True,True,True,True,True,False)).allowed
    assert r.authorize_new_entry(G).allowed

def test_reconciliation_unknown_incomplete_and_duplicate_fill():
    a=Snapshot(1,fills=("f1","f2")); b=Snapshot(1,fills=("f2","f1"))
    assert reconcile(a,b) is ReconciliationState.MATCH
    assert reconcile(a,None) is ReconciliationState.UNKNOWN
    assert reconcile(a,Snapshot(1,complete=False)) is ReconciliationState.INCOMPLETE
    assert reconcile(a,Snapshot(1,fills=("f1","f1"))) is ReconciliationState.MISMATCH

def test_unknown_result_cannot_retry_blindly():
    assert classify_result(OrderEvidence("x")) is OrderResult.UNKNOWN
    assert not retry_allowed(OrderResult.UNKNOWN,reconciled=True)
    assert retry_allowed(OrderResult.FAILED,reconciled=True) is True

def test_lifecycle_invariant_is_enforced():
    with pytest.raises(ValueError): LifecycleOrder("x",Decimal("1"),Decimal(".4"),Decimal("0"))
    o=LifecycleOrder("x",Decimal("1")); o.acknowledge("e1"); o.apply_fill(".4","100"); o.request_cancel(); o.cancel_unknown()
    assert o.filled_qty+o.remaining_qty==o.requested_qty

def test_restart_starts_frozen():
    g=MurphyGuard(); assert g.state is GuardState.FROZEN
    g.recover(); assert not g.allow_new_entry()
