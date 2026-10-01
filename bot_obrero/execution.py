from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
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
        for kind in required:
            record = self.record(kind)
            if (
                record is None
                or record.correlation_id != self.correlation_id
                or not record.valid_for(intent_id, now)
            ):
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
    """The only object authorized to cross from the internal flow to an adapter."""

    def __init__(self, adapter: ExchangeAdapter):
        self._adapter = adapter

    def submit(self, order):
        return self._adapter.submit(order, _permit=_BOUNDARY_PERMIT)

    def cancel(self, order):
        return self._adapter.cancel(order, _permit=_BOUNDARY_PERMIT)


class ExecutionOrchestrator:
    """Mandatory pre-effect pipeline for simulated/external execution adapters."""

    def __init__(self, *, adapter, ledger, murphy_guard, readiness_gate=None, clock=None):
        from .order_lifecycle import LifecycleOrder

        self.boundary = ExecutionBoundary(adapter)
        self.ledger = ledger
        self.murphy = murphy_guard
        self.readiness = readiness_gate or ReadinessGate()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._LifecycleOrder = LifecycleOrder

    @staticmethod
    def _validate_intent(intent: OrderIntent) -> None:
        quantity = intent.payload.get("quantity")
        if quantity is None or float(quantity) <= 0:
            raise ValueError("INVALID_ORDER_INTENT_QUANTITY")

    def execute(self, intent: OrderIntent, evidence: EvidenceBundle) -> ExecutionDecision:
        self._validate_intent(intent)
        if not isinstance(evidence, EvidenceBundle):
            self.murphy.freeze()
            return ExecutionDecision(False, "FAIL_CLOSED")
        now = self.clock()
        readiness = self.readiness.evaluate_evidence(evidence, intent.client_order_id, now)
        if not readiness.allowed:
            self.murphy.freeze()
            return readiness

        risk = RiskGuard(self.murphy, self.readiness)
        risk_decision = risk.authorize_new_entry(ReadinessEvidence())
        if not risk_decision.allowed:
            return risk_decision

        try:
            registered = self.ledger.register(intent.client_order_id, dict(intent.payload))
        except Exception:
            self.murphy.freeze()
            raise
        if not registered:
            return ExecutionDecision(False, "DUPLICATE")

        order = self._LifecycleOrder(intent.client_order_id, intent.payload["quantity"])
        order.submit()
        try:
            self.boundary.submit(intent)
        except TimeoutError:
            self.ledger.mark_result(intent.client_order_id, "ORDER_RESULT_UNKNOWN")
            self.murphy.freeze()
            return ExecutionDecision(False, "UNKNOWN")
        except Exception:
            self.ledger.mark_result(intent.client_order_id, "ORDER_RESULT_UNKNOWN")
            self.murphy.freeze()
            return ExecutionDecision(False, "UNKNOWN")

        self.ledger.mark_result(intent.client_order_id, "SUBMITTED")
        return ExecutionDecision(True, "SUBMITTED")

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
