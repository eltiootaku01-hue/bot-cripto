import pytest
from decimal import Decimal
from bot_obrero.order_lifecycle import (
    LifecycleOrder,
    LifecycleStatus,
    InvalidLifecycleTransition,
)

def test_valid_submission_and_acknowledgement():
    order = LifecycleOrder("x", Decimal("1"))
    assert order.submit() is LifecycleStatus.PENDING_SUBMIT
    assert order.acknowledge("e1") is LifecycleStatus.ACKNOWLEDGED

def test_legacy_direct_acknowledgement_remains_explicitly_supported():
    order = LifecycleOrder("x", Decimal("1"))
    assert order.acknowledge("e1") is LifecycleStatus.ACKNOWLEDGED

def test_valid_partial_and_final_fill():
    order = LifecycleOrder("x", Decimal("1"))
    order.acknowledge("e1")
    assert order.apply_fill(".4", "100") is LifecycleStatus.PARTIALLY_FILLED
    assert order.filled_qty == Decimal(".4")
    assert order.remaining_qty == Decimal(".6")
    assert order.apply_fill(".6", "101") is LifecycleStatus.FILLED
    assert order.filled_qty == Decimal("1")
    assert order.remaining_qty == Decimal("0")

@pytest.mark.parametrize(
    "status",
    [
        LifecycleStatus.FILLED,
        LifecycleStatus.CANCELED,
        LifecycleStatus.REJECTED,
        LifecycleStatus.EXPIRED,
    ],
)
def test_terminal_states_reject_late_fill_without_mutation(status):
    if status is LifecycleStatus.FILLED:
        order = LifecycleOrder("x", Decimal("1"), Decimal("1"), Decimal("0"), status=status)
    elif status is LifecycleStatus.REJECTED:
        order = LifecycleOrder("x", Decimal("1"), status=status)
    else:
        order = LifecycleOrder("x", Decimal("1"), Decimal(".4"), Decimal(".6"), status=status)
    before = (order.filled_qty, order.remaining_qty, order.average_fill_price, order.status)
    with pytest.raises(InvalidLifecycleTransition):
        order.apply_fill(".1", "100")
    assert (order.filled_qty, order.remaining_qty, order.average_fill_price, order.status) == before

@pytest.mark.parametrize(
    "status",
    [
        LifecycleStatus.FILLED,
        LifecycleStatus.CANCELED,
        LifecycleStatus.REJECTED,
        LifecycleStatus.EXPIRED,
    ],
)
def test_terminal_states_reject_cancel_without_mutation(status):
    if status is LifecycleStatus.FILLED:
        order = LifecycleOrder("x", Decimal("1"), Decimal("1"), Decimal("0"), status=status)
    elif status is LifecycleStatus.REJECTED:
        order = LifecycleOrder("x", Decimal("1"), status=status)
    else:
        order = LifecycleOrder("x", Decimal("1"), Decimal(".4"), Decimal(".6"), status=status)
    before = (order.filled_qty, order.remaining_qty, order.status)
    with pytest.raises(InvalidLifecycleTransition):
        order.request_cancel()
    assert (order.filled_qty, order.remaining_qty, order.status) == before

@pytest.mark.parametrize(
    "status",
    [
        LifecycleStatus.FILLED,
        LifecycleStatus.CANCELED,
        LifecycleStatus.REJECTED,
        LifecycleStatus.EXPIRED,
    ],
)
def test_terminal_states_reject_cancel_unknown_without_mutation(status):
    if status is LifecycleStatus.FILLED:
        order = LifecycleOrder("x", Decimal("1"), Decimal("1"), Decimal("0"), status=status)
    elif status is LifecycleStatus.REJECTED:
        order = LifecycleOrder("x", Decimal("1"), status=status)
    else:
        order = LifecycleOrder("x", Decimal("1"), Decimal(".4"), Decimal(".6"), status=status)
    before = (order.filled_qty, order.remaining_qty, order.status)
    with pytest.raises(InvalidLifecycleTransition):
        order.cancel_unknown()
    assert (order.filled_qty, order.remaining_qty, order.status) == before

def test_partial_fill_then_cancel_unknown_is_valid():
    order = LifecycleOrder("x", Decimal("1"))
    order.acknowledge("e1")
    order.apply_fill(".4", "100")
    order.request_cancel()
    assert order.cancel_unknown() is LifecycleStatus.CANCEL_UNKNOWN
    assert order.filled_qty + order.remaining_qty == order.requested_qty

def test_cancel_unknown_is_not_confirmed_cancel():
    order = LifecycleOrder("x", Decimal("1"))
    order.acknowledge("e1")
    order.request_cancel()
    order.cancel_unknown()
    assert order.status is LifecycleStatus.CANCEL_UNKNOWN
    assert order.confirm_cancel() is LifecycleStatus.CANCELED

def test_impossible_quantity_state_is_rejected():
    with pytest.raises(ValueError, match="INCONSISTENT_ORDER_QUANTITY"):
        LifecycleOrder("x", Decimal("1"), Decimal(".4"), Decimal("0"))

def test_invalid_filled_state_is_rejected():
    with pytest.raises(ValueError, match="INVALID_FILLED_STATE"):
        LifecycleOrder("x", Decimal("1"), Decimal(".4"), Decimal(".6"), status=LifecycleStatus.FILLED)

def test_invalid_partial_state_is_rejected():
    with pytest.raises(ValueError, match="INVALID_PARTIAL_STATE"):
        LifecycleOrder("x", Decimal("1"), Decimal("0"), Decimal("1"), status=LifecycleStatus.PARTIALLY_FILLED)

def test_rejected_order_cannot_fill():
    order = LifecycleOrder("x", Decimal("1"), status=LifecycleStatus.REJECTED)
    with pytest.raises(InvalidLifecycleTransition):
        order.apply_fill(".1", "100")

def test_expired_order_cannot_cancel():
    order = LifecycleOrder("x", Decimal("1"), Decimal(".4"), Decimal(".6"), status=LifecycleStatus.EXPIRED)
    with pytest.raises(InvalidLifecycleTransition):
        order.request_cancel()


def test_invalid_fill_price_is_rejected_without_mutation():
    order = LifecycleOrder("x", Decimal("1"))
    order.acknowledge("e1")
    before = (order.filled_qty, order.remaining_qty, order.average_fill_price, order.status)
    with pytest.raises(ValueError, match="INVALID_FILL_PRICE"):
        order.apply_fill("0.1", "0")
    assert (order.filled_qty, order.remaining_qty, order.average_fill_price, order.status) == before
