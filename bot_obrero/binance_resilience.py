"""Bounded resilience policy for the Binance Spot REST boundary."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class BinanceRetryPolicy:
    """Deterministic, bounded retry policy for public Binance REST GETs."""

    max_attempts: int = 3
    backoff_base_seconds: float = 0.5
    max_backoff_seconds: float = 5.0
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must be >= 0")
        if self.max_backoff_seconds < self.backoff_base_seconds:
            raise ValueError("max_backoff_seconds must be >= backoff_base_seconds")
        if not callable(self.sleeper):
            raise ValueError("sleeper must be callable")

    def exponential_backoff(self, failed_attempt: int) -> float:
        if failed_attempt < 1:
            raise ValueError("failed_attempt must be >= 1")
        return min(self.backoff_base_seconds * (2 ** (failed_attempt - 1)), self.max_backoff_seconds)

    def delay_for(self, failed_attempt: int, retry_after_seconds: float | None) -> float:
        if retry_after_seconds is not None:
            return retry_after_seconds
        return self.exponential_backoff(failed_attempt)

    def sleep_before_retry(self, *, failed_attempt: int, retry_after_seconds: float | None = None) -> float:
        delay = self.delay_for(failed_attempt, retry_after_seconds)
        self.sleeper(delay)
        return delay

    def execute(
        self,
        operation: Callable[[], object],
        *,
        should_retry: Callable[[Exception], bool],
        retry_after: Callable[[Exception], float | None],
        before_attempt: Callable[[], None] | None = None,
    ) -> object:
        for attempt in range(1, self.max_attempts + 1):
            if before_attempt is not None:
                before_attempt()
            try:
                return operation()
            except Exception as exc:
                if attempt >= self.max_attempts or not should_retry(exc):
                    raise
                self.sleep_before_retry(failed_attempt=attempt, retry_after_seconds=retry_after(exc))
        raise RuntimeError("unreachable Binance retry state")

@dataclass
class BinanceRequestBudget:
    """Counts physical HTTP attempts for one bounded historical acquisition."""

    max_requests: int
    used_requests: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_requests, bool) or not isinstance(self.max_requests, int):
            raise ValueError("max_requests must be an integer")
        if self.max_requests < 1:
            raise ValueError("max_requests must be >= 1")

    def consume(self) -> None:
        if self.used_requests >= self.max_requests:
            raise RuntimeError(f"historical acquisition exceeded max_requests={self.max_requests}")
        self.used_requests += 1

__all__ = ["BinanceRequestBudget", "BinanceRetryPolicy"]