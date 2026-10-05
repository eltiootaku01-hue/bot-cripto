"""Provider-neutral evidence bindings and deterministic snapshot identities.

HUESO 02-I3 implements only auxiliary evidence primitives:
- AvailabilityBinding
- deterministic ReservationReadSet identity
- RiskLimitResolution status/result contract

It does not evaluate risk, resolve risk-limit applicability, persist evidence,
or alter canonical risk, reservation, or temporal contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from .reservation import Reservation, ReservationReadSet


class AvailabilitySubjectKind(str, Enum):
    """Closed provider-neutral vocabulary for availability-bound subjects."""

    ACCOUNT_STATE = "ACCOUNT_STATE"
    EXPOSURE = "EXPOSURE"
    RISK_LIMIT_SET = "RISK_LIMIT_SET"
    RESERVATION_READ_SET = "RESERVATION_READ_SET"
    MARKET_DATA = "MARKET_DATA"
    MARKET_OBSERVATION = "MARKET_OBSERVATION"


class RiskLimitResolutionStatus(str, Enum):
    """Contractual status of a future RiskLimit applicability resolution."""

    AVAILABLE = "AVAILABLE"
    NO_APPLICABLE_LIMIT = "NO_APPLICABLE_LIMIT"
    LIMITS_UNAVAILABLE = "LIMITS_UNAVAILABLE"
    LIMITS_INCOMPLETE = "LIMITS_INCOMPLETE"


def _require_nonempty(value: str, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_aware(value: datetime, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_datetime(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_decimal(value: Any) -> str:
    return str(value)


def _canonical_reservation(reservation: Reservation) -> dict[str, Any]:
    if not isinstance(reservation, Reservation):
        raise TypeError("reservations must contain only Reservation values")

    return {
        "reservation_id": reservation.reservation_id,
        "account_id": reservation.account_id,
        "resource_kind": reservation.resource_kind.value,
        "asset": reservation.asset,
        "reserved_amount": _canonical_decimal(reservation.reserved_amount),
        "consumed_amount": _canonical_decimal(reservation.consumed_amount),
        "remaining_amount": _canonical_decimal(reservation.remaining_amount),
        "state": reservation.state.value,
        "proposal_id": reservation.proposal_id,
        "risk_decision_id": reservation.risk_decision_id,
        "correlation_id": reservation.correlation_id,
        "client_order_id": reservation.client_order_id,
        "exchange_order_id": reservation.exchange_order_id,
        "created_at": _canonical_datetime(reservation.created_at),
        "updated_at": _canonical_datetime(reservation.updated_at),
    }


@dataclass(frozen=True)
class AvailabilityBinding:
    """Immutable proof of when a concrete subject became available."""

    subject_kind: AvailabilitySubjectKind
    subject_id: str
    available_at: datetime
    source: str
    evidence_reference: str
    binding_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.subject_kind, AvailabilitySubjectKind):
            raise ValueError("subject_kind must be AvailabilitySubjectKind")
        _require_nonempty(self.subject_id, "subject_id")
        _require_aware(self.available_at, "available_at")
        _require_nonempty(self.source, "source")
        _require_nonempty(self.evidence_reference, "evidence_reference")

        identity_payload = {
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "available_at": _canonical_datetime(self.available_at),
            "source": self.source,
            "evidence_reference": self.evidence_reference,
        }
        object.__setattr__(self, "binding_id", _sha256(identity_payload))

    def validate_for(self, evaluation_timestamp: datetime) -> None:
        """Fail closed unless this binding was available by evaluation time."""

        _require_aware(evaluation_timestamp, "evaluation_timestamp")
        if self.available_at > evaluation_timestamp:
            raise ValueError(
                "availability binding is not valid for evaluation_timestamp"
            )


def derive_reservation_read_set_id(read_set: ReservationReadSet) -> str:
    """Derive a deterministic content identity without using read_at.

    The canonical representation preserves Reservation order exactly as supplied
    by the existing ReservationReadSet contract.
    """

    if not isinstance(read_set, ReservationReadSet):
        raise TypeError("read_set must be ReservationReadSet")

    payload = {
        "account_id": read_set.account_id,
        "completeness": read_set.completeness.value,
        "reservations": [
            _canonical_reservation(item) for item in read_set.reservations
        ],
    }
    return _sha256(payload)


@dataclass(frozen=True)
class RiskLimitResolution:
    """Result contract for future RiskLimit applicability resolution.

    This object records only the resolution status and evidence identity.
    It does not resolve, match, or calculate applicable limits.
    """

    status: RiskLimitResolutionStatus
    risk_limit_set_id: str | None
    applicable_limit_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, RiskLimitResolutionStatus):
            raise ValueError("status must be RiskLimitResolutionStatus")

        if self.risk_limit_set_id is not None:
            _require_nonempty(self.risk_limit_set_id, "risk_limit_set_id")

        ids = tuple(self.applicable_limit_ids)
        if not all(type(item) is str and item.strip() for item in ids):
            raise ValueError(
                "applicable_limit_ids must contain only non-empty strings"
            )
        if len(ids) != len(set(ids)):
            raise ValueError("applicable_limit_ids must not contain duplicates")
        object.__setattr__(self, "applicable_limit_ids", ids)

        if self.status is RiskLimitResolutionStatus.LIMITS_UNAVAILABLE:
            if self.risk_limit_set_id is not None:
                raise ValueError(
                    "LIMITS_UNAVAILABLE cannot claim a risk_limit_set_id"
                )
            if ids:
                raise ValueError(
                    "LIMITS_UNAVAILABLE cannot claim applicable limits"
                )

        elif self.status is RiskLimitResolutionStatus.LIMITS_INCOMPLETE:
            _require_nonempty(self.risk_limit_set_id or "", "risk_limit_set_id")
            if ids:
                raise ValueError(
                    "LIMITS_INCOMPLETE cannot claim applicable limits"
                )

        elif self.status is RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT:
            _require_nonempty(self.risk_limit_set_id or "", "risk_limit_set_id")
            if ids:
                raise ValueError(
                    "NO_APPLICABLE_LIMIT requires applicable_limit_ids == ()"
                )

        elif self.status is RiskLimitResolutionStatus.AVAILABLE:
            _require_nonempty(self.risk_limit_set_id or "", "risk_limit_set_id")


__all__ = [
    "AvailabilityBinding",
    "AvailabilitySubjectKind",
    "RiskLimitResolution",
    "RiskLimitResolutionStatus",
    "derive_reservation_read_set_id",
]
