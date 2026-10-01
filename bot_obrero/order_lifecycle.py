from dataclasses import dataclass
from enum import Enum
from datetime import datetime
from decimal import Decimal
class LifecycleStatus(str,Enum):
    NEW="NEW"; PENDING_SUBMIT="PENDING_SUBMIT"; ACKNOWLEDGED="ACKNOWLEDGED"; PARTIALLY_FILLED="PARTIALLY_FILLED"; FILLED="FILLED"; PENDING_CANCEL="PENDING_CANCEL"; CANCELED="CANCELED"; CANCEL_UNKNOWN="CANCEL_UNKNOWN"; REJECTED="REJECTED"; EXPIRED="EXPIRED"; UNKNOWN="UNKNOWN"
@dataclass
class LifecycleOrder:
    client_order_id:str
    requested_qty:Decimal|float
    filled_qty:Decimal|float=0
    remaining_qty:Decimal|float|None=None
    average_fill_price:Decimal|float|None=None
    status:LifecycleStatus=LifecycleStatus.NEW
    exchange_order_id:str|None=None
    created_at:datetime|None=None
    updated_at:datetime|None=None
    def __post_init__(self):
        self.requested_qty=Decimal(str(self.requested_qty)); self.filled_qty=Decimal(str(self.filled_qty))
        expected=self.requested_qty-self.filled_qty
        supplied=expected if self.remaining_qty is None else Decimal(str(self.remaining_qty))
        if self.requested_qty<=0 or self.filled_qty<0 or supplied<0 or self.filled_qty>self.requested_qty: raise ValueError("INVALID_ORDER_QUANTITY")
        if supplied!=expected: raise ValueError("INCONSISTENT_ORDER_QUANTITY")
        self.remaining_qty=supplied
    def _check_invariant(self):
        if self.filled_qty+self.remaining_qty!=self.requested_qty: raise ValueError("INCONSISTENT_ORDER_QUANTITY")
    def acknowledge(self,exchange_order_id=None):
        self.exchange_order_id=exchange_order_id or self.exchange_order_id; self.status=LifecycleStatus.ACKNOWLEDGED; self._check_invariant()
    def apply_fill(self,quantity,price):
        qty,px=Decimal(str(quantity)),Decimal(str(price))
        if qty<=0 or qty>self.remaining_qty: raise ValueError("INVALID_FILL")
        prior=self.filled_qty; self.filled_qty+=qty; self.remaining_qty-=qty
        self.average_fill_price=px if prior==0 else ((Decimal(str(self.average_fill_price))*prior+px*qty)/self.filled_qty)
        self.status=LifecycleStatus.FILLED if self.remaining_qty==0 else LifecycleStatus.PARTIALLY_FILLED; self._check_invariant()
    def request_cancel(self):
        if self.status in {LifecycleStatus.FILLED,LifecycleStatus.CANCELED,LifecycleStatus.REJECTED,LifecycleStatus.EXPIRED}: return self.status
        self.status=LifecycleStatus.PENDING_CANCEL; self._check_invariant(); return self.status
    def cancel_unknown(self):
        self.status=LifecycleStatus.CANCEL_UNKNOWN; self._check_invariant()
