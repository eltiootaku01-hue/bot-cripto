from dataclasses import dataclass
from enum import Enum

class OrderStatus(str, Enum):
    NEW="NEW"; PARTIALLY_FILLED="PARTIALLY_FILLED"; FILLED="FILLED"; CANCELED="CANCELED"; UNKNOWN="ORDER_RESULT_UNKNOWN"

@dataclass
class Order:
    client_order_id: str
    requested_qty: float
    filled_qty: float=0.0
    average_fill_price: float|None=None
    status: OrderStatus=OrderStatus.NEW
    @property
    def remaining_qty(self): return max(0.0,self.requested_qty-self.filled_qty)
    def apply_fill(self,qty,price):
        if qty<=0 or qty>self.remaining_qty: raise ValueError("invalid fill")
        total=(self.average_fill_price or 0.0)*self.filled_qty+qty*price
        self.filled_qty+=qty; self.average_fill_price=total/self.filled_qty
        self.status=OrderStatus.FILLED if self.remaining_qty==0 else OrderStatus.PARTIALLY_FILLED

class IdempotencyLedger:
    def __init__(self): self._orders={}
    def register(self,client_order_id):
        if client_order_id in self._orders: return False
        self._orders[client_order_id]="SUBMITTED"; return True
    def mark_result(self,client_order_id,result): self._orders[client_order_id]=result.value
    def status(self,client_order_id): return self._orders.get(client_order_id)
