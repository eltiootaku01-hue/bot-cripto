"""Deterministic, provider-neutral RiskLimit applicability resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .evidence_binding import RiskLimitResolution, RiskLimitResolutionStatus
from .risk_contracts import RiskLimitSet


@dataclass(frozen=True)
class RiskLimitApplicabilityResolver:
    """Resolve exact scope/metric/time applicability without fallback rules."""

    def resolve(
        self,
        risk_limit_set: RiskLimitSet | None,
        *,
        scope: str,
        metric: str,
        evaluation_timestamp: datetime,
    ) -> RiskLimitResolution:
        if risk_limit_set is None:
            return RiskLimitResolution(
                status=RiskLimitResolutionStatus.LIMITS_UNAVAILABLE,
                risk_limit_set_id=None,
            )

        _require_nonempty(scope, "scope")
        _require_nonempty(metric, "metric")
        _require_aware(evaluation_timestamp, "evaluation_timestamp")

        applicable = tuple(
            limit
            for limit in risk_limit_set.limits
            if limit.scope == scope
            and limit.metric == metric
            and limit.effective_from <= evaluation_timestamp
            and (
                limit.effective_until is None
                or evaluation_timestamp < limit.effective_until
            )
        )

        if not applicable:
            return RiskLimitResolution(
                status=RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT,
                risk_limit_set_id=risk_limit_set.risk_limit_set_id,
            )

        if len(applicable) > 1:
            return RiskLimitResolution(
                status=RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
                risk_limit_set_id=risk_limit_set.risk_limit_set_id,
            )

        return RiskLimitResolution(
            status=RiskLimitResolutionStatus.AVAILABLE,
            risk_limit_set_id=risk_limit_set.risk_limit_set_id,
            applicable_limit_ids=(applicable[0].risk_limit_id,),
        )


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


__all__ = ["RiskLimitApplicabilityResolver"]
