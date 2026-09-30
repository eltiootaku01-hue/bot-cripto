from dataclasses import dataclass
from enum import Enum
import time

class InfrastructureError(str,Enum):
    NETWORK_FAILURE="NETWORK_FAILURE"; API_FAILURE="API_FAILURE"; EXCHANGE_UNAVAILABLE="EXCHANGE_UNAVAILABLE"
    EXCHANGE_MAINTENANCE="EXCHANGE_MAINTENANCE"; WEBSOCKET_FAILURE="WEBSOCKET_FAILURE"; WEBSOCKET_STALE="WEBSOCKET_STALE"
    AUTHENTICATION_FAILURE="AUTHENTICATION_FAILURE"; RATE_LIMIT="RATE_LIMIT"; TIMEOUT="TIMEOUT"; INVALID_REQUEST="INVALID_REQUEST"
    SERVER_ERROR="5xx"

@dataclass(frozen=True)
class StreamHealth:
    last_event_timestamp: object
    last_message_monotonic: float
    expected_event_frequency: float
    def stale(self, now_monotonic=None):
        now=time.monotonic() if now_monotonic is None else now_monotonic
        return now-self.last_message_monotonic > self.expected_event_frequency

@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int=3
    base_delay: float=0.5
    jitter: float=0.1
    def can_retry(self,attempt): return attempt < self.max_retries
