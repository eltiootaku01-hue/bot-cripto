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
    exposure_pct,
    modeled_spread_slippage_cost,
    parse_filters,
    return_pct,
    return_series,
    ticker_snapshots,
    _parser,
    _validate_args,
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


def test_product_v2_defaults_keep_ars_budget_separate_and_use_180_days():
    args = _parser().parse_args([])
    _validate_args(args)
    assert args.initial_ars == Decimal("10000")
    assert args.days == 180
    assert args.fee_rate == Decimal("0.001")
    assert args.spread_bps == Decimal("10")
    assert args.slippage_bps == Decimal("5")
    assert args.fast_ema_period == 12
    assert args.slow_ema_period == 26


@pytest.mark.parametrize("argv", [
    ["--days", "179"],
    ["--universe-limit", "13"],
    ["--fast-ema-period", "26", "--slow-ema-period", "12"],
    ["--position-fraction", "1.01"],
    ["--fee-rate", "0.2"],
    ["--spread-bps", "-1"],
])
def test_product_v2_rejects_invalid_parameters_before_network(argv):
    args = _parser().parse_args(argv)
    with pytest.raises(ProductV2Error):
        _validate_args(args)


def test_universe_instrument_preserves_provider_base_asset_for_stablecoin_exclusion():
    universe = active_usdt_universe({"symbols": [
        {"symbol": "USD1USDT", "baseAsset": "USD1", "quoteAsset": "USDT",
         "status": "TRADING", "isSpotTradingAllowed": True, "filters": []},
    ]})
    assert universe["USD1USDT"].base_asset == "USD1"


def test_exposure_is_calculated_from_fill_timestamps():
    items = _series(["10", "11", "12"])
    fills = [
        SimpleNamespace(timestamp=items[1].payload.start, side="BUY", quantity=Decimal("2"), price=Decimal("10")),
        SimpleNamespace(timestamp=items[2].payload.start, side="SELL", quantity=Decimal("2"), price=Decimal("12")),
    ]
    assert exposure_pct(items, fills) == Decimal("100") / Decimal("3")


def test_modeled_spread_and_slippage_cost_is_explicit_and_decimal():
    fill = SimpleNamespace(quantity=Decimal("2"), price=Decimal("10"))
    assert modeled_spread_slippage_cost([fill], Decimal("10"), Decimal("5")) == Decimal("0.020")


def test_product_v2_strategy_and_cost_parameters_are_configurable():
    args = _parser().parse_args([
        "--fee-rate", "0.002", "--spread-bps", "20", "--slippage-bps", "8",
        "--fast-ema-period", "10", "--slow-ema-period", "30", "--rsi-period", "9",
        "--rsi-buy-min", "55", "--rsi-sell-at", "75", "--position-fraction", "0.8",
    ])
    _validate_args(args)
    assert args.fee_rate == Decimal("0.002")
    assert args.spread_bps == Decimal("20")
    assert args.slippage_bps == Decimal("8")
    assert (args.fast_ema_period, args.slow_ema_period, args.rsi_period) == (10, 30, 9)
    assert args.rsi_buy_min == Decimal("55")
    assert args.rsi_sell_at == Decimal("75")
    assert args.position_fraction == Decimal("0.8")


def test_ars_capital_scenarios_never_invent_conversion_or_available_usdt():
    from bot_obrero.product_v2 import ars_capital_scenarios

    rows = ars_capital_scenarios()
    assert [(row["scenario"], row["capital_ars"]) for row in rows] == [
        ("A", Decimal("10000")), ("B", Decimal("25000")), ("C", Decimal("50000")),
    ]
    assert all(row["conversion"] == "CONVERSION_UNKNOWN" for row in rows)
    assert all(row["usdt_net"] == "UNKNOWN" for row in rows)
    assert all(row["conversion_costs"] == "UNKNOWN" for row in rows)
    assert all(row["full_trade_cost_ars"] == "UNKNOWN" for row in rows)
    assert all(row["historical_net_return"] == "NOT_COMPUTABLE" for row in rows)
    assert all(row["decision"] == "NO_TRADE" for row in rows)


def test_normalized_affordability_respects_quantity_step_notional_and_costs():
    from bot_obrero.product_v2 import InstrumentFilters, normalized_affordability

    filters = InstrumentFilters(Decimal("0.1"), Decimal("0.1"), Decimal("20"))
    kwargs = dict(fee_rate=Decimal("0.001"), spread_bps=Decimal("10"),
                  slippage_bps=Decimal("5"), position_fraction=Decimal("0.95"))
    low = normalized_affordability(Decimal("10"), filters, Decimal("10"), **kwargs)
    high = normalized_affordability(Decimal("10"), filters, Decimal("100"), **kwargs)
    assert low == (False, "MIN_NOTIONAL")
    assert high == (True, "PASS")


def test_normalized_backtest_budget_is_forwarded_without_claiming_ars_conversion(monkeypatch):
    from bot_obrero import product_v2

    captured = []

    def fake_run(items, config):
        captured.append(config)
        return SimpleNamespace(config=config)

    monkeypatch.setattr(product_v2, "run_virtual_backtest", fake_run)
    for budget in (Decimal("100"), Decimal("250"), Decimal("500")):
        result = product_v2.backtest(
            "ALTUSDT", [], 1080, initial_quote_balance=budget,
            fast_ema_period=12, slow_ema_period=26, rsi_period=14,
        )
        assert result.config.initial_quote_balance == budget
    assert [config.initial_quote_balance for config in captured] == [
        Decimal("100"), Decimal("250"), Decimal("500"),
    ]
