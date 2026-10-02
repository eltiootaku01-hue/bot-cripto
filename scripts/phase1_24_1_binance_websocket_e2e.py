"""FASE 1.24.1 — Real Binance Spot WebSocket E2E verification.

This script intentionally exercises the production WebSocket adapter with a
real Binance public WebSocket connection and real ExchangeInfo metadata.
It does not implement another WebSocket client, parser, retry loop, or
reconnection mechanism.
"""

from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from bot_obrero.binance_instruments import BinanceSpotInstrumentMetadata
from bot_obrero.binance_websocket import (
    BinanceSpotWebSocketAdapter,
    BinanceWebSocketError,
    WebSocketConnection,
    WebSocketFactory,
    _default_websocket_factory,
    build_binance_spot_kline_stream_url,
)
from bot_obrero.market_data import CandleFinality, CandleState, DataCompleteness, MarketData
from bot_obrero.market_data_consumption import (
    MarketDataConsumptionPolicy,
    MarketDataConsumptionStatus,
    evaluate_market_data_consumption,
)

SYMBOL = "BTCUSDT"
INTERVAL = "1m"
MAX_MESSAGES = 3
ADAPTER_TIMEOUT_SECONDS = 10.0
GLOBAL_TIMEOUT_SECONDS = 60


class E2ETimeoutError(RuntimeError):
    """Global E2E wall-clock timeout."""


class RecordingConnection:
    """Transparent wrapper around the production WebSocket connection.

    It delegates all transport work to the connection returned by the
    production module's _default_websocket_factory and records only the raw
    messages needed to prove E -> observed_at.
    """

    def __init__(self, inner: WebSocketConnection) -> None:
        self._inner = inner
        self.messages: list[str | bytes] = []

    def recv(self) -> str | bytes:
        message = self._inner.recv()
        self.messages.append(message)
        return message

    def close(self) -> None:
        self._inner.close()


def _recording_factory(expected_url: str, timeout_seconds: float, state: dict[str, Any]) -> WebSocketConnection:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    connection = _default_websocket_factory(expected_url, timeout_seconds)
    state["connected_url"] = expected_url
    state["connection"] = RecordingConnection(connection)
    return state["connection"]


def _decode_json(message: str | bytes) -> dict[str, Any]:
    if isinstance(message, bytes):
        text = message.decode("utf-8")
    else:
        text = message
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise AssertionError("REAL E2E received a non-object WebSocket message")
    return decoded


def _assert_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AssertionError(f"{name} is not timezone-aware")
    if value.astimezone(timezone.utc) != value:
        raise AssertionError(f"{name} is not expressed in UTC")


def _event_time_from_raw(message: str | bytes) -> datetime:
    payload = _decode_json(message)
    event_time_ms = payload["E"]
    if isinstance(event_time_ms, bool) or not isinstance(event_time_ms, int):
        raise AssertionError("REAL E2E field E is not an integer millisecond timestamp")
    return datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)


def _validate_market_data(item: MarketData, *, raw_message: str | bytes) -> None:
    if item.instrument.instrument_id != "binance:SPOT:BTCUSDT":
        raise AssertionError(f"unexpected instrument_id: {item.instrument.instrument_id}")
    if item.instrument.symbol != "BTC/USDT":
        raise AssertionError(f"unexpected canonical symbol: {item.instrument.symbol}")
    if item.instrument.market != "SPOT":
        raise AssertionError(f"unexpected instrument market: {item.instrument.market}")

    if item.source.source_id != "binance-spot-websocket":
        raise AssertionError(f"unexpected source_id: {item.source.source_id}")
    if item.source.provider != "binance":
        raise AssertionError(f"unexpected source provider: {item.source.provider}")
    if item.source.venue != "BINANCE":
        raise AssertionError(f"unexpected source venue: {item.source.venue}")

    if item.payload.timeframe != INTERVAL:
        raise AssertionError(f"unexpected timeframe: {item.payload.timeframe}")

    for name, value in (
        ("open", item.payload.open),
        ("high", item.payload.high),
        ("low", item.payload.low),
        ("close", item.payload.close),
        ("volume", item.payload.volume),
    ):
        if not isinstance(value, Decimal):
            raise AssertionError(f"payload.{name} is not Decimal")

    for name, value in (
        ("observed_at", item.observed_at),
        ("received_at", item.received_at),
        ("payload.start", item.payload.start),
        ("payload.end", item.payload.end),
    ):
        _assert_utc(value, name)

    raw_event_time = _event_time_from_raw(raw_message)
    if item.observed_at != raw_event_time:
        raise AssertionError(
            "observed_at does not equal event field E from the same real Binance message: "
            f"observed_at={item.observed_at.isoformat()} E={raw_event_time.isoformat()}"
        )

    if item.received_at == item.observed_at and item.received_at is not None:
        # Equality is possible in theory only at millisecond precision, but the
        # important invariant is that received_at comes from the adapter clock.
        pass

    expected_identity = (
        "binance:SPOT:BTCUSDT",
        INTERVAL,
        item.payload.start,
    )
    if item.candle_identity != expected_identity:
        raise AssertionError(
            f"unexpected candle_identity: {item.candle_identity!r}"
        )

    if item.payload.candle_state is CandleState.OPEN:
        expected_completeness = DataCompleteness.PARTIAL
    elif item.payload.candle_state is CandleState.CLOSED:
        expected_completeness = DataCompleteness.COMPLETE
    else:
        raise AssertionError("real Binance Kline has unknown CandleState")

    if item.payload.completeness is not expected_completeness:
        raise AssertionError(
            f"candle completeness mismatch: state={item.payload.candle_state.value} "
            f"completeness={item.payload.completeness.value}"
        )

    if item.payload.finality is not CandleFinality.UNKNOWN:
        raise AssertionError(
            f"finality was inferred unexpectedly: {item.payload.finality.value}"
        )

    if item.completeness is not DataCompleteness.UNKNOWN:
        raise AssertionError(
            f"market-data completeness was inferred unexpectedly: {item.completeness.value}"
        )

    if item.available_at is not None:
        raise AssertionError(
            f"available_at must remain None without consumer handoff: {item.available_at!r}"
        )


def _print_evidence(
    *,
    endpoint: str,
    items: list[MarketData],
    raw_messages: list[str | bytes],
    consumption_status: MarketDataConsumptionStatus,
) -> None:
    first = items[0]
    first_raw = _decode_json(raw_messages[0])
    first_kline = first_raw["k"]

    print("FASE 1.24.1 — REAL BINANCE SPOT WEBSOCKET E2E")
    print()
    print(f"endpoint={endpoint}")
    print(f"symbol={SYMBOL}")
    print(f"interval={INTERVAL}")
    print()
    print("CONNECTED=YES")
    print(f"MESSAGE_COUNT={len(items)}")
    print(f"EVENT_TYPE={first_raw.get('e')}")
    print(f"EVENT_TIME={first.observed_at.isoformat()}")
    print(f"RAW_E={first_raw.get('E')}")
    print(f"RECEIVED_AT={first.received_at.isoformat()}")
    print()
    print(f"INSTRUMENT_ID={first.instrument.instrument_id}")
    print(f"SYMBOL={first.instrument.symbol}")
    print("MARKET=SPOT")
    print(f"PROVIDER={first.source.provider}")
    print(f"VENUE={first.source.venue}")
    print(f"SOURCE_ID={first.source.source_id}")
    print()
    print(f"CANDLE_START={first.payload.start.isoformat()}")
    print(f"CANDLE_END={first.payload.end.isoformat()}")
    print(f"CANDLE_STATE={first.payload.candle_state.value}")
    print(f"CANDLE_COMPLETENESS={first.payload.completeness.value}")
    print(f"FINALITY={first.payload.finality.value}")
    print(f"AVAILABLE_AT={first.available_at!r}")
    print(f"MARKET_DATA_ID={first.market_data_id}")
    print(f"CANDLE_IDENTITY={first.candle_identity!r}")
    print(
        "RAW_X="
        + str(first_kline.get("x"))
    )
    print(
        "OHLCV_DECIMAL_TYPES="
        + str(
            all(
                isinstance(value, Decimal)
                for value in (
                    first.payload.open,
                    first.payload.high,
                    first.payload.low,
                    first.payload.close,
                    first.payload.volume,
                )
            )
        )
    )
    print()
    print(f"CONSUMPTION_STATUS={consumption_status.value}")
    print(
        "CONSUMPTION_DECISION_TIMESTAMP="
        + datetime.now(timezone.utc).isoformat()
    )
    print()
    print("LIMITATIONS=connectivity-and-real-receipt-only; no reconnect; no retry; no resilience")
    print("RESULT: REAL E2E PASS")


def main() -> int:
    state: dict[str, Any] = {}

    def _timeout_handler(signum: int, frame: Any) -> None:
        raise E2ETimeoutError(
            f"REAL E2E FAIL: global timeout of {GLOBAL_TIMEOUT_SECONDS}s exceeded"
        )

    if not hasattr(signal, "SIGALRM"):
        raise RuntimeError("FASE 1.24.1 requires a POSIX runner with SIGALRM")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(GLOBAL_TIMEOUT_SECONDS)

    try:
        endpoint = build_binance_spot_kline_stream_url(SYMBOL, INTERVAL)

        metadata = BinanceSpotInstrumentMetadata()
        # This call is intentionally real: no injected HTTP getter is supplied.
        instrument = metadata.resolve_active(SYMBOL)
        if instrument.instrument_id != "binance:SPOT:BTCUSDT":
            raise AssertionError(
                f"real ExchangeInfo resolved unexpected instrument: {instrument.instrument_id}"
            )
        if instrument.symbol != "BTC/USDT":
            raise AssertionError(
                f"real ExchangeInfo resolved unexpected symbol: {instrument.symbol}"
            )

        def factory(url: str, timeout_seconds: float) -> WebSocketConnection:
            if url != endpoint:
                raise AssertionError(
                    f"adapter generated unexpected endpoint: expected={endpoint} received={url}"
                )
            return _recording_factory(url, timeout_seconds, state)

        adapter = BinanceSpotWebSocketAdapter(
            metadata=metadata,
            connection_factory=factory,
            timeout_seconds=ADAPTER_TIMEOUT_SECONDS,
        )

        print("Starting real Binance Spot WebSocket E2E...")
        started = time.monotonic()
        items = adapter.listen(
            symbol=SYMBOL,
            interval=INTERVAL,
            max_messages=MAX_MESSAGES,
        )
        elapsed = time.monotonic() - started

        if not items:
            raise AssertionError("REAL E2E FAIL: no MarketData was produced")
        if len(items) > MAX_MESSAGES:
            raise AssertionError(
                f"REAL E2E FAIL: adapter produced {len(items)} messages, max={MAX_MESSAGES}"
            )

        connected_url = state.get("connected_url")
        if connected_url != endpoint:
            raise AssertionError(
                f"REAL E2E FAIL: connection URL mismatch: {connected_url!r}"
            )

        raw_messages = state.get("connection").messages if state.get("connection") else []
        if len(raw_messages) != len(items):
            raise AssertionError(
                "REAL E2E FAIL: recorded real message count does not match MarketData count"
            )

        for item, raw in zip(items, raw_messages, strict=True):
            _validate_market_data(item, raw_message=raw)

        first = items[0]
        decision_timestamp = max(
            datetime.now(timezone.utc),
            first.received_at,
            first.observed_at,
        )
        consumption = evaluate_market_data_consumption(
            first,
            decision_timestamp=decision_timestamp,
            policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
        )
        if consumption.status is not MarketDataConsumptionStatus.UNKNOWN:
            raise AssertionError(
                f"expected UNKNOWN consumption status, got {consumption.status.value}"
            )

        print(f"ELAPSED_SECONDS={elapsed:.3f}")
        _print_evidence(
            endpoint=endpoint,
            items=items,
            raw_messages=raw_messages,
            consumption_status=consumption.status,
        )
        return 0
    except (BinanceWebSocketError, E2ETimeoutError, AssertionError, KeyError, ValueError, OSError) as exc:
        print(f"RESULT: REAL E2E FAIL — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"RESULT: REAL E2E FAIL — unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
