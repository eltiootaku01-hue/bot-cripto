"""Immutable arithmetic policy for persisted PAPER economic contracts.

Contract v1 uses Decimal precision 28 with ROUND_HALF_EVEN at each arithmetic
operation. Values are stored canonically as decimal strings; V1 does not apply
exchange tick-size, lot-size, or currency-quantization rules.
"""
from contextlib import contextmanager
from decimal import ROUND_HALF_EVEN, localcontext
from typing import Iterator

PAPER_ECONOMIC_CONTRACT_VERSION = 1
PAPER_DECIMAL_PRECISION = 28
PAPER_DECIMAL_ROUNDING = "ROUND_HALF_EVEN"


@contextmanager
def paper_decimal_context() -> Iterator[None]:
    """Apply deterministic V1 Decimal precision/rounding without changing callers."""
    with localcontext() as context:
        context.prec = PAPER_DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        yield
