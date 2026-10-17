from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class EvidenceTimestamp:
    event_timestamp: datetime
    available_timestamp: datetime
    decision_timestamp: datetime
    execution_timestamp: datetime|None=None
    def __post_init__(self):
        for value in (
            self.event_timestamp,
            self.available_timestamp,
            self.decision_timestamp,
            self.execution_timestamp,
        ):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError("timestamps must be timezone-aware")
        if self.available_timestamp > self.decision_timestamp:
            raise ValueError("look-ahead: evidence unavailable")
        if (
            self.execution_timestamp is not None
            and self.execution_timestamp < self.decision_timestamp
        ):
            raise ValueError("execution before decision")
