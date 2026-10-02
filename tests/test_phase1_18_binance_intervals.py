import json
from datetime import datetime, timedelta, timezone

import pytest
from bot_obrero.binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from bot_obrero.binance_intervals import BinanceSpotInterval, BinanceSpotIntervalError, BinanceSpotIntervalUnit, validate_binance_spot_interval
from bot_obrero.binance_spot import BinanceSpotRestConfig

OPEN = 1790942400000
CLOSE = OPEN + 59999
RECEIVED = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
HANDOFF = RECEIVED + timedelta(seconds=1)
OFFICIAL = ("1s","1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M")

def record(symbol, base, quote):
    return {"symbol": symbol, "status": "TRADING", "baseAsset": base, "quoteAsset": quote, "isSpotTradingAllowed": True}

def exchange_payload(*records):
    return {"timezone": "UTC", "symbols": list(records)}

def metadata_getter(payload):
    body = json.dumps(payload).encode()
    def getter(url, headers, timeout):
        return 200, {}, body
    return getter

def row(open_time=OPEN, close_time=CLOSE):
    return [open_time, "123.4500", "124.0000", "123.0000", "123.7500", "0.0100", close_time, "1.2345000", 42, "0.0050", "0.6172500", "0"]

def adapter_for(payload, market_payload, *, handoff=None):
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    body = json.dumps(market_payload).encode()
    def market_getter(url, headers, timeout):
        return 200, {}, body
    return BinanceMetadataBackedAdapter(metadata=metadata, config=BinanceSpotRestConfig(timeout_seconds=3), http_get=market_getter, clock=lambda: RECEIVED, consumer_handoff_clock=handoff)

@pytest.mark.parametrize("value", OFFICIAL)
def test_every_current_official_binance_kline_interval_is_accepted(value):
    parsed = validate_binance_spot_interval(value)
    assert parsed.value == value
    assert BinanceSpotInterval.parse(value) == parsed

def test_supported_values_match_current_official_rest_list():
    assert BinanceSpotInterval.supported_values() == OFFICIAL

def test_interval_units_and_exact_durations_are_provider_specific():
    assert validate_binance_spot_interval("1s").duration == timedelta(seconds=1)
    assert validate_binance_spot_interval("1m").duration == timedelta(minutes=1)
    assert validate_binance_spot_interval("1h").duration == timedelta(hours=1)
    assert validate_binance_spot_interval("1d").duration == timedelta(days=1)
    assert validate_binance_spot_interval("1w").duration == timedelta(weeks=1)
    month = validate_binance_spot_interval("1M")
    assert month.unit is BinanceSpotIntervalUnit.MONTH
    assert month.magnitude == 1
    assert month.duration is None

@pytest.mark.parametrize("value", ["", " ", " 1m", "1m ", "1M ", "1H", "1S", "2m", "60m", "24h", "2w", "1mo", "1month", "1", "foo"])
def test_invalid_or_ambiguous_intervals_are_rejected_without_case_or_whitespace_normalization(value):
    with pytest.raises(BinanceSpotIntervalError):
        validate_binance_spot_interval(value)

@pytest.mark.parametrize("value", [None, True, False, 1, 1.0, [], {}])
def test_non_string_intervals_are_rejected(value):
    with pytest.raises(BinanceSpotIntervalError):
        validate_binance_spot_interval(value)

def test_validated_interval_is_the_exact_value_sent_to_binance_and_timeframe():
    captured = {}
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(exchange_payload(record("BTCUSDT","BTC","USDT"))))
    body = json.dumps([row()]).encode()
    def getter(url, headers, timeout):
        captured["url"] = url
        return 200, {}, body
    adapter = BinanceMetadataBackedAdapter(metadata=metadata, http_get=getter, clock=lambda: RECEIVED)
    result = adapter.fetch_market_data(symbol="BTCUSDT", interval="1M", limit=1)
    assert "interval=1M" in captured["url"]
    assert result[0].payload.timeframe == "1M"

def test_invalid_interval_is_rejected_before_market_data_http():
    calls = []
    adapter = adapter_for(exchange_payload(record("BTCUSDT","BTC","USDT")), [row()])
    original = adapter._http_get
    def capture(url, headers, timeout):
        calls.append(url)
        return original(url, headers, timeout)
    adapter._http_get = capture
    with pytest.raises(BinanceSpotIntervalError):
        adapter.fetch_market_data(symbol="BTCUSDT", interval="2m", limit=1)
    assert calls == []

def test_btcusdt_and_ethusdt_live_remain_metadata_backed_with_validated_interval():
    adapter = adapter_for(exchange_payload(record("BTCUSDT","BTC","USDT"), record("ETHUSDT","ETH","USDT")), [row()])
    btc = adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)[0]
    eth = adapter.fetch_market_data(symbol="ETHUSDT", interval="5m", limit=1)[0]
    assert btc.instrument.instrument_id == "binance:SPOT:BTCUSDT"
    assert eth.instrument.instrument_id == "binance:SPOT:ETHUSDT"
    assert btc.payload.timeframe == "1m"
    assert eth.payload.timeframe == "5m"

def test_metadata_backed_historical_uses_the_same_interval_boundary():
    payload = exchange_payload(record("BTCUSDT","BTC","USDT"))
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    pages = [[row()], [row(OPEN + 60000, CLOSE + 60000)]]
    calls = []
    def getter(url, headers, timeout):
        calls.append(url)
        return 200, {}, json.dumps(pages[len(calls)-1]).encode()
    adapter = BinanceMetadataBackedAdapter(metadata=metadata, http_get=getter, clock=lambda: RECEIVED)
    result = adapter.fetch_historical_market_data(symbol="BTCUSDT", interval="1m", start_time=OPEN, end_time=OPEN+60000, page_limit=1, max_requests=2)
    assert len(result) == 2
    assert all(item.payload.timeframe == "1m" for item in result)
    assert all("interval=1m" in url for url in calls)

def test_historical_invalid_interval_is_rejected_before_http_and_before_pagination():
    calls = []
    payload = exchange_payload(record("BTCUSDT","BTC","USDT"))
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    def getter(url, headers, timeout):
        calls.append(url)
        return 200, {}, json.dumps([row()]).encode()
    adapter = BinanceMetadataBackedAdapter(metadata=metadata, http_get=getter)
    with pytest.raises(BinanceSpotIntervalError):
        adapter.fetch_historical_market_data(symbol="BTCUSDT", interval="1H", start_time=OPEN, end_time=OPEN, page_limit=1)
    assert calls == []

def test_availability_semantics_are_unchanged_by_interval_validation():
    adapter = adapter_for(exchange_payload(record("BTCUSDT","BTC","USDT")), [row()], handoff=lambda: HANDOFF)
    item = adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)[0]
    assert item.received_at == RECEIVED
    assert item.available_at == HANDOFF
    assert item.available_at != item.observed_at

def test_month_interval_is_not_converted_to_fixed_duration():
    month = BinanceSpotInterval.parse("1M")
    assert month.duration is None
    assert month.value == "1M"
