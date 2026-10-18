"""Bounded public Binance Spot book-ticker WebSocket adapter.

This adapter captures best-bid/ask observations only. It never places orders.
The Spot bookTicker payload has no exchange event timestamp in its documented
shape, so observed_at is the local receive time unless an optional E field is
actually supplied by the provider. The raw update ID and receive/handoff clocks
are preserved so consumers do not invent historical availability.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

import websocket

from .binance_websocket import (
    RAW_STREAM_BASE_URL,
    BinanceWebSocketError,
    BinanceWebSocketPayloadError,
    BinanceWebSocketProtocolError,
    BinanceWebSocketServerShutdown,
    BinanceWebSocketTransportError,
    WebSocketFactory,
    _decode_message,
    _default_websocket_factory,
)

APPROVED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
BOOK_TICKER_SOURCE = "binance-public:spot-websocket-bookTicker"
BOOK_TICKER_OBSERVATION_TYPE = "BINANCE_SPOT_BOOK_TICKER"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class BinanceBookTicker:
    """Immutable normalized quote observation from one public Spot event."""

    symbol: str
    update_id: int
    bid: str
    bid_quantity: str
    ask: str
    ask_quantity: str
    received_at: datetime
    available_at: datetime
    observed_at: datetime
    provider_event_time: datetime | None
    source: str
    observation_type: str
    evidence_hash: str


def _aware_utc(raw: datetime, field: str) -> datetime:
    if not isinstance(raw, datetime) or raw.tzinfo is None or raw.utcoffset() is None:
        raise BinanceWebSocketPayloadError(f"{field} must be timezone-aware")
    return raw.astimezone(timezone.utc)


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _decimal_string(value: Any, field: str, *, positive: bool = False) -> str:
    if type(value) is not str or not value.strip():
        raise BinanceWebSocketPayloadError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise BinanceWebSocketPayloadError(f"{field} is not a valid decimal string") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (not positive and parsed < 0):
        raise BinanceWebSocketPayloadError(f"{field} is outside its permitted numeric range")
    return _canonical_decimal(parsed)


def _event_time(value: Any) -> datetime:
    if type(value) is not int or value < 0:
        raise BinanceWebSocketPayloadError("E must be a non-negative integer millisecond timestamp")
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise BinanceWebSocketPayloadError("E is outside the supported timestamp range") from exc


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_binance_spot_book_ticker_stream_url(symbol: str) -> str:
    """Build a raw Spot bookTicker URL for the approved BTC/ETH pair only."""
    if type(symbol) is not str or symbol not in APPROVED_SYMBOLS or not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("bookTicker capture requires the approved BTCUSDT/ETHUSDT symbol")
    return f"{RAW_STREAM_BASE_URL}/{symbol.lower()}@bookTicker"


def parse_binance_book_ticker_event(
    event: Any,
    *,
    requested_symbol: str,
    received_at: datetime,
    available_at: datetime,
) -> BinanceBookTicker:
    """Validate one public best-bid/ask message and bind it to local receive/handoff clocks."""
    if not isinstance(event, Mapping):
        raise BinanceWebSocketProtocolError("Binance bookTicker message must be an object")
    if requested_symbol not in APPROVED_SYMBOLS:
        raise BinanceWebSocketPayloadError("requested symbol is outside the approved BTC/ETH pair")
    if event.get("e") == "serverShutdown":
        raise BinanceWebSocketServerShutdown("Binance sent serverShutdown; reconnect is required")
    event_type = event.get("e")
    if "e" in event and event_type is not None and event_type != "bookTicker":
        raise BinanceWebSocketProtocolError("unexpected Binance WebSocket event type")
    symbol = event.get("s")
    if type(symbol) is not str or symbol != requested_symbol:
        raise BinanceWebSocketPayloadError("bookTicker symbol mismatch")
    update_id = event.get("u")
    if type(update_id) is not int or update_id < 0:
        raise BinanceWebSocketPayloadError("u must be a non-negative integer update ID")

    bid = _decimal_string(event.get("b"), "b", positive=True)
    bid_quantity = _decimal_string(event.get("B"), "B")
    ask = _decimal_string(event.get("a"), "a", positive=True)
    ask_quantity = _decimal_string(event.get("A"), "A")
    if Decimal(ask) < Decimal(bid):
        raise BinanceWebSocketPayloadError("bookTicker ask is below bid")

    received = _aware_utc(received_at, "received_at")
    available = _aware_utc(available_at, "available_at")
    provider_event_time = _event_time(event["E"]) if "E" in event else None
    observed = provider_event_time or received
    if observed > received or received > available:
        raise BinanceWebSocketPayloadError("bookTicker event/receive/availability ordering is invalid")

    payload = {
        "schema": "prospective-book-ticker-v1",
        "symbol": symbol,
        "update_id": update_id,
        "bid": bid,
        "bid_quantity": bid_quantity,
        "ask": ask,
        "ask_quantity": ask_quantity,
        "observed_at": observed.isoformat(),
        "received_at": received.isoformat(),
        "available_at": available.isoformat(),
        "provider_event_time": None if provider_event_time is None else provider_event_time.isoformat(),
        "source": BOOK_TICKER_SOURCE,
        "observation_type": BOOK_TICKER_OBSERVATION_TYPE,
        "venue": "BINANCE",
        "market": "SPOT",
    }
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return BinanceBookTicker(
        symbol=symbol,
        update_id=update_id,
        bid=bid,
        bid_quantity=bid_quantity,
        ask=ask,
        ask_quantity=ask_quantity,
        received_at=received,
        available_at=available,
        observed_at=observed,
        provider_event_time=provider_event_time,
        source=BOOK_TICKER_SOURCE,
        observation_type=BOOK_TICKER_OBSERVATION_TYPE,
        evidence_hash=digest,
    )


class BinanceSpotBookTickerWebSocketAdapter:
    """Bounded injectable WebSocket boundary for public Spot best-bid/ask events."""

    def __init__(
        self,
        *,
        connection_factory: WebSocketFactory = _default_websocket_factory,
        clock: Clock | None = None,
        consumer_handoff_clock: Clock | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._connection_factory = connection_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._consumer_handoff_clock = consumer_handoff_clock
        self.timeout_seconds = timeout_seconds

    def listen(
        self,
        *,
        symbol: str,
        max_messages: int = 1,
        on_item: Callable[[BinanceBookTicker], None],
        should_stop: Callable[[], bool] | None = None,
        retain_results: bool = True,
    ) -> list[BinanceBookTicker]:
        """Receive a bounded batch and hand each quote to its sink immediately.

        Set retain_results=False for long-running durable captures so the adapter
        does not accumulate a second in-memory copy of every streamed quote.
        """
        if symbol not in APPROVED_SYMBOLS:
            raise ValueError("bookTicker capture requires the approved BTCUSDT/ETHUSDT symbol")
        if isinstance(max_messages, bool) or type(max_messages) is not int or max_messages < 1:
            raise ValueError("max_messages must be an integer >= 1")
        if not callable(on_item):
            raise ValueError("on_item must be callable")
        if type(retain_results) is not bool:
            raise ValueError("retain_results must be a boolean")
        if self._consumer_handoff_clock is None:
            raise ValueError("bookTicker capture requires an explicit consumer_handoff_clock")
        if should_stop is not None and not callable(should_stop):
            raise ValueError("should_stop must be callable when supplied")

        url = build_binance_spot_book_ticker_stream_url(symbol)
        try:
            connection = self._connection_factory(url, self.timeout_seconds)
        except (websocket.WebSocketException, socket.timeout, TimeoutError, OSError) as exc:
            raise BinanceWebSocketTransportError(f"Binance bookTicker connection failed: {exc}") from exc

        results: list[BinanceBookTicker] = []
        try:
            for _ in range(max_messages):
                if should_stop is not None and should_stop():
                    break
                try:
                    message = connection.recv()
                    received = self._clock()
                except (websocket.WebSocketTimeoutException, socket.timeout, TimeoutError) as exc:
                    raise BinanceWebSocketTransportError(f"Binance bookTicker receive timeout: {exc}") from exc
                except (websocket.WebSocketConnectionClosedException, websocket.WebSocketException, OSError) as exc:
                    raise BinanceWebSocketTransportError(f"Binance bookTicker connection closed or failed: {exc}") from exc
                decoded = _decode_message(message)
                if decoded.get("e") == "serverShutdown":
                    raise BinanceWebSocketServerShutdown("Binance sent serverShutdown; reconnect is required")
                handoff = self._consumer_handoff_clock()
                item = parse_binance_book_ticker_event(
                    decoded,
                    requested_symbol=symbol,
                    received_at=received,
                    available_at=handoff,
                )
                # Sink exceptions intentionally propagate; a quote must not be acknowledged as stored if persistence failed.
                on_item(item)
                if retain_results:
                    results.append(item)
            return results
        finally:
            try:
                connection.close()
            except Exception:
                pass


__all__ = [
    "APPROVED_SYMBOLS",
    "BOOK_TICKER_OBSERVATION_TYPE",
    "BOOK_TICKER_SOURCE",
    "BinanceBookTicker",
    "BinanceSpotBookTickerWebSocketAdapter",
    "build_binance_spot_book_ticker_stream_url",
    "parse_binance_book_ticker_event",
]
