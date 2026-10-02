"""Binance Spot kline interval validation and provider-specific semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum


class BinanceSpotIntervalError(ValueError):
    """Raised when a Binance Spot kline interval input is invalid."""


class BinanceSpotIntervalUnit(str, Enum):
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


_SUPPORTED_INTERVALS = {
    "1s": (BinanceSpotIntervalUnit.SECOND, 1, timedelta(seconds=1)),
    "1m": (BinanceSpotIntervalUnit.MINUTE, 1, timedelta(minutes=1)),
    "3m": (BinanceSpotIntervalUnit.MINUTE, 3, timedelta(minutes=3)),
    "5m": (BinanceSpotIntervalUnit.MINUTE, 5, timedelta(minutes=5)),
    "15m": (BinanceSpotIntervalUnit.MINUTE, 15, timedelta(minutes=15)),
    "30m": (BinanceSpotIntervalUnit.MINUTE, 30, timedelta(minutes=30)),
    "1h": (BinanceSpotIntervalUnit.HOUR, 1, timedelta(hours=1)),
    "2h": (BinanceSpotIntervalUnit.HOUR, 2, timedelta(hours=2)),
    "4h": (BinanceSpotIntervalUnit.HOUR, 4, timedelta(hours=4)),
    "6h": (BinanceSpotIntervalUnit.HOUR, 6, timedelta(hours=6)),
    "8h": (BinanceSpotIntervalUnit.HOUR, 8, timedelta(hours=8)),
    "12h": (BinanceSpotIntervalUnit.HOUR, 12, timedelta(hours=12)),
    "1d": (BinanceSpotIntervalUnit.DAY, 1, timedelta(days=1)),
    "3d": (BinanceSpotIntervalUnit.DAY, 3, timedelta(days=3)),
    "1w": (BinanceSpotIntervalUnit.WEEK, 1, timedelta(weeks=1)),
    "1M": (BinanceSpotIntervalUnit.MONTH, 1, None),
}


@dataclass(frozen=True)
class BinanceSpotInterval:
    """Exact provider-specific Binance Spot interval semantics."""

    value: str
    unit: BinanceSpotIntervalUnit
    magnitude: int
    duration: timedelta | None

    def __post_init__(self) -> None:
        expected = _SUPPORTED_INTERVALS.get(self.value)
        if expected is None or (self.unit, self.magnitude, self.duration) != expected:
            raise BinanceSpotIntervalError(
                f"invalid Binance Spot interval representation: {self.value!r}"
            )

    @classmethod
    def parse(cls, value: str) -> "BinanceSpotInterval":
        if isinstance(value, bool) or not isinstance(value, str):
            raise BinanceSpotIntervalError("Binance Spot interval must be a string")
        if value == "" or value != value.strip():
            raise BinanceSpotIntervalError(
                "Binance Spot interval must be non-empty and contain no surrounding whitespace"
            )
        spec = _SUPPORTED_INTERVALS.get(value)
        if spec is None:
            raise BinanceSpotIntervalError(f"unsupported Binance Spot interval: {value!r}")
        unit, magnitude, duration = spec
        return cls(value=value, unit=unit, magnitude=magnitude, duration=duration)

    @classmethod
    def supported_values(cls) -> tuple[str, ...]:
        return tuple(_SUPPORTED_INTERVALS)


def validate_binance_spot_interval(value: str) -> BinanceSpotInterval:
    """Validate and return the exact provider interval representation."""
    return BinanceSpotInterval.parse(value)


__all__ = [
    "BinanceSpotInterval",
    "BinanceSpotIntervalError",
    "BinanceSpotIntervalUnit",
    "validate_binance_spot_interval",
]