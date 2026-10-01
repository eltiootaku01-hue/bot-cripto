import pytest
from bot_obrero.execution import ReadinessGate,ReadinessInputs
def test_readiness_gate_requires_all_deterministic_evidence():
    gate=ReadinessGate()
    bad=ReadinessInputs(True,True,True,True,True,False)
    assert not gate.evaluate(bad).allowed
    good=ReadinessInputs(True,True,True,True,True,True,True)
    assert gate.evaluate(good).allowed
def test_protection_defaults_closed():
    good_without_protection=ReadinessInputs(True,True,True,True,True,True)
    assert not gate_result(good_without_protection)
def gate_result(inputs):
    return ReadinessGate().evaluate(inputs).allowed
