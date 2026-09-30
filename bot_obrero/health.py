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
def trading_allowed(h):
    return all(x is ResourceState.HEALTHY for x in (h.cpu,h.memory,h.disk,h.process,h.database,h.network))
