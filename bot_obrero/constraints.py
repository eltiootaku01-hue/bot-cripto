from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

@dataclass(frozen=True)
class OrderConstraints:
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal|None=None
    min_notional: Decimal|None=None
    max_notional: Decimal|None=None
    price_precision: int|None=None
    quantity_precision: int|None=None

def quantize_down(value, step):
    value=Decimal(str(value)); step=Decimal(str(step))
    return (value/step).to_integral_value(rounding=ROUND_DOWN)*step

def validate_order(symbol, price, quantity, notional, constraints):
    p=Decimal(str(price)); q=Decimal(str(quantity)); n=Decimal(str(notional))
    if q < constraints.min_qty: return False
    if constraints.max_qty is not None and q > constraints.max_qty: return False
    if quantize_down(q,constraints.step_size) != q: return False
    if quantize_down(p,constraints.tick_size) != p: return False
    if constraints.min_notional is not None and n < constraints.min_notional: return False
    if constraints.max_notional is not None and n > constraints.max_notional: return False
    return True
