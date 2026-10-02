import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import websocket

from bot_obrero.binance_instruments import BinanceSpotInstrumentMetadata, InstrumentInactive
from bot_obrero.binance_intervals import validate_binance_spot_interval
from bot_obrero.binance_websocket import (
    RAW_STREAM_BASE_URL,
    BinanceSpotWebSocketAdapter,
    BinanceWebSocketPayloadError,
    BinanceWebSocketProtocolError,
    BinanceWebSocketTransportError,
    build_binance_spot_kline_stream_url,
)
from bot_obrero.market_data import (
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
)
from bot_obrero.market_data_consumption import (
    MarketDataConsumptionPolicy,
    MarketDataConsumptionStatus,
    evaluate_market_data_consumption,
)


RECEIVED_1 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
RECEIVED_2 = RECEIVED_1 + timedelta(seconds=1)
RECEIVED_3 = RECEIVED_1 + timedelta(seconds=2)
HANDOFF = RECEIVED_1 + timedelta(seconds=3)
START_MS = 1790942400000
END_MS = START_MS + 59_999
EVENT_MS_1 = START_MS + 1_000
EVENT_MS_2 = START_MS + 2_000
EVENT_MS_3 = START_MS + 3_000


def exchange_info_payload(
    *,
    symbol="BTCUSDT",
    base="BTC",
    quote="USDT",
    status="TRADING",
    spot_allowed=True,
):
    return {
        "timezone": "UTC",
        "serverTime": 1790920487690,
        "symbols": [
            {
                "symbol": symbol,
                "status": status,
                "baseAsset": base,
                "quoteAsset": quote,
                "isSpotTradingAllowed": spot_allowed,
            }
        ],
    }


def metadata_getter(payload):
    body = json.dumps(payload).encode()

    def getter(url, headers, timeout):
        return 200, {"Content-Type": "application/json"}, body

    return getter


def make_metadata(*, payload=None):
    return BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(
            payload or exchange_info_payload()
        )
    )


def kline_message(
    *,
    event_time=EVENT_MS_1,
    symbol="BTCUSDT",
    interval="1m",
    start=START_MS,
    end=END_MS,
    open_value="123.4500",
    high_value="124.0000",
    low_value="123.0000",
    close_value="123.7500",
    volume="0.0100",
    trade_count=42,
    closed=False,
):
    return json.dumps(
        {
            "e": "kline",
            "E": event_time,
            "s": symbol,
            "k": {
                "t": start,
                "T": end,
                "s": symbol,
                "i": interval,
                "f": 100,
                "L": 200,
                "o": open_value,
                "c": close_value,
                "h": high_value,
                "l": low_value,
                "v": volume,
                "n": trade_count,
                "x": closed,
                "q": "1.2345000",
                "V": "0.0050",
                "Q": "0.6172500",
                "B": "0",
            },
        }
    )


class FakeConnection:
    def __init__(self, messages=None, *, error=None):
        self.messages = list(messages or [])
        self.error = error
        self.closed = False
        self.recv_calls = 0

    def recv(self):
        self.recv_calls += 1
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        if not self.messages:
            raise websocket.WebSocketConnectionClosedException("fake closed")
        return self.messages.pop(0)

    def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        return self.connection


def adapter(
    connection,
    *,
    clock=lambda: RECEIVED_1,
    handoff_clock=None,
    timeout=4.5,
    metadata=None,
):
    factory = FakeFactory(connection)
    instance = BinanceSpotWebSocketAdapter(
        metadata=metadata or make_metadata(),
        connection_factory=factory,
        clock=clock,
        consumer_handoff_clock=handoff_clock,
        timeout_seconds=timeout,
    )
    return instance, factory


def test_raw_stream_url_uses_lowercase_symbol_and_exact_interval():
    assert (
        build_binance_spot_kline_stream_url("BTCUSDT", "1m")
        == f"{RAW_STREAM_BASE_URL}/btcusdt@kline_1m"
    )


def test_raw_stream_url_reuses_provider_interval_validation():
    with pytest.raises(ValueError):
        build_binance_spot_kline_stream_url("BTCUSDT", "1M ")
    assert validate_binance_spot_interval("1M").value == "1M"


def test_valid_open_event_produces_canonical_market_data():
    connection = FakeConnection([kline_message(closed=False)])
    ws, factory = adapter(connection)
    result = ws.listen(symbol="BTCUSDT", interval="1m", max_messages=1)

    assert len(result) == 1
    item = result[0]
    assert item.source.source_id == "binance-spot-websocket"
    assert item.source.provider == "binance"
    assert item.source.venue == "BINANCE"
    assert item.instrument.instrument_id == "binance:SPOT:BTCUSDT"
    assert item.instrument.symbol == "BTC/USDT"
    assert item.payload.open == Decimal("123.4500")
    assert item.payload.high == Decimal("124.0000")
    assert item.payload.low == Decimal("123.0000")
    assert item.payload.close == Decimal("123.7500")
    assert item.payload.volume == Decimal("0.0100")
    assert item.payload.candle_state is CandleState.OPEN
    assert item.payload.completeness is DataCompleteness.PARTIAL
    assert item.payload.finality is CandleFinality.UNKNOWN
    assert item.quality is DataQuality.VALID
    assert item.completeness is DataCompleteness.UNKNOWN
    assert item.source_sequence is None
    assert factory.calls == [
        (
            f"{RAW_STREAM_BASE_URL}/btcusdt@kline_1m",
            4.5,
        )
    ]
    assert connection.closed


def test_valid_closed_event_maps_closed_and_complete_but_not_final():
    connection = FakeConnection(
        [kline_message(event_time=EVENT_MS_2, closed=True)]
    )
    ws, _ = adapter(connection)
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    assert item.payload.candle_state is CandleState.CLOSED
    assert item.payload.completeness is DataCompleteness.COMPLETE
    assert item.payload.finality is CandleFinality.UNKNOWN


def test_event_time_is_observed_at_and_receive_clock_is_received_at():
    connection = FakeConnection([kline_message(event_time=EVENT_MS_2)])
    ws, _ = adapter(
        connection,
        clock=lambda: RECEIVED_2,
    )
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    assert item.observed_at == datetime.fromtimestamp(
        EVENT_MS_2 / 1000, tz=timezone.utc
    )
    assert item.observed_at != item.payload.start
    assert item.received_at == RECEIVED_2
    assert item.received_at.tzinfo == timezone.utc


def test_received_clock_must_be_timezone_aware():
    connection = FakeConnection([kline_message()])
    ws, _ = adapter(
        connection,
        clock=lambda: datetime(2026, 10, 2, 12, 0),
    )
    with pytest.raises(BinanceWebSocketPayloadError, match="timezone-aware"):
        ws.listen(symbol="BTCUSDT", interval="1m")


@pytest.mark.parametrize(
    "field",
    ["e", "E", "s", "k"],
)
def test_missing_required_event_fields_are_rejected(field):
    payload = json.loads(kline_message())
    payload.pop(field)
    ws, _ = adapter(FakeConnection([json.dumps(payload)]))
    with pytest.raises(BinanceWebSocketPayloadError):
        ws.listen(symbol="BTCUSDT", interval="1m")


@pytest.mark.parametrize(
    "field",
    ["t", "T", "s", "i", "o", "c", "h", "l", "v", "n", "x"],
)
def test_missing_required_kline_fields_are_rejected(field):
    payload = json.loads(kline_message())
    payload["k"].pop(field)
    ws, _ = adapter(FakeConnection([json.dumps(payload)]))
    with pytest.raises(BinanceWebSocketPayloadError):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_invalid_json_is_protocol_error():
    ws, _ = adapter(FakeConnection(["not-json"]))
    with pytest.raises(BinanceWebSocketProtocolError):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_wrong_event_type_is_protocol_error():
    payload = json.loads(kline_message())
    payload["e"] = "trade"
    ws, _ = adapter(FakeConnection([json.dumps(payload)]))
    with pytest.raises(BinanceWebSocketProtocolError):
        ws.listen(symbol="BTCUSDT", interval="1m")


@pytest.mark.parametrize(
    "field,value",
    [
        ("E", True),
        ("s", 123),
        ("t", "bad"),
        ("T", "bad"),
        ("n", "42"),
        ("x", "false"),
        ("o", 123.45),
        ("c", 123.45),
        ("h", 123.45),
        ("l", 123.45),
        ("v", 123.45),
    ],
)
def test_wrong_field_types_are_rejected(field, value):
    payload = json.loads(kline_message())
    target = payload if field in {"E", "s"} else payload["k"]
    target[field] = value
    ws, _ = adapter(FakeConnection([json.dumps(payload)]))
    with pytest.raises(BinanceWebSocketPayloadError):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_interval_mismatch_is_rejected():
    payload = json.loads(kline_message(interval="5m"))
    ws, _ = adapter(FakeConnection([json.dumps(payload)]))
    with pytest.raises(BinanceWebSocketPayloadError, match="interval mismatch"):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_symbol_mismatch_is_rejected():
    payload = json.loads(kline_message(symbol="ETHUSDT"))
    ws, _ = adapter(FakeConnection([json.dumps(payload)]))
    with pytest.raises(BinanceWebSocketPayloadError, match="symbol mismatch"):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_nested_kline_symbol_mismatch_is_rejected():
    payload = json.loads(kline_message())
    payload["k"]["s"] = "ETHUSDT"
    ws, _ = adapter(FakeConnection([json.dumps(payload)]))
    with pytest.raises(BinanceWebSocketPayloadError, match="Kline symbol mismatch"):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_decimal_ohlcv_are_exact_and_canonicalized():
    connection = FakeConnection([kline_message()])
    ws, _ = adapter(connection)
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    assert isinstance(item.payload.open, Decimal)
    assert isinstance(item.payload.close, Decimal)
    assert isinstance(item.payload.high, Decimal)
    assert isinstance(item.payload.low, Decimal)
    assert isinstance(item.payload.volume, Decimal)


def test_invalid_ohlcv_relationship_is_rejected_by_canonical_boundary():
    connection = FakeConnection(
        [kline_message(high_value="123.5000", close_value="124.0000")]
    )
    ws, _ = adapter(connection)
    with pytest.raises(BinanceWebSocketPayloadError, match="canonical"):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_inactive_instrument_is_rejected_before_websocket_connection():
    metadata = make_metadata(
        payload=exchange_info_payload(status="HALT")
    )
    connection = FakeConnection([kline_message()])
    ws, factory = adapter(connection, metadata=metadata)

    with pytest.raises(BinanceWebSocketPayloadError):
        ws.listen(symbol="BTCUSDT", interval="1m")

    assert factory.calls == []


def test_metadata_resolution_uses_exchangeinfo_identity_not_manual_symbol_parsing():
    metadata = make_metadata(
        payload=exchange_info_payload(
            symbol="BTCUSDT",
            base="BASE",
            quote="QUOTE",
        )
    )
    ws, _ = adapter(FakeConnection([kline_message()]), metadata=metadata)
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    assert item.instrument.instrument_id == "binance:SPOT:BTCUSDT"
    assert item.instrument.symbol == "BASE/QUOTE"


def test_unknown_availability_is_preserved_without_handoff_clock():
    ws, _ = adapter(FakeConnection([kline_message()]))
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    assert item.available_at is None


def test_consumer_handoff_sets_explicit_availability_after_canonicalization():
    observed = {"returned": False}

    def handoff_clock():
        assert observed["returned"] is False
        return HANDOFF

    ws, _ = adapter(
        FakeConnection([kline_message()]),
        handoff_clock=handoff_clock,
    )
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]
    observed["returned"] = True

    assert item.available_at == HANDOFF
    assert item.available_at > item.received_at


def test_consumer_handoff_must_not_precede_any_received_message():
    ws, _ = adapter(
        FakeConnection([kline_message()]),
        clock=lambda: RECEIVED_2,
        handoff_clock=lambda: RECEIVED_1,
    )
    with pytest.raises(Exception, match="cannot precede"):
        ws.listen(symbol="BTCUSDT", interval="1m")


def test_websocket_output_is_compatible_with_market_data_consumption():
    ws, _ = adapter(
        FakeConnection([kline_message()]),
        handoff_clock=lambda: HANDOFF,
    )
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    accepted = evaluate_market_data_consumption(
        item,
        decision_timestamp=HANDOFF,
        policy=MarketDataConsumptionPolicy.REQUIRE_AVAILABLE,
    )
    assert accepted.status is MarketDataConsumptionStatus.ACCEPTED


def test_unknown_availability_remains_unknown_for_consumption():
    ws, _ = adapter(FakeConnection([kline_message()]))
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    result = evaluate_market_data_consumption(
        item,
        decision_timestamp=RECEIVED_2,
        policy=MarketDataConsumptionPolicy.ALLOW_UNKNOWN,
    )
    assert result.status is MarketDataConsumptionStatus.UNKNOWN
    assert result.reason == "AVAILABLE_AT_UNKNOWN"


def test_same_candle_identity_may_receive_multiple_live_updates():
    messages = [
        kline_message(event_time=EVENT_MS_1, close_value="123.6000", closed=False),
        kline_message(event_time=EVENT_MS_2, close_value="123.7000", closed=False),
        kline_message(event_time=EVENT_MS_3, close_value="123.7500", closed=True),
    ]
    received = iter([RECEIVED_1, RECEIVED_2, RECEIVED_3])
    ws, _ = adapter(
        FakeConnection(messages),
        clock=lambda: next(received),
    )
    results = ws.listen(symbol="BTCUSDT", interval="1m", max_messages=3)

    assert [item.payload.candle_state for item in results] == [
        CandleState.OPEN,
        CandleState.OPEN,
        CandleState.CLOSED,
    ]
    assert [item.payload.completeness for item in results] == [
        DataCompleteness.PARTIAL,
        DataCompleteness.PARTIAL,
        DataCompleteness.COMPLETE,
    ]
    assert len({item.market_data_id for item in results}) == 3
    assert len({item.candle_identity for item in results}) == 1


def test_max_messages_is_a_hard_bound_and_no_background_loop_is_created():
    messages = [kline_message(event_time=EVENT_MS_1) for _ in range(5)]
    connection = FakeConnection(messages)
    ws, _ = adapter(connection)
    results = ws.listen(symbol="BTCUSDT", interval="1m", max_messages=2)

    assert len(results) == 2
    assert connection.recv_calls == 2
    assert len(connection.messages) == 3


def test_connection_timeout_is_classified_and_not_retried():
    connection = FakeConnection(
        error=websocket.WebSocketTimeoutException("simulated timeout")
    )
    ws, factory = adapter(connection)

    with pytest.raises(BinanceWebSocketTransportError, match="timeout"):
        ws.listen(symbol="BTCUSDT", interval="1m")

    assert len(factory.calls) == 1
    assert connection.closed


def test_connection_closed_is_classified_and_not_retried():
    connection = FakeConnection(
        error=websocket.WebSocketConnectionClosedException("simulated close")
    )
    ws, factory = adapter(connection)

    with pytest.raises(BinanceWebSocketTransportError, match="closed"):
        ws.listen(symbol="BTCUSDT", interval="1m")

    assert len(factory.calls) == 1
    assert connection.closed


def test_websocket_transport_error_is_not_retried():
    connection = FakeConnection(
        error=websocket.WebSocketException("simulated websocket failure")
    )
    ws, factory = adapter(connection)

    with pytest.raises(BinanceWebSocketTransportError):
        ws.listen(symbol="BTCUSDT", interval="1m")

    assert len(factory.calls) == 1


def test_empty_or_invalid_max_messages_is_rejected_before_connection():
    connection = FakeConnection([kline_message()])
    ws, factory = adapter(connection)

    for value in [0, -1, True, "1"]:
        with pytest.raises(ValueError):
            ws.listen(symbol="BTCUSDT", interval="1m", max_messages=value)
    assert factory.calls == []


def test_consumption_guard_still_rejects_decision_before_available_at():
    ws, _ = adapter(
        FakeConnection([kline_message()]),
        handoff_clock=lambda: HANDOFF,
    )
    item = ws.listen(symbol="BTCUSDT", interval="1m")[0]

    with pytest.raises(ValueError, match="look-ahead"):
        item.evidence_at(RECEIVED_2)
