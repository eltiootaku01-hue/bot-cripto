from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bot_obrero.product_v2 import (
    ProductV2Error,
    Ticker,
    buy_hold_net_return,
    btc_relationship_reasons,
    classify_btc_regime,
    histories_temporally_aligned,
    active_usdt_universe,
    correlation_beta,
    exposure_pct,
    daily_equity_metrics,
    modeled_spread_slippage_cost,
    parse_filters,
    effective_market_lot_rules,
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
    assert filters.market_lot_size_present is False
    assert filters.market_min_qty is None


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
    assert args.hypothetical_usdt_balance == Decimal("31")
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


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
def test_hypothetical_usdt_balance_must_be_finite_and_positive(value):
    args = _parser().parse_args([])
    args.hypothetical_usdt_balance = Decimal(value)
    with pytest.raises(ProductV2Error, match="hypothetical-usdt-balance"):
        _validate_args(args)


def test_product_v2_accepts_explicit_31_usdt_without_converting_ars():
    args = _parser().parse_args(["--initial-ars", "50000", "--hypothetical-usdt-balance", "31"])
    _validate_args(args)
    assert args.initial_ars == Decimal("50000")
    assert args.hypothetical_usdt_balance == Decimal("31")
    from bot_obrero.product_v2 import ars_capital_scenarios
    assert next(row for row in ars_capital_scenarios() if row["capital_ars"] == Decimal("50000"))["conversion"] == "CONVERSION_UNKNOWN"



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

    def fake_run(items, config, **kwargs):
        captured.append((config, kwargs))
        return SimpleNamespace(config=config)

    monkeypatch.setattr(product_v2, "run_virtual_backtest", fake_run)
    for budget in (Decimal("31"), Decimal("100"), Decimal("250"), Decimal("500")):
        result = product_v2.backtest(
            "ALTUSDT", [], 1080, initial_quote_balance=budget,
            fast_ema_period=12, slow_ema_period=26, rsi_period=14,
        )
        assert result.config.initial_quote_balance == budget
    assert [config.initial_quote_balance for config, _ in captured] == [
        Decimal("31"), Decimal("100"), Decimal("250"), Decimal("500"),
    ]
    assert all({"evaluation_start", "evaluation_end", "entry_admission"} <= set(kwargs)
               for _, kwargs in captured)
    assert all(config.initial_quote_balance != Decimal("1000") for config, _ in captured)



def test_low_btc_correlation_rejects_otherwise_profitable_candidate():
    reasons = btc_relationship_reasons(0.30, 0.40, 1.2, Decimal("5"), Decimal("0.50"), Decimal("0.60"))
    assert reasons == ["BTC_CORRELATION_30D_BELOW_MINIMUM", "BTC_CORRELATION_90D_BELOW_MINIMUM"]


def test_sufficient_btc_correlations_only_clear_relationship_gate_not_other_controls():
    reasons = btc_relationship_reasons(0.75, 0.82, 1.1, Decimal("3"), Decimal("0.50"), Decimal("0.60"))
    assert reasons == []


@pytest.mark.parametrize("metrics", [
    (None, 0.8, 1.0, Decimal("1")),
    (0.8, None, 1.0, Decimal("1")),
    (float("nan"), 0.8, 1.0, Decimal("1")),
    (0.8, 0.8, None, Decimal("1")),
    (0.8, 0.8, 1.0, None),
])
def test_unknown_or_invalid_btc_metrics_fail_closed(metrics):
    assert btc_relationship_reasons(*metrics, Decimal("0.50"), Decimal("0.60")) == ["BTC_METRICS_INSUFFICIENT"]


def test_btc_below_both_emas_is_not_bullish():
    assert classify_btc_regime(Decimal("90"), Decimal("100"), Decimal("110"), Decimal("45"), Decimal("50")) == "BEARISH"


def test_btc_regime_uses_closed_candle_close_not_an_independent_spot_price():
    closed_candle_close = Decimal("105")
    fast, slow, rsi = Decimal("104"), Decimal("103"), Decimal("55")
    spot_price = Decimal("80")
    regime = classify_btc_regime(closed_candle_close, fast, slow, rsi, Decimal("50"))
    assert regime == "BULLISH"
    assert spot_price < slow
    assert classify_btc_regime(closed_candle_close, fast, slow, rsi, Decimal("50")) == regime


def test_history_timestamp_misalignment_fails_closed():
    btc = _series(["100", "101", "102"])
    alt = _series(["100", "101", "102"])
    assert histories_temporally_aligned(alt, btc)
    alt[1].payload.start = alt[1].payload.start + timedelta(hours=4)
    assert not histories_temporally_aligned(alt, btc)


@pytest.mark.parametrize("argv", [
    ["--min-btc-corr-30d", "-1.1"],
    ["--min-btc-corr-90d", "1.1"],
])
def test_invalid_btc_correlation_thresholds_are_rejected_before_network(argv):
    args = _parser().parse_args(argv)
    with pytest.raises(ProductV2Error, match="correlation minimums"):
        _validate_args(args)


def test_btc_correlation_threshold_cli_defaults_are_explicit():
    args = _parser().parse_args([])
    assert args.min_btc_corr_30d == Decimal("0.50")
    assert args.min_btc_corr_90d == Decimal("0.60")



def test_usdt_spot_universe_is_not_a_complete_ars_fiat_or_p2p_conversion_search():
    universe = active_usdt_universe({"symbols": [
        {"symbol": "ALTUSDT", "baseAsset": "ALT", "quoteAsset": "USDT",
         "status": "TRADING", "isSpotTradingAllowed": True, "filters": []},
        {"symbol": "BTCARS", "baseAsset": "BTC", "quoteAsset": "ARS",
         "status": "TRADING", "isSpotTradingAllowed": True, "filters": []},
    ]})
    assert "ALTUSDT" in universe
    assert "BTCARS" not in universe



def test_round_trip_break_even_includes_both_fees_spread_and_slippage():
    from bot_obrero.product_v2 import modeled_round_trip_breakeven_pct

    baseline = modeled_round_trip_breakeven_pct(Decimal("0.001"), Decimal("10"), Decimal("5"))
    more_fees = modeled_round_trip_breakeven_pct(Decimal("0.002"), Decimal("10"), Decimal("5"))
    more_spread = modeled_round_trip_breakeven_pct(Decimal("0.001"), Decimal("20"), Decimal("5"))
    more_slippage = modeled_round_trip_breakeven_pct(Decimal("0.001"), Decimal("10"), Decimal("8"))
    assert baseline > 0
    assert more_fees > baseline
    assert more_spread > baseline
    assert more_slippage > baseline



def test_historical_btc_gate_uses_only_past_aligned_data(monkeypatch):
    from types import SimpleNamespace
    import bot_obrero.product_v2 as product

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def make_history():
        result = []
        for i in range(700):
            close = Decimal(100 + i)
            result.append(SimpleNamespace(
                payload=SimpleNamespace(
                    start=start + timedelta(hours=4 * i),
                    end=start + timedelta(hours=4 * i + 4),
                    close=close,
                    candle_state=SimpleNamespace(value="CLOSED"),
                    completeness=SimpleNamespace(value="COMPLETE"),
                    finality=SimpleNamespace(value="UNKNOWN"),
                ),
                instrument=SimpleNamespace(symbol="ALT/USDT"),
                quality=SimpleNamespace(value="VALID"), completeness=SimpleNamespace(value="UNKNOWN"),
            ))
        return result

    monkeypatch.setattr(product, "_observation", lambda item: SimpleNamespace(symbol="BTC/USDT"))
    monkeypatch.setattr(product, "_indicators", lambda observations, config: {
        "fast_ema": Decimal("120"), "slow_ema": Decimal("110"), "rsi": Decimal("60")
    })
    candidate, btc = make_history(), make_history()
    gate = product.historical_btc_entry_admission(candidate, btc, fast_ema_period=12, slow_ema_period=26,
        rsi_period=14, rsi_buy_min=Decimal("50"), minimum_corr30d=Decimal("0.5"), minimum_corr90d=Decimal("0.6"))
    requested_windows = []
    original_correlation_beta = product.correlation_beta

    def recording_correlation_beta(asset_returns, btc_returns, periods):
        requested_windows.append(periods)
        return original_correlation_beta(asset_returns, btc_returns, periods)

    monkeypatch.setattr(product, "correlation_beta", recording_correlation_beta)
    expected = gate(600, ())
    assert requested_windows == [30 * 6, 90 * 6]
    changed_future = make_history()
    for i in range(601, len(changed_future)):
        changed_future[i].payload.close = Decimal(1000000 + i)
    future_gate = product.historical_btc_entry_admission(changed_future, btc, fast_ema_period=12, slow_ema_period=26,
        rsi_period=14, rsi_buy_min=Decimal("50"), minimum_corr30d=Decimal("0.5"), minimum_corr90d=Decimal("0.6"))
    assert expected == future_gate(600, ())
    assert expected[0] is True



def test_historical_btc_gate_rejects_non_bullish_regime_even_when_correlations_pass(monkeypatch):
    import bot_obrero.product_v2 as product

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def make_history():
        return [
            SimpleNamespace(
                payload=SimpleNamespace(
                    start=start + timedelta(hours=4 * i),
                    end=start + timedelta(hours=4 * i + 4),
                    close=Decimal(100 + i),
                    candle_state=SimpleNamespace(value="CLOSED"),
                    completeness=SimpleNamespace(value="COMPLETE"),
                    finality=SimpleNamespace(value="UNKNOWN"),
                ),
                instrument=SimpleNamespace(symbol="ALT/USDT"),
                quality=SimpleNamespace(value="VALID"),
                completeness=SimpleNamespace(value="UNKNOWN"),
            )
            for i in range(700)
        ]

    monkeypatch.setattr(product, "_observation", lambda item: SimpleNamespace(symbol="BTC/USDT"))
    monkeypatch.setattr(product, "_indicators", lambda observations, config: {
        "fast_ema": Decimal("90"), "slow_ema": Decimal("100"), "rsi": Decimal("40")
    })
    candidate, btc = make_history(), make_history()
    gate = product.historical_btc_entry_admission(
        candidate, btc, fast_ema_period=12, slow_ema_period=26, rsi_period=14,
        rsi_buy_min=Decimal("50"), minimum_corr30d=Decimal("0.50"),
        minimum_corr90d=Decimal("0.60"),
    )

    allowed, detail = gate(600, ())
    assert allowed is False
    assert "BTC_REGIME_NOT_BULLISH" in detail
    assert "corr30d=" in detail and "corr90d=" in detail



def test_historical_btc_gate_rejects_partial_marketdata_envelope_fail_closed(monkeypatch):
    import bot_obrero.product_v2 as product

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def make_history():
        return [
            SimpleNamespace(
                payload=SimpleNamespace(
                    start=start + timedelta(hours=4 * i),
                    end=start + timedelta(hours=4 * i + 4),
                    close=Decimal(100 + i),
                    candle_state=SimpleNamespace(value="CLOSED"),
                    completeness=SimpleNamespace(value="COMPLETE"),
                    finality=SimpleNamespace(value="UNKNOWN"),
                ),
                instrument=SimpleNamespace(symbol="ALT/USDT"),
                quality=SimpleNamespace(value="VALID"),
                completeness=SimpleNamespace(value="UNKNOWN"),
            )
            for i in range(700)
        ]

    monkeypatch.setattr(product, "_observation", lambda item: SimpleNamespace(symbol="BTC/USDT"))
    monkeypatch.setattr(product, "_indicators", lambda observations, config: {
        "fast_ema": Decimal("120"), "slow_ema": Decimal("110"), "rsi": Decimal("60")
    })
    candidate, btc = make_history(), make_history()
    candidate[10].completeness = SimpleNamespace(value="PARTIAL")
    gate = product.historical_btc_entry_admission(
        candidate, btc, fast_ema_period=12, slow_ema_period=26, rsi_period=14,
        rsi_buy_min=Decimal("50"), minimum_corr30d=Decimal("0.50"),
        minimum_corr90d=Decimal("0.60"),
    )

    allowed, detail = gate(600, ())
    assert allowed is False
    assert "BTC_METRICS_INSUFFICIENT" in detail



def test_walk_forward_windows_require_final_boundary_and_are_consecutive():
    from bot_obrero.product_v2 import validation_window_bounds
    warmup, evaluation, window = 90*6, 180*6, 60*6
    exact = warmup + evaluation + 3*window + 1
    bounds = validation_window_bounds(exact, warmup, evaluation, window)
    assert bounds == ((1620, 1980), (1980, 2340), (2340, 2700))
    assert all(end-start == window for start, end in bounds)
    with pytest.raises(ProductV2Error, match="history insufficient"):
        validation_window_bounds(exact-1, warmup, evaluation, window)
    assert validation_window_bounds(exact+1, warmup, evaluation, window) == bounds


def test_walk_forward_cost_scenarios_match_requested_assumptions():
    from bot_obrero.product_v2 import VALIDATION_COST_SCENARIOS
    assert VALIDATION_COST_SCENARIOS == (
        ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
        ("STRESS_1_FEE_0.1_SPREAD_25_SLIP_10", Decimal("0.001"), Decimal("25"), Decimal("10")),
        ("STRESS_2_FEE_0.2_SPREAD_50_SLIP_20", Decimal("0.002"), Decimal("50"), Decimal("20")),
    )


def test_buy_hold_benchmark_reports_net_return_and_drawdown_under_costs():
    from bot_obrero.product_v2 import buy_hold_metrics
    ret, dd = buy_hold_metrics(_series(["100", "120", "90", "110"]), Decimal("0.001"), Decimal("10"), Decimal("5"))
    assert ret < Decimal("10")
    assert dd < 0



def test_daily_equity_metrics_separate_equity_change_realized_unrealized_and_costs():
    from bot_obrero.virtual_backtest import EquityPoint, VirtualFill

    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    close_1ms = timedelta(milliseconds=1)
    points = (
        EquityPoint(day1 + timedelta(hours=4) - close_1ms, Decimal("10"), Decimal("989.9"), Decimal("1"), Decimal("1000"), Decimal("0")),
        EquityPoint(day1 + timedelta(hours=8) - close_1ms, Decimal("10.05"), Decimal("989.9"), Decimal("1"), Decimal("1005"), Decimal("0")),
        EquityPoint(day1 + timedelta(hours=12) - close_1ms, Decimal("10.10"), Decimal("989.9"), Decimal("1"), Decimal("1010"), Decimal("0")),
        EquityPoint(day1 + timedelta(hours=16) - close_1ms, Decimal("10.08"), Decimal("989.9"), Decimal("1"), Decimal("1008"), Decimal("0")),
        EquityPoint(day1 + timedelta(hours=20) - close_1ms, Decimal("10.12"), Decimal("989.9"), Decimal("1"), Decimal("1012"), Decimal("0")),
        EquityPoint(day2 - close_1ms, Decimal("10.15"), Decimal("989.9"), Decimal("1"), Decimal("1015"), Decimal("0")),
        EquityPoint(day2 + timedelta(hours=4) - close_1ms, Decimal("10.10"), Decimal("990"), Decimal("1"), Decimal("1010"), Decimal("0")),
        EquityPoint(day2 + timedelta(hours=8) - close_1ms, Decimal("10"), Decimal("990"), Decimal("1"), Decimal("1000"), Decimal("0")),
        EquityPoint(day2 + timedelta(hours=12) - close_1ms, Decimal("10"), Decimal("990"), Decimal("1"), Decimal("1000"), Decimal("0")),
        EquityPoint(day2 + timedelta(hours=16) - close_1ms, Decimal("10"), Decimal("990"), Decimal("1"), Decimal("1000"), Decimal("0")),
        EquityPoint(day2 + timedelta(hours=20) - close_1ms, Decimal("10"), Decimal("990"), Decimal("1"), Decimal("1000"), Decimal("0")),
        EquityPoint(day2 + timedelta(days=1) - close_1ms, Decimal("10"), Decimal("990"), Decimal("1"), Decimal("1000"), Decimal("0")),
    )
    fills = (
        VirtualFill(day1 + timedelta(hours=1), "BUY", Decimal("1"), Decimal("10"), Decimal("0.1"), Decimal("989.9"), "entry"),
        VirtualFill(day1 + timedelta(hours=5), "SELL", Decimal("1"), Decimal("12"), Decimal("0.12"), Decimal("1001.88"), "exit", Decimal("1.78")),
        VirtualFill(day1 + timedelta(hours=7), "BUY", Decimal("1"), Decimal("10"), Decimal("0.1"), Decimal("990"), "entry"),
    )
    result = SimpleNamespace(
        first_candle=day1,
        equity_curve=points, fills=fills, entry_rejections=("ORDER_REJECTED:MIN_NOTIONAL",),
        config=SimpleNamespace(initial_quote_balance=Decimal("1000")),
    )

    rows, summary = daily_equity_metrics(result)
    assert len(rows) == 2
    assert rows[0]["day_utc"] == "2026-01-01"
    assert rows[0]["day_status"] == "FULL_UTC_DAY"
    assert rows[0]["net_change"] == Decimal("15")
    assert rows[0]["realized_pnl"] == Decimal("1.78")
    assert rows[0]["fees"] == Decimal("0.32")
    assert rows[0]["buys"] == 2 and rows[0]["sells"] == 1
    assert rows[1]["day_utc"] == "2026-01-02"
    assert rows[1]["day_status"] == "FULL_UTC_DAY"
    assert rows[1]["net_change"] == Decimal("-15")
    assert rows[1]["unrealized_pnl"] == Decimal("-0.1")
    assert rows[1]["equity_close"] - Decimal("990") - Decimal("10.1") == rows[1]["unrealized_pnl"]
    assert rows[1]["no_trade"] is True
    assert summary["net_pnl"] == Decimal("0")
    assert summary["positive_days"] == 1 and summary["negative_days"] == 1
    assert summary["no_trade_days"] == 1
    assert summary["max_consecutive_losing_days"] == 1
    assert summary["rejection_reasons"] == ("ORDER_REJECTED:MIN_NOTIONAL",)


def test_daily_equity_metrics_marks_partial_days_and_uses_prior_close_reference():
    from bot_obrero.virtual_backtest import EquityPoint

    start = datetime(2026, 2, 1, 4, tzinfo=timezone.utc)
    result = SimpleNamespace(
        equity_curve=(
            EquityPoint(start, Decimal("110"), Decimal("110"), Decimal("0"), Decimal("110"), Decimal("0")),
            EquityPoint(start + timedelta(hours=4), Decimal("120"), Decimal("120"), Decimal("0"), Decimal("120"), Decimal("0")),
        ),
        fills=(), entry_rejections=(),
        config=SimpleNamespace(initial_quote_balance=Decimal("100")),
    )
    rows, summary = daily_equity_metrics(result)
    assert rows[0]["day_status"] == "PARTIAL_UTC_DAY"
    assert rows[0]["net_change"] == Decimal("20")
    assert rows[0]["return_pct"] == Decimal("20")
    assert summary["partial_days"] == 1
    assert summary["no_trade_days"] == 1



def test_daily_metrics_exclude_partial_days_from_complete_day_distribution_and_verify_timestamps():
    from bot_obrero.virtual_backtest import EquityPoint
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    close_1ms = timedelta(milliseconds=1)
    full_closes = [start + timedelta(hours=4 * i) - close_1ms for i in range(1, 6)] + [start + timedelta(days=1) - close_1ms]
    points = tuple(EquityPoint(ts, Decimal("10"), Decimal("100"), Decimal("0"), Decimal("100"), Decimal("0")) for ts in full_closes) + (
        EquityPoint(start + timedelta(days=1, hours=4) - close_1ms, Decimal("10"), Decimal("200"), Decimal("0"), Decimal("200"), Decimal("0")),
        EquityPoint(start + timedelta(days=1, hours=8) - close_1ms, Decimal("10"), Decimal("300"), Decimal("0"), Decimal("300"), Decimal("0")),
    )
    result = SimpleNamespace(equity_curve=points, fills=(), entry_rejections=(), config=SimpleNamespace(initial_quote_balance=Decimal("100")))
    rows, summary = daily_equity_metrics(result)
    assert [row["day_status"] for row in rows] == ["FULL_UTC_DAY", "PARTIAL_UTC_DAY"]
    assert rows[1]["bars"] == 2 and rows[1]["net_change"] == Decimal("200")
    assert summary["complete_days"] == 1 and summary["partial_days"] == 1
    assert summary["daily_mean_usdt"] == Decimal("0") and summary["daily_median_usdt"] == Decimal("0")
    assert summary["positive_days"] == 0 and summary["negative_days"] == 0
    assert summary["full_days_without_fills"] == 1 and summary["days_without_fills"] == 2


def test_daily_metrics_marks_unreconstructable_open_position_cost_basis_unknown():
    from bot_obrero.virtual_backtest import EquityPoint
    timestamp = datetime(2026, 4, 1, 4, tzinfo=timezone.utc)
    result = SimpleNamespace(equity_curve=(EquityPoint(timestamp, Decimal("12"), Decimal("2"), Decimal("1"), Decimal("12"), Decimal("0")),), fills=(), entry_rejections=(), config=SimpleNamespace(initial_quote_balance=Decimal("10")))
    rows, summary = daily_equity_metrics(result)
    assert rows[0]["unrealized_pnl"] is None and summary["final_unrealized_pnl"] is None


def test_daily_profitability_classification_requires_consistent_full_period_and_walk_forward():
    from bot_obrero.product_v2 import daily_profitability_status
    positive = {"complete_days": 180, "net_pnl": Decimal("10"), "daily_mean_usdt": Decimal("1"), "daily_median_usdt": Decimal("0.5"), "positive_days": 120, "negative_days": 50}
    good_window = {"complete_days": 60, "net_pnl": Decimal("2"), "daily_mean_usdt": Decimal("0.1"), "daily_median_usdt": Decimal("0.05"), "positive_days": 40, "negative_days": 15}
    assert daily_profitability_status(positive, [good_window, good_window, good_window]) == "DAILY_NET_PROFITABILITY_POSITIVE"
    assert daily_profitability_status(positive, [good_window, good_window, {**good_window, "net_pnl": Decimal("-1")}]) == "DAILY_NET_PROFITABILITY_NOT_DEMONSTRATED"
    negative = {**positive, "net_pnl": Decimal("-10"), "daily_mean_usdt": Decimal("-1")}
    assert daily_profitability_status(negative, [good_window, good_window, good_window]) == "DAILY_NET_PROFITABILITY_NEGATIVE"



def _daily_metrics_result(points, fills=(), *, first_candle=None, initial=Decimal("1000")):
    return SimpleNamespace(first_candle=first_candle, equity_curve=tuple(points), fills=tuple(fills),
                           entry_rejections=(), config=SimpleNamespace(initial_quote_balance=initial))


def _equity_point(timestamp, *, close="10", quote="1000", position="0", equity=None):
    from bot_obrero.virtual_backtest import EquityPoint
    close_d, quote_d, position_d = Decimal(close), Decimal(quote), Decimal(position)
    return EquityPoint(timestamp, close_d, quote_d, position_d,
                       Decimal(equity) if equity is not None else quote_d + position_d * close_d,
                       Decimal("0"))


def _virtual_fill(timestamp, side, *, quantity="1", price="10", fee="0.1", realized=None):
    from bot_obrero.virtual_backtest import VirtualFill
    return VirtualFill(timestamp, side, Decimal(quantity), Decimal(price), Decimal(fee),
                       Decimal("0"), side.lower(), None if realized is None else Decimal(realized))


def test_daily_metrics_recognizes_six_inclusive_binance_four_hour_closes():
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    close_1ms = timedelta(milliseconds=1)
    timestamps = [start + timedelta(hours=4 * i) - close_1ms for i in range(1, 6)]
    timestamps.append(start + timedelta(days=1) - close_1ms)
    rows, summary = daily_equity_metrics(_daily_metrics_result(
        [_equity_point(ts, equity="1000") for ts in timestamps], initial=Decimal("1000")
    ))
    assert len(rows) == 1
    assert rows[0]["day_status"] == "FULL_UTC_DAY"
    assert summary["complete_days"] == 1


def test_daily_metrics_preserves_midnight_previous_period_attribution_but_not_full_day():
    midnight = datetime(2026, 6, 2, tzinfo=timezone.utc)
    rows, _ = daily_equity_metrics(_daily_metrics_result([
        _equity_point(midnight - timedelta(hours=4)), _equity_point(midnight)
    ]))
    assert rows[0]["day_utc"] == "2026-06-01"
    assert rows[0]["day_status"] == "PARTIAL_UTC_DAY"


def test_daily_metrics_rejects_duplicate_and_unordered_equity_timestamps():
    ts = datetime(2026, 6, 1, 4, tzinfo=timezone.utc)
    with pytest.raises(ProductV2Error, match="duplicate equity timestamp"):
        daily_equity_metrics(_daily_metrics_result([_equity_point(ts), _equity_point(ts)]))
    with pytest.raises(ProductV2Error, match="strictly increasing"):
        daily_equity_metrics(_daily_metrics_result([
            _equity_point(ts + timedelta(hours=4)), _equity_point(ts)
        ]))


def test_daily_metrics_fill_at_equity_timestamp_is_applied_only_to_next_point():
    first = datetime(2026, 7, 1, 20, tzinfo=timezone.utc)
    p1 = _equity_point(first + timedelta(hours=4), close="10", quote="989.9", position="1", equity="999.9")
    p2 = _equity_point(first + timedelta(hours=8), close="11", quote="989.9", position="1", equity="1000.9")
    fill = _virtual_fill(first + timedelta(hours=4), "BUY", price="10", fee="0.1")
    rows, _ = daily_equity_metrics(_daily_metrics_result([p1, p2], [fill], first_candle=first))
    assert rows[0]["day_utc"] == "2026-07-01"
    assert rows[0]["unrealized_pnl"] is None
    assert rows[1]["day_utc"] == "2026-07-02"
    assert rows[1]["unrealized_pnl"] == Decimal("0.9")
    assert rows[1]["buys"] == 1


def test_daily_metrics_rejects_fill_at_final_equity_timestamp_and_outside_horizon():
    first = datetime(2026, 8, 1, tzinfo=timezone.utc)
    last = first + timedelta(hours=8)
    points = [_equity_point(first + timedelta(hours=4)), _equity_point(last)]
    with pytest.raises(ProductV2Error, match="unobservable"):
        daily_equity_metrics(_daily_metrics_result(points, [_virtual_fill(last, "BUY")], first_candle=first))
    with pytest.raises(ProductV2Error, match="precedes backtest horizon"):
        daily_equity_metrics(_daily_metrics_result(points, [_virtual_fill(first - timedelta(microseconds=1), "BUY")], first_candle=first))
    with pytest.raises(ProductV2Error, match="exceeds backtest horizon"):
        daily_equity_metrics(_daily_metrics_result(points, [_virtual_fill(last + timedelta(microseconds=1), "BUY")], first_candle=first))


def test_daily_metrics_sell_pnl_is_attributed_to_sell_utc_day_and_cost_basis_includes_buy_fee():
    day1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    day2 = datetime(2026, 9, 2, tzinfo=timezone.utc)
    points = [
        _equity_point(day1 + timedelta(hours=20), close="10", quote="989.9", position="1", equity="999.9"),
        _equity_point(day2 + timedelta(hours=4), close="12", quote="989.9", position="1", equity="1001.9"),
        _equity_point(day2 + timedelta(hours=8), close="12", quote="1001.78", position="0", equity="1001.78"),
    ]
    buy = _virtual_fill(day1 + timedelta(hours=1), "BUY", price="10", fee="0.1")
    sell = _virtual_fill(day2 + timedelta(hours=5), "SELL", price="12", fee="0.12", realized="1.78")
    rows, summary = daily_equity_metrics(_daily_metrics_result(points, [buy, sell], first_candle=day1))
    row_by_day = {row["day_utc"]: row for row in rows}
    assert row_by_day["2026-09-02"]["realized_pnl"] == Decimal("1.78")
    assert row_by_day["2026-09-01"]["realized_pnl"] == Decimal("0")
    assert summary["fees"] == Decimal("0.22")
    assert summary["realized_pnl"] == Decimal("1.78")
    assert row_by_day["2026-09-01"]["unrealized_pnl"] == Decimal("-0.1")
    assert row_by_day["2026-09-02"]["unrealized_pnl"] == Decimal("0")


def test_daily_metrics_sell_without_known_buy_does_not_fabricate_zero_cost_basis():
    first = datetime(2026, 10, 1, tzinfo=timezone.utc)
    points = [
        _equity_point(first + timedelta(hours=4), close="10", quote="900", position="10", equity="1000"),
        _equity_point(first + timedelta(hours=8), close="10", quote="950", position="5", equity="1000"),
    ]
    sell = _virtual_fill(first + timedelta(hours=5), "SELL", quantity="5", price="10", fee="0.1", realized="0")
    rows, _ = daily_equity_metrics(_daily_metrics_result(points, [sell], first_candle=first))
    assert rows[-1]["unrealized_pnl"] is None


def test_daily_metrics_equity_pnl_fees_reconcile_for_valid_buy_hold_sell_sequence():
    first = datetime(2026, 11, 1, tzinfo=timezone.utc)
    close_1ms = timedelta(milliseconds=1)
    buy = _virtual_fill(first, "BUY", price="10", fee="0.1")
    sell = _virtual_fill(first + timedelta(days=2, hours=6), "SELL", price="12", fee="0.12", realized="1.78")
    points = [
        _equity_point(first + timedelta(days=1) - close_1ms, close="10", quote="989.9", position="1", equity="999.9"),
        _equity_point(first + timedelta(days=2) - close_1ms, close="12", quote="989.9", position="1", equity="1001.9"),
        _equity_point(first + timedelta(days=3) - close_1ms, close="12", quote="1001.78", position="0", equity="1001.78"),
    ]
    rows, summary = daily_equity_metrics(_daily_metrics_result(points, [buy, sell], first_candle=first))
    assert rows[0]["unrealized_pnl"] == Decimal("-0.1")
    assert rows[1]["unrealized_pnl"] == Decimal("1.9")
    assert rows[-1]["unrealized_pnl"] == Decimal("0")
    assert summary["net_pnl"] == Decimal("1.78")
    assert summary["realized_pnl"] == Decimal("1.78")
    assert summary["final_unrealized_pnl"] == Decimal("0")
    assert summary["fees"] == Decimal("0.22")

def test_daily_metrics_aggregates_worst_daily_close_drawdown_as_negative_percentage():
    start = datetime(2026, 12, 1, tzinfo=timezone.utc)
    close_1ms = timedelta(milliseconds=1)
    equities = ("100", "80", "90")
    points = [
        _equity_point(start + timedelta(days=index + 1) - close_1ms, close=equity, quote=equity, equity=equity)
        for index, equity in enumerate(equities)
    ]

    rows, summary = daily_equity_metrics(_daily_metrics_result(points, initial=Decimal("100")))

    expected = min(row["daily_drawdown"] for row in rows) * Decimal("100")
    assert [row["equity_close"] for row in rows] == [Decimal("100"), Decimal("80"), Decimal("90")]
    assert expected == Decimal("-20.0")
    assert summary["max_daily_close_drawdown_pct"] == expected


def test_daily_metrics_aggregated_close_drawdown_stays_zero_without_a_decline():
    start = datetime(2026, 12, 1, tzinfo=timezone.utc)
    close_1ms = timedelta(milliseconds=1)
    equities = ("100", "110", "120")
    points = [
        _equity_point(start + timedelta(days=index + 1) - close_1ms, close=equity, quote=equity, equity=equity)
        for index, equity in enumerate(equities)
    ]

    rows, summary = daily_equity_metrics(_daily_metrics_result(points, initial=Decimal("100")))

    assert all(row["daily_drawdown"] == Decimal("0") for row in rows)
    assert summary["max_daily_close_drawdown_pct"] == Decimal("0")




def test_market_orders_use_market_lot_size_not_the_regular_lot_size():
    filters = parse_filters([
        {"filterType": "LOT_SIZE", "minQty": "0.0100", "maxQty": "1000.0000", "stepSize": "0.0100"},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0.1000", "maxQty": "10.0000", "stepSize": "0.1000"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5.00"},
    ])
    assert filters.min_qty == Decimal("0.0100")
    assert filters.step_size == Decimal("0.0100")
    assert filters.market_lot_size_present is True
    assert filters.market_min_qty == Decimal("0.1000")
    assert filters.market_step_size == Decimal("0.1000")
    assert filters.market_max_qty == Decimal("10.0000")


def test_missing_market_lot_size_blocks_market_order_backtest():
    from bot_obrero.product_v2 import InstrumentFilters, backtest

    filters = InstrumentFilters(Decimal("0.01"), Decimal("0.01"), Decimal("5"), market_lot_size_present=False)
    with pytest.raises(ProductV2Error, match="MARKET_LOT_SIZE_UNKNOWN"):
        backtest("ETHUSDT", [], 10, filters)


def test_normalized_affordability_blocks_unknown_market_lot_size():
    from bot_obrero.product_v2 import InstrumentFilters, normalized_affordability

    filters = InstrumentFilters(Decimal("0.01"), Decimal("0.01"), Decimal("5"), market_lot_size_present=False)
    result = normalized_affordability(
        Decimal("100"), filters, Decimal("100"),
        fee_rate=Decimal("0.001"), spread_bps=Decimal("10"),
        slippage_bps=Decimal("5"), position_fraction=Decimal("0.95"),
    )
    assert result == (None, "MARKET_LOT_SIZE_UNKNOWN")



def test_market_min_qty_zero_is_valid_and_disables_only_lower_bound(monkeypatch):
    from types import SimpleNamespace
    from bot_obrero import product_v2

    filters = product_v2.parse_filters([
        {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "1000", "stepSize": "0.001"},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0", "maxQty": "100", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
    ])
    assert filters.market_lot_size_present is True
    assert filters.market_min_qty == Decimal("0")
    captured = []
    monkeypatch.setattr(
        product_v2, "run_virtual_backtest",
        lambda items, config, **kwargs: captured.append((config, kwargs)) or SimpleNamespace(config=config),
    )
    product_v2.backtest("ETHUSDT", [], 6, filters, interval="1h")
    assert captured[0][0].interval == "1h"
    assert captured[0][0].min_qty is None
    assert captured[0][0].quantity_step == Decimal("0.001")
    assert captured[0][0].max_qty == Decimal("100")



def test_zero_market_lot_fields_fall_back_only_to_valid_lot_size_constraints():
    filters = __import__("bot_obrero.product_v2", fromlist=["InstrumentFilters"]).InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0.001"),
        min_notional=Decimal("5"), max_qty=Decimal("1000"),
        market_min_qty=Decimal("0"), market_step_size=Decimal("0"),
        market_max_qty=Decimal("0"), market_lot_size_present=True,
    )
    assert effective_market_lot_rules(filters) == (
        None, Decimal("0.001"), Decimal("1000")
    )


def test_zero_market_lot_fields_without_positive_fallback_fail_closed():
    filters = __import__("bot_obrero.product_v2", fromlist=["InstrumentFilters"]).InstrumentFilters(
        min_qty=None, step_size=None, min_notional=Decimal("5"), max_qty=None,
        market_min_qty=Decimal("0"), market_step_size=Decimal("0"),
        market_max_qty=Decimal("0"), market_lot_size_present=True,
    )
    with pytest.raises(ProductV2Error, match="MARKET_LOT_SIZE_INVALID_OR_INCOMPLETE"):
        effective_market_lot_rules(filters)


def test_product_backtest_uses_effective_market_lot_rules(monkeypatch):
    from bot_obrero import product_v2

    captured = []

    def fake_run(items, config, **kwargs):
        captured.append(config)
        return SimpleNamespace(config=config)

    monkeypatch.setattr(product_v2, "run_virtual_backtest", fake_run)
    filters = product_v2.InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0.001"),
        min_notional=Decimal("5"), max_qty=Decimal("1000"),
        market_min_qty=Decimal("0.01"), market_step_size=Decimal("0.01"),
        market_max_qty=Decimal("100"), market_lot_size_present=True,
    )
    result = product_v2.backtest("ALTUSDT", [], 1080, filters=filters)
    assert result.config.min_qty == Decimal("0.01")
    assert result.config.quantity_step == Decimal("0.01")
    assert result.config.max_qty == Decimal("100")



def test_normalized_affordability_fails_closed_on_zero_market_and_regular_steps():
    from bot_obrero.product_v2 import InstrumentFilters, normalized_affordability

    filters = InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0"),
        min_notional=Decimal("5"), max_qty=Decimal("1000"),
        market_min_qty=Decimal("0.001"), market_step_size=Decimal("0"),
        market_max_qty=Decimal("0"), market_lot_size_present=True,
    )
    result = normalized_affordability(
        Decimal("2"), filters, Decimal("100"),
        fee_rate=Decimal("0.001"), spread_bps=Decimal("10"),
        slippage_bps=Decimal("5"), position_fraction=Decimal("0.95"),
    )
    assert result == (None, "MARKET_LOT_SIZE_UNKNOWN_OR_INVALID")


def test_normalized_affordability_uses_valid_market_step_when_regular_step_is_zero():
    from bot_obrero.product_v2 import InstrumentFilters, normalized_affordability

    filters = InstrumentFilters(
        min_qty=Decimal("0.001"), step_size=Decimal("0"),
        min_notional=Decimal("5"), max_qty=Decimal("1000"),
        market_min_qty=Decimal("0.001"), market_step_size=Decimal("0.01"),
        market_max_qty=Decimal("100"), market_lot_size_present=True,
    )
    result = normalized_affordability(
        Decimal("2"), filters, Decimal("100"),
        fee_rate=Decimal("0.001"), spread_bps=Decimal("10"),
        slippage_bps=Decimal("5"), position_fraction=Decimal("0.95"),
    )
    assert result == (True, "PASS")
