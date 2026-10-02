import json
from datetime import datetime, timezone

import pytest

from bot_obrero.acquisition import InstrumentMapper, InstrumentMappingRule
from bot_obrero.binance_instruments import (
    BinanceInstrumentResolution,
    BinanceMetadataBackedAdapter,
    BinanceSpotInstrumentMetadata,
    ExchangeInfoPayloadError,
    InstrumentInactive,
    InstrumentMappingAmbiguous,
    InstrumentMetadataInvalid,
    InstrumentNotFound,
)
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.market_data import InstrumentIdentity, MarketData


def symbol_record(
    *,
    symbol="BTCUSDT",
    status="TRADING",
    base="BTC",
    quote="USDT",
    spot_allowed=True,
):
    return {
        "symbol": symbol,
        "status": status,
        "baseAsset": base,
        "quoteAsset": quote,
        "isSpotTradingAllowed": spot_allowed,
    }


def exchange_payload(*records):
    return {"timezone": "UTC", "serverTime": 1790920487690, "symbols": list(records)}


def metadata_getter(payload, captures=None):
    body = json.dumps(payload).encode()

    def getter(url, headers, timeout):
        if captures is not None:
            captures.append(url)
        return 200, {"Content-Type": "application/json"}, body

    return getter


def test_valid_metadata_parses_provider_fields():
    metadata = BinanceSpotInstrumentMetadata(
        config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"),
        http_get=metadata_getter(exchange_payload(symbol_record())),
    )
    record = metadata.fetch("BTCUSDT")
    assert record.provider_symbol == "BTCUSDT"
    assert record.base_asset == "BTC"
    assert record.quote_asset == "USDT"
    assert record.market == "SPOT"
    assert record.venue == "BINANCE"
    assert record.status == "TRADING"


def test_malformed_root_is_rejected():
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter([]),
    )
    with pytest.raises(ExchangeInfoPayloadError, match="root"):
        metadata.fetch("BTCUSDT")


def test_malformed_symbol_record_is_rejected():
    payload = exchange_payload({"symbol": "BTCUSDT"})
    metadata = BinanceSpotInstrumentMetadata(http_get=metadata_getter(payload))
    with pytest.raises(ExchangeInfoPayloadError, match="missing required fields"):
        metadata.fetch("BTCUSDT")


def test_unknown_symbol_is_not_found():
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(exchange_payload()),
    )
    with pytest.raises(Exception, match="not found"):
        metadata.fetch("DOGEUSDT")
    assert metadata.resolve("DOGEUSDT").outcome.value == "NOT_FOUND"


def test_duplicate_provider_symbol_is_ambiguous():
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(
            exchange_payload(symbol_record(), symbol_record())
        ),
    )
    with pytest.raises(InstrumentMappingAmbiguous, match="multiple"):
        metadata.fetch("BTCUSDT")
    assert metadata.resolve("BTCUSDT").outcome is BinanceInstrumentResolution.AMBIGUOUS


def test_resolution_uses_explicit_base_and_quote_fields():
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(
            exchange_payload(
                symbol_record(symbol="FOOUSD", base="XBASE", quote="XQUOTE")
            )
        ),
    )
    result = metadata.resolve("FOOUSD")
    assert result.outcome is BinanceInstrumentResolution.FOUND
    assert result.instrument == InstrumentIdentity(
        "binance:SPOT:FOOUSD",
        "XBASE/XQUOTE",
        "SPOT",
    )


def test_spot_and_venue_are_provider_specific_and_separate_from_source():
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(exchange_payload(symbol_record())),
    )
    record = metadata.fetch("BTCUSDT")
    assert record.market == "SPOT"
    assert record.venue == "BINANCE"

    instrument = metadata.resolve_active("BTCUSDT")
    assert instrument.market == "SPOT"


@pytest.mark.parametrize("status", ["HALT", "BREAK", "END_OF_DAY", "CANCEL_ONLY"])
def test_inactive_status_is_rejected_for_acquisition(status):
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(
            exchange_payload(symbol_record(status=status))
        ),
    )
    with pytest.raises(InstrumentInactive, match="inactive"):
        metadata.resolve_active("BTCUSDT")


def test_spot_trading_disabled_is_rejected():
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(
            exchange_payload(symbol_record(spot_allowed=False))
        ),
    )
    with pytest.raises(InstrumentInactive, match="isSpotTradingAllowed=False"):
        metadata.resolve_active("BTCUSDT")


def test_cache_avoids_second_exchange_info_request():
    calls = []
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(exchange_payload(symbol_record()), calls),
    )
    metadata.fetch("BTCUSDT")
    metadata.fetch("BTCUSDT")
    assert len(calls) == 1


def test_refresh_forces_exchange_info_request():
    calls = []
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(exchange_payload(symbol_record()), calls),
    )
    metadata.fetch("BTCUSDT")
    metadata.fetch("BTCUSDT", refresh=True)
    assert len(calls) == 2


def test_resolution_never_claims_cross_provider_equivalence():
    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(exchange_payload(symbol_record())),
    )
    result = metadata.resolve("BTCUSDT")
    assert result.instrument is not None
    assert result.instrument.instrument_id == "binance:SPOT:BTCUSDT"
    assert result.instrument.symbol == "BTC/USDT"


def test_metadata_backed_adapter_reuses_canonical_market_data_path():
    kline = [[
        1790942400000,
        "123.4500",
        "124.0000",
        "123.0000",
        "123.7500",
        "0.0100",
        1790942459999,
        "1.2345000",
        42,
        "0.0050",
        "0.6172500",
        "0",
    ]]

    metadata = BinanceSpotInstrumentMetadata(
        http_get=metadata_getter(exchange_payload(symbol_record()))
    )

    def market_getter(url, headers, timeout):
        return 200, {}, json.dumps(kline).encode()

    adapter = BinanceMetadataBackedAdapter(
        metadata=metadata,
        config=BinanceSpotRestConfig(timeout_seconds=3),
        http_get=market_getter,
        clock=lambda: datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc),
    )
    result = adapter.fetch_market_data(symbol="BTCUSDT", interval="1m", limit=1)
    assert len(result) == 1
    assert isinstance(result[0], MarketData)
    assert result[0].instrument.instrument_id == "binance:SPOT:BTCUSDT"
    assert result[0].source.source_id == "binance-spot-rest"
    assert result[0].source.provider == "binance"
    assert result[0].source.venue == "BINANCE"


def test_legacy_manual_mapper_remains_compatible():
    mapper = InstrumentMapper(
        [
            InstrumentMappingRule(
                provider="binance",
                provider_symbol="BTCUSDT",
                provider_market="SPOT",
                provider_venue="BINANCE",
                instrument=InstrumentIdentity("btc-usdt-spot", "BTC/USDT", "SPOT"),
            )
        ]
    )
    assert mapper.resolve(
        __import__("bot_obrero.acquisition", fromlist=["ProviderRecord"]).ProviderRecord(
            provider_symbol="BTCUSDT",
            provider="binance",
            provider_market="SPOT",
            provider_venue="BINANCE",
            observed_at=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
            available_at=None,
            candle_start=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
            candle_end=datetime(2026, 10, 2, 12, 1, tzinfo=timezone.utc),
            timeframe="1m",
            open="1",
            high="2",
            low="1",
            close="2",
            volume="1",
        )
    ).instrument_id == "btc-usdt-spot"
