from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

@dataclass(frozen=True)
class OrderConstraints:
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal | None = None
    min_notional: Decimal | None = None
    max_notional: Decimal | None = None
    price_precision: int | None = None
    quantity_precision: int | None = None

def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))

def quantize_down(value, step):
    value, step = _decimal(value), _decimal(step)
    if step <= 0:
        raise ValueError("INVALID_STEP_SIZE")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

def normalize_order(price, quantity, constraints):
    p = quantize_down(price, constraints.tick_size)
    q = quantize_down(quantity, constraints.step_size)
    if constraints.price_precision is not None and -p.as_tuple().exponent > constraints.price_precision:
        raise ValueError("PRICE_PRECISION")
    if constraints.quantity_precision is not None and -q.as_tuple().exponent > constraints.quantity_precision:
        raise ValueError("QUANTITY_PRECISION")
    return p, q

def validate_order(symbol, price, quantity, notional, constraints):
    if not symbol:
        return False
    p, q, n = _decimal(price), _decimal(quantity), _decimal(notional)
    if p <= 0 or q <= 0 or n <= 0:
        return False
    if q < constraints.min_qty:
        return False
    if constraints.max_qty is not None and q > constraints.max_qty:
        return False
    if quantize_down(q, constraints.step_size) != q:
        return False
    if quantize_down(p, constraints.tick_size) != p:
        return False
    if constraints.min_notional is not None and n < constraints.min_notional:
        return False
    if constraints.max_notional is not None and n > constraints.max_notional:
        return False
    return True
