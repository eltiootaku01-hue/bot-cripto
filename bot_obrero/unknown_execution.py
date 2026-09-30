from dataclasses import dataclass
from .murphy import OrderResult

@dataclass(frozen=True)
class OrderEvidence:
    client_order_id: str
    order_found: bool|None=None
    fills_found: bool|None=None
    position_seen: bool|None=None

def classify_result(e):
    if e.order_found is True and (e.fills_found is True or e.position_seen is True): return OrderResult.CONFIRMED
    if e.order_found is False and e.fills_found is False and e.position_seen is False: return OrderResult.FAILED
    return OrderResult.UNKNOWN

class AmbiguousExecutionRecovery:
    def __init__(self,guard): self.guard=guard
    def recover(self,evidence):
        result=classify_result(evidence)
        if result is OrderResult.UNKNOWN: self.guard.freeze()
        return result
