import json
from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.acquisition import InstrumentMapper, InstrumentMappingRule, ProviderRecord
from bot_obrero.binance_instruments import (
    BinanceMetadataBackedAdapter,
    BinanceSpotInstrumentMetadata,
    ExchangeInfoHTTPError,
    ExchangeInfoPayloadError,
    ExchangeInfoTransportError,
    InstrumentInactive,
    InstrumentMappingAmbiguous,
    InstrumentNotFound,
)
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.market_data import InstrumentIdentity, MarketData


OPEN_1 = 1_790_942_400_000
OPEN_2 = OPEN_1 + 60_000
CLOSE_1 = OPEN_1 + 59_999
CLOSE_2 = OPEN_2 + 59_999
RECEIVED = datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc)
HANDOFF = RECEIVED + timedelta(seconds=1)


def symbol_record(symbol, base, quote, *, status="TRADING", spot_allowed=True):
    return {
        "symbol": symbol,
        "status": status,
        "baseAsset": base,
        "quoteAsset": quote,
        "isSpotTradingAllowed": spot_allowed,
    }


def exchange_payload(*records):
    return {"timezone": "UTC", "serverTime": 1790920487690, "symbols": list(records)}


def metadata_getter(payload):
    body = json.dumps(payload).encode()

    def getter(url, headers, timeout):
        return 200, {"Content-Type": "application/json"}, body

    return getter


def market_row(open_time, close_time, *, open_value="123.4500"):
    return [
        open_time,
        open_value,
        "124.0000",
        "123.0000",
        "123.7500",
        "0.0100",
        close_time,
        "1.2345000",
        42,
        "0.0050",
        "0.6172500",
        "0",
    ]


def adapter_for(payload, market_payload, *, handoff_clock=None):
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    body = json.dumps(market_payload).encode()

    def market_getter(url, headers, timeout):
        return 200, {"Content-Type": "application/json"}, body

    return BinanceMetadataBackedAdapter(
        metadata=metadata,
        config=BinanceSpotRestConfig(timeout_seconds=3),
        http_get=market_getter,
        clock=lambda: RECEIVED,
        consumer_handoff_clock=handoff_clock,
    )


def test_btcusdt_and_ethusdt_resolve_from_explicit_exchangeinfo_fields():
    payload = exchange_payload(
        symbol_record("BTCUSDT", "BTC", "USDT"),
        symbol_record("ETHUSDT", "ETH", "USDT"),
    )
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))

    btc = metadata.resolve_active("BTCUSDT")
    eth = metadata.resolve_active("ETHUSDT")

    assert btc == InstrumentIdentity("binance:SPOT:BTCUSDT", "BTC/USDT", "SPOT", base_asset="BTC", quote_asset="USDT")
    assert eth == InstrumentIdentity("binance:SPOT:ETHUSDT", "ETH/USDT", "SPOT", base_asset="ETH", quote_asset="USDT")
    assert btc.instrument_id != eth.instrument_id


@pytest.mark.parametrize(
    ("symbol", "base", "quote"),
    [("FOOUSD", "XBASE", "XQUOTE"), ("BTCUSDT", "BTC", "USDT")],
)
def test_canonical_symbol_never_parses_provider_symbol(symbol, base, quote):
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(
            exchange_payload(symbol_record(symbol, base, quote))
        )
    )
    result = metadata.resolve(symbol)
    assert result.instrument is not None
    assert result.instrument.symbol == f"{base}/{quote}"


def test_metadata_backed_live_path_produces_canonical_market_data_for_btcusdt():
    adapter = adapter_for(
        exchange_payload(symbol_record("BTCUSDT", "BTC", "USDT")),
        [market_row(OPEN_1, CLOSE_1)],
    )
    result = adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)

    assert len(result) == 1
    assert isinstance(result[0], MarketData)
    assert result[0].instrument.instrument_id == "binance:SPOT:BTCUSDT"
    assert result[0].instrument.symbol == "BTC/USDT"
    assert result[0].source.source_id == "binance-spot-rest"
    assert result[0].source.provider == "binance"
    assert result[0].source.venue == "BINANCE"


def test_metadata_backed_live_path_produces_canonical_market_data_for_ethusdt():
    adapter = adapter_for(
        exchange_payload(symbol_record("ETHUSDT", "ETH", "USDT")),
        [market_row(OPEN_1, CLOSE_1, open_value="123.7500")],
    )
    result = adapter.fetch_market_data(symbol="ETHUSDT", interval="1m", limit=1)

    assert len(result) == 1
    assert result[0].instrument.instrument_id == "binance:SPOT:ETHUSDT"
    assert result[0].instrument.symbol == "ETH/USDT"


def test_metadata_backed_historical_path_reuses_existing_pagination():
    payload = exchange_payload(symbol_record("BTCUSDT", "BTC", "USDT"))
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    pages = [
        [market_row(OPEN_1, CLOSE_1)],
        [market_row(OPEN_2, CLOSE_2, open_value="123.7500")],
    ]
    calls = []

    def market_getter(url, headers, timeout):
        calls.append(url)
        page = pages[len(calls) - 1]
        return 200, {}, json.dumps(page).encode()

    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        config=BinanceSpotRestConfig(timeout_seconds=3),
        http_get=market_getter,
        clock=lambda: RECEIVED,
    )
    result = adapter.fetch_historical_market_data(
        symbol="BTCUSDT",
        interval="1m",
        start_time=OPEN_1,
        end_time=OPEN_2,
        page_limit=1,
        max_requests=2,
    )

    assert len(result) == 2
    assert [item.instrument.instrument_id for item in result] == [
        "binance:SPOT:BTCUSDT",
        "binance:SPOT:BTCUSDT",
    ]
    assert len(calls) == 2
    assert "startTime=1790942460000" in calls[1]


def test_metadata_backed_availability_preserves_existing_handoff_semantics():
    adapter = adapter_for(
        exchange_payload(symbol_record("BTCUSDT", "BTC", "USDT")),
        [market_row(OPEN_1, CLOSE_1)],
        handoff_clock=lambda: HANDOFF,
    )
    result = adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)

    assert result[0].received_at == RECEIVED
    assert result[0].available_at == HANDOFF
    assert result[0].available_at != result[0].observed_at


def test_metadata_backed_path_without_handoff_keeps_availability_unknown():
    adapter = adapter_for(
        exchange_payload(symbol_record("BTCUSDT", "BTC", "USDT")),
        [market_row(OPEN_1, CLOSE_1)],
    )
    result = adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)
    assert result[0].available_at is None


def test_manual_legacy_mapper_remains_explicitly_usable():
    instrument = InstrumentIdentity("btc-usdt-spot", "BTC/USDT", "SPOT", base_asset="BTC", quote_asset="USDT")
    mapper = InstrumentMapper(
        [
            InstrumentMappingRule(
                provider="binance",
                provider_symbol="BTCUSDT",
                provider_market="SPOT",
                provider_venue="BINANCE",
                instrument=instrument,
            )
        ]
    )
    record = ProviderRecord(
        provider_symbol="BTCUSDT",
        provider="binance",
        provider_market="SPOT",
        provider_venue="BINANCE",
        observed_at=RECEIVED,
        available_at=None,
        candle_start=RECEIVED,
        candle_end=RECEIVED + timedelta(minutes=1),
        timeframe="1m",
        open="1",
        high="2",
        low="1",
        close="2",
        volume="1",
    )
    assert mapper.resolve(record) == instrument


def test_unknown_symbol_does_not_fallback_to_manual_mapping():
    payload = exchange_payload()
    adapter = adapter_for(payload, [market_row(OPEN_1, CLOSE_1)])
    with pytest.raises(InstrumentNotFound):
        adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)


def test_inactive_symbol_is_rejected_before_market_data_request():
    payload = exchange_payload(
        symbol_record("BTCUSDT", "BTC", "USDT", status="HALT")
    )
    calls = []

    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))

    def market_getter(url, headers, timeout):
        calls.append(url)
        return 200, {}, json.dumps([market_row(OPEN_1, CLOSE_1)]).encode()

    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        http_get=market_getter,
    )
    with pytest.raises(InstrumentInactive):
        adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)
    assert calls == []


def test_spot_trading_disabled_is_rejected():
    payload = exchange_payload(
        symbol_record("BTCUSDT", "BTC", "USDT", spot_allowed=False)
    )
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    with pytest.raises(InstrumentInactive):
        metadata.resolve_active("BTCUSDT")


def test_ambiguous_exchangeinfo_is_rejected_without_fallback():
    payload = exchange_payload(
        symbol_record("BTCUSDT", "BTC", "USDT"),
        symbol_record("BTCUSDT", "BTC", "USDT"),
    )
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    with pytest.raises(InstrumentMappingAmbiguous):
        metadata.resolve_active("BTCUSDT")


def test_invalid_exchangeinfo_is_rejected():
    payload = {"timezone": "UTC", "symbols": [{"symbol": "BTCUSDT"}]}
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    with pytest.raises(ExchangeInfoPayloadError):
        metadata.fetch("BTCUSDT")


def test_transport_error_is_exposed():
    def getter(url, headers, timeout):
        raise TimeoutError("simulated transport failure")

    metadata = BinanceSpotInstrumentMetadata(http_get=getter)
    with pytest.raises(ExchangeInfoTransportError, match="transport"):
        metadata.fetch("BTCUSDT")


def test_http_error_is_exposed():
    def getter(url, headers, timeout):
        return 503, {}, b"upstream unavailable"

    metadata = BinanceSpotInstrumentMetadata(http_get=getter)
    with pytest.raises(ExchangeInfoHTTPError, match="503"):
        metadata.fetch("BTCUSDT")


def test_metadata_cache_is_still_process_local_and_refreshable():
    calls = []

    def getter(url, headers, timeout):
        calls.append(url)
        return 200, {}, json.dumps(
            exchange_payload(symbol_record("BTCUSDT", "BTC", "USDT"))
        ).encode()

    metadata = BinanceSpotInstrumentMetadata(http_get=getter)
    metadata.fetch("BTCUSDT")
    metadata.fetch("BTCUSDT")
    metadata.fetch("BTCUSDT", refresh=True)
    assert len(calls) == 2


def test_public_resolve_instrument_is_the_metadata_backed_acquisition_boundary():
    payload = exchange_payload(symbol_record("BTCUSDT", "BTC", "USDT"))
    adapter = adapter_for(payload, [market_row(OPEN_1, CLOSE_1)])
    instrument = adapter.resolve_instrument("BTCUSDT")
    assert instrument == InstrumentIdentity("binance:SPOT:BTCUSDT", "BTC/USDT", "SPOT", base_asset="BTC", quote_asset="USDT")
