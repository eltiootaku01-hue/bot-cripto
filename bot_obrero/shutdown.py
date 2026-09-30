from enum import Enum
from dataclasses import dataclass
class ShutdownReason(str,Enum):
    NORMAL="NORMAL_SHUTDOWN"; EMERGENCY="EMERGENCY_SHUTDOWN"; CRASH="CRASH"; KILL="KILL"; PROCESS_FAILURE="PROCESS_FAILURE"
@dataclass(frozen=True)
class ShutdownRecord:
    reason:ShutdownReason
    persisted:bool
    critical_events_flushed:bool
