"""Deterministic, provider-neutral and fail-closed RiskEngine v1.0.

HUESO 05-B evaluates one TradeProposal against an immutable RiskEvaluationContext,
RiskEvaluationPolicy and deterministic RiskLimit applicability resolution.

The first metric is MAX_NOTIONAL over ACCOUNT. In v1.0 the proposal notional is
derived only from explicit proposal data:
- LIMIT order: requested_quantity * requested_price
- BUY MARKET order: max_quote_spend
- other cases without an explicit proposal-side valuation: UNKNOWN

The engine never queries external state, never uses a clock, and never mutates
its inputs.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from .evidence_binding import RiskLimitResolutionStatus
from .risk_contracts import (
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidenceRef,
    RiskLimit,
)
from .risk_evaluation_context import RiskEvaluationContext
from .risk_evaluation_policy import (
    RiskEvaluationPolicy,
    ValuationFreshnessStatus,
)
from .risk_limit_applicability import RiskLimitApplicabilityResolver
from .trade_proposal import TradeOrderType, TradeProposal, TradeSide


MAX_NOTIONAL_SCOPE = "ACCOUNT"
MAX_NOTIONAL_METRIC = "MAX_NOTIONAL"

APPROVED_REASON = "MAX_NOTIONAL_WITHIN_LIMIT"
REJECTED_LIMIT_REASON = "MAX_NOTIONAL_EXCEEDED"
REJECTED_PROPOSAL_REASON = "TRADE_PROPOSAL_INVALID"
REJECTED_VALUATION_REASON = "VALUATION_STALE"

UNKNOWN_CONTEXT_REASON = "RISK_CONTEXT_INCOMPLETE"
UNKNOWN_AVAILABILITY_REASON = "RISK_AVAILABILITY_UNPROVEN"
UNKNOWN_RESOLUTION_REASON = "RISK_LIMIT_RESOLUTION_UNAVAILABLE"
UNKNOWN_RESOLUTION_MISMATCH_REASON = "RISK_LIMIT_RESOLUTION_MISMATCH"
UNKNOWN_NOTIONAL_REASON = "MAX_NOTIONAL_INPUT_UNAVAILABLE"
UNKNOWN_VALUATION_REASON = "VALUATION_FRESHNESS_UNKNOWN"
UNKNOWN_EVIDENCE_REASON = "RISK_APPROVAL_EVIDENCE_INSUFFICIENT"
UNKNOWN_PREREQUISITE_REASON = "RISK_APPROVAL_PREREQUISITES_UNSATISFIED"
UNKNOWN_INPUT_REASON = "RISK_INPUT_INVALID"
UNKNOWN_UNIT_MISMATCH_REASON = "MAX_NOTIONAL_LIMIT_UNIT_MISMATCH"


class RiskEngine:
    """Evaluate a TradeProposal without provider access or mutable side effects."""

    def evaluate(
        self,
        *,
        proposal: TradeProposal,
        context: RiskEvaluationContext,
        policy: RiskEvaluationPolicy,
        risk_limit_resolver: RiskLimitApplicabilityResolver,
    ) -> RiskDecision:
        if not isinstance(proposal, TradeProposal):
            raise TypeError("proposal must be TradeProposal")

        if not isinstance(context, RiskEvaluationContext):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.REJECTED,
                reason=UNKNOWN_INPUT_REASON,
                risk_evidence=(),
            )

        if not isinstance(policy, RiskEvaluationPolicy):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.REJECTED,
                reason=UNKNOWN_INPUT_REASON,
                risk_evidence=(),
            )

        if not isinstance(
            risk_limit_resolver,
            RiskLimitApplicabilityResolver,
        ):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.REJECTED,
                reason=UNKNOWN_INPUT_REASON,
                risk_evidence=(),
            )

        try:
            proposal.__post_init__()
        except (TypeError, ValueError):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.REJECTED,
                reason=REJECTED_PROPOSAL_REASON,
                risk_evidence=(),
            )

        if proposal != context.trade_proposal:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.REJECTED,
                reason=UNKNOWN_INPUT_REASON,
                risk_evidence=(),
            )

        if not context.is_complete:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_CONTEXT_REASON,
                risk_evidence=self._available_evidence(
                    proposal=proposal,
                    context=context,
                ),
            )

        if not policy.availability_requirements_satisfied(
            bindings=context.availability_bindings,
            evaluation_timestamp=context.evaluation_timestamp,
        ):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_AVAILABILITY_REASON,
                risk_evidence=self._available_evidence(
                    proposal=proposal,
                    context=context,
                ),
            )

        try:
            resolved = risk_limit_resolver.resolve(
                context.risk_limit_set,
                scope=MAX_NOTIONAL_SCOPE,
                metric=MAX_NOTIONAL_METRIC,
                evaluation_timestamp=context.evaluation_timestamp,
            )
        except (TypeError, ValueError):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_RESOLUTION_REASON,
                risk_evidence=self._available_evidence(
                    proposal=proposal,
                    context=context,
                ),
            )

        if context.risk_limit_resolution != resolved:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_RESOLUTION_MISMATCH_REASON,
                risk_evidence=self._available_evidence(
                    proposal=proposal,
                    context=context,
                ),
            )

        if resolved.status is not RiskLimitResolutionStatus.AVAILABLE:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_RESOLUTION_REASON,
                risk_evidence=self._available_evidence(
                    proposal=proposal,
                    context=context,
                ),
            )

        applicable_limit = self._applicable_limit(
            context=context,
            resolved_limit_ids=resolved.applicable_limit_ids,
        )
        if applicable_limit is None:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_RESOLUTION_REASON,
                risk_evidence=self._available_evidence(
                    proposal=proposal,
                    context=context,
                ),
            )

        risk_evidence = self._build_risk_evidence(
            proposal=proposal,
            context=context,
            applicable_limit=applicable_limit,
        )

        if applicable_limit.unit != context.instrument.quote_asset:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_UNIT_MISMATCH_REASON,
                risk_evidence=risk_evidence,
            )

        valuation_freshness = policy.valuation_freshness_policy.evaluate(
            valuation_as_of=(
                None
                if context.canonical_exposure is None
                else context.canonical_exposure.valuation_as_of
            ),
            evaluation_timestamp=context.evaluation_timestamp,
        )

        if (
            policy.max_valuation_age is not None
            and valuation_freshness is ValuationFreshnessStatus.STALE
        ):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.REJECTED,
                reason=REJECTED_VALUATION_REASON,
                risk_evidence=risk_evidence,
            )

        if (
            policy.max_valuation_age is not None
            and valuation_freshness is ValuationFreshnessStatus.UNKNOWN
        ):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_VALUATION_REASON,
                risk_evidence=risk_evidence,
            )

        if not policy.approval_evidence_policy.has_minimum_evidence(
            risk_evidence
        ):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_EVIDENCE_REASON,
                risk_evidence=risk_evidence,
            )

        if not policy.approval_prerequisites_satisfied(
            context_complete=context.is_complete,
            risk_limit_status=resolved.status,
            risk_evidence=risk_evidence,
            valuation_freshness=valuation_freshness,
        ):
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_PREREQUISITE_REASON,
                risk_evidence=risk_evidence,
            )

        notional = self._proposal_notional(proposal)
        if notional is None:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.UNKNOWN,
                reason=UNKNOWN_NOTIONAL_REASON,
                risk_evidence=risk_evidence,
            )

        if notional > applicable_limit.threshold:
            return self._decision(
                proposal=proposal,
                outcome=RiskDecisionOutcome.REJECTED,
                reason=REJECTED_LIMIT_REASON,
                risk_evidence=risk_evidence,
            )

        return self._decision(
            proposal=proposal,
            outcome=RiskDecisionOutcome.APPROVED,
            reason=APPROVED_REASON,
            risk_evidence=risk_evidence,
        )

    @staticmethod
    def _proposal_notional(proposal: TradeProposal) -> Decimal | None:
        if proposal.order_type is TradeOrderType.LIMIT:
            if proposal.requested_price is None:
                return None
            return proposal.requested_quantity * proposal.requested_price

        if (
            proposal.order_type is TradeOrderType.MARKET
            and proposal.side is TradeSide.BUY
        ):
            return proposal.max_quote_spend

        return None

    @staticmethod
    def _applicable_limit(
        *,
        context: RiskEvaluationContext,
        resolved_limit_ids: tuple[str, ...],
    ) -> RiskLimit | None:
        if len(resolved_limit_ids) != 1:
            return None

        limit_id = resolved_limit_ids[0]
        matches = tuple(
            item
            for item in context.risk_limit_set.limits
            if item.risk_limit_id == limit_id
        )
        if len(matches) != 1:
            return None
        return matches[0]

    @classmethod
    def _available_evidence(
        cls,
        *,
        proposal: TradeProposal,
        context: RiskEvaluationContext,
    ) -> tuple[RiskEvidenceRef, ...]:
        evidence = [
            RiskEvidenceRef(
                kind="TRADE_PROPOSAL",
                reference_id=proposal.proposal_id,
                as_of=proposal.decision_timestamp,
            ),
            RiskEvidenceRef(
                kind="RISK_LIMIT_SET",
                reference_id=context.risk_limit_set.risk_limit_set_id,
                as_of=context.risk_limit_set.as_of,
            ),
        ]
        return tuple(sorted(evidence, key=lambda item: (item.kind, item.reference_id)))

    @classmethod
    def _build_risk_evidence(
        cls,
        *,
        proposal: TradeProposal,
        context: RiskEvaluationContext,
        applicable_limit: RiskLimit,
    ) -> tuple[RiskEvidenceRef, ...]:
        evidence = list(
            cls._available_evidence(
                proposal=proposal,
                context=context,
            )
        )
        if context.canonical_exposure is not None:
            evidence.append(
                RiskEvidenceRef(
                    kind="CANONICAL_EXPOSURE",
                    reference_id=context.canonical_exposure.exposure_id,
                    as_of=(
                        context.canonical_exposure.valuation_as_of
                        or context.canonical_exposure.as_of
                    ),
                )
            )
        evidence.append(
            RiskEvidenceRef(
                kind="RISK_LIMIT",
                reference_id=applicable_limit.risk_limit_id,
                as_of=context.risk_limit_set.as_of,
            )
        )
        return tuple(
            sorted(evidence, key=lambda item: (item.kind, item.reference_id))
        )

    @staticmethod
    def _decision(
        *,
        proposal: TradeProposal,
        outcome: RiskDecisionOutcome,
        reason: str,
        risk_evidence: tuple[RiskEvidenceRef, ...],
    ) -> RiskDecision:
        return RiskDecision.from_trade_proposal(
            proposal=proposal,
            risk_decision_id=uuid4().hex,
            outcome=outcome,
            reason=reason,
            decision_timestamp=proposal.decision_timestamp,
            risk_evidence=risk_evidence,
        )


__all__ = [
    "APPROVED_REASON",
    "MAX_NOTIONAL_METRIC",
    "MAX_NOTIONAL_SCOPE",
    "REJECTED_LIMIT_REASON",
    "UNKNOWN_UNIT_MISMATCH_REASON",
    "REJECTED_PROPOSAL_REASON",
    "REJECTED_VALUATION_REASON",
    "RiskEngine",
]
