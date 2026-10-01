from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
class OrderStatus(str,Enum):
    NEW="NEW"; PARTIALLY_FILLED="PARTIALLY_FILLED"; FILLED="FILLED"; CANCELED="CANCELED"; UNKNOWN="ORDER_RESULT_UNKNOWN"
@dataclass
class Order:
    client_order_id:str
    requested_qty:Decimal|float
    filled_qty:Decimal|float=0
    average_fill_price:Decimal|float|None=None
    status:OrderStatus=OrderStatus.NEW
    def __post_init__(self):
        self.requested_qty=Decimal(str(self.requested_qty)); self.filled_qty=Decimal(str(self.filled_qty))
        if self.requested_qty<=0 or self.filled_qty<0 or self.filled_qty>self.requested_qty: raise ValueError("INVALID_ORDER_QUANTITY")
    @property
    def remaining_qty(self): return self.requested_qty-self.filled_qty
    def apply_fill(self,qty,price):
        qty,price=Decimal(str(qty)),Decimal(str(price))
        if qty<=0 or qty>self.remaining_qty: raise ValueError("invalid fill")
        total=(self.average_fill_price or Decimal("0"))*self.filled_qty+qty*price
        self.filled_qty+=qty; self.average_fill_price=total/self.filled_qty
        self.status=OrderStatus.FILLED if self.remaining_qty==0 else OrderStatus.PARTIALLY_FILLED
class IdempotencyLedger:
    def __init__(self): self._orders={}
    def register(self,client_order_id):
        if client_order_id in self._orders: return False
        self._orders[client_order_id]="SUBMITTED"; return True
    def mark_result(self,client_order_id,result): self._orders[client_order_id]=result.value
    def status(self,client_order_id): return self._orders.get(client_order_id)
