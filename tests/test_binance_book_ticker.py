from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.binance_book_ticker import (
    BOOK_TICKER_OBSERVATION_TYPE,
    BOOK_TICKER_SOURCE,
    BinanceSpotBookTickerWebSocketAdapter,
    build_binance_spot_book_ticker_stream_url,
    parse_binance_book_ticker_event,
)
from bot_obrero.binance_websocket import (
    BinanceWebSocketPayloadError,
    BinanceWebSocketServerShutdown,
)

UTC = timezone.utc
START = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)


def payload(**overrides):
    event = {
        "u": 123456,
        "s": "BTCUSDT",
        "b": "65000.10000000",
        "B": "0.12000000",
        "a": "65000.20000000",
        "A": "0.34000000",
    }
    event.update(overrides)
    return event


def test_book_ticker_url_is_restricted_to_approved_spot_symbols():
    assert build_binance_spot_book_ticker_stream_url("BTCUSDT") == (
        "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
    )
    assert build_binance_spot_book_ticker_stream_url("ETHUSDT").endswith("/ethusdt@bookTicker")
    for symbol in ("BNBUSDT", "btcusdt", "BTC/USDT", "", "BTCUSDT?x=1"):
        with pytest.raises(ValueError):
            build_binance_spot_book_ticker_stream_url(symbol)


def test_parser_normalizes_decimal_strings_and_hash_binds_timestamps():
    available = START + timedelta(milliseconds=3)
    event = payload(E=int(START.timestamp() * 1000))

    quote = parse_binance_book_ticker_event(
        event,
        requested_symbol="BTCUSDT",
        received_at=START + timedelta(milliseconds=1),
        available_at=available,
    )

    assert quote.bid == "65000.1"
    assert quote.bid_quantity == "0.12"
    assert quote.ask == "65000.2"
    assert quote.ask_quantity == "0.34"
    assert quote.provider_event_time == START
    assert quote.observed_at == START
    assert quote.received_at == START + timedelta(milliseconds=1)
    assert quote.available_at == available
    assert quote.source == BOOK_TICKER_SOURCE
    assert quote.observation_type == BOOK_TICKER_OBSERVATION_TYPE
    assert len(quote.evidence_hash) == 64


def test_parser_does_not_invent_exchange_event_time_when_not_provided():
    received = START + timedelta(seconds=1)
    quote = parse_binance_book_ticker_event(
        payload(),
        requested_symbol="BTCUSDT",
        received_at=received,
        available_at=received + timedelta(milliseconds=1),
    )
    assert quote.provider_event_time is None
    assert quote.observed_at == received


@pytest.mark.parametrize(
    ("event", "symbol", "received", "available", "error"),
    [
        (payload(u=True), "BTCUSDT", START, START + timedelta(seconds=1), "update ID"),
        (payload(s="ETHUSDT"), "BTCUSDT", START, START + timedelta(seconds=1), "symbol mismatch"),
        (payload(b="not-a-price"), "BTCUSDT", START, START + timedelta(seconds=1), "decimal string"),
        (payload(b="0"), "BTCUSDT", START, START + timedelta(seconds=1), "numeric range"),
        (payload(b="65001", a="65000"), "BTCUSDT", START, START + timedelta(seconds=1), "ask is below bid"),
        (payload(), "BTCUSDT", START + timedelta(seconds=1), START, "ordering is invalid"),
        (payload(E=True), "BTCUSDT", START, START + timedelta(seconds=1), "E must be"),
    ],
)
def test_parser_fails_closed_on_invalid_quote_contract(event, symbol, received, available, error):
    with pytest.raises(BinanceWebSocketPayloadError, match=error):
        parse_binance_book_ticker_event(
            event,
            requested_symbol=symbol,
            received_at=received,
            available_at=available,
        )


def test_parser_rejects_mismatched_requested_symbol():
    with pytest.raises(BinanceWebSocketPayloadError, match="approved BTC/ETH pair"):
        parse_binance_book_ticker_event(
            payload(),
            requested_symbol="DOGEUSDT",
            received_at=START,
            available_at=START + timedelta(seconds=1),
        )


def test_server_shutdown_is_classified_as_reconnectable_transport():
    with pytest.raises(BinanceWebSocketServerShutdown):
        parse_binance_book_ticker_event(
            {"e": "serverShutdown"},
            requested_symbol="BTCUSDT",
            received_at=START,
            available_at=START + timedelta(seconds=1),
        )


class FakeConnection:
    def __init__(self, messages):
        self.messages = list(messages)
        self.closed = False

    def recv(self):
        return self.messages.pop(0)

    def close(self):
        self.closed = True


def test_adapter_delivers_each_quote_with_separate_receive_and_handoff_times():
    messages = [
        json.dumps(payload(u=100)),
        json.dumps(payload(u=101, b="65001.0", a="65001.1")),
    ]
    connection = FakeConnection(messages)
    now_values = iter([START, START + timedelta(milliseconds=2)])
    handoff_values = iter([START + timedelta(milliseconds=1), START + timedelta(milliseconds=3)])
    opened_urls = []
    adapter = BinanceSpotBookTickerWebSocketAdapter(
        connection_factory=lambda url, timeout: (opened_urls.append(url) or connection),
        clock=lambda: next(now_values),
        consumer_handoff_clock=lambda: next(handoff_values),
        timeout_seconds=1,
    )
    received = []
    results = adapter.listen(symbol="BTCUSDT", max_messages=2, on_item=received.append)

    assert len(results) == 2
    assert results == received
    assert [item.update_id for item in results] == [100, 101]
    assert results[0].received_at == START
    assert results[0].available_at == START + timedelta(milliseconds=1)
    assert results[1].received_at == START + timedelta(milliseconds=2)
    assert results[1].available_at == START + timedelta(milliseconds=3)
    assert opened_urls == ["wss://stream.binance.com:9443/ws/btcusdt@bookTicker"]
    assert connection.closed


def test_adapter_streaming_sink_does_not_retain_all_quote_results():
    connection = FakeConnection([
        json.dumps(payload(u=200)),
        json.dumps(payload(u=201)),
        json.dumps(payload(u=202)),
    ])
    now_values = iter([START, START + timedelta(milliseconds=2), START + timedelta(milliseconds=4)])
    handoff_values = iter([
        START + timedelta(milliseconds=1),
        START + timedelta(milliseconds=3),
        START + timedelta(milliseconds=5),
    ])
    adapter = BinanceSpotBookTickerWebSocketAdapter(
        connection_factory=lambda _url, _timeout: connection,
        clock=lambda: next(now_values),
        consumer_handoff_clock=lambda: next(handoff_values),
        timeout_seconds=1,
    )
    stored = []
    result = adapter.listen(
        symbol="BTCUSDT",
        max_messages=3,
        on_item=stored.append,
        retain_results=False,
    )

    assert result == []
    assert [item.update_id for item in stored] == [200, 201, 202]
    assert connection.closed


def test_adapter_closes_connection_when_durable_consumer_fails():
    connection = FakeConnection([json.dumps(payload())])
    adapter = BinanceSpotBookTickerWebSocketAdapter(
        connection_factory=lambda url, timeout: connection,
        clock=lambda: START,
        consumer_handoff_clock=lambda: START + timedelta(milliseconds=1),
        timeout_seconds=1,
    )

    def fail(_item):
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        adapter.listen(symbol="BTCUSDT", max_messages=1, on_item=fail)
    assert connection.closed
