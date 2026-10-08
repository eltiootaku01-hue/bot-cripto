"""Provider-neutral RiskAuthorization V1.0 boundary.

RiskAuthorization is the explicit authorization artifact for one already-created
RiskDecision. This module validates structural provenance only and never
re-evaluates risk or reaches external state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import uuid4

from .risk_contracts import RiskDecision, RiskDecisionOutcome, RiskEvidenceRef
from .risk_evaluation_context import RiskEvaluationContext
from .risk_evaluation_policy import RiskEvaluationPolicy
from .trade_proposal import TradeProposal


class RiskAuthorizationContractError(ValueError):
    """Raised when an authorization binding cannot be proven."""


class RiskAuthorizationStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RiskAuthorizationContractError(f"{field_name} must not be empty")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RiskAuthorizationContractError(
            f"{field_name} must be timezone-aware"
        )


@dataclass(frozen=True)
class RiskAuthorization:
    """Immutable authorized artifact bound to one RiskDecision/context/policy."""

    authorization_id: str
    risk_decision_id: str
    proposal_id: str
    signal_id: str
    correlation_id: str
    evaluation_context_id: str
    policy_id: str
    policy_version: str
    decision_timestamp: datetime
    risk_evidence: tuple[RiskEvidenceRef, ...]
    status: RiskAuthorizationStatus = RiskAuthorizationStatus.AUTHORIZED

    def __post_init__(self) -> None:
        _require_nonempty(self.authorization_id, "authorization_id")
        _require_nonempty(self.risk_decision_id, "risk_decision_id")
        _require_nonempty(self.proposal_id, "proposal_id")
        _require_nonempty(self.signal_id, "signal_id")
        _require_nonempty(self.correlation_id, "correlation_id")
        _require_nonempty(self.evaluation_context_id, "evaluation_context_id")
        _require_nonempty(self.policy_id, "policy_id")
        _require_nonempty(self.policy_version, "policy_version")
        _require_aware(self.decision_timestamp, "decision_timestamp")
        if self.status is not RiskAuthorizationStatus.AUTHORIZED:
            raise RiskAuthorizationContractError(
                "RiskAuthorization artifacts must be AUTHORIZED"
            )
        evidence = tuple(self.risk_evidence)
        if not evidence:
            raise RiskAuthorizationContractError(
                "RiskAuthorization requires non-empty risk_evidence"
            )
        if not all(isinstance(item, RiskEvidenceRef) for item in evidence):
            raise RiskAuthorizationContractError(
                "risk_evidence must contain only RiskEvidenceRef values"
            )
        object.__setattr__(self, "risk_evidence", evidence)


@dataclass(frozen=True)
class RiskAuthorizationResult:
    """Immutable typed fail-closed result of the authorization boundary."""

    status: RiskAuthorizationStatus
    authorization: RiskAuthorization | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, RiskAuthorizationStatus):
            raise RiskAuthorizationContractError(
                "status must be RiskAuthorizationStatus"
            )
        _require_nonempty(self.reason, "reason")
        if self.status is RiskAuthorizationStatus.AUTHORIZED:
            if not isinstance(self.authorization, RiskAuthorization):
                raise RiskAuthorizationContractError(
                    "AUTHORIZED result requires RiskAuthorization"
                )
        elif self.authorization is not None:
            raise RiskAuthorizationContractError(
                "REJECTED/UNKNOWN result must not expose RiskAuthorization"
            )


def _result(
    status: RiskAuthorizationStatus,
    reason: str,
    authorization: RiskAuthorization | None = None,
) -> RiskAuthorizationResult:
    return RiskAuthorizationResult(
        status=status,
        authorization=authorization,
        reason=reason,
    )


def authorize_risk_decision(
    *,
    proposal: TradeProposal,
    risk_decision: RiskDecision,
    context: RiskEvaluationContext,
    policy: RiskEvaluationPolicy,
) -> RiskAuthorizationResult:
    """Verify a completed risk decision and produce an explicit authorization."""

    if not isinstance(proposal, TradeProposal):
        return _result(RiskAuthorizationStatus.UNKNOWN, "PROPOSAL_INVALID")
    if not isinstance(risk_decision, RiskDecision):
        return _result(RiskAuthorizationStatus.UNKNOWN, "RISK_DECISION_INVALID")
    if not isinstance(context, RiskEvaluationContext):
        return _result(RiskAuthorizationStatus.UNKNOWN, "RISK_CONTEXT_INVALID")
    if not isinstance(policy, RiskEvaluationPolicy):
        return _result(RiskAuthorizationStatus.UNKNOWN, "RISK_POLICY_INVALID")

    if risk_decision.outcome is RiskDecisionOutcome.REJECTED:
        return _result(RiskAuthorizationStatus.REJECTED, risk_decision.reason)
    if risk_decision.outcome is RiskDecisionOutcome.UNKNOWN:
        return _result(RiskAuthorizationStatus.UNKNOWN, risk_decision.reason)
    if risk_decision.outcome is not RiskDecisionOutcome.APPROVED:
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "RISK_DECISION_OUTCOME_INVALID",
        )

    if context.trade_proposal != proposal:
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "PROPOSAL_CONTEXT_BINDING_MISMATCH",
        )

    if proposal.proposal_id != risk_decision.proposal_id:
        return _result(RiskAuthorizationStatus.UNKNOWN, "PROPOSAL_ID_MISMATCH")
    if proposal.signal_id != risk_decision.signal_id:
        return _result(RiskAuthorizationStatus.UNKNOWN, "SIGNAL_ID_MISMATCH")
    if proposal.correlation_id != risk_decision.correlation_id:
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "CORRELATION_ID_MISMATCH",
        )

    if risk_decision.evaluation_context_id != context.evaluation_context_id:
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "EVALUATION_CONTEXT_ID_MISMATCH",
        )

    if risk_decision.policy_id != policy.policy_id:
        return _result(RiskAuthorizationStatus.UNKNOWN, "POLICY_ID_MISMATCH")
    if risk_decision.policy_version != policy.policy_version:
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "POLICY_VERSION_MISMATCH",
        )

    if (
        risk_decision.decision_timestamp != proposal.decision_timestamp
        or risk_decision.decision_timestamp != context.decision_timestamp
    ):
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "DECISION_TIMESTAMP_MISMATCH",
        )

    if not context.is_complete:
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "RISK_CONTEXT_INCOMPLETE",
        )

    if not risk_decision.risk_evidence:
        return _result(RiskAuthorizationStatus.UNKNOWN, "RISK_EVIDENCE_EMPTY")
    if not all(
        isinstance(item, RiskEvidenceRef)
        for item in risk_decision.risk_evidence
    ):
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "RISK_EVIDENCE_INVALID",
        )

    try:
        authorization = RiskAuthorization(
            authorization_id=uuid4().hex,
            risk_decision_id=risk_decision.risk_decision_id,
            proposal_id=proposal.proposal_id,
            signal_id=proposal.signal_id,
            correlation_id=proposal.correlation_id,
            evaluation_context_id=context.evaluation_context_id,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            decision_timestamp=risk_decision.decision_timestamp,
            risk_evidence=risk_decision.risk_evidence,
        )
    except (TypeError, ValueError):
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "RISK_AUTHORIZATION_CONSTRUCTION_FAILED",
        )

    if authorization.risk_evidence != risk_decision.risk_evidence:
        return _result(
            RiskAuthorizationStatus.UNKNOWN,
            "RISK_EVIDENCE_REBIND_FAILED",
        )

    return _result(
        RiskAuthorizationStatus.AUTHORIZED,
        "RISK_AUTHORIZATION_CREATED",
        authorization,
    )


__all__ = [
    "RiskAuthorization",
    "RiskAuthorizationContractError",
    "RiskAuthorizationResult",
    "RiskAuthorizationStatus",
    "authorize_risk_decision",
]
