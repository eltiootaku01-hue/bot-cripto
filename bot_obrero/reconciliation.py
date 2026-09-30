from dataclasses import dataclass
from enum import Enum

class ReconciliationState(str, Enum):
    CONSISTENT="CONSISTENT"; STATE_MISMATCH="STATE_MISMATCH"; STATE_CONFLICT="STATE_CONFLICT"; UNKNOWN_STATE="UNKNOWN_STATE"

@dataclass(frozen=True)
class Snapshot:
    position: float
    open_orders: frozenset[str]=frozenset()
    fills: tuple[str,...]=()

def reconcile(rest,websocket):
    if rest is None or websocket is None: return ReconciliationState.UNKNOWN_STATE
    return ReconciliationState.CONSISTENT if rest==websocket else ReconciliationState.STATE_CONFLICT
