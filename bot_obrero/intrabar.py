from dataclasses import dataclass
from enum import Enum
class IntrabarState(str,Enum): DETERMINED="DETERMINED"; AMBIGUOUS="AMBIGUOUS"
@dataclass(frozen=True)
class OHLC: open:float; high:float; low:float; close:float
def classify(bar,stop=None,target=None):
    if stop is not None and target is not None and bar.low<=stop<=bar.high and bar.low<=target<=bar.high: return IntrabarState.AMBIGUOUS
    return IntrabarState.DETERMINED
def require_unambiguous(state):
    if state is IntrabarState.AMBIGUOUS: raise ValueError("intrabar order unknowable from OHLC")
