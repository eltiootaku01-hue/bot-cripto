from bot_obrero.murphy import MurphyGuard, GuardState
from bot_obrero.execution import ReadinessInputs
import pytest
GOOD=ReadinessInputs(True,True,True,True,True,True,True)
def test_ready_is_frozen_without_evidence():
    g=MurphyGuard()
    assert g.state is GuardState.FROZEN
    with pytest.raises(ValueError): g.ready()
    assert not g.allow_new_entry()
def test_ready_requires_complete_evidence():
    g=MurphyGuard()
    g.recover()
    with pytest.raises(ValueError): g.ready()
    g.ready(GOOD)
    assert g.allow_new_entry()
