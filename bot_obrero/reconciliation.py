from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
class ReconciliationState(str,Enum):
    MATCH="MATCH"; MISMATCH="MISMATCH"; INCOMPLETE="INCOMPLETE"; UNKNOWN="UNKNOWN"
    CONSISTENT="MATCH"; STATE_MISMATCH="MISMATCH"; STATE_CONFLICT="MISMATCH"; UNKNOWN_STATE="UNKNOWN"
@dataclass(frozen=True)
class Snapshot:
    position: float|Decimal
    open_orders: frozenset[str]=frozenset()
    fills: tuple[str,...]=()
    balances: tuple[tuple[str,str],...]=()
    complete: bool=True
    source: str="unknown"
    def canonical(self):
        return (Decimal(str(self.position)),frozenset(self.open_orders),tuple(sorted(self.fills)),tuple(sorted(self.balances)))
def reconcile(rest,websocket):
    if rest is None or websocket is None: return ReconciliationState.UNKNOWN
    if not rest.complete or not websocket.complete: return ReconciliationState.INCOMPLETE
    return ReconciliationState.MATCH if rest.canonical()==websocket.canonical() else ReconciliationState.MISMATCH
def reconcile_sources(*snapshots):
    if not snapshots or any(s is None for s in snapshots): return ReconciliationState.UNKNOWN
    if any(not s.complete for s in snapshots): return ReconciliationState.INCOMPLETE
    canonical=snapshots[0].canonical()
    return ReconciliationState.MATCH if all(s.canonical()==canonical for s in snapshots[1:]) else ReconciliationState.MISMATCH
