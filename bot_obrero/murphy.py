from dataclasses import dataclass
from enum import Enum

class TradingMode(str, Enum):
    NORMAL="NORMAL"; HIGH_VOLATILITY="HIGH_VOLATILITY"; DEGRADED="DEGRADED"; EMERGENCY="EMERGENCY"; UNKNOWN="UNKNOWN"
class GuardState(str, Enum):
    READY="READY"; FROZEN="FROZEN"; RECOVERY="RECOVERY"
class OrderResult(str, Enum):
    CONFIRMED="CONFIRMED"; FAILED="FAILED"; UNKNOWN="ORDER_RESULT_UNKNOWN"
class ProtectionState(str, Enum):
    POSITION_OPEN="POSITION_OPEN"; PROTECTION_PENDING="PROTECTION_PENDING"; PROTECTION_CONFIRMED="PROTECTION_CONFIRMED"; PROTECTION_UNKNOWN="PROTECTION_UNKNOWN"; UNPROTECTED="UNPROTECTED"
class Health(str, Enum):
    HEALTHY="HEALTHY"; UNHEALTHY="UNHEALTHY"; UNKNOWN="UNKNOWN"
@dataclass(frozen=True)
class HealthModel:
    liveness: Health=Health.HEALTHY
    readiness: bool=False
    data_health: Health=Health.UNKNOWN
    exchange_health: Health=Health.UNKNOWN
    state_health: Health=Health.UNKNOWN
    risk_health: Health=Health.UNKNOWN
    reconciliation_health: Health=Health.UNKNOWN
    execution_health: Health=Health.UNKNOWN
    protection_health: Health=Health.UNKNOWN
@dataclass(frozen=True)
class ExecutionPolicy:
    mode: TradingMode
    allow_new_entries: bool
    priority: str
def execution_policy(mode):
    if mode is TradingMode.NORMAL: return ExecutionPolicy(mode,True,"COST")
    if mode is TradingMode.HIGH_VOLATILITY: return ExecutionPolicy(mode,True,"SLIPPAGE_PROTECTION")
    if mode is TradingMode.DEGRADED: return ExecutionPolicy(mode,False,"RISK")
    if mode is TradingMode.EMERGENCY: return ExecutionPolicy(mode,False,"REDUCE_EXPOSURE")
    return ExecutionPolicy(TradingMode.UNKNOWN,False,"FREEZE")
class MurphyGuard:
    def __init__(self):
        self._state=GuardState.FROZEN
        self._readiness_confirmed=False
    @property
    def state(self): return self._state
    def freeze(self):
        self._state=GuardState.FROZEN; self._readiness_confirmed=False
    def recover(self):
        self._state=GuardState.RECOVERY; self._readiness_confirmed=False
    def ready(self,evidence=None):
        required=("reconciliation_match","clock_valid","stream_ready","resources_healthy","permissions_safe","configuration_valid","protection_safe")
        if evidence is None or any(getattr(evidence,n,False) is not True for n in required):
            self.freeze(); raise ValueError("READY_REQUIRES_COMPLETE_DETERMINISTIC_EVIDENCE")
        self._state=GuardState.READY; self._readiness_confirmed=True
    def allow_new_entry(self):
        return self._state is GuardState.READY and self._readiness_confirmed
