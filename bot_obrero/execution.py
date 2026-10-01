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
    @abstractmethod
    def submit(self, order): raise NotImplementedError
    @abstractmethod
    def cancel(self, order): raise NotImplementedError
    @abstractmethod
    def snapshot(self): raise NotImplementedError
@dataclass(frozen=True)
class ReadinessInputs:
    reconciliation_match: bool
    clock_valid: bool
    stream_ready: bool
    resources_healthy: bool
    permissions_safe: bool
    configuration_valid: bool
    protection_safe: bool=False
class ReadinessGate:
    def evaluate(self,inputs):
        checks=(inputs.reconciliation_match,inputs.clock_valid,inputs.stream_ready,inputs.resources_healthy,inputs.permissions_safe,inputs.configuration_valid,inputs.protection_safe)
        return ExecutionDecision(all(checks),"READY" if all(checks) else "FAIL_CLOSED")
class RiskGuard:
    def __init__(self,murphy_guard,readiness_gate=None):
        self.guard=murphy_guard; self.readiness_gate=readiness_gate or ReadinessGate()
    def authorize_new_entry(self,readiness):
        decision=self.readiness_gate.evaluate(readiness)
        if not decision.allowed:
            self.guard.freeze(); return decision
        try: self.guard.ready(readiness)
        except ValueError:
            self.guard.freeze(); return ExecutionDecision(False,"FAIL_CLOSED")
        return ExecutionDecision(True,"READY")
