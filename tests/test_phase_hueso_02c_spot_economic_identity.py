from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot_obrero.market_data import (
    CANDLE_DATA_TYPE,
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    InstrumentType,
    MarketData,
    MarketDataError,
    SourceIdentity,
)


BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def make_instrument(
    *,
    instrument_id: str = "instrument-btcusdt",
    symbol: str = "BTC/USDT",
    market: str = "SPOT",
    base_asset: str | None = "BTC",
    quote_asset: str | None = "USDT",
) -> InstrumentIdentity:
    return InstrumentIdentity(
        instrument_id=instrument_id,
        symbol=symbol,
        market=market,
        instrument_type=InstrumentType.CRYPTO_SPOT,
        base_asset=base_asset,
        quote_asset=quote_asset,
    )


def make_market_data(instrument: InstrumentIdentity) -> MarketData:
    candle = Candle(
        start=BASE,
        end=BASE.replace(minute=1),
        timeframe="1m",
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.FINAL,
    )
    return MarketData(
        instrument=instrument,
        source=SourceIdentity(
            source_id="synthetic-source",
            provider="synthetic",
            venue="SYNTHETIC",
        ),
        data_type=CANDLE_DATA_TYPE,
        observed_at=BASE,
        received_at=BASE.replace(second=1),
        available_at=BASE.replace(second=2),
        payload=candle,
        quality=DataQuality.VALID,
        completeness=DataCompleteness.COMPLETE,
        source_sequence="1",
        market_data_id="md-1",
    )


def test_instrument_identity_exposes_explicit_spot_economic_terms():
    instrument = make_instrument()

    assert instrument.instrument_id == "instrument-btcusdt"
    assert instrument.symbol == "BTC/USDT"
    assert instrument.base_asset == "BTC"
    assert instrument.quote_asset == "USDT"
    assert instrument.instrument_type is InstrumentType.CRYPTO_SPOT


def test_spot_base_and_quote_are_mandatory():
    with pytest.raises(MarketDataError, match="base_asset"):
        make_instrument(base_asset=None)

    with pytest.raises(MarketDataError, match="quote_asset"):
        make_instrument(quote_asset=None)


@pytest.mark.parametrize(
    ("base_asset", "quote_asset"),
    [
        ("", "USDT"),
        ("   ", "USDT"),
        ("BTC", ""),
        ("BTC", "   "),
    ],
)
def test_spot_assets_must_be_non_empty_strings(base_asset, quote_asset):
    with pytest.raises(MarketDataError):
        make_instrument(base_asset=base_asset, quote_asset=quote_asset)


def test_spot_base_and_quote_must_be_distinct():
    with pytest.raises(MarketDataError, match="distinct"):
        make_instrument(base_asset="BTC", quote_asset="BTC")


def test_spot_symbol_is_checked_against_explicit_terms_without_inferring_them():
    with pytest.raises(MarketDataError, match="match explicit"):
        make_instrument(symbol="BTC/ETH", base_asset="BTC", quote_asset="USDT")


def test_spot_identity_is_immutable():
    instrument = make_instrument()

    with pytest.raises(FrozenInstanceError):
        instrument.base_asset = "ETH"

    with pytest.raises(FrozenInstanceError):
        instrument.quote_asset = "BTC"


def test_instrument_identity_contract_shape_is_explicit():
    assert [field.name for field in fields(InstrumentIdentity)] == [
        "instrument_id",
        "symbol",
        "market",
        "instrument_type",
        "base_asset",
        "quote_asset",
    ]


def test_market_data_serialization_preserves_base_and_quote_terms():
    instrument = make_instrument()
    market_data = make_market_data(instrument)

    restored = MarketData.from_dict(market_data.to_dict())

    assert restored.instrument == instrument
    assert restored.instrument.base_asset == "BTC"
    assert restored.instrument.quote_asset == "USDT"


def test_missing_spot_terms_are_rejected_during_market_data_deserialization():
    instrument = make_instrument()
    payload = make_market_data(instrument).to_dict()
    del payload["instrument"]["base_asset"]

    with pytest.raises(MarketDataError, match="base_asset"):
        MarketData.from_dict(payload)


def test_provider_neutrality_of_canonical_contract_module():
    from pathlib import Path

    source = Path("bot_obrero/market_data.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append((node.module or "").lower())

    forbidden_fragments = (
        "binance",
        "requests",
        "websocket",
        "exchangeinfo",
        "sdk",
        "provider",
        "http",
    )
    for fragment in forbidden_fragments:
        assert all(fragment not in module for module in imported_modules)


def test_spot_identity_does_not_carry_financial_amount_fields():
    instrument = make_instrument()

    assert not hasattr(instrument, "price")
    assert not hasattr(instrument, "quantity")
    assert not hasattr(instrument, "notional")
    assert not hasattr(instrument, "fee")
    assert not hasattr(instrument, "commission")


def test_spot_identity_is_deterministic():
    first = make_instrument()
    second = make_instrument()

    assert first == second
    assert (
        first.instrument_id,
        first.base_asset,
        first.quote_asset,
    ) == (
        second.instrument_id,
        second.base_asset,
        second.quote_asset,
    )


def test_market_data_identity_remains_provider_neutral():
    instrument = make_instrument(
        instrument_id="binance:SPOT:BTCUSDT",
        symbol="BTC/USDT",
        market="SPOT",
    )

    assert instrument.provider_symbol if hasattr(instrument, "provider_symbol") else None is None
    assert instrument.base_asset == "BTC"
    assert instrument.quote_asset == "USDT"


def test_other_instrument_types_do_not_receive_spot_terms():
    assert list(InstrumentType) == [InstrumentType.CRYPTO_SPOT]


def test_explicit_terms_are_not_normalized_or_uppercased():
    instrument = InstrumentIdentity(
        instrument_id="instrument-custom",
        symbol="btc/usdt",
        market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT,
        base_asset="btc",
        quote_asset="usdt",
    )

    assert instrument.base_asset == "btc"
    assert instrument.quote_asset == "usdt"
    assert instrument.symbol == "btc/usdt"
