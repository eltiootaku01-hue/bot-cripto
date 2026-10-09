"""Provider-neutral Financial Admission Boundary contract for HUESO 02-H2.

This module closes the local boundary between an APPROVED RiskDecision and the
atomic Reservation admission primitive. It validates immutable request
identity, approved financial capacity, account completeness, resource binding,
and admission evidence before delegating exclusively to SQLiteReservationStore.admit().

No Risk calculation, sizing, provider access, Execution integration, temporal
freshness inference, or final-admission logic belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json

from .reservation import (
    Reservation,
    ReservationAdmissionBusy,
    ReservationAuthorizationBindingInput,
    ReservationAdmissionRejected,
    ReservationConflict,
    ReservationResourceKind,
    SQLiteReservationStore,
)
from .risk_authorization import (
    RiskAuthorization,
    RiskAuthorizationStatus,
    risk_authorization_semantic_fingerprint,
)
from .risk_evaluation_context import RiskEvaluationContext
from .risk_evaluation_policy import RiskEvaluationPolicy
from .risk_contracts import (
    CanonicalAccountState,
    Completeness,
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidenceRef,
)
from .trade_proposal import TradeOrderType, TradeProposal, TradeSide


class FinancialAdmissionContractError(ValueError):
    """Invalid or internally inconsistent financial admission request."""


class FinancialAdmissionStatus(str, Enum):
    ADMITTED = "ADMITTED"
    ALREADY_ADMITTED = "ALREADY_ADMITTED"
    REJECTED = "REJECTED"
    BUSY = "BUSY"


@dataclass(frozen=True)
class FinancialAdmissionRequest:
    """Immutable request proving the complete risk-authorization binding."""

    proposal: TradeProposal
    risk_decision: RiskDecision
    context: RiskEvaluationContext
    policy: RiskEvaluationPolicy
    authorization: RiskAuthorization
    account_id: str
    resource_kind: ReservationResourceKind
    asset: str
    approved_reserved_amount: Decimal
    canonical_account_state: CanonicalAccountState
    created_at: datetime
    admission_idempotency_key: str
    evidence: tuple[RiskEvidenceRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, TradeProposal):
            raise FinancialAdmissionContractError("proposal must be TradeProposal")
        if not isinstance(self.risk_decision, RiskDecision):
            raise FinancialAdmissionContractError("risk_decision must be RiskDecision")
        if self.risk_decision.outcome is not RiskDecisionOutcome.APPROVED:
            raise FinancialAdmissionContractError(
                "financial admission requires RiskDecisionOutcome.APPROVED"
            )
        if not isinstance(self.context, RiskEvaluationContext):
            raise FinancialAdmissionContractError("context must be RiskEvaluationContext")
        if not isinstance(self.policy, RiskEvaluationPolicy):
            raise FinancialAdmissionContractError("policy must be RiskEvaluationPolicy")
        if not isinstance(self.authorization, RiskAuthorization):
            raise FinancialAdmissionContractError("authorization must be RiskAuthorization")
        if self.authorization.status is not RiskAuthorizationStatus.AUTHORIZED:
            raise FinancialAdmissionContractError(
                "financial admission requires AUTHORIZED RiskAuthorization"
            )

        decision = self.risk_decision
        authorization = self.authorization
        context = self.context
        policy = self.policy
        proposal = self.proposal

        if context.trade_proposal != proposal:
            raise FinancialAdmissionContractError(
                "context.trade_proposal must structurally match proposal"
            )
        if not context.is_complete:
            raise FinancialAdmissionContractError("risk evaluation context must be COMPLETE")
        if context.instrument.symbol != proposal.symbol:
            raise FinancialAdmissionContractError(
                "proposal.symbol must match context.instrument.symbol"
            )
        if context.canonical_account_state != self.canonical_account_state:
            raise FinancialAdmissionContractError(
                "canonical_account_state must match context.canonical_account_state"
            )

        if proposal.proposal_id != decision.proposal_id:
            raise FinancialAdmissionContractError("proposal_id mismatch")
        if proposal.signal_id != decision.signal_id:
            raise FinancialAdmissionContractError("signal_id mismatch")
        if proposal.correlation_id != decision.correlation_id:
            raise FinancialAdmissionContractError("correlation_id mismatch")
        if authorization.risk_decision_id != decision.risk_decision_id:
            raise FinancialAdmissionContractError("authorization risk_decision_id mismatch")
        if (
            authorization.proposal_id != proposal.proposal_id
            or authorization.signal_id != proposal.signal_id
            or authorization.correlation_id != proposal.correlation_id
        ):
            raise FinancialAdmissionContractError(
                "authorization proposal/signal/correlation binding mismatch"
            )

        context_id = context.evaluation_context_id
        if not context_id or decision.evaluation_context_id != context_id:
            raise FinancialAdmissionContractError(
                "evaluation_context_id mismatch or missing decision binding"
            )
        if authorization.evaluation_context_id != context_id:
            raise FinancialAdmissionContractError("authorization evaluation_context_id mismatch")
        if (
            not decision.policy_id
            or not decision.policy_version
            or decision.policy_id != policy.policy_id
            or decision.policy_version != policy.policy_version
        ):
            raise FinancialAdmissionContractError(
                "policy_id/policy_version mismatch or missing decision binding"
            )
        if (
            authorization.policy_id != policy.policy_id
            or authorization.policy_version != policy.policy_version
        ):
            raise FinancialAdmissionContractError("authorization policy binding mismatch")

        decision_timestamp = decision.decision_timestamp
        if not (
            proposal.decision_timestamp
            == context.decision_timestamp
            == decision_timestamp
            == authorization.decision_timestamp
        ):
            raise FinancialAdmissionContractError("decision_timestamp mismatch")

        evidence = tuple(self.evidence)
        if not evidence:
            raise FinancialAdmissionContractError(
                "financial admission requires explicit risk evidence"
            )
        if not all(isinstance(item, RiskEvidenceRef) for item in evidence):
            raise FinancialAdmissionContractError(
                "evidence must contain only RiskEvidenceRef values"
            )
        if evidence != decision.risk_evidence:
            raise FinancialAdmissionContractError(
                "evidence must match risk_decision.risk_evidence exactly"
            )
        if evidence != authorization.risk_evidence:
            raise FinancialAdmissionContractError(
                "authorization evidence must match risk_decision evidence exactly"
            )
        object.__setattr__(self, "evidence", evidence)

        if type(self.account_id) is not str or not self.account_id.strip():
            raise FinancialAdmissionContractError("account_id must be a non-empty str")
        if not isinstance(self.resource_kind, ReservationResourceKind):
            raise FinancialAdmissionContractError(
                "resource_kind must be ReservationResourceKind"
            )
        if type(self.asset) is not str or not self.asset.strip():
            raise FinancialAdmissionContractError("asset must be a non-empty str")
        if type(self.approved_reserved_amount) is not Decimal:
            raise FinancialAdmissionContractError(
                "approved_reserved_amount must be Decimal"
            )
        if (
            not self.approved_reserved_amount.is_finite()
            or self.approved_reserved_amount <= 0
        ):
            raise FinancialAdmissionContractError(
                "approved_reserved_amount must be finite and greater than zero"
            )
        if not isinstance(self.canonical_account_state, CanonicalAccountState):
            raise FinancialAdmissionContractError(
                "canonical_account_state must be CanonicalAccountState"
            )
        if self.canonical_account_state.account_id != self.account_id:
            raise FinancialAdmissionContractError(
                "canonical_account_state.account_id must match account_id"
            )
        if self.canonical_account_state.completeness is not Completeness.COMPLETE:
            raise FinancialAdmissionContractError(
                "canonical_account_state.completeness must be COMPLETE"
            )
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise FinancialAdmissionContractError("created_at must be timezone-aware")
        if type(self.admission_idempotency_key) is not str or not self.admission_idempotency_key:
            raise FinancialAdmissionContractError(
                "admission_idempotency_key must be a non-empty str"
            )

        expected_resource, expected_asset, expected_amount = _required_reservation_terms(
            proposal, context
        )
        if self.resource_kind is not expected_resource:
            raise FinancialAdmissionContractError(
                "resource_kind does not match proposal economic terms"
            )
        if self.asset != expected_asset:
            raise FinancialAdmissionContractError(
                "asset does not match canonical instrument economic terms"
            )
        if self.approved_reserved_amount != expected_amount:
            raise FinancialAdmissionContractError(
                "approved_reserved_amount does not match proposal economic terms"
            )

        fingerprint = risk_authorization_semantic_fingerprint(authorization)
        expected_key = build_admission_idempotency_key(
            risk_decision_id=decision.risk_decision_id,
            proposal_id=proposal.proposal_id,
            account_id=self.account_id,
            resource_kind=self.resource_kind,
            asset=self.asset,
            approved_reserved_amount=self.approved_reserved_amount,
            correlation_id=proposal.correlation_id,
            authorization_fingerprint=fingerprint,
        )
        if self.admission_idempotency_key != expected_key:
            raise FinancialAdmissionContractError(
                "admission_idempotency_key does not bind v2 financial and authorization context"
            )

    @property
    def authorization_fingerprint(self) -> str:
        return risk_authorization_semantic_fingerprint(self.authorization)


def _required_reservation_terms(
    proposal: TradeProposal,
    context: RiskEvaluationContext,
) -> tuple[ReservationResourceKind, str, Decimal]:
    """Derive the sole admissible reserve asset/amount from proposal + canonical instrument."""
    instrument = context.instrument
    if proposal.symbol != instrument.symbol:
        raise FinancialAdmissionContractError(
            "proposal symbol and canonical instrument are incompatible"
        )

    if proposal.side is TradeSide.BUY and proposal.order_type is TradeOrderType.LIMIT:
        if proposal.requested_price is None:
            raise FinancialAdmissionContractError("BUY LIMIT requires requested_price")
        return (
            ReservationResourceKind.QUOTE,
            instrument.quote_asset,
            proposal.requested_quantity * proposal.requested_price,
        )
    if proposal.side is TradeSide.BUY and proposal.order_type is TradeOrderType.MARKET:
        if proposal.max_quote_spend is None:
            raise FinancialAdmissionContractError("BUY MARKET requires max_quote_spend")
        return (
            ReservationResourceKind.QUOTE,
            instrument.quote_asset,
            proposal.max_quote_spend,
        )
    if proposal.side is TradeSide.SELL and proposal.order_type in {
        TradeOrderType.LIMIT,
        TradeOrderType.MARKET,
    }:
        return (
            ReservationResourceKind.BASE,
            instrument.base_asset,
            proposal.requested_quantity,
        )
    raise FinancialAdmissionContractError("unsupported proposal economic terms")


@dataclass(frozen=True)
class FinancialAdmissionResult:
    """Typed fail-closed outcome of the Financial Admission Boundary."""

    status: FinancialAdmissionStatus
    reservation: Reservation | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, FinancialAdmissionStatus):
            raise FinancialAdmissionContractError(
                "status must be FinancialAdmissionStatus"
            )
        if type(self.reason) is not str or not self.reason.strip():
            raise FinancialAdmissionContractError("reason must be a non-empty str")

        admitted = self.status in {
            FinancialAdmissionStatus.ADMITTED,
            FinancialAdmissionStatus.ALREADY_ADMITTED,
        }
        if admitted and not isinstance(self.reservation, Reservation):
            raise FinancialAdmissionContractError(
                "admitted result requires a Reservation"
            )
        if not admitted and self.reservation is not None:
            raise FinancialAdmissionContractError(
                "rejected/busy result must not expose a Reservation"
            )


def build_admission_idempotency_key(
    *,
    risk_decision_id: str,
    proposal_id: str,
    account_id: str,
    resource_kind: ReservationResourceKind,
    asset: str,
    approved_reserved_amount: Decimal,
    correlation_id: str,
    authorization_fingerprint: str,
) -> str:
    """Build a v2 key bound to financial terms and semantic authorization identity."""
    if not isinstance(resource_kind, ReservationResourceKind):
        raise FinancialAdmissionContractError(
            "resource_kind must be ReservationResourceKind"
        )
    if type(approved_reserved_amount) is not Decimal:
        raise FinancialAdmissionContractError(
            "approved_reserved_amount must be Decimal"
        )
    if not approved_reserved_amount.is_finite():
        raise FinancialAdmissionContractError(
            "approved_reserved_amount must be finite"
        )
    if (
        type(authorization_fingerprint) is not str
        or not authorization_fingerprint.startswith("risk-authorization-semantic-v1:")
    ):
        raise FinancialAdmissionContractError(
            "authorization_fingerprint must be a semantic RiskAuthorization fingerprint"
        )

    fields = {
        "account_id": account_id,
        "approved_reserved_amount": str(approved_reserved_amount),
        "asset": asset,
        "authorization_fingerprint": authorization_fingerprint,
        "correlation_id": correlation_id,
        "proposal_id": proposal_id,
        "resource_kind": resource_kind.value,
        "risk_decision_id": risk_decision_id,
        "version": 2,
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"financial-admission-v2:{digest}"


def _reservation_matches_request(
    reservation: Reservation,
    request: FinancialAdmissionRequest,
) -> bool:
    return (
        reservation.proposal_id == request.proposal.proposal_id
        and reservation.risk_decision_id == request.risk_decision.risk_decision_id
        and reservation.correlation_id == request.proposal.correlation_id
        and reservation.account_id == request.account_id
        and reservation.resource_kind is request.resource_kind
        and reservation.asset == request.asset
        and reservation.reserved_amount == request.approved_reserved_amount
    )


def _persisted_binding_matches_request(
    binding,
    request: FinancialAdmissionRequest,
) -> bool:
    if binding is None:
        return False
    authorization = request.authorization
    decision = request.risk_decision
    return (
        binding.semantic_fingerprint == request.authorization_fingerprint
        and binding.risk_decision_id == decision.risk_decision_id
        and binding.proposal_id == request.proposal.proposal_id
        and binding.signal_id == request.proposal.signal_id
        and binding.correlation_id == request.proposal.correlation_id
        and binding.evaluation_context_id == request.context.evaluation_context_id
        and binding.policy_id == request.policy.policy_id
        and binding.policy_version == request.policy.policy_version
        and binding.decision_timestamp == authorization.decision_timestamp
        and binding.risk_evidence == request.evidence
    )


class FinancialAdmissionBoundary:
    """Final local admission gate after explicit authorization; never re-evaluates risk."""

    def __init__(self, store: SQLiteReservationStore):
        self._store = store

    def admit(self, request: FinancialAdmissionRequest) -> FinancialAdmissionResult:
        if not isinstance(request, FinancialAdmissionRequest):
            raise TypeError("request must be FinancialAdmissionRequest")

        try:
            existing = self._matching_prior_reservation(request)
        except Exception as exc:
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.REJECTED,
                reservation=None,
                reason=f"ADMISSION_LOOKUP_FAIL_CLOSED:{type(exc).__name__}",
            )

        if existing is not None:
            return self._existing_result(existing, request)

        try:
            authorization = request.authorization
            binding = ReservationAuthorizationBindingInput(
                authorization_id=authorization.authorization_id,
                semantic_fingerprint=request.authorization_fingerprint,
                risk_decision_id=authorization.risk_decision_id,
                proposal_id=authorization.proposal_id,
                signal_id=authorization.signal_id,
                correlation_id=authorization.correlation_id,
                evaluation_context_id=authorization.evaluation_context_id,
                policy_id=authorization.policy_id,
                policy_version=authorization.policy_version,
                decision_timestamp=authorization.decision_timestamp,
                risk_evidence=request.evidence,
            )
            reservation = self._store.admit(
                proposal=request.proposal,
                risk_decision=request.risk_decision,
                account_id=request.account_id,
                resource_kind=request.resource_kind,
                asset=request.asset,
                reserved_amount=request.approved_reserved_amount,
                canonical_account_state=request.canonical_account_state,
                created_at=request.created_at,
                authorization_binding=binding,
            )
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.ADMITTED,
                reservation=reservation,
                reason="ADMITTED",
            )
        except ReservationConflict:
            try:
                concurrent = self._matching_prior_reservation(request)
            except Exception as exc:
                return FinancialAdmissionResult(
                    status=FinancialAdmissionStatus.REJECTED,
                    reservation=None,
                    reason=f"ADMISSION_LOOKUP_FAIL_CLOSED:{type(exc).__name__}",
                )
            if concurrent is not None:
                return self._existing_result(concurrent, request)
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.REJECTED,
                reservation=None,
                reason="RESERVATION_CONFLICT",
            )
        except ReservationAdmissionBusy:
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.BUSY,
                reservation=None,
                reason="SQLITE_ADMISSION_BUSY",
            )
        except ReservationAdmissionRejected as exc:
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.REJECTED,
                reservation=None,
                reason=str(exc),
            )
        except Exception as exc:
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.REJECTED,
                reservation=None,
                reason=f"ADMISSION_FAIL_CLOSED:{type(exc).__name__}",
            )

    def _existing_result(
        self,
        reservation: Reservation,
        request: FinancialAdmissionRequest,
    ) -> FinancialAdmissionResult:
        try:
            getter = getattr(self._store, "get_authorization_binding", None)
            binding = (
                None
                if not callable(getter)
                else getter(reservation.reservation_id)
            )
        except Exception:
            binding = None
        if binding is None:
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.REJECTED,
                reservation=None,
                reason="EXISTING_RESERVATION_AUTHORIZATION_UNKNOWN",
            )
        if (
            _reservation_matches_request(reservation, request)
            and _persisted_binding_matches_request(binding, request)
        ):
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.ALREADY_ADMITTED,
                reservation=reservation,
                reason="ADMISSION_ALREADY_COMPLETED",
            )
        return FinancialAdmissionResult(
            status=FinancialAdmissionStatus.REJECTED,
            reservation=None,
            reason="IDEMPOTENCY_CONTEXT_CONFLICT",
        )

    def _matching_prior_reservation(
        self,
        request: FinancialAdmissionRequest,
    ) -> Reservation | None:
        candidates = self._store.list_for_proposal(request.proposal.proposal_id)
        same_decision = tuple(
            item
            for item in candidates
            if item.risk_decision_id == request.risk_decision.risk_decision_id
        )
        if not same_decision:
            return None
        if len(same_decision) > 1:
            raise FinancialAdmissionContractError(
                "idempotency context is non-unique"
            )
        return same_decision[0]


__all__ = [
    "FinancialAdmissionBoundary",
    "FinancialAdmissionContractError",
    "FinancialAdmissionRequest",
    "FinancialAdmissionResult",
    "FinancialAdmissionStatus",
    "build_admission_idempotency_key",
]
