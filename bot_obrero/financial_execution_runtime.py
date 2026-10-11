"""HUESO 05-F-R1: reservation-anchored local financial execution runtime.

This composition layer delegates every economic, authorization, persistence and
external-effect decision to the existing contracts. It does not reconcile with
an exchange and never sends through an adapter directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from .execution import (
    EvidenceBundle,
    ExecutionDecision,
    ExecutionOrchestrator,
)
from .financial_admission import (
    FinancialAdmissionBoundary,
    FinancialAdmissionStatus,
    build_financial_admission_request,
)
from .persistent_ledger import SQLiteIdempotencyLedger
from .reservation import (
    Reservation,
    ReservationState,
    SQLiteReservationStore,
)
from .reservation_execution_bridge import (
    ExecutionBridgeStatus,
    PreparedExecutionIntent,
    ReservationExecutionBridge,
)
from .risk_authorization import (
    RiskAuthorizationResult,
    RiskAuthorizationStatus,
    authorize_risk_decision,
)
from .risk_contracts import (
    CanonicalAccountState,
    RiskDecision,
    RiskDecisionOutcome,
)
from .risk_engine import RiskEngine
from .risk_evaluation_context import RiskEvaluationContext
from .risk_evaluation_policy import RiskEvaluationPolicy
from .risk_limit_applicability import RiskLimitApplicabilityResolver
from .trade_proposal import TradeProposal


class FinancialExecutionRuntimeStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    RESUME_REQUIRED = "RESUME_REQUIRED"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_UNKNOWN = "RISK_UNKNOWN"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    ADMISSION_BUSY = "ADMISSION_BUSY"
    READINESS_BLOCKED = "READINESS_BLOCKED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"
    ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
    RESERVATION_NOT_FOUND = "RESERVATION_NOT_FOUND"


@dataclass(frozen=True)
class FinancialExecutionRuntimeResult:
    status: FinancialExecutionRuntimeStatus
    reason: str
    proposal_id: str | None = None
    reservation_id: str | None = None
    execution_result: ExecutionDecision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, FinancialExecutionRuntimeStatus):
            raise TypeError("status must be FinancialExecutionRuntimeStatus")
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        for name in ("proposal_id", "reservation_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string when provided")
        if self.execution_result is not None and not isinstance(
            self.execution_result, ExecutionDecision
        ):
            raise TypeError("execution_result must be ExecutionDecision")


EvidenceProvider = Callable[[PreparedExecutionIntent], EvidenceBundle]


class FinancialExecutionRuntime:
    """Compose risk, authorization, admission and reservation-bound execution."""

    def __init__(
        self,
        *,
        store: SQLiteReservationStore,
        risk_engine: RiskEngine,
        risk_limit_resolver: RiskLimitApplicabilityResolver,
        admission_boundary: FinancialAdmissionBoundary,
        reservation_bridge: ReservationExecutionBridge,
        execution_orchestrator: ExecutionOrchestrator,
    ) -> None:
        if not isinstance(store, SQLiteReservationStore):
            raise TypeError("store must be SQLiteReservationStore")
        if not isinstance(risk_engine, RiskEngine):
            raise TypeError("risk_engine must be RiskEngine")
        if not isinstance(risk_limit_resolver, RiskLimitApplicabilityResolver):
            raise TypeError("risk_limit_resolver must be RiskLimitApplicabilityResolver")
        if not isinstance(admission_boundary, FinancialAdmissionBoundary):
            raise TypeError("admission_boundary must be FinancialAdmissionBoundary")
        if not isinstance(reservation_bridge, ReservationExecutionBridge):
            raise TypeError("reservation_bridge must be ReservationExecutionBridge")
        if not isinstance(execution_orchestrator, ExecutionOrchestrator):
            raise TypeError("execution_orchestrator must be ExecutionOrchestrator")

        # Shared persistence is part of the safety contract, not a convenience.
        if admission_boundary._store is not store or reservation_bridge.store is not store:
            raise ValueError("RUNTIME_COMPONENTS_MUST_SHARE_RESERVATION_STORE")
        if execution_orchestrator.reservation_bridge is not reservation_bridge:
            raise ValueError("ORCHESTRATOR_MUST_USE_RUNTIME_RESERVATION_BRIDGE")
        if not isinstance(execution_orchestrator.ledger, SQLiteIdempotencyLedger):
            raise TypeError("execution_orchestrator must use SQLiteIdempotencyLedger")

        self.store = store
        self.risk_engine = risk_engine
        self.risk_limit_resolver = risk_limit_resolver
        self.admission_boundary = admission_boundary
        self.reservation_bridge = reservation_bridge
        self.execution_orchestrator = execution_orchestrator

    @staticmethod
    def _result(
        status: FinancialExecutionRuntimeStatus,
        reason: str,
        *,
        proposal_id: str | None = None,
        reservation_id: str | None = None,
        execution_result: ExecutionDecision | None = None,
    ) -> FinancialExecutionRuntimeResult:
        return FinancialExecutionRuntimeResult(
            status=status,
            reason=reason or status.value,
            proposal_id=proposal_id,
            reservation_id=reservation_id,
            execution_result=execution_result,
        )

    def _find_proposal_reservations(
        self, proposal_id: str
    ) -> tuple[Reservation, ...] | None:
        try:
            result = self.store.list_for_proposal(proposal_id)
            if not isinstance(result, tuple) or not all(
                isinstance(item, Reservation) for item in result
            ):
                return None
            return result
        except Exception:
            return None

    def _existing_proposal_result(
        self, proposal_id: str
    ) -> FinancialExecutionRuntimeResult | None:
        existing = self._find_proposal_reservations(proposal_id)
        if existing is None:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                "PROPOSAL_RESERVATION_LOOKUP_FAILED",
                proposal_id=proposal_id,
            )
        if len(existing) == 0:
            return None
        if len(existing) != 1:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                "MULTIPLE_RESERVATIONS_FOR_PROPOSAL",
                proposal_id=proposal_id,
            )
        return self._result(
            FinancialExecutionRuntimeStatus.RESUME_REQUIRED,
            "EXISTING_RESERVATION_REQUIRES_EXPLICIT_RESUME",
            proposal_id=proposal_id,
            reservation_id=existing[0].reservation_id,
        )

    def start(
        self,
        *,
        proposal: TradeProposal,
        context: RiskEvaluationContext,
        policy: RiskEvaluationPolicy,
        canonical_account_state: CanonicalAccountState,
        account_id: str,
        created_at: datetime,
        evidence_provider: EvidenceProvider,
    ) -> FinancialExecutionRuntimeResult:
        """Start one new proposal; an existing reservation is never auto-resumed."""
        proposal_id = getattr(proposal, "proposal_id", None)
        if not isinstance(proposal, TradeProposal):
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_UNKNOWN,
                "TRADE_PROPOSAL_INVALID",
            )
        if (
            not isinstance(context, RiskEvaluationContext)
            or not isinstance(policy, RiskEvaluationPolicy)
            or not isinstance(canonical_account_state, CanonicalAccountState)
            or type(account_id) is not str
            or not account_id.strip()
            or not isinstance(created_at, datetime)
            or created_at.tzinfo is None
            or created_at.utcoffset() is None
            or not callable(evidence_provider)
        ):
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_UNKNOWN,
                "RUNTIME_INPUTS_INCOMPLETE_OR_INVALID",
                proposal_id=proposal_id,
            )
        if (
            context.trade_proposal != proposal
            or context.canonical_account_state != canonical_account_state
            or canonical_account_state.account_id != account_id
        ):
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_UNKNOWN,
                "RUNTIME_CANONICAL_CONTEXT_MISMATCH",
                proposal_id=proposal_id,
            )

        prior = self._existing_proposal_result(proposal.proposal_id)
        if prior is not None:
            return prior

        try:
            decision = self.risk_engine.evaluate(
                proposal=proposal,
                context=context,
                policy=policy,
                risk_limit_resolver=self.risk_limit_resolver,
            )
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_UNKNOWN,
                f"RISK_EVALUATION_FAILED:{type(exc).__name__}",
                proposal_id=proposal.proposal_id,
            )
        if not isinstance(decision, RiskDecision):
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_UNKNOWN,
                "RISK_DECISION_CONTRACT_INVALID",
                proposal_id=proposal.proposal_id,
            )
        if decision.outcome is RiskDecisionOutcome.REJECTED:
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_REJECTED,
                getattr(decision, "reason", "RISK_REJECTED"),
                proposal_id=proposal.proposal_id,
            )
        if decision.outcome is RiskDecisionOutcome.UNKNOWN:
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_UNKNOWN,
                getattr(decision, "reason", "RISK_UNKNOWN"),
                proposal_id=proposal.proposal_id,
            )
        if decision.outcome is not RiskDecisionOutcome.APPROVED:
            return self._result(
                FinancialExecutionRuntimeStatus.RISK_UNKNOWN,
                "RISK_DECISION_OUTCOME_INVALID",
                proposal_id=proposal.proposal_id,
            )

        try:
            authorization_result = authorize_risk_decision(
                proposal=proposal,
                risk_decision=decision,
                context=context,
                policy=policy,
            )
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                f"RISK_AUTHORIZATION_FAILED:{type(exc).__name__}",
                proposal_id=proposal.proposal_id,
            )
        if not isinstance(authorization_result, RiskAuthorizationResult):
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                "RISK_AUTHORIZATION_RESULT_CONTRACT_INVALID",
                proposal_id=proposal.proposal_id,
            )
        if (
            authorization_result.status is not RiskAuthorizationStatus.AUTHORIZED
            or authorization_result.authorization is None
        ):
            status = (
                FinancialExecutionRuntimeStatus.AUTHORIZATION_REJECTED
                if authorization_result.status is RiskAuthorizationStatus.REJECTED
                else FinancialExecutionRuntimeStatus.UNKNOWN
            )
            return self._result(
                status,
                authorization_result.reason,
                proposal_id=proposal.proposal_id,
            )

        try:
            request = build_financial_admission_request(
                proposal=proposal,
                risk_decision=decision,
                context=context,
                policy=policy,
                authorization=authorization_result.authorization,
                account_id=account_id,
                canonical_account_state=canonical_account_state,
                created_at=created_at,
            )
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.ADMISSION_REJECTED,
                f"ADMISSION_REQUEST_INVALID:{type(exc).__name__}",
                proposal_id=proposal.proposal_id,
            )

        try:
            admission = self.admission_boundary.admit(request)
        except Exception as exc:
            return self._admission_failure(
                proposal.proposal_id, None, f"ADMISSION_EXCEPTION:{type(exc).__name__}"
            )

        from .financial_admission import FinancialAdmissionResult
        if not isinstance(admission, FinancialAdmissionResult):
            return self._admission_failure(
                proposal.proposal_id, None, "ADMISSION_RESULT_CONTRACT_INVALID"
            )
        if admission.status is FinancialAdmissionStatus.ALREADY_ADMITTED:
            reservation = admission.reservation
            return self._result(
                FinancialExecutionRuntimeStatus.RESUME_REQUIRED,
                admission.reason,
                proposal_id=proposal.proposal_id,
                reservation_id=None if reservation is None else reservation.reservation_id,
            )
        if admission.status is not FinancialAdmissionStatus.ADMITTED:
            return self._admission_failure(
                proposal.proposal_id,
                admission.status,
                admission.reason,
            )
        reservation = admission.reservation
        if not isinstance(reservation, Reservation):
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                "ADMISSION_DID_NOT_CONFIRM_RESERVATION",
                proposal_id=proposal.proposal_id,
            )
        return self._prepare_and_execute(
            reservation_id=reservation.reservation_id,
            proposal_id=proposal.proposal_id,
            evidence_provider=evidence_provider,
        )

    def _admission_failure(
        self,
        proposal_id: str,
        admission_status: FinancialAdmissionStatus | None,
        reason: str,
    ) -> FinancialExecutionRuntimeResult:
        # Conflict/BUSY can race with another process. Persistence is authoritative.
        existing = self._existing_proposal_result(proposal_id)
        if existing is not None:
            if existing.status is FinancialExecutionRuntimeStatus.RESUME_REQUIRED:
                return existing
            if existing.status is FinancialExecutionRuntimeStatus.UNKNOWN:
                return existing
        if admission_status is FinancialAdmissionStatus.BUSY:
            status = FinancialExecutionRuntimeStatus.ADMISSION_BUSY
        else:
            status = FinancialExecutionRuntimeStatus.ADMISSION_REJECTED
        return self._result(status, reason or status.value, proposal_id=proposal_id)

    def _prepare_and_execute(
        self,
        *,
        reservation_id: str,
        proposal_id: str | None,
        evidence_provider: EvidenceProvider,
    ) -> FinancialExecutionRuntimeResult:
        try:
            preparation = self.reservation_bridge.prepare(reservation_id)
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                f"EXECUTION_PREPARATION_FAILED:{type(exc).__name__}",
                proposal_id=proposal_id,
                reservation_id=reservation_id,
            )
        if preparation.status in {
            ExecutionBridgeStatus.CONFLICT,
            ExecutionBridgeStatus.BLOCKED,
            ExecutionBridgeStatus.REJECTED,
        }:
            status = (
                FinancialExecutionRuntimeStatus.BLOCKED
                if preparation.status is ExecutionBridgeStatus.BLOCKED
                else FinancialExecutionRuntimeStatus.UNKNOWN
            )
            return self._result(
                status,
                preparation.reason,
                proposal_id=proposal_id,
                reservation_id=reservation_id,
            )
        if preparation.status is ExecutionBridgeStatus.UNKNOWN:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                preparation.reason,
                proposal_id=proposal_id,
                reservation_id=reservation_id,
            )
        if preparation.status not in {
            ExecutionBridgeStatus.PREPARED,
            ExecutionBridgeStatus.ALREADY_PREPARED,
        } or not isinstance(preparation.intent, PreparedExecutionIntent):
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                "PREPARED_EXECUTION_INTENT_UNAVAILABLE",
                proposal_id=proposal_id,
                reservation_id=reservation_id,
            )
        return self._execute_prepared(
            preparation.intent,
            proposal_id=proposal_id,
            evidence_provider=evidence_provider,
        )

    def _execute_prepared(
        self,
        prepared: PreparedExecutionIntent,
        *,
        proposal_id: str | None,
        evidence_provider: EvidenceProvider,
    ) -> FinancialExecutionRuntimeResult:
        try:
            binding = self.reservation_bridge.verify_prepared(prepared)
            evidence = evidence_provider(prepared)
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.READINESS_BLOCKED,
                f"EVIDENCE_PROVIDER_OR_BINDING_FAILED:{type(exc).__name__}",
                proposal_id=proposal_id,
                reservation_id=prepared.reservation_id,
            )
        if not isinstance(evidence, EvidenceBundle):
            return self._result(
                FinancialExecutionRuntimeStatus.READINESS_BLOCKED,
                "EVIDENCE_BUNDLE_MISSING_OR_INVALID",
                proposal_id=proposal_id,
                reservation_id=prepared.reservation_id,
            )
        now = self.execution_orchestrator.clock()
        try:
            if not self.execution_orchestrator.readiness.evaluate_evidence(
                evidence, prepared.client_order_id, now
            ).allowed:
                return self._result(
                    FinancialExecutionRuntimeStatus.READINESS_BLOCKED,
                    "READINESS_EVIDENCE_INVALID_OR_NOT_READY",
                    proposal_id=proposal_id,
                    reservation_id=prepared.reservation_id,
                )
            if evidence.correlation_id != binding.payload.get("correlation_id"):
                return self._result(
                    FinancialExecutionRuntimeStatus.READINESS_BLOCKED,
                    "EVIDENCE_CORRELATION_MISMATCH",
                    proposal_id=proposal_id,
                    reservation_id=prepared.reservation_id,
                )
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.READINESS_BLOCKED,
                f"READINESS_VALIDATION_FAILED:{type(exc).__name__}",
                proposal_id=proposal_id,
                reservation_id=prepared.reservation_id,
            )

        try:
            execution = self.execution_orchestrator.execute(prepared, evidence)
        except Exception as exc:
            # A thrown orchestrator exception may occur after its durable send marker.
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                f"EXECUTION_ORCHESTRATOR_EXCEPTION:{type(exc).__name__}",
                proposal_id=proposal_id,
                reservation_id=prepared.reservation_id,
            )
        if execution.allowed and execution.reason == "SUBMITTED":
            return self._result(
                FinancialExecutionRuntimeStatus.SUBMITTED,
                "SUBMITTED",
                proposal_id=proposal_id,
                reservation_id=prepared.reservation_id,
                execution_result=execution,
            )
        if execution.reason == "UNKNOWN":
            status = FinancialExecutionRuntimeStatus.UNKNOWN
        elif execution.reason == "BLOCKED":
            status = FinancialExecutionRuntimeStatus.BLOCKED
        else:
            status = FinancialExecutionRuntimeStatus.READINESS_BLOCKED
        return self._result(
            status,
            execution.reason or status.value,
            proposal_id=proposal_id,
            reservation_id=prepared.reservation_id,
            execution_result=execution,
        )

    def resume(
        self,
        reservation_id: str,
        *,
        evidence_provider: EvidenceProvider,
    ) -> FinancialExecutionRuntimeResult:
        """Resume only by persisted reservation ID; never re-runs risk/admission."""
        if type(reservation_id) is not str or not reservation_id.strip():
            return self._result(
                FinancialExecutionRuntimeStatus.RESERVATION_NOT_FOUND,
                "INVALID_RESERVATION_ID",
            )
        if not callable(evidence_provider):
            return self._result(
                FinancialExecutionRuntimeStatus.READINESS_BLOCKED,
                "EVIDENCE_PROVIDER_REQUIRED",
                reservation_id=reservation_id,
            )
        try:
            reservation = self.store.get(reservation_id)
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                f"RESERVATION_LOOKUP_FAILED:{type(exc).__name__}",
                reservation_id=reservation_id,
            )
        if reservation is None:
            return self._result(
                FinancialExecutionRuntimeStatus.RESERVATION_NOT_FOUND,
                "RESERVATION_NOT_FOUND",
                reservation_id=reservation_id,
            )
        try:
            snapshot = self.store.get_trade_terms_snapshot(reservation_id)
            authorization_binding = self.store.get_authorization_binding(reservation_id)
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.BLOCKED,
                f"PERSISTED_BINDING_LOOKUP_FAILED:{type(exc).__name__}",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        if snapshot is None or authorization_binding is None:
            return self._result(
                FinancialExecutionRuntimeStatus.BLOCKED,
                "CANONICAL_TERMS_OR_AUTHORIZATION_MISSING",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        try:
            persisted_execution_binding = self.reservation_bridge.get_binding(reservation_id)
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.BLOCKED,
                f"EXECUTION_BINDING_LOOKUP_FAILED:{type(exc).__name__}",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        if persisted_execution_binding is None:
            return self._result(
                FinancialExecutionRuntimeStatus.BLOCKED,
                "PERSISTED_EXECUTION_BINDING_MISSING",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        try:
            preparation = self.reservation_bridge.prepare(reservation_id)
        except Exception as exc:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                f"RESUME_PREPARATION_FAILED:{type(exc).__name__}",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )

        # prepare() validates the persisted snapshot, authorization binding and intent
        # before returning any of these persisted execution states.
        binding = self.reservation_bridge.get_binding(reservation_id)
        if binding is not None and binding.state == "SUBMITTED":
            projection = self.execution_orchestrator.recover_projection(reservation_id)
            if projection.reason == "BLOCKED":
                return self._result(
                    FinancialExecutionRuntimeStatus.BLOCKED,
                    "SUBMITTED_LEDGER_PROJECTION_REPAIR_FAILED",
                    proposal_id=reservation.proposal_id,
                    reservation_id=reservation_id,
                )
            if preparation.reason != "ORDER_ALREADY_SUBMITTED":
                return self._result(
                    FinancialExecutionRuntimeStatus.BLOCKED,
                    preparation.reason,
                    proposal_id=reservation.proposal_id,
                    reservation_id=reservation_id,
                )
            return self._result(
                FinancialExecutionRuntimeStatus.ALREADY_SUBMITTED,
                "ALREADY_SUBMITTED_LOCAL_RESULT_ONLY",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        if binding is not None and binding.state in {"SUBMISSION_STARTED", "UNKNOWN"}:
            try:
                self.execution_orchestrator.recover_projection(reservation_id)
            except Exception:
                pass
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                "EXECUTION_RESULT_AMBIGUOUS_NO_AUTOMATIC_RESEND",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        if binding is not None and binding.state == "BLOCKED":
            return self._result(
                FinancialExecutionRuntimeStatus.BLOCKED,
                "PERSISTED_EXECUTION_BINDING_BLOCKED",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        if preparation.status is ExecutionBridgeStatus.UNKNOWN:
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                preparation.reason,
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        if preparation.status in {
            ExecutionBridgeStatus.BLOCKED,
            ExecutionBridgeStatus.CONFLICT,
            ExecutionBridgeStatus.REJECTED,
        }:
            return self._result(
                FinancialExecutionRuntimeStatus.BLOCKED,
                preparation.reason,
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        if preparation.status not in {
            ExecutionBridgeStatus.PREPARED,
            ExecutionBridgeStatus.ALREADY_PREPARED,
        } or not isinstance(preparation.intent, PreparedExecutionIntent):
            return self._result(
                FinancialExecutionRuntimeStatus.UNKNOWN,
                "RESUME_INTENT_UNAVAILABLE",
                proposal_id=reservation.proposal_id,
                reservation_id=reservation_id,
            )
        return self._execute_prepared(
            preparation.intent,
            proposal_id=reservation.proposal_id,
            evidence_provider=evidence_provider,
        )


__all__ = [
    "EvidenceProvider",
    "FinancialExecutionRuntime",
    "FinancialExecutionRuntimeResult",
    "FinancialExecutionRuntimeStatus",
]
