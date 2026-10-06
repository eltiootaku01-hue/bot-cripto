"""Deterministic replay primitives for FRANKESTAIN.

Architectural inspiration:
- NautilusTrader deterministic event-driven backtest/replay model
- Passivbot replay/live-event model

This is a pure candidate layer. It does not access exchanges, clocks, files,
databases or execution adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen = {
            str(key): _freeze(item)
            for key, item in value.items()
        }
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ReplayEvent:
    event_id: str
    event_type: str
    event_timestamp: datetime
    available_at: datetime
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be non-empty")
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError("event_type must be non-empty")

        _aware(self.event_timestamp, "event_timestamp")
        _aware(self.available_at, "available_at")

        if not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping")

        if self.available_at < self.event_timestamp:
            raise ValueError("available_at cannot precede event_timestamp")

        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True)
class ReplayTimeline:
    events: tuple[ReplayEvent, ...]

    def __post_init__(self) -> None:
        events = tuple(self.events)
        if not all(isinstance(item, ReplayEvent) for item in events):
            raise ValueError("events must contain only ReplayEvent")
        if len({item.event_id for item in events}) != len(events):
            raise ValueError("event_id values must be unique")

        ordered = sorted(
            events,
            key=lambda item: (
                item.event_timestamp,
                item.available_at,
                item.event_id,
            ),
        )
        object.__setattr__(self, "events", ordered)

    def available_by(self, evaluation_timestamp: datetime) -> tuple[ReplayEvent, ...]:
        _aware(evaluation_timestamp, "evaluation_timestamp")
        return tuple(
            event
            for event in self.events
            if event.available_at <= evaluation_timestamp
        )


__all__ = [
    "ReplayEvent",
    "ReplayTimeline",
]
