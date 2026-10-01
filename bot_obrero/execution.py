from dataclasses import dataclass
from abc import ABC, abstractmethod
from enum import Enum

class ExecutionContext(str, Enum):
    NORMAL="NORMAL"; RISK_REDUCTION="RISK_REDUCTION"; UNKNOWN="UNKNOWN"

@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str

class ExchangeAdapter(ABC):
    """Abstract boundary: this phase contains no live exchange implementation."""
    @abstractmethod
    def submit(self, order):
        raise NotImplementedError

    @abstractmethod
    def cancel(self, order):
        raise NotImplementedError

    @abstractmethod
    def snapshot(self):
        raise NotImplementedError

class RiskGuard:
    def __init__(self, murphy_guard):
        self.guard = murphy_guard

    def authorize_new_entry(self):
        if not self.guard.allow_new_entry():
            return ExecutionDecision(False, "FAIL_CLOSED")
        return ExecutionDecision(True, "READY")

@dataclass(frozen=True)
class ReadinessInputs:
    reconciliation_match: bool
    clock_valid: bool
    stream_ready: bool
    resources_healthy: bool
    permissions_safe: bool
    configuration_valid: bool

class ReadinessGate:
    """Only deterministic evidence can transition the core to entry-ready."""
    def evaluate(self, inputs: ReadinessInputs):
        checks = (
            inputs.reconciliation_match,
            inputs.clock_valid,
            inputs.stream_ready,
            inputs.resources_healthy,
            inputs.permissions_safe,
            inputs.configuration_valid,
        )
        if not all(checks):
            return ExecutionDecision(False, "FAIL_CLOSED")
        return ExecutionDecision(True, "READY")
