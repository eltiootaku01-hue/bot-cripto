"""Canonical Market Data Contract v1.0.

The legacy Candle in bot_obrero.data remains available for compatibility.
New canonical market data must use the Candle defined in this module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

DEFAULT_MARKET_DATA_ROUNDING = ROUND_HALF_EVEN
CANDLE_DATA_TYPE = "CANDLE"


class MarketDataError(ValueError):
    """Raised when canonical market data violates its contract."""


class InstrumentType(str, Enum):
    CRYPTO_SPOT = "CRYPTO_SPOT"


class DataQuality(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class DataCompleteness(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class CandleState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class CandleFinality(str, Enum):
    NOT_FINAL = "NOT_FINAL"
    FINAL = "FINAL"
    UNKNOWN = "UNKNOWN"


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketDataError(f"{name} must be a non-empty string")
    return value


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError(f"{name} must be timezone-aware")


def _decimal(value: Decimal | str | int, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise MarketDataError(f"{name} must not be supplied as float")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise MarketDataError(f"{name} must be an exact decimal value") from exc
    if not result.is_finite():
        raise MarketDataError(f"{name} must be finite")
    return result


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_datetime(value: str | None, name: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise MarketDataError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MarketDataError(f"{name} must be an ISO-8601 timestamp") from exc
    _aware(parsed, name)
    return parsed


@dataclass(frozen=True)
class InstrumentIdentity:
    instrument_id: str
    symbol: str
    market: str
    instrument_type: InstrumentType = InstrumentType.CRYPTO_SPOT

    def __post_init__(self) -> None:
        _nonempty(self.instrument_id, "instrument_id")
        _nonempty(self.symbol, "symbol")
        _nonempty(self.market, "market")
        try:
            object.__setattr__(self, "instrument_type", InstrumentType(self.instrument_type))
        except ValueError as exc:
            raise MarketDataError("instrument_type is not supported by v1.0") from exc


@dataclass(frozen=True)
class SourceIdentity:
    source_id: str
    provider: str
    venue: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.source_id, "source_id")
        _nonempty(self.provider, "provider")
        if self.venue is not None:
            _nonempty(self.venue, "venue")


@dataclass(frozen=True)
class Candle:
    start: datetime
    end: datetime
    timeframe: str
    open: Decimal | str | int
    high: Decimal | str | int
    low: Decimal | str | int
    close: Decimal | str | int
    volume: Decimal | str | int
    candle_state: CandleState = CandleState.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.UNKNOWN
    finality: CandleFinality = CandleFinality.UNKNOWN

    def __post_init__(self) -> None:
        _aware(self.start, "start")
        _aware(self.end, "end")
        if self.end <= self.start:
            raise MarketDataError("candle end must follow start")
        _nonempty(self.timeframe, "timeframe")
        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))

        # Structural OHLCV validation is independent from quality, completeness,
        # candle_state and finality. PARTIAL/CLOSED/NOT_FINAL remain valid combinations.
        if self.high < self.open:
            raise MarketDataError("candle high must be >= open")
        if self.high < self.close:
            raise MarketDataError("candle high must be >= close")
        if self.high < self.low:
            raise MarketDataError("candle high must be >= low")
        if self.low > self.open:
            raise MarketDataError("candle low must be <= open")
        if self.low > self.close:
            raise MarketDataError("candle low must be <= close")
        if self.low > self.high:
            raise MarketDataError("candle low must be <= high")
        if self.volume < 0:
            raise MarketDataError("candle volume must be >= 0")

        for name, enum_type in (
            ("candle_state", CandleState),
            ("completeness", DataCompleteness),
            ("finality", CandleFinality),
        ):
            try:
                object.__setattr__(self, name, enum_type(getattr(self, name)))
            except ValueError as exc:
                raise MarketDataError(f"invalid {name}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timeframe": self.timeframe,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "candle_state": self.candle_state.value,
            "completeness": self.completeness.value,
            "finality": self.finality.value,
        }


@dataclass(frozen=True)
class MarketData:
    instrument: InstrumentIdentity
    source: SourceIdentity
    data_type: str
    observed_at: datetime
    received_at: datetime
    available_at: datetime | None
    payload: Candle
    quality: DataQuality = DataQuality.UNKNOWN
    completeness: DataCompleteness = DataCompleteness.UNKNOWN
    source_sequence: str | int | None = None
    market_data_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentIdentity):
            raise MarketDataError("instrument must be InstrumentIdentity")
        if not isinstance(self.source, SourceIdentity):
            raise MarketDataError("source must be SourceIdentity")
        if not isinstance(self.payload, Candle):
            raise MarketDataError("payload must be canonical Candle")
        _nonempty(self.market_data_id, "market_data_id")
        _nonempty(self.data_type, "data_type")
        if self.data_type != CANDLE_DATA_TYPE:
            raise MarketDataError(f"unsupported data_type: {self.data_type}")
        _aware(self.observed_at, "observed_at")
        _aware(self.received_at, "received_at")
        if self.available_at is not None:
            _aware(self.available_at, "available_at")
        try:
            object.__setattr__(self, "quality", DataQuality(self.quality))
            object.__setattr__(self, "completeness", DataCompleteness(self.completeness))
        except ValueError as exc:
            raise MarketDataError("invalid quality or completeness") from exc
        if isinstance(self.source_sequence, bool) or not (
            self.source_sequence is None or isinstance(self.source_sequence, (str, int))
        ):
            raise MarketDataError("source_sequence must be a source-provided string, integer, or None")
        if isinstance(self.source_sequence, str) and not self.source_sequence.strip():
            raise MarketDataError("source_sequence must not be empty")

    @property
    def instrument_id(self) -> str:
        return self.instrument.instrument_id

    @property
    def source_id(self) -> str:
        return self.source.source_id

    @property
    def candle_identity(self) -> tuple[str, str, datetime]:
        """Logical candle identity; source identity is intentionally excluded."""
        return (self.instrument_id, self.payload.timeframe, self.payload.start)

    def evidence_at(self, decision_at: datetime):
        """Return the existing EvidenceTimestamp guard; unknown availability fails closed."""
        if self.available_at is None:
            raise MarketDataError("AVAILABLE_AT_UNKNOWN")
        from .temporal import EvidenceTimestamp
        return EvidenceTimestamp(
            event_timestamp=self.observed_at,
            available_timestamp=self.available_at,
            decision_timestamp=decision_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_data_id": self.market_data_id,
            "instrument": {
                "instrument_id": self.instrument.instrument_id,
                "symbol": self.instrument.symbol,
                "market": self.instrument.market,
                "instrument_type": self.instrument.instrument_type.value,
            },
            "source": {
                "source_id": self.source.source_id,
                "provider": self.source.provider,
                "venue": self.source.venue,
            },
            "data_type": self.data_type,
            "observed_at": _datetime_text(self.observed_at),
            "received_at": _datetime_text(self.received_at),
            "available_at": _datetime_text(self.available_at),
            "payload": self.payload.to_dict(),
            "quality": self.quality.value,
            "completeness": self.completeness.value,
            "source_sequence": self.source_sequence,
        }

    def to_json(self) -> str:
        """Serialize Decimal values as exact, reconstructible strings."""
        return json.dumps(self.to_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MarketData":
        try:
            instrument_data = value["instrument"]
            source_data = value["source"]
            candle_data = value["payload"]
            candle = Candle(
                start=_parse_datetime(candle_data["start"], "payload.start"),
                end=_parse_datetime(candle_data["end"], "payload.end"),
                timeframe=candle_data["timeframe"],
                open=candle_data["open"],
                high=candle_data["high"],
                low=candle_data["low"],
                close=candle_data["close"],
                volume=candle_data["volume"],
                candle_state=candle_data["candle_state"],
                completeness=candle_data["completeness"],
                finality=candle_data["finality"],
            )
            return cls(
                market_data_id=value["market_data_id"],
                instrument=InstrumentIdentity(**instrument_data),
                source=SourceIdentity(**source_data),
                data_type=value["data_type"],
                observed_at=_parse_datetime(value["observed_at"], "observed_at"),
                received_at=_parse_datetime(value["received_at"], "received_at"),
                available_at=_parse_datetime(value.get("available_at"), "available_at", optional=True),
                payload=candle,
                quality=value["quality"],
                completeness=value["completeness"],
                source_sequence=value.get("source_sequence"),
            )
        except KeyError as exc:
            raise MarketDataError(f"missing MarketData field: {exc.args[0]}") from exc

    @classmethod
    def from_json(cls, value: str) -> "MarketData":
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MarketDataError("invalid MarketData JSON") from exc
        if not isinstance(decoded, dict):
            raise MarketDataError("MarketData JSON root must be an object")
        return cls.from_dict(decoded)


def to_market_observation(market_data: MarketData):
    """Promote valid, temporally available MarketData into the existing Phase 1.6 evidence contract.

    UNKNOWN availability remains unpromoted. INVALID data is never accepted as an observation.
    """
    if not isinstance(market_data, MarketData):
        raise TypeError("market_data must be MarketData")
    if market_data.quality is not DataQuality.VALID:
        raise MarketDataError("MARKET_DATA_QUALITY_NOT_VALID")
    if market_data.available_at is None:
        raise MarketDataError("AVAILABLE_AT_UNKNOWN")
    from .analysis_contracts import ArtifactNature, MarketObservation, Provenance
    candle = market_data.payload
    values = {
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "timeframe": candle.timeframe,
        "start": candle.start.isoformat(),
        "end": candle.end.isoformat(),
        "candle_state": candle.candle_state.value,
        "candle_completeness": candle.completeness.value,
        "finality": candle.finality.value,
        "quality": market_data.quality.value,
        "completeness": market_data.completeness.value,
        "market_data_id": market_data.market_data_id,
        "instrument_id": market_data.instrument_id,
        "source_id": market_data.source_id,
        "provider": market_data.source.provider,
        "received_at": market_data.received_at.isoformat(),
    }
    return MarketObservation(
        symbol=market_data.instrument.symbol,
        observation_timestamp=market_data.observed_at,
        available_timestamp=market_data.available_at,
        observation_type=market_data.data_type,
        values=values,
        provenance=Provenance(
            source=market_data.source_id,
            nature=ArtifactNature.OBSERVED,
            reference=market_data.market_data_id,
            metadata={
                "instrument_id": market_data.instrument_id,
                "provider": market_data.source.provider,
                "venue": market_data.source.venue,
                "quality": market_data.quality.value,
                "completeness": market_data.completeness.value,
            },
        ),
        venue=market_data.source.venue,
    )
