from dataclasses import dataclass
from datetime import datetime
@dataclass(frozen=True)
class Candle:
    start:datetime
    end:datetime
    close:float
    closed:bool
    def require_closed(self):
        if not self.closed: raise ValueError("INCOMPLETE_CANDLE")
@dataclass(frozen=True)
class LiquidityModel:
    available_quantity:float
    complete:bool
    def validate(self,order_quantity):
        if not self.complete: raise ValueError("EXECUTION_MODEL_LIMITED")
        if order_quantity>self.available_quantity: raise ValueError("INSUFFICIENT_LIQUIDITY")
