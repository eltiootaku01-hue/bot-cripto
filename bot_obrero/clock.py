from dataclasses import dataclass
from datetime import datetime, timezone
import time

@dataclass(frozen=True)
class ClockCheck:
    local_now: datetime
    exchange_now: datetime
    max_skew_seconds: float = 2.0
    @property
    def offset_seconds(self):
        return (self.exchange_now - self.local_now).total_seconds()
    @property
    def healthy(self):
        return abs(self.offset_seconds) <= self.max_skew_seconds

@dataclass(frozen=True)
class ClockSnapshot:
    utc_now: datetime
    monotonic: float
    exchange_time: datetime
    offset_seconds: float
    tolerance_seconds: float
    @property
    def valid(self):
        return abs(self.offset_seconds) <= self.tolerance_seconds

class ClockService:
    def __init__(self, tolerance_seconds: float):
        if tolerance_seconds < 0:
            raise ValueError("INVALID_CLOCK_TOLERANCE")
        self.tolerance_seconds = tolerance_seconds

    def snapshot(self, exchange_time: datetime, local_time: datetime | None = None, monotonic_time: float | None = None):
        local_time = local_time or datetime.now(timezone.utc)
        monotonic_time = time.monotonic() if monotonic_time is None else monotonic_time
        if local_time.tzinfo is None or exchange_time.tzinfo is None:
            raise ValueError("CLOCK_TIMESTAMPS_MUST_BE_TIMEZONE_AWARE")
        offset = (exchange_time - local_time).total_seconds()
        return ClockSnapshot(local_time, monotonic_time, exchange_time, offset, self.tolerance_seconds)
