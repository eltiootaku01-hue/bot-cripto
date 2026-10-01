from dataclasses import dataclass
from enum import Enum

class ResourceState(str,Enum):
    HEALTHY="HEALTHY"; DEGRADED="DEGRADED"; CRITICAL="CRITICAL"; UNKNOWN="UNKNOWN"

@dataclass(frozen=True)
class ResourceHealth:
    cpu:ResourceState=ResourceState.UNKNOWN
    memory:ResourceState=ResourceState.UNKNOWN
    disk:ResourceState=ResourceState.UNKNOWN
    process:ResourceState=ResourceState.UNKNOWN
    database:ResourceState=ResourceState.UNKNOWN
    network:ResourceState=ResourceState.UNKNOWN

@dataclass(frozen=True)
class ResourceThresholds:
    cpu_critical: float = 0.95
    memory_critical: float = 0.95
    disk_critical: float = 0.90

def trading_allowed(h):
    return all(x is ResourceState.HEALTHY for x in (h.cpu,h.memory,h.disk,h.process,h.database,h.network))

def state_from_ratio(ratio: float, critical: float):
    if not 0 <= ratio <= 1:
        raise ValueError("INVALID_RESOURCE_RATIO")
    if ratio >= critical:
        return ResourceState.CRITICAL
    if ratio >= critical * 0.8:
        return ResourceState.DEGRADED
    return ResourceState.HEALTHY
