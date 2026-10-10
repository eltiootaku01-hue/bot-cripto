from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Mapping

from .temporal import EvidenceTimestamp


class ExecutionContext(str, Enum):
    NORMAL = "NORMAL"
    RISK_REDUCTION = "RISK_REDUCTION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class EvidenceRecord:
    """One auditable fact used to authorize an execution decision."""

    kind: str
    value: Any
    source: str
    observed_at: datetime
    decision_at: datetime
    intent_id: str
    correlation_id: str
    sequence: int | str | None = None
    expires_at: datetime | None = None
    timestamps: EvidenceTimestamp | None = None

    def __post_init__(self):
        for value in (self.observed_at, self.decision_at, self.expires_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("EVIDENCE_TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
        if not self.kind or not self.source or not self.intent_id or not self.correlation_id:
            raise ValueError("EVIDENCE_PROVENANCE_INCOMPLETE")
        if self.observed_at > self.decision_at:
            raise ValueError("EVIDENCE_OBSERVED_AFTER_DECISION")
        if self.expires_at is not None and self.expires_at <= self.decision_at:
            raise ValueError("EVIDENCE_EXPIRY_NOT_AFTER_DECISION")
        object.__setattr__(
            self,
            "timestamps",
            EvidenceTimestamp(self.observed_at, self.observed_at, self.decision_at),
        )

    def valid_for(self, intent_id: str, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("EVIDENCE_VALIDATION_TIMEZONE_REQUIRED")
        return (
            self.intent_id == intent_id
            and self.value is True
            and self.observed_at <= self.decision_at <= now
            and (self.expires_at is None or now < self.expires_at)
        )


@dataclass(frozen=True)
class EvidenceBundle:
    records: tuple[EvidenceRecord, ...]
    correlation_id: str

    def record(self, kind: str) -> EvidenceRecord | None:
        matches = [record for record in self.records if record.kind == kind]
        return matches[0] if len(matches) == 1 else None

    def valid_for(self, intent_id: str, now: datetime, required: tuple[str, ...]) -> bool:
        if not self.correlation_id:
            return False
        if any(
            record.intent_id != intent_id or record.correlation_id != self.correlation_id
            for record in self.records
        ):
            return False
        for kind in required:
            record = self.record(kind)
            if record is None or not record.valid_for(intent_id, now):
                return False
        return True


@dataclass(frozen=True)
class OrderIntent:
    client_order_id: str
    payload: Mapping[str, Any]
    correlation_id: str

    def __post_init__(self):
        if not self.client_order_id or not self.correlation_id or not self.payload:
            raise ValueError("INVALID_ORDER_INTENT")


@dataclass(frozen=True)
class ReadinessInputs:
    reconciliation_match: bool
    clock_valid: bool
    stream_ready: bool
    resources_healthy: bool
    permissions_safe: bool
    configuration_valid: bool
    protection_safe: bool = False


@dataclass(frozen=True)
class ReadinessEvidence:
    reconciliation_match: bool = True
    clock_valid: bool = True
    stream_ready: bool = True
    resources_healthy: bool = True
    permissions_safe: bool = True
    configuration_valid: bool = True
    protection_safe: bool = True


_BOUNDARY_PERMIT = object()


class _BoundaryAuthorizationError(PermissionError):
    """Fail-closed rejection before an adapter is invoked."""


class ExchangeAdapter(ABC):
    """External-effect interface whose public execution methods are boundary-controlled."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "submit" in cls.__dict__ or "cancel" in cls.__dict__:
            raise TypeError("EXCHANGE_ADAPTER_EXECUTION_METHODS_ARE_BOUNDARY_CONTROLLED")

    def submit(self, order, *, _permit=None):
        if _permit is not _BOUNDARY_PERMIT:
            raise PermissionError("EXECUTION_BOUNDARY_REQUIRED")
        return self._submit(order)

    def cancel(self, order, *, _permit=None):
        if _permit is not _BOUNDARY_PERMIT:
            raise PermissionError("EXECUTION_BOUNDARY_REQUIRED")
        return self._cancel(order)

    @abstractmethod
    def _submit(self, order):
        raise NotImplementedError

    @abstractmethod
    def _cancel(self, order):
        raise NotImplementedError

    @abstractmethod
    def snapshot(self):
        raise NotImplementedError


class ReadinessGate:
    REQUIRED = (
        "reconciliation_match",
        "clock_valid",
        "stream_ready",
        "resources_healthy",
        "permissions_safe",
        "configuration_valid",
        "protection_safe",
    )

    def evaluate(self, inputs):
        checks = tuple(getattr(inputs, name, False) is True for name in self.REQUIRED)
        return ExecutionDecision(all(checks), "READY" if all(checks) else "FAIL_CLOSED")

    def evaluate_evidence(self, evidence: EvidenceBundle, intent_id: str, now: datetime):
        if not evidence.valid_for(intent_id, now, self.REQUIRED):
            return ExecutionDecision(False, "FAIL_CLOSED")
        return ExecutionDecision(True, "READY")


class RiskGuard:
    def __init__(self, murphy_guard, readiness_gate=None):
        self.guard = murphy_guard
        self.readiness_gate = readiness_gate or ReadinessGate()

    def authorize_new_entry(self, readiness):
        decision = self.readiness_gate.evaluate(readiness)
        if not decision.allowed:
            self.guard.freeze()
            return decision
        try:
            self.guard.ready(readiness)
        except ValueError:
            self.guard.freeze()
            return ExecutionDecision(False, "FAIL_CLOSED")
        return ExecutionDecision(True, "READY")


class ExecutionBoundary:
    """Final admission gate for a new order immediately before adapter invocation."""

    def __init__(
        self,
        adapter: ExchangeAdapter,
        *,
        reservation_bridge=None,
    ):
        self._adapter = adapter
        self.__reservation_bridge = reservation_bridge
        # The public constructor never accepts or installs a caller-chosen authority.
        self.__submission_authority = None
        self.__consumed_submissions: set[tuple[str, str, str]] = set()
        self.__dispatch_lock = Lock()

    def submit(self, prepared, *, _submission_authority=None):
        # The orchestrator's private capability is necessary but not sufficient:
        # independently revalidate the authoritative persisted binding and state.
        if (
            self.__submission_authority is None
            or _submission_authority is not self.__submission_authority
        ):
            raise _BoundaryAuthorizationError(
                "PERSISTED_RESERVATION_SUBMISSION_AUTHORITY_REQUIRED"
            )
        if self.__reservation_bridge is None:
            raise _BoundaryAuthorizationError("PERSISTED_RESERVATION_BINDING_REQUIRED")

        from .reservation_execution_bridge import PreparedExecutionIntent

        if type(prepared) is not PreparedExecutionIntent:
            raise _BoundaryAuthorizationError("PREPARED_EXECUTION_INTENT_REQUIRED")
        try:
            binding = self.__reservation_bridge.verify_submission_started(prepared)
            payload = binding.payload
            correlation_id = payload.get("correlation_id")
            canonical_intent = OrderIntent(
                client_order_id=binding.client_order_id,
                payload=payload,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            raise _BoundaryAuthorizationError(
                "PERSISTED_RESERVATION_SUBMISSION_NOT_AUTHORIZED"
            ) from exc

        # A persisted marker is necessary but not sufficient: each binding gets
        # at most one physical adapter invocation from this boundary instance.
        dispatch_key = (
            binding.reservation_id,
            binding.client_order_id,
            binding.intent_hash,
        )
        with self.__dispatch_lock:
            if dispatch_key in self.__consumed_submissions:
                raise _BoundaryAuthorizationError("EXECUTION_DISPATCH_ALREADY_CONSUMED")
            # Consume before the effect so exceptions and ambiguous outcomes cannot
            # authorize a second call on this boundary.
            self.__consumed_submissions.add(dispatch_key)

        # Never send caller-supplied economics. The adapter sees only terms
        # reloaded and verified from the persistent reservation binding.
        return self._adapter.submit(canonical_intent, _permit=_BOUNDARY_PERMIT)

    def cancel(self, order):
        # Cancellation semantics intentionally remain unchanged by HUESO 05-E-R1.
        return self._adapter.cancel(order, _permit=_BOUNDARY_PERMIT)


class _OrchestratedExecutionBoundary(ExecutionBoundary):
    """Private boundary variant whose capability is installed only by the orchestrator."""

    def __init__(self, adapter, *, reservation_bridge, submission_authority):
        super().__init__(adapter, reservation_bridge=reservation_bridge)
        self._ExecutionBoundary__submission_authority = submission_authority


class ExecutionOrchestrator:
    """Pre-effect execution pipeline requiring a persisted Reservation bridge binding."""

    def __init__(self, *, adapter, ledger, murphy_guard, reservation_bridge=None, readiness_gate=None, clock=None):
        from .order_lifecycle import LifecycleOrder

        self.ledger = ledger
        self.murphy = murphy_guard
        self.reservation_bridge = reservation_bridge
        self.__submission_authority = object() if reservation_bridge is not None else None
        self.boundary = _OrchestratedExecutionBoundary(
            adapter,
            reservation_bridge=reservation_bridge,
            submission_authority=self.__submission_authority,
        )
        self.readiness = readiness_gate or ReadinessGate()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._LifecycleOrder = LifecycleOrder

    def execute(self, intent, evidence: EvidenceBundle) -> ExecutionDecision:
        from .reservation_execution_bridge import (
            ExecutionBridgeBlocked,
            ExecutionBridgeConflict,
            ExecutionBridgeRejected,
            PreparedExecutionIntent,
        )

        if not isinstance(evidence, EvidenceBundle):
            self.murphy.freeze()
            return ExecutionDecision(False, "FAIL_CLOSED")
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            self.murphy.freeze()
            return ExecutionDecision(False, "FAIL_CLOSED")

        canonical_payload = None
        if type(intent) is PreparedExecutionIntent:
            if self.reservation_bridge is None:
                self.murphy.freeze()
                return ExecutionDecision(False, "RESERVATION_EXECUTION_BRIDGE_REQUIRED")
            try:
                persisted = self.reservation_bridge.verify_prepared(intent)
            except ExecutionBridgeConflict:
                self.murphy.freeze()
                return ExecutionDecision(False, "RESERVATION_EXECUTION_BINDING_CONFLICT")
            except ExecutionBridgeBlocked:
                self.murphy.freeze()
                return ExecutionDecision(False, "UNKNOWN_OR_INTEGRITY_BLOCKED")
            except ExecutionBridgeRejected:
                self.murphy.freeze()
                return ExecutionDecision(False, "RESERVATION_EXECUTION_BINDING_REJECTED")
            canonical_payload = persisted.payload
            client_order_id = persisted.client_order_id
            correlation_id = canonical_payload.get("correlation_id")
        else:
            # Legacy/generic OrderIntent objects are never upgraded from caller payloads.
            client_order_id = getattr(intent, "client_order_id", "")
            correlation_id = getattr(intent, "correlation_id", "")

        if not isinstance(client_order_id, str) or not client_order_id:
            self.murphy.freeze()
            return ExecutionDecision(False, "RESERVATION_EXECUTION_BINDING_REQUIRED")
        if not isinstance(correlation_id, str) or not correlation_id:
            self.murphy.freeze()
            return ExecutionDecision(False, "RESERVATION_EXECUTION_BINDING_REQUIRED")
        if evidence.correlation_id != correlation_id:
            self.murphy.freeze()
            return ExecutionDecision(False, "FAIL_CLOSED")

        readiness = self.readiness.evaluate_evidence(evidence, client_order_id, now)
        if not readiness.allowed:
            self.murphy.freeze()
            return readiness
        if canonical_payload is None:
            self.murphy.freeze()
            return ExecutionDecision(False, "RESERVATION_EXECUTION_BINDING_REQUIRED")

        # The prior evidence bundle was checked record-by-record. Propagate those
        # exact booleans to Murphy instead of constructing favorable defaults.
        validated_readiness = ReadinessInputs(**{
            name: evidence.record(name).value is True
            for name in self.readiness.REQUIRED
        })
        risk_decision = RiskGuard(self.murphy, self.readiness).authorize_new_entry(validated_readiness)
        if not risk_decision.allowed:
            return risk_decision

        try:
            self.reservation_bridge.begin_submission(intent, occurred_at=now)
        except Exception:
            self.murphy.freeze()
            return ExecutionDecision(False, "BLOCKED")

        try:
            registered = self.ledger.register(client_order_id, canonical_payload)
        except Exception:
            try:
                self.reservation_bridge.finish_submission(
                    intent,
                    outcome="BLOCKED",
                    occurred_at=self.clock(),
                    error="LEDGER_REGISTER_FAILED_BEFORE_ADAPTER",
                )
            except Exception:
                pass
            self.murphy.freeze()
            return ExecutionDecision(False, "BLOCKED")
        if not registered:
            try:
                self.reservation_bridge.finish_submission(
                    intent,
                    outcome="BLOCKED",
                    occurred_at=self.clock(),
                    error="LEDGER_ALREADY_CONTAINS_CLIENT_ORDER_ID",
                )
            except Exception:
                pass
            self.murphy.freeze()
            return ExecutionDecision(False, "DUPLICATE")

        try:
            # The boundary re-reads the persisted binding and reconstructs the
            # outgoing OrderIntent itself; this capability is not part of the public API.
            self.boundary.submit(
                intent,
                _submission_authority=self.__submission_authority,
            )
        except _BoundaryAuthorizationError as exc:
            try:
                self.reservation_bridge.finish_submission(
                    intent,
                    outcome="BLOCKED",
                    occurred_at=self.clock(),
                    error=(type(exc).__name__ + ":" + str(exc)[:256]),
                )
            except Exception:
                pass
            try:
                self.ledger.mark_result(client_order_id, "BLOCKED")
            except Exception:
                pass
            self.murphy.freeze()
            return ExecutionDecision(False, "BLOCKED")
        except Exception as exc:
            try:
                self.reservation_bridge.finish_submission(
                    intent,
                    outcome="UNKNOWN",
                    occurred_at=self.clock(),
                    error=(type(exc).__name__ + ":" + str(exc)[:256]),
                )
            except Exception:
                # SUBMISSION_STARTED is durable evidence that a send may have happened;
                # recovery will conservatively convert it to UNKNOWN and never resend.
                pass
            try:
                self.ledger.mark_result(client_order_id, "ORDER_RESULT_UNKNOWN")
            except Exception:
                pass
            self.murphy.freeze()
            return ExecutionDecision(False, "UNKNOWN")

        try:
            self.reservation_bridge.finish_submission(
                intent, outcome="SUBMITTED", occurred_at=self.clock()
            )
        except Exception:
            try:
                self.ledger.mark_result(client_order_id, "ORDER_RESULT_UNKNOWN")
            except Exception:
                pass
            self.murphy.freeze()
            return ExecutionDecision(False, "UNKNOWN")

        try:
            self.ledger.mark_result(client_order_id, "SUBMITTED")
        except Exception:
            # The canonical binding already records SUBMITTED; the separate ledger can
            # be repaired on restart without reissuing an external call.
            self.murphy.freeze()
            return ExecutionDecision(False, "UNKNOWN")
        return ExecutionDecision(True, "SUBMITTED")

    def recover_projection(self, reservation_id: str) -> ExecutionDecision:
        """Repair the separate ledger from canonical binding state; never contacts exchange."""
        if self.reservation_bridge is None:
            self.murphy.freeze()
            return ExecutionDecision(False, "BLOCKED")
        try:
            binding = self.reservation_bridge.recover_ambiguous(
                reservation_id, occurred_at=self.clock()
            )
            if binding is None:
                return ExecutionDecision(False, "RESERVATION_EXECUTION_BINDING_NOT_FOUND")
            if binding.state == "PREPARED":
                # No attempt marker means no authorized adapter invocation was started.
                return ExecutionDecision(False, "NO_SUBMISSION_PROJECTION_REQUIRED")
            ledger_result = {
                "SUBMISSION_STARTED": "ORDER_RESULT_UNKNOWN",
                "UNKNOWN": "ORDER_RESULT_UNKNOWN",
                "SUBMITTED": "SUBMITTED",
                "BLOCKED": "BLOCKED",
            }.get(binding.state)
            if ledger_result is None:
                self.murphy.freeze()
                return ExecutionDecision(False, "BLOCKED")
            self.ledger.synchronize_projection(
                binding.client_order_id, binding.payload, ledger_result
            )
            if binding.state in {"UNKNOWN", "SUBMISSION_STARTED"}:
                self.murphy.freeze()
                return ExecutionDecision(False, "UNKNOWN")
            return ExecutionDecision(False, "PROJECTION_REPAIRED")
        except Exception:
            self.murphy.freeze()
            return ExecutionDecision(False, "BLOCKED")



    def apply_external_fill(self, order, fill_id: str, quantity, price) -> bool:
        """Apply a venue fill only once; identity is persisted in the same SQLite ledger."""
        registered = self.ledger.register_fill(fill_id, order.client_order_id)
        if not registered:
            return False
        try:
            order.apply_fill(quantity, price)
        except Exception:
            self.ledger.unregister_fill(fill_id)
            raise
        return True
