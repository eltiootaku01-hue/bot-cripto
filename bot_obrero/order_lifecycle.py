from dataclasses import dataclass
from enum import Enum
from datetime import datetime
from decimal import Decimal

class LifecycleStatus(str, Enum):
    NEW="NEW"
    PENDING_SUBMIT="PENDING_SUBMIT"
    ACKNOWLEDGED="ACKNOWLEDGED"
    PARTIALLY_FILLED="PARTIALLY_FILLED"
    FILLED="FILLED"
    PENDING_CANCEL="PENDING_CANCEL"
    CANCELED="CANCELED"
    CANCEL_UNKNOWN="CANCEL_UNKNOWN"
    REJECTED="REJECTED"
    EXPIRED="EXPIRED"
    UNKNOWN="UNKNOWN"

class InvalidLifecycleTransition(ValueError):
    pass

_ACTIONS = {
    "submit": {LifecycleStatus.NEW},
    "acknowledge": {LifecycleStatus.NEW, LifecycleStatus.PENDING_SUBMIT},
    "apply_fill": {
        LifecycleStatus.ACKNOWLEDGED,
        LifecycleStatus.PARTIALLY_FILLED,
        LifecycleStatus.PENDING_CANCEL,
        LifecycleStatus.CANCEL_UNKNOWN,
    },
    "request_cancel": {
        LifecycleStatus.ACKNOWLEDGED,
        LifecycleStatus.PARTIALLY_FILLED,
        LifecycleStatus.PENDING_CANCEL,
    },
    "cancel_unknown": {LifecycleStatus.PENDING_CANCEL},
    "confirm_cancel": {LifecycleStatus.PENDING_CANCEL, LifecycleStatus.CANCEL_UNKNOWN},
    "reject": {LifecycleStatus.NEW, LifecycleStatus.PENDING_SUBMIT},
    "expire": {
        LifecycleStatus.ACKNOWLEDGED,
        LifecycleStatus.PARTIALLY_FILLED,
        LifecycleStatus.PENDING_CANCEL,
        LifecycleStatus.CANCEL_UNKNOWN,
    },
}

@dataclass
class LifecycleOrder:
    client_order_id: str
    requested_qty: Decimal | float
    filled_qty: Decimal | float = 0
    remaining_qty: Decimal | float | None = None
    average_fill_price: Decimal | float | None = None
    status: LifecycleStatus = LifecycleStatus.NEW
    exchange_order_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self):
        self.requested_qty = Decimal(str(self.requested_qty))
        self.filled_qty = Decimal(str(self.filled_qty))
        expected_remaining = self.requested_qty - self.filled_qty
        supplied_remaining = (
            expected_remaining
            if self.remaining_qty is None
            else Decimal(str(self.remaining_qty))
        )
        if (
            self.requested_qty <= 0
            or self.filled_qty < 0
            or supplied_remaining < 0
            or self.filled_qty > self.requested_qty
        ):
            raise ValueError("INVALID_ORDER_QUANTITY")
        if supplied_remaining != expected_remaining:
            raise ValueError("INCONSISTENT_ORDER_QUANTITY")
        self.remaining_qty = supplied_remaining
        self._check_status_invariant()

    def _check_invariant(self):
        if self.filled_qty + self.remaining_qty != self.requested_qty:
            raise ValueError("INCONSISTENT_ORDER_QUANTITY")

    def _check_status_invariant(self):
        self._check_invariant()
        if self.status is LifecycleStatus.FILLED:
            if self.filled_qty != self.requested_qty or self.remaining_qty != 0:
                raise ValueError("INVALID_FILLED_STATE")
        elif self.status is LifecycleStatus.PARTIALLY_FILLED:
            if not (0 < self.filled_qty < self.requested_qty and self.remaining_qty > 0):
                raise ValueError("INVALID_PARTIAL_STATE")
        elif self.status is LifecycleStatus.REJECTED:
            if self.filled_qty != 0 or self.remaining_qty != self.requested_qty:
                raise ValueError("INVALID_REJECTED_STATE")
        elif self.status in {
            LifecycleStatus.NEW,
            LifecycleStatus.PENDING_SUBMIT,
            LifecycleStatus.ACKNOWLEDGED,
            LifecycleStatus.PENDING_CANCEL,
            LifecycleStatus.CANCEL_UNKNOWN,
            LifecycleStatus.CANCELED,
            LifecycleStatus.EXPIRED,
        }:
            if self.filled_qty >= self.requested_qty:
                raise ValueError("INVALID_NONTERMINAL_FILL_STATE")

    def _require_transition(self, action):
        if self.status not in _ACTIONS[action]:
            raise InvalidLifecycleTransition(
                f"{self.status.value} -> {action} is not allowed"
            )

    def _set_status(self, status):
        self.status = status
        self._check_status_invariant()

    def submit(self):
        self._require_transition("submit")
        self._set_status(LifecycleStatus.PENDING_SUBMIT)
        return self.status

    def acknowledge(self, exchange_order_id: str | None = None):
        self._require_transition("acknowledge")
        self.exchange_order_id = exchange_order_id or self.exchange_order_id
        self._set_status(LifecycleStatus.ACKNOWLEDGED)
        return self.status

    def reject(self):
        self._require_transition("reject")
        self._set_status(LifecycleStatus.REJECTED)
        return self.status

    def expire(self):
        self._require_transition("expire")
        self._set_status(LifecycleStatus.EXPIRED)
        return self.status

    def apply_fill(self, quantity, price):
        self._require_transition("apply_fill")
        qty, px = Decimal(str(quantity)), Decimal(str(price))
        if qty <= 0 or qty > self.remaining_qty:
            raise ValueError("INVALID_FILL")
        prior = self.filled_qty
        self.filled_qty += qty
        self.remaining_qty -= qty
        if prior == 0:
            self.average_fill_price = px
        else:
            if self.average_fill_price is None:
                raise ValueError("MISSING_AVERAGE_FILL_PRICE")
            self.average_fill_price = (
                Decimal(str(self.average_fill_price)) * prior + px * qty
            ) / self.filled_qty
        self._set_status(
            LifecycleStatus.FILLED
            if self.remaining_qty == 0
            else LifecycleStatus.PARTIALLY_FILLED
        )
        return self.status

    def request_cancel(self):
        self._require_transition("request_cancel")
        self._set_status(LifecycleStatus.PENDING_CANCEL)
        return self.status

    def cancel_unknown(self):
        self._require_transition("cancel_unknown")
        self._set_status(LifecycleStatus.CANCEL_UNKNOWN)
        return self.status

    def confirm_cancel(self):
        self._require_transition("confirm_cancel")
        self._set_status(LifecycleStatus.CANCELED)
        return self.status
