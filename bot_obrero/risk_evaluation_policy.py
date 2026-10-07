"""Provider-neutral policy primitives for deterministic risk evaluation.

HUESO 05-A defines policy and evidence semantics only. It does not evaluate a
trade, create a decision, access providers, or authorize execution.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Sequence

from .evidence_binding import (
    AvailabilityBinding,
    AvailabilitySubjectKind,
    RiskLimitResolutionStatus,
)
from .risk_contracts import RiskEvidenceRef


class ValuationFreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ValuationFreshnessPolicy:
    """Provider-neutral rule for determining valuation freshness."""

    max_valuation_age: timedelta | None

    def __post_init__(self) -> None:
        if self.max_valuation_age is not None:
            if not isinstance(self.max_valuation_age, timedelta):
                raise TypeError("max_valuation_age must be timedelta or None")
            if self.max_valuation_age.total_seconds() < 0:
                raise ValueError("max_valuation_age must not be negative")

    def evaluate(
        self,
        *,
        valuation_as_of: datetime | None,
        evaluation_timestamp: datetime | None,
    ) -> ValuationFreshnessStatus:
        """Return FRESH, STALE, or UNKNOWN without consulting a clock."""

        if valuation_as_of is None or evaluation_timestamp is None:
            return ValuationFreshnessStatus.UNKNOWN

        _require_aware(valuation_as_of, "valuation_as_of")
        _require_aware(evaluation_timestamp, "evaluation_timestamp")

        if valuation_as_of > evaluation_timestamp:
            return ValuationFreshnessStatus.UNKNOWN

        if self.max_valuation_age is None:
            return ValuationFreshnessStatus.FRESH

        age = evaluation_timestamp - valuation_as_of
        if age <= self.max_valuation_age:
            return ValuationFreshnessStatus.FRESH
        return ValuationFreshnessStatus.STALE


@dataclass(frozen=True)
class ApprovalEvidencePolicy:
    """Minimum evidence rule for a future approval-producing evaluation."""

    require_risk_evidence_for_approval: bool

    def __post_init__(self) -> None:
        if type(self.require_risk_evidence_for_approval) is not bool:
            raise TypeError("require_risk_evidence_for_approval must be bool")

    def has_minimum_evidence(
        self,
        risk_evidence: Sequence[RiskEvidenceRef] | None,
    ) -> bool:
        if not self.require_risk_evidence_for_approval:
            return True
        if risk_evidence is None:
            return False
        evidence = tuple(risk_evidence)
        return bool(evidence) and all(
            isinstance(item, RiskEvidenceRef) for item in evidence
        )


@dataclass(frozen=True)
class RiskEvaluationPolicy:
    """Immutable, provider-neutral precondition policy for future evaluation."""

    policy_id: str
    policy_version: str
    required_availability_subjects: tuple[AvailabilitySubjectKind, ...]
    require_context_complete: bool
    require_risk_evidence_for_approval: bool
    max_valuation_age: timedelta | None

    def __post_init__(self) -> None:
        _require_nonempty(self.policy_id, "policy_id")
        _require_nonempty(self.policy_version, "policy_version")

        subjects = tuple(self.required_availability_subjects)
        if not all(
            isinstance(item, AvailabilitySubjectKind) for item in subjects
        ):
            raise TypeError(
                "required_availability_subjects must contain only AvailabilitySubjectKind"
            )
        if len(subjects) != len(set(subjects)):
            raise ValueError(
                "required_availability_subjects must not contain duplicates"
            )
        object.__setattr__(self, "required_availability_subjects", subjects)

        if type(self.require_context_complete) is not bool:
            raise TypeError("require_context_complete must be bool")
        if type(self.require_risk_evidence_for_approval) is not bool:
            raise TypeError("require_risk_evidence_for_approval must be bool")

        ValuationFreshnessPolicy(self.max_valuation_age)

    @property
    def valuation_freshness_policy(self) -> ValuationFreshnessPolicy:
        return ValuationFreshnessPolicy(self.max_valuation_age)

    @property
    def approval_evidence_policy(self) -> ApprovalEvidencePolicy:
        return ApprovalEvidencePolicy(self.require_risk_evidence_for_approval)

    def approval_prerequisites_satisfied(
        self,
        *,
        context_complete: bool,
        risk_limit_status: RiskLimitResolutionStatus,
        risk_evidence: Sequence[RiskEvidenceRef] | None,
        valuation_freshness: ValuationFreshnessStatus | None,
    ) -> bool:
        """Return whether policy preconditions permit an approval path to continue.

        This method produces only a boolean precondition result. It does not
        construct or mutate any decision object.
        """

        if type(context_complete) is not bool:
            raise TypeError("context_complete must be bool")
        if not isinstance(risk_limit_status, RiskLimitResolutionStatus):
            raise TypeError(
                "risk_limit_status must be RiskLimitResolutionStatus"
            )

        if self.require_context_complete and not context_complete:
            return False

        if (
            self.require_risk_evidence_for_approval
            and not self.approval_evidence_policy.has_minimum_evidence(risk_evidence)
        ):
            return False

        if risk_limit_status is not RiskLimitResolutionStatus.AVAILABLE:
            return False

        if self.max_valuation_age is not None:
            if valuation_freshness is not ValuationFreshnessStatus.FRESH:
                return False

        return True

    def availability_requirements_satisfied(
        self,
        *,
        bindings: Sequence[AvailabilityBinding],
        evaluation_timestamp: datetime,
    ) -> bool:
        """Return whether every required subject kind has exactly one valid binding."""

        _require_aware(evaluation_timestamp, "evaluation_timestamp")
        normalized = tuple(bindings)

        if not all(isinstance(item, AvailabilityBinding) for item in normalized):
            raise TypeError(
                "bindings must contain only AvailabilityBinding values"
            )

        for subject_kind in self.required_availability_subjects:
            matches = tuple(
                item
                for item in normalized
                if item.subject_kind is subject_kind
            )
            if len(matches) != 1:
                return False
            try:
                matches[0].validate_for(evaluation_timestamp)
            except ValueError:
                return False

        return True


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


__all__ = [
    "ApprovalEvidencePolicy",
    "RiskEvaluationPolicy",
    "ValuationFreshnessPolicy",
    "ValuationFreshnessStatus",
]
