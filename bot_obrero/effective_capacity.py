"""Provider-neutral derived read model for effective financial capacity.

EffectiveCapacity combines one CanonicalAccountState bucket with the
corresponding ReservationReadSet. It does not persist state, enforce
concurrency, or apply temporal freshness rules.

Arithmetic status and source completeness are deliberately separate:
- status reports whether the derived arithmetic is negative (OVERCOMMITTED).
- completeness reports whether the source snapshot is fully usable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .reservation import ReservationReadSet, ReservationResourceKind
from .risk_contracts import CanonicalAccountState, Completeness


class EffectiveCapacityError(ValueError):
    """Raised when effective-capacity inputs are structurally incompatible."""


class EffectiveCapacityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    OVERCOMMITTED = "OVERCOMMITTED"


_COMPLETENESS_RANK = {
    Completeness.COMPLETE: 0,
    Completeness.PARTIAL: 1,
    Completeness.UNKNOWN: 2,
}


def _combine_completeness(canonical: Completeness, reservations: Completeness) -> Completeness:
    return max((canonical, reservations), key=_COMPLETENESS_RANK.__getitem__)


def _nonempty(value: str, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise EffectiveCapacityError(f"{field_name} must be a non-empty str")


def _finite_decimal(value: Decimal, field_name: str) -> None:
    if type(value) is not Decimal:
        raise EffectiveCapacityError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise EffectiveCapacityError(f"{field_name} must be finite")


@dataclass(frozen=True)
class EffectiveCapacity:
    """Derived financial capacity for one (account, resource_kind, asset) bucket.

    status is arithmetic only; completeness carries source confidence.
    """

    account_id: str
    resource_kind: ReservationResourceKind
    asset: str
    canonical_available: Decimal
    protected_active_reserved: Decimal
    effective_available: Decimal
    status: EffectiveCapacityStatus
    completeness: Completeness

    def __post_init__(self) -> None:
        _nonempty(self.account_id, "account_id")
        if not isinstance(self.resource_kind, ReservationResourceKind):
            raise EffectiveCapacityError("resource_kind must be ReservationResourceKind")
        _nonempty(self.asset, "asset")

        for field_name in ("canonical_available", "protected_active_reserved", "effective_available"):
            _finite_decimal(getattr(self, field_name), field_name)

        if self.canonical_available < 0:
            raise EffectiveCapacityError("canonical_available must be non-negative")
        if self.protected_active_reserved < 0:
            raise EffectiveCapacityError("protected_active_reserved must be non-negative")
        if self.effective_available != self.canonical_available - self.protected_active_reserved:
            raise EffectiveCapacityError("effective_available must equal canonical_available - protected_active_reserved")

        expected_status = (
            EffectiveCapacityStatus.OVERCOMMITTED
            if self.effective_available < 0
            else EffectiveCapacityStatus.AVAILABLE
        )
        if self.status is not expected_status:
            raise EffectiveCapacityError("status does not match effective_available")
        if not isinstance(self.completeness, Completeness):
            raise EffectiveCapacityError("completeness must be Completeness")


def calculate_effective_capacity(
    canonical_account_state: CanonicalAccountState,
    reservation_read_set: ReservationReadSet,
    *,
    resource_kind: ReservationResourceKind,
    asset: str,
) -> EffectiveCapacity:
    """Calculate one effective-capacity bucket from existing canonical contracts."""

    if not isinstance(canonical_account_state, CanonicalAccountState):
        raise TypeError("canonical_account_state must be CanonicalAccountState")
    if not isinstance(reservation_read_set, ReservationReadSet):
        raise TypeError("reservation_read_set must be ReservationReadSet")
    if not isinstance(resource_kind, ReservationResourceKind):
        raise TypeError("resource_kind must be ReservationResourceKind")
    _nonempty(asset, "asset")

    if canonical_account_state.account_id != reservation_read_set.account_id:
        raise EffectiveCapacityError("canonical account and reservation read set account_id must match")

    matching_balances = tuple(item for item in canonical_account_state.balances if item.asset == asset)
    if not matching_balances:
        raise EffectiveCapacityError(f"canonical balance for asset {asset!r} is missing")
    if len(matching_balances) != 1:
        raise EffectiveCapacityError(f"canonical balance for asset {asset!r} is duplicated")

    canonical_available = matching_balances[0].available
    protected_active_reserved = sum(
        (
            reservation.protected_capacity
            for reservation in reservation_read_set.relevant_reservations
            if reservation.resource_kind is resource_kind and reservation.asset == asset
        ),
        Decimal("0"),
    )
    effective_available = canonical_available - protected_active_reserved
    status = EffectiveCapacityStatus.OVERCOMMITTED if effective_available < 0 else EffectiveCapacityStatus.AVAILABLE

    return EffectiveCapacity(
        account_id=canonical_account_state.account_id,
        resource_kind=resource_kind,
        asset=asset,
        canonical_available=canonical_available,
        protected_active_reserved=protected_active_reserved,
        effective_available=effective_available,
        status=status,
        completeness=_combine_completeness(canonical_account_state.completeness, reservation_read_set.completeness),
    )


__all__ = [
    "EffectiveCapacity",
    "EffectiveCapacityError",
    "EffectiveCapacityStatus",
    "calculate_effective_capacity",
]