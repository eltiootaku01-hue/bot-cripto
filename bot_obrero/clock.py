from dataclasses import dataclass

@dataclass(frozen=True)
class ClockCheck:
    local_now: object
    exchange_now: object
    max_skew_seconds: float=2.0
    @property
    def offset_seconds(self): return (self.exchange_now-self.local_now).total_seconds()
    @property
    def healthy(self): return abs(self.offset_seconds)<=self.max_skew_seconds
