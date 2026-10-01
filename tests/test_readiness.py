from bot_obrero.execution import ReadinessGate, ReadinessInputs

def test_readiness_gate_requires_all_deterministic_evidence():
    gate = ReadinessGate()
    bad = ReadinessInputs(True, True, True, True, True, False)
    assert not gate.evaluate(bad).allowed
    good = ReadinessInputs(True, True, True, True, True, True)
    assert gate.evaluate(good).allowed
