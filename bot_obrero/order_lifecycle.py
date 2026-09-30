from dataclasses import dataclass
from enum import Enum
from datetime import datetime

class LifecycleStatus(str,Enum):
    NEW="NEW"; PENDING_SUBMIT="PENDING_SUBMIT"; ACKNOWLEDGED="ACKNOWLEDGED"
    PARTIALLY_FILLED="PARTIALLY_FILLED"; FILLED="FILLED"; PENDING_CANCEL="PENDING_CANCEL"
    CANCELED="CANCELED"; CANCEL_UNKNOWN="CANCEL_UNKNOWN"; REJECTED="REJECTED"; EXPIRED="EXPIRED"; UNKNOWN="UNKNOWN"

@dataclass
class LifecycleOrder:
    client_order_id: str
    requested_qty: object
    filled_qty: object=0
    remaining_qty: object=0
    average_fill_price: object|None=None
    status: LifecycleStatus=LifecycleStatus.NEW
    exchange_order_id: str|None=None
    created_at: datetime|None=None
    updated_at: datetime|None=None
    def cancel_unknown(self): self.status=LifecycleStatus.CANCEL_UNKNOWN
