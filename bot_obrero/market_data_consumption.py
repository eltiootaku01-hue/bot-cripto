"""Provider-neutral MarketData consumption decision contract.

This module evaluates whether one canonical MarketData instance can be used at
an explicit decision timestamp. It never changes MarketData or infers
availability from received_at/observed_at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .acquisition_evidence import AcquisitionOperationEvidence
from .market_data import MarketData, MarketDataError
from .temporal import EvidenceTimestamp


class MarketDataConsumptionError(ValueError):
    """Base error for the provider-neutral consumption boundary."""


class MarketDataConsumptionPolicy(str, Enum):
    REQUIRE_AVAILABLE = "REQUIRE_AVAILABLE"
    ALLOW_UNKNOWN = "ALLOW_UNKNOWN"


class MarketDataConsumptionStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


def _aware_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise MarketDataConsumptionError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataConsumptionError(f"{field_name} must be timezone-aware")
    return value


@dataclass(frozen=True)
class MarketDataConsumptionResult:
    """Immutable decision result that references the original MarketData."""

    market_data: MarketData
    decision_timestamp: datetime
    policy: MarketDataConsumptionPolicy
    status: MarketDataConsumptionStatus
    evidence_timestamp: EvidenceTimestamp | None = None
    acquisition_evidence: AcquisitionOperationEvidence | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.market_data, MarketData):
            raise MarketDataConsumptionError("market_data must be MarketData")
        _aware_timestamp(self.decision_timestamp, "decision_timestamp")
        if not isinstance(self.policy, MarketDataConsumptionPolicy):
            raise MarketDataConsumptionError("policy must be MarketDataConsumptionPolicy")
        if not isinstance(self.status, MarketDataConsumptionStatus):
            raise MarketDataConsumptionError("status must be MarketDataConsumptionStatus")
        if self.evidence_timestamp is not None and not isinstance(
            self.evidence_timestamp, EvidenceTimestamp
        ):
            raise MarketDataConsumptionError(
                "evidence_timestamp must be EvidenceTimestamp or None"
            )
        if self.acquisition_evidence is not None and not isinstance(
            self.acquisition_evidence, AcquisitionOperationEvidence
        ):
            raise MarketDataConsumptionError(
                "acquisition_evidence must be AcquisitionOperationEvidence or None"
            )
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise MarketDataConsumptionError("reason must be a non-empty string or None")

        if self.status is MarketDataConsumptionStatus.ACCEPTED:
            if self.evidence_timestamp is None:
                raise MarketDataConsumptionError("ACCEPTED requires EvidenceTimestamp")
            if self.reason is not None:
                raise MarketDataConsumptionError("ACCEPTED cannot contain a rejection/unknown reason")
        elif self.evidence_timestamp is not None:
            raise MarketDataConsumptionError(
                "evidence_timestamp is only valid for ACCEPTED results"
            )
        elif self.reason is None:
            raise MarketDataConsumptionError(
                "REJECTED/UNKNOWN results require an explicit reason"
            )

    @property
    def market_data_id(self) -> str:
        """Original canonical MarketData identity."""
        return self.market_data.market_data_id

    @property
    def candle_identity(self) -> tuple[str, str, datetime]:
        """Original canonical candle identity; never regenerated."""
        return self.market_data.candle_identity

    @property
    def availability_is_unknown(self) -> bool:
        return self.status is MarketDataConsumptionStatus.UNKNOWN


def evaluate_market_data_consumption(
    market_data: MarketData,
    *,
    decision_timestamp: datetime,
    policy: MarketDataConsumptionPolicy,
    acquisition_evidence: AcquisitionOperationEvidence | None = None,
) -> MarketDataConsumptionResult:
    """Evaluate one MarketData instance under an explicit decision policy.

    The decision timestamp is mandatory and explicit. Availability is never
    inferred from observed_at, received_at, or the current wall clock.

    The temporal comparison is delegated to the existing MarketData.evidence_at()
    / EvidenceTimestamp contract. This function only classifies its result as
    ACCEPTED, REJECTED, or UNKNOWN.
    """

    if not isinstance(market_data, MarketData):
        raise MarketDataConsumptionError("market_data must be MarketData")
    decision_timestamp = _aware_timestamp(decision_timestamp, "decision_timestamp")
    try:
        policy = MarketDataConsumptionPolicy(policy)
    except (TypeError, ValueError) as exc:
        raise MarketDataConsumptionError("policy is invalid") from exc

    if acquisition_evidence is not None and not isinstance(
        acquisition_evidence, AcquisitionOperationEvidence
    ):
        raise MarketDataConsumptionError(
            "acquisition_evidence must be AcquisitionOperationEvidence or None"
        )

    if market_data.available_at is None:
        return MarketDataConsumptionResult(
            market_data=market_data,
            decision_timestamp=decision_timestamp,
            policy=policy,
            status=MarketDataConsumptionStatus.UNKNOWN,
            acquisition_evidence=acquisition_evidence,
            reason="AVAILABLE_AT_UNKNOWN",
        )

    try:
        evidence_timestamp = market_data.evidence_at(decision_timestamp)
    except (MarketDataError, ValueError) as exc:
        return MarketDataConsumptionResult(
            market_data=market_data,
            decision_timestamp=decision_timestamp,
            policy=policy,
            status=MarketDataConsumptionStatus.REJECTED,
            acquisition_evidence=acquisition_evidence,
            reason=str(exc) or "availability evidence rejects this decision timestamp",
        )

    return MarketDataConsumptionResult(
        market_data=market_data,
        decision_timestamp=decision_timestamp,
        policy=policy,
        status=MarketDataConsumptionStatus.ACCEPTED,
        evidence_timestamp=evidence_timestamp,
        acquisition_evidence=acquisition_evidence,
    )


__all__ = [
    "MarketDataConsumptionError",
    "MarketDataConsumptionPolicy",
    "MarketDataConsumptionResult",
    "MarketDataConsumptionStatus",
    "evaluate_market_data_consumption",
]
