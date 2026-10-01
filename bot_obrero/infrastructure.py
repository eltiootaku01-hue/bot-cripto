from dataclasses import dataclass
from enum import Enum
import random
import time

class InfrastructureError(str, Enum):
    NETWORK_FAILURE="NETWORK_FAILURE"; API_FAILURE="API_FAILURE"; EXCHANGE_UNAVAILABLE="EXCHANGE_UNAVAILABLE"
    EXCHANGE_MAINTENANCE="MAINTENANCE"; WEBSOCKET_FAILURE="WS_FAILURE"; WEBSOCKET_STALE="WS_STALE"
    AUTHENTICATION_FAILURE="AUTH"; RATE_LIMIT="RATE_LIMIT"; TIMEOUT="TIMEOUT"; INVALID_REQUEST="INVALID_REQUEST"
    SERVER_ERROR="5xx"

@dataclass(frozen=True)
class StreamHealth:
    last_event_timestamp: object
    last_message_monotonic: float
    expected_event_frequency: float
    last_sequence: int | None = None

    def stale(self, now_monotonic=None):
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return now - self.last_message_monotonic > self.expected_event_frequency

    def accepts_event(self, sequence: int | None):
        if sequence is None or self.last_sequence is None:
            return True
        return sequence > self.last_sequence

@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 0.5
    jitter: float = 0.1

    def can_retry(self, attempt):
        return 0 <= attempt < self.max_retries

    def delay(self, attempt, rng=None):
        if not self.can_retry(attempt):
            raise ValueError("RETRY_EXHAUSTED")
        rng = rng or random
        jitter = rng.uniform(0, self.jitter) if self.jitter else 0
        return self.base_delay * (2 ** attempt) + jitter

def classify_http_failure(status_code: int | None, *, timeout=False, network=False):
    if timeout:
        return InfrastructureError.TIMEOUT
    if network:
        return InfrastructureError.NETWORK_FAILURE
    if status_code == 429:
        return InfrastructureError.RATE_LIMIT
    if status_code in (401, 403):
        return InfrastructureError.AUTHENTICATION_FAILURE
    if status_code is not None and 500 <= status_code <= 599:
        return InfrastructureError.SERVER_ERROR
    if status_code is not None and 400 <= status_code <= 499:
        return InfrastructureError.INVALID_REQUEST
    return InfrastructureError.API_FAILURE

def retryable(error: InfrastructureError):
    return error in {
        InfrastructureError.NETWORK_FAILURE, InfrastructureError.TIMEOUT,
        InfrastructureError.SERVER_ERROR, InfrastructureError.RATE_LIMIT,
    }

@dataclass(frozen=True)
class ReconnectGate:
    requires_snapshot: bool = True
    snapshot_confirmed: bool = False
    def ready(self):
        return self.snapshot_confirmed if self.requires_snapshot else True
