"""Binance Spot raw WebSocket Kline acquisition boundary.

This module provides a bounded, injectable WebSocket boundary for Binance Spot
Kline streams. It stops at canonical MarketData and deliberately excludes
reconnects, retries, persistence, background workers, and REST fallback.
"""

from __future__ import annotations

import json
import socket
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

import websocket

from .acquisition import (
    CanonicalValidationError,
    InstrumentMapper,
    InstrumentMappingRule,
    NormalizationError,
    ProviderPayloadError,
    canonicalize_market_data,
    normalize_provider_record,
    parse_provider_payload,
)
from .availability import AvailabilityEvidence, resolve_availability
from .binance_instruments import (
    BinanceSpotInstrumentMetadata,
    InstrumentInactive,
    InstrumentMappingAmbiguous,
    InstrumentMetadataInvalid,
    InstrumentNotFound,
)
from .binance_intervals import validate_binance_spot_interval
from .market_data import MarketData


RAW_STREAM_BASE_URL = "wss://stream.binance.com:9443/ws"
WEBSOCKET_SOURCE_ID = "binance-spot-websocket"
WEBSOCKET_PROVIDER = "binance"
WEBSOCKET_MARKET = "SPOT"
WEBSOCKET_VENUE = "BINANCE"


class BinanceWebSocketError(RuntimeError):
    """Base error for the bounded Binance Spot WebSocket boundary."""


class BinanceWebSocketTransportError(BinanceWebSocketError):
    """Network, timeout, or closed-connection failure."""


class BinanceWebSocketProtocolError(BinanceWebSocketError):
    """Failure to interpret the WebSocket message envelope/protocol."""


class BinanceWebSocketPayloadError(BinanceWebSocketError):
    """Failure to validate or canonicalize a Binance Kline payload."""


@runtime_checkable
class WebSocketConnection(Protocol):
    """Minimal connection surface required by the adapter."""

    def recv(self) -> str | bytes:
        """Receive one WebSocket message."""

    def close(self) -> None:
        """Close the connection."""


WebSocketFactory = Callable[[str, float], WebSocketConnection]
Clock = Callable[[], datetime]


def _default_websocket_factory(url: str, timeout_seconds: float) -> WebSocketConnection:
    return websocket.create_connection(url, timeout=timeout_seconds)


def build_binance_spot_kline_stream_url(symbol: str, interval: str) -> str:
    """Build the authorized Binance raw Spot Kline stream URL."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    if symbol != symbol.strip():
        raise ValueError("symbol must not contain surrounding whitespace")
    validated_interval = validate_binance_spot_interval(interval)
    return (
        f"{RAW_STREAM_BASE_URL}/"
        f"{symbol.lower()}@kline_{validated_interval.value}"
    )


def _ms_to_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BinanceWebSocketPayloadError(
            f"{field_name} must be a non-negative integer millisecond timestamp"
        )
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise BinanceWebSocketPayloadError(
            f"{field_name} is outside supported datetime range"
        ) from exc


def _required_mapping_field(
    mapping: Mapping[str, Any],
    name: str,
) -> Any:
    if name not in mapping:
        raise BinanceWebSocketPayloadError(
            f"Binance kline payload missing required field: {name}"
        )
    return mapping[name]


def _required_text(
    value: Any,
    field_name: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BinanceWebSocketPayloadError(
            f"{field_name} must be a non-empty string"
        )
    return value


def _required_nonnegative_int(
    value: Any,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BinanceWebSocketPayloadError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _required_decimal_string(
    value: Any,
    field_name: str,
) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise BinanceWebSocketPayloadError(
            f"{field_name} must be a non-empty decimal string"
        )
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise BinanceWebSocketPayloadError(
            f"{field_name} is not a valid decimal string"
        ) from exc
    if not result.is_finite():
        raise BinanceWebSocketPayloadError(
            f"{field_name} must be finite"
        )
    return result


def _decode_message(message: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(message, Mapping):
        payload = message
    else:
        if isinstance(message, bytes):
            try:
                text = message.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BinanceWebSocketProtocolError(
                    "WebSocket message is not valid UTF-8"
                ) from exc
        elif isinstance(message, str):
            text = message
        else:
            raise BinanceWebSocketProtocolError(
                "WebSocket message must be text, UTF-8 bytes, or a mapping"
            )
        try:
            decoded = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BinanceWebSocketProtocolError(
                "WebSocket message is not valid JSON"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise BinanceWebSocketProtocolError(
                "WebSocket message root must be a JSON object"
            )
        payload = decoded

    return payload


def parse_binance_kline_event(
    message: str | bytes | Mapping[str, Any],
    *,
    requested_symbol: str,
    requested_interval: str,
) -> Any:
    """Parse one raw Binance Kline event into the provider-neutral ProviderRecord."""
    if not isinstance(requested_symbol, str) or not requested_symbol.strip():
        raise ValueError("requested_symbol must be a non-empty string")
    if requested_symbol != requested_symbol.strip():
        raise ValueError("requested_symbol must not contain surrounding whitespace")
    validated_interval = validate_binance_spot_interval(requested_interval)

    root = _decode_message(message)

    event_type = _required_mapping_field(root, "e")
    if event_type != "kline":
        raise BinanceWebSocketProtocolError(
            f"unexpected Binance WebSocket event type: {event_type!r}"
        )

    event_time_ms = _required_mapping_field(root, "E")
    event_symbol = _required_mapping_field(root, "s")
    kline = _required_mapping_field(root, "k")

    event_time_ms = _required_nonnegative_int(event_time_ms, "E")
    event_symbol = _required_text(event_symbol, "s")
    if event_symbol != requested_symbol:
        raise BinanceWebSocketPayloadError(
            f"Binance WebSocket symbol mismatch: expected {requested_symbol!r}, "
            f"received {event_symbol!r}"
        )
    if not isinstance(kline, Mapping):
        raise BinanceWebSocketPayloadError("k must be an object")

    kline_symbol = _required_text(
        _required_mapping_field(kline, "s"),
        "k.s",
    )
    if kline_symbol != requested_symbol:
        raise BinanceWebSocketPayloadError(
            f"Binance Kline symbol mismatch: expected {requested_symbol!r}, "
            f"received {kline_symbol!r}"
        )

    kline_interval = _required_text(
        _required_mapping_field(kline, "i"),
        "k.i",
    )
    try:
        payload_interval = validate_binance_spot_interval(kline_interval)
    except ValueError as exc:
        raise BinanceWebSocketPayloadError(
            f"invalid Binance WebSocket Kline interval: {kline_interval!r}"
        ) from exc
    if payload_interval.value != validated_interval.value:
        raise BinanceWebSocketPayloadError(
            f"Binance WebSocket interval mismatch: expected "
            f"{validated_interval.value!r}, received {payload_interval.value!r}"
        )

    candle_start_ms = _required_nonnegative_int(
        _required_mapping_field(kline, "t"),
        "k.t",
    )
    candle_end_ms = _required_nonnegative_int(
        _required_mapping_field(kline, "T"),
        "k.T",
    )
    if candle_end_ms <= candle_start_ms:
        raise BinanceWebSocketPayloadError(
            "k.T must be greater than k.t"
        )

    open_value = _required_decimal_string(
        _required_mapping_field(kline, "o"),
        "k.o",
    )
    close_value = _required_decimal_string(
        _required_mapping_field(kline, "c"),
        "k.c",
    )
    high_value = _required_decimal_string(
        _required_mapping_field(kline, "h"),
        "k.h",
    )
    low_value = _required_decimal_string(
        _required_mapping_field(kline, "l"),
        "k.l",
    )
    volume_value = _required_decimal_string(
        _required_mapping_field(kline, "v"),
        "k.v",
    )
    trade_count = _required_mapping_field(kline, "n")
    trade_count = _required_nonnegative_int(trade_count, "k.n")

    closed = _required_mapping_field(kline, "x")
    if not isinstance(closed, bool):
        raise BinanceWebSocketPayloadError("k.x must be boolean")

    from .acquisition import ProviderRecord

    return ProviderRecord(
        provider_symbol=requested_symbol,
        provider=WEBSOCKET_PROVIDER,
        provider_market=WEBSOCKET_MARKET,
        provider_venue=WEBSOCKET_VENUE,
        observed_at=_ms_to_datetime(event_time_ms, "E"),
        available_at=None,
        candle_start=_ms_to_datetime(candle_start_ms, "k.t"),
        candle_end=_ms_to_datetime(candle_end_ms, "k.T"),
        timeframe=payload_interval.value,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume_value,
        quality="VALID",
        candle_completeness="COMPLETE" if closed else "PARTIAL",
        market_data_completeness="UNKNOWN",
        candle_state="CLOSED" if closed else "OPEN",
        finality="UNKNOWN",
        source_sequence=None,
        metadata={
            "binance_event_time_ms": event_time_ms,
            "binance_number_of_trades": trade_count,
            "binance_is_closed": closed,
        },
    )


class BinanceSpotWebSocketAdapter:
    """Bounded one-shot Binance Spot Kline WebSocket adapter."""

    CONSUMER_SCOPE = (
        "BinanceSpotWebSocketAdapter.listen pre-return availability boundary"
    )
    AVAILABILITY_EVIDENCE_REFERENCE = (
        "binance-spot-websocket.listen:pre-return-boundary"
    )

    def __init__(
        self,
        *,
        metadata: BinanceSpotInstrumentMetadata,
        connection_factory: WebSocketFactory = _default_websocket_factory,
        clock: Clock | None = None,
        consumer_handoff_clock: Clock | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not isinstance(metadata, BinanceSpotInstrumentMetadata):
            raise TypeError("metadata must be BinanceSpotInstrumentMetadata")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self.metadata = metadata
        self._connection_factory = connection_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._consumer_handoff_clock = consumer_handoff_clock
        self.timeout_seconds = timeout_seconds

    def build_stream_url(self, *, symbol: str, interval: str) -> str:
        return build_binance_spot_kline_stream_url(symbol, interval)

    def _mapping_for(
        self,
        *,
        symbol: str,
        instrument: Any,
    ) -> InstrumentMapper:
        metadata_record = self.metadata.fetch(symbol)
        return InstrumentMapper(
            [
                InstrumentMappingRule(
                    provider=WEBSOCKET_PROVIDER,
                    provider_symbol=metadata_record.provider_symbol,
                    provider_market=WEBSOCKET_MARKET,
                    provider_venue=WEBSOCKET_VENUE,
                    instrument=instrument,
                )
            ]
        )

    def _canonicalize_event(
        self,
        *,
        provider_record: Any,
        received_at: datetime,
        instrument_mapper: InstrumentMapper,
        interval: str,
    ) -> MarketData:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise BinanceWebSocketPayloadError(
                "clock must return a timezone-aware datetime"
            )

        parsed = parse_provider_payload(
            {
                "provider_symbol": provider_record.provider_symbol,
                "provider": provider_record.provider,
                "provider_market": provider_record.provider_market,
                "provider_venue": provider_record.provider_venue,
                "observed_at": provider_record.observed_at,
                "available_at": None,
                "candle_start": provider_record.candle_start,
                "candle_end": provider_record.candle_end,
                "timeframe": interval,
                "open": provider_record.open,
                "high": provider_record.high,
                "low": provider_record.low,
                "close": provider_record.close,
                "volume": provider_record.volume,
                "quality": provider_record.quality,
                "candle_completeness": provider_record.candle_completeness,
                "market_data_completeness": provider_record.market_data_completeness,
                "candle_state": provider_record.candle_state,
                "finality": provider_record.finality,
                "source_sequence": provider_record.source_sequence,
                "metadata": provider_record.metadata,
            }
        ).record

        try:
            normalized = normalize_provider_record(
                parsed,
                received_at=received_at,
                source_id=WEBSOCKET_SOURCE_ID,
                instrument_mapper=instrument_mapper,
            )
            return canonicalize_market_data(normalized.value)
        except (NormalizationError, ProviderPayloadError, CanonicalValidationError) as exc:
            raise BinanceWebSocketPayloadError(
                f"Binance WebSocket payload rejected by acquisition/canonical boundary: {exc}"
            ) from exc

    def listen(
        self,
        *,
        symbol: str,
        interval: str,
        max_messages: int = 1,
    ) -> list[MarketData]:
        """Receive at most max_messages events and return canonical MarketData."""
        if isinstance(max_messages, bool) or not isinstance(max_messages, int):
            raise ValueError("max_messages must be an integer >= 1")
        if max_messages < 1:
            raise ValueError("max_messages must be >= 1")

        validated_interval = validate_binance_spot_interval(interval)
        try:
            instrument = self.metadata.resolve_active(symbol)
        except (
            InstrumentNotFound,
            InstrumentMappingAmbiguous,
            InstrumentMetadataInvalid,
            InstrumentInactive,
        ) as exc:
            raise BinanceWebSocketPayloadError(
                f"Binance WebSocket instrument resolution failed: {exc}"
            ) from exc

        instrument_mapper = self._mapping_for(
            symbol=symbol,
            instrument=instrument,
        )
        url = self.build_stream_url(
            symbol=symbol,
            interval=validated_interval.value,
        )

        try:
            connection = self._connection_factory(
                url,
                self.timeout_seconds,
            )
        except (websocket.WebSocketException, socket.timeout, TimeoutError, OSError) as exc:
            raise BinanceWebSocketTransportError(
                f"Binance WebSocket connection failed: {exc}"
            ) from exc

        results: list[MarketData] = []
        try:
            for _ in range(max_messages):
                try:
                    message = connection.recv()
                    received_at = self._clock()
                except (
                    websocket.WebSocketTimeoutException,
                    socket.timeout,
                    TimeoutError,
                ) as exc:
                    raise BinanceWebSocketTransportError(
                        f"Binance WebSocket receive timeout: {exc}"
                    ) from exc
                except (
                    websocket.WebSocketConnectionClosedException,
                    websocket.WebSocketException,
                    OSError,
                ) as exc:
                    raise BinanceWebSocketTransportError(
                        f"Binance WebSocket connection closed or failed: {exc}"
                    ) from exc

                try:
                    provider_record = parse_binance_kline_event(
                        message,
                        requested_symbol=symbol,
                        requested_interval=validated_interval.value,
                    )
                    results.append(
                        self._canonicalize_event(
                            provider_record=provider_record,
                            received_at=received_at,
                            instrument_mapper=instrument_mapper,
                            interval=validated_interval.value,
                        )
                    )
                except BinanceWebSocketError:
                    raise
                except Exception as exc:
                    raise BinanceWebSocketPayloadError(
                        f"Binance WebSocket Kline processing failed: {exc}"
                    ) from exc

            if self._consumer_handoff_clock is None:
                evidence = AvailabilityEvidence.unknown()
            else:
                handoff_at = self._consumer_handoff_clock()
                evidence = AvailabilityEvidence.consumer_handoff(
                    received_at=results[0].received_at,
                    available_at=handoff_at,
                    evidence_reference=self.AVAILABILITY_EVIDENCE_REFERENCE,
                    consumer_scope=self.CONSUMER_SCOPE,
                )
                for item in results[1:]:
                    evidence.resolve(received_at=item.received_at)

            available_at = resolve_availability(
                evidence,
                received_at=results[0].received_at,
            )
            if available_at is None:
                return results
            return [
                replace(item, available_at=available_at)
                for item in results
            ]
        finally:
            try:
                connection.close()
            except Exception:
                pass


__all__ = [
    "RAW_STREAM_BASE_URL",
    "WEBSOCKET_MARKET",
    "WEBSOCKET_PROVIDER",
    "WEBSOCKET_SOURCE_ID",
    "WEBSOCKET_VENUE",
    "BinanceSpotWebSocketAdapter",
    "BinanceWebSocketError",
    "BinanceWebSocketPayloadError",
    "BinanceWebSocketProtocolError",
    "BinanceWebSocketTransportError",
    "WebSocketConnection",
    "WebSocketFactory",
    "build_binance_spot_kline_stream_url",
    "parse_binance_kline_event",
]
