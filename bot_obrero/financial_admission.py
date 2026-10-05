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
    ReservationAdmissionRejected,
    ReservationConflict,
    ReservationResourceKind,
    SQLiteReservationStore,
)
from .risk_contracts import (
    CanonicalAccountState,
    Completeness,
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidenceRef,
)
from .trade_proposal import TradeProposal


class FinancialAdmissionContractError(ValueError):
    """Invalid or internally inconsistent financial admission request."""


class FinancialAdmissionStatus(str, Enum):
    ADMITTED = "ADMITTED"
    ALREADY_ADMITTED = "ALREADY_ADMITTED"
    REJECTED = "REJECTED"
    BUSY = "BUSY"


@dataclass(frozen=True)
class FinancialAdmissionRequest:
    """Immutable, provider-neutral input to the Financial Admission Boundary."""

    proposal: TradeProposal
    risk_decision: RiskDecision
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
            raise FinancialAdmissionContractError(
                "risk_decision must be RiskDecision"
            )

        if self.proposal.proposal_id != self.risk_decision.proposal_id:
            raise FinancialAdmissionContractError(
                "proposal_id mismatch between proposal and risk_decision"
            )
        if self.proposal.signal_id != self.risk_decision.signal_id:
            raise FinancialAdmissionContractError(
                "signal_id mismatch between proposal and risk_decision"
            )
        if self.proposal.correlation_id != self.risk_decision.correlation_id:
            raise FinancialAdmissionContractError(
                "correlation_id mismatch between proposal and risk_decision"
            )

        if self.risk_decision.outcome is not RiskDecisionOutcome.APPROVED:
            raise FinancialAdmissionContractError(
                "financial admission requires RiskDecisionOutcome.APPROVED"
            )

        if type(self.account_id) is not str or not self.account_id.strip():
            raise FinancialAdmissionContractError(
                "account_id must be a non-empty str"
            )
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
        if not self.approved_reserved_amount.is_finite():
            raise FinancialAdmissionContractError(
                "approved_reserved_amount must be finite"
            )
        if self.approved_reserved_amount <= 0:
            raise FinancialAdmissionContractError(
                "approved_reserved_amount must be greater than zero"
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

        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise FinancialAdmissionContractError(
                "created_at must be timezone-aware"
            )

        if type(self.admission_idempotency_key) is not str or not self.admission_idempotency_key:
            raise FinancialAdmissionContractError(
                "admission_idempotency_key must be a non-empty str"
            )

        evidence = tuple(self.evidence)
        if not evidence:
            raise FinancialAdmissionContractError(
                "financial admission requires explicit risk evidence"
            )
        if not all(isinstance(item, RiskEvidenceRef) for item in evidence):
            raise FinancialAdmissionContractError(
                "evidence must contain only RiskEvidenceRef values"
            )
        if evidence != self.risk_decision.risk_evidence:
            raise FinancialAdmissionContractError(
                "evidence must match risk_decision.risk_evidence exactly"
            )
        object.__setattr__(self, "evidence", evidence)

        expected_key = build_admission_idempotency_key(
            risk_decision_id=self.risk_decision.risk_decision_id,
            proposal_id=self.proposal.proposal_id,
            account_id=self.account_id,
            resource_kind=self.resource_kind,
            asset=self.asset,
            approved_reserved_amount=self.approved_reserved_amount,
            correlation_id=self.proposal.correlation_id,
        )
        if self.admission_idempotency_key != expected_key:
            raise FinancialAdmissionContractError(
                "admission_idempotency_key does not bind the full admission context"
            )


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
) -> str:
    """Build the stable admission identity from the complete financial context."""

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

    fields = {
        "account_id": account_id,
        "approved_reserved_amount": str(approved_reserved_amount),
        "asset": asset,
        "correlation_id": correlation_id,
        "proposal_id": proposal_id,
        "resource_kind": resource_kind.value,
        "risk_decision_id": risk_decision_id,
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"financial-admission-v1:{digest}"


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


class FinancialAdmissionBoundary:
    """Authorize financial commitment only through SQLiteReservationStore.admit()."""

    def __init__(self, store: SQLiteReservationStore):
        self._store = store

    def admit(self, request: FinancialAdmissionRequest) -> FinancialAdmissionResult:
        if not isinstance(request, FinancialAdmissionRequest):
            raise TypeError("request must be FinancialAdmissionRequest")

        existing = self._matching_prior_reservation(request)

        if existing is not None:
            if _reservation_matches_request(existing, request):
                return FinancialAdmissionResult(
                    status=FinancialAdmissionStatus.ALREADY_ADMITTED,
                    reservation=existing,
                    reason="ADMISSION_ALREADY_COMPLETED",
                )
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.REJECTED,
                reservation=None,
                reason="IDEMPOTENCY_CONTEXT_CONFLICT",
            )

        try:
            reservation = self._store.admit(
                proposal=request.proposal,
                risk_decision=request.risk_decision,
                account_id=request.account_id,
                resource_kind=request.resource_kind,
                asset=request.asset,
                reserved_amount=request.approved_reserved_amount,
                canonical_account_state=request.canonical_account_state,
                created_at=request.created_at,
            )
            return FinancialAdmissionResult(
                status=FinancialAdmissionStatus.ADMITTED,
                reservation=reservation,
                reason="ADMITTED",
            )
        except ReservationConflict:
            concurrent = self._matching_prior_reservation(request)
            if concurrent is not None and _reservation_matches_request(
                concurrent, request
            ):
                return FinancialAdmissionResult(
                    status=FinancialAdmissionStatus.ALREADY_ADMITTED,
                    reservation=concurrent,
                    reason="ADMISSION_ALREADY_COMPLETED",
                )
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
