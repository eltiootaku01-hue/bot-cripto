from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bot_obrero.product_v2 import (
    ProductV2Error,
    Ticker,
    buy_hold_net_return,
    active_usdt_universe,
    correlation_beta,
    parse_filters,
    return_pct,
    return_series,
    ticker_snapshots,
)
from bot_obrero.virtual_backtest import BacktestConfig, BacktestError


def test_universe_only_keeps_active_spot_usdt_and_excludes_leveraged_suffixes():
    payload = {"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
         "status": "TRADING", "isSpotTradingAllowed": True, "filters": []},
        {"symbol": "FOOUPUSDT", "baseAsset": "FOOUP", "quoteAsset": "USDT",
         "status": "TRADING", "isSpotTradingAllowed": True, "filters": []},
        {"symbol": "OLDUSDT", "baseAsset": "OLD", "quoteAsset": "USDT",
         "status": "BREAK", "isSpotTradingAllowed": True, "filters": []},
        {"symbol": "FOOUSDC", "baseAsset": "FOO", "quoteAsset": "USDC",
         "status": "TRADING", "isSpotTradingAllowed": True, "filters": []},
    ]}
    assert list(active_usdt_universe(payload)) == ["BTCUSDT"]


def test_instrument_filters_preserve_exact_decimal_values():
    filters = parse_filters([
        {"filterType": "LOT_SIZE", "minQty": "0.0100", "stepSize": "0.0010"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5.00"},
    ])
    assert filters.min_qty == Decimal("0.0100")
    assert filters.step_size == Decimal("0.0010")
    assert filters.min_notional == Decimal("5.00")


def test_unknown_or_invalid_instrument_filters_fail_closed():
    assert parse_filters(None).min_qty is None
    assert parse_filters([{"filterType": "LOT_SIZE", "minQty": "NaN", "stepSize": "0.1"}]).step_size is None


def test_ticker_snapshot_calculates_spread_and_uses_quote_volume():
    universe = {"ALTUSDT": object()}
    daily = [{"symbol": "ALTUSDT", "lastPrice": "2", "quoteVolume": "1500000"}]
    book = [{"symbol": "ALTUSDT", "bidPrice": "1.99", "askPrice": "2.01"}]
    item = ticker_snapshots(universe, daily, book)["ALTUSDT"]
    assert item.price == Decimal("2")
    assert item.quote_volume == Decimal("1500000")
    assert item.spread_pct == Decimal("1.00")


def test_ticker_missing_or_crossed_book_does_not_claim_a_valid_spread():
    assert Ticker("ALTUSDT", Decimal("2"), Decimal("10"), Decimal("0"), Decimal("2")).spread_pct is None
    assert Ticker("ALTUSDT", Decimal("2"), Decimal("10"), Decimal("2.1"), Decimal("2")).spread_pct is None


def test_correlation_and_beta_are_deterministic_for_linearly_related_returns():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    btc = {start + timedelta(hours=i): Decimal(i + 1) / Decimal("10000") for i in range(6)}
    alt = {key: value * Decimal("2") for key, value in btc.items()}
    corr, beta = correlation_beta(alt, btc, 6)
    assert corr == pytest.approx(1.0)
    assert beta == pytest.approx(2.0)


def test_correlation_and_beta_return_unknown_when_history_or_variance_is_insufficient():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    few = {start + timedelta(hours=i): Decimal("0.01") for i in range(3)}
    assert correlation_beta(few, few, 5) == (None, None)
    flat = {start + timedelta(hours=i): Decimal("0.01") for i in range(5)}
    assert correlation_beta(flat, flat, 5) == (None, None)


def _series(values):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        SimpleNamespace(payload=SimpleNamespace(
            start=start + timedelta(hours=4 * i), close=Decimal(value)
        ))
        for i, value in enumerate(values)
    ]


def test_historical_returns_are_timestamp_aligned_and_decimal_based():
    items = _series(["10", "11", "9"])
    returns = return_series(items)
    assert returns[items[1].payload.start] == Decimal("0.1")
    assert returns[items[2].payload.start] == Decimal("9") / Decimal("11") - Decimal("1")
    assert return_pct(items, 2) == Decimal("-10")


def test_return_pct_requires_the_full_requested_window():
    assert return_pct(_series(["10", "11"]), 2) is None


def test_backtest_allows_180_days_of_four_hour_candles_and_caps_entries_at_five():
    config = BacktestConfig(symbol="BTCUSDT", interval="4h", candles=1080)
    config.validate()
    with pytest.raises(BacktestError, match="between 1 and 5"):
        BacktestConfig(symbol="BTCUSDT", interval="4h", candles=1080, max_entries_per_30d=6).validate()


def test_product_market_data_error_is_a_runtime_failure_not_a_fake_empty_result():
    with pytest.raises(ProductV2Error, match="symbols array"):
        active_usdt_universe({"symbols": None})


def test_btc_buy_hold_reference_applies_fee_spread_and_slippage():
    items = _series(["100", "110"])
    net = buy_hold_net_return(items)
    gross = Decimal("10")
    assert net < gross
