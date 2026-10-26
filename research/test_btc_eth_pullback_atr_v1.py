from __future__ import annotations
import math
import numpy as np
import pandas as pd
from research import btc_eth_pullback_atr_v1 as study

def synthetic_4h(days=520, phase=0.0):
    idx = pd.date_range("2024-01-01T00:00:00Z", periods=days*6, freq="4h")
    bars = {}
    n = len(idx)
    t = np.arange(n, dtype=float)
    for symbol, base, drift, amplitude in (
        ("BTCUSDT", 100.0, 0.00010, 0.024),
        ("ETHUSDT", 50.0, 0.00009, 0.031),
    ):
        close = base * np.exp(drift*t + amplitude*np.sin(t/25.0+phase) + 0.011*np.sin(t/4.3+phase))
        open_ = np.r_[close[0]*(1-0.001), close[:-1]]
        high = np.maximum(open_, close) * 1.004
        low = np.minimum(open_, close) * 0.996
        bars[symbol] = pd.DataFrame({
            "timestamp": idx, "open": open_, "high": high, "low": low,
            "close": close, "volume": np.full(n, 100.0), "source_hour_count": np.full(n, 4),
        }).set_index("timestamp", drop=False)
    return bars

def test_daily_aggregation_accepts_only_six_contiguous_complete_bars():
    bars = synthetic_4h(days=3)["BTCUSDT"]
    accepted = study.aggregate_daily(bars.iloc[:-2])
    assert len(accepted) == 2
    assert (accepted.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all()

def test_indicators_and_signals_are_invariant_to_future_suffix():
    full = synthetic_4h(days=520)
    all_ind = study.prepare_indicators(full)
    prefix_len = 390*6
    prefix = {s: d.iloc[:prefix_len].copy() for s, d in full.items()}
    prefix_ind = study.prepare_indicators(prefix)
    compare_len = 350*6
    cols = ["ema20", "ema50", "ema200", "atr14", "momentum42", "macro_btc_close", "macro_btc_ema200", "buy_signal", "exit_signal"]
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for col in cols:
            a = all_ind[symbol][col].iloc[:compare_len].reset_index(drop=True)
            b = prefix_ind[symbol][col].iloc[:compare_len].reset_index(drop=True)
            pd.testing.assert_series_equal(a, b, check_names=False, check_dtype=False)

def test_modeled_costs_are_strictly_more_adverse_and_capped_weight_is_valid():
    prices = {}
    for name, cost in study.COSTS.items():
        buy, fee = study.cost_price(100.0, cost, "BUY")
        sell, _ = study.cost_price(102.0, cost, "SELL")
        prices[name] = sell*(1-fee)/(buy*(1+fee))-1
    assert prices["BASE"] > prices["STRESS_1"] > prices["STRESS_2"]
    assert study.floor_qty(0.123459) == 0.12345
    assert study.floor_qty(0.000009) == 0.0
    bars = synthetic_4h(days=520)
    indicators = study.prepare_indicators(bars)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        weights = indicators[symbol]["weight"].dropna()
        assert not weights.empty
        assert (weights > 0).all()
        assert (weights <= study.MAX_WEIGHT).all()

def test_virtual_replay_has_next_open_fills_no_overlap_and_nonnegative_equity():
    indicators = study.prepare_indicators(synthetic_4h(days=1050))
    results = [
        study.simulate_window(indicators, "2025", 60.0, "BASE", study.COSTS["BASE"]),
        study.simulate_window(indicators, "2026_YTD", 100.0, "STRESS_2", study.COSTS["STRESS_2"]),
    ]
    for result in results:
        assert result["status"] == "COMPLETED"
        assert math.isfinite(result["net_pnl_usdt"])
        assert math.isfinite(result["max_drawdown_pct"])
        assert result["net_pnl_usdt"] >= -result["initial_usdt"]
        assert all(point["equity_net_liquidation_usdt"] >= 0 for point in result["equity"])
        trades = result["trades"]
        for trade in trades:
            assert pd.Timestamp(trade["entry_time"]) == pd.Timestamp(trade["signal_close_time"])
            assert pd.Timestamp(trade["exit_time"]) >= pd.Timestamp(trade["entry_time"])
            assert trade["quantity"] > 0
            assert trade["entry_debit_usdt"] <= result["initial_usdt"] + 1e-8 or trade["entry_weight"] <= 0.50
        for prev, curr in zip(trades, trades[1:]):
            assert pd.Timestamp(curr["entry_time"]) >= pd.Timestamp(prev["exit_time"])
