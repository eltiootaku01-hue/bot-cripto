from __future__ import annotations
import math
import numpy as np
import pandas as pd
from decimal import Decimal
from research import btc_eth_ratio_trend_weekly_v1 as study

def synthetic_frames(days=2851):
    idx = pd.date_range("2019-01-01T00:00:00Z", periods=days, freq="D")
    t = np.arange(days, dtype=float)
    # Deterministic non-constant ratio creates relative-trend transitions.
    btc = 9000.0 * np.exp(0.00045*t + 0.12*np.sin(t/190.0) + 0.015*np.sin(t/8.0))
    eth = 350.0 * np.exp(0.00043*t + 0.23*np.sin(t/115.0 + 0.5) + 0.023*np.sin(t/11.0))
    out = {}
    for symbol, close in (("BTCUSDT", btc), ("ETHUSDT", eth)):
        open_ = np.r_[close[0]*0.998, close[:-1]]
        high = np.maximum(open_, close)*1.018
        low = np.minimum(open_, close)*0.982
        frame = pd.DataFrame({
            "timestamp": idx, "open": open_, "high": high, "low": low,
            "close": close, "volume": np.full(days, 1000.0),
            "open_s": [f"{x:.8f}" for x in open_],
            "high_s": [f"{x:.8f}" for x in high],
            "low_s": [f"{x:.8f}" for x in low],
            "close_s": [f"{x:.8f}" for x in close],
        }).set_index("timestamp", drop=False)
        out[symbol] = frame
    return out

def test_indicators_and_targets_are_prefix_invariant_no_lookahead():
    full = synthetic_frames()
    full_ind = study.compute_indicators(full)
    cut = 2000
    prefix = {s: f.iloc[:cut].copy() for s, f in full.items()}
    prefix_ind = study.compute_indicators(prefix)
    for col in ("ratio_eth_btc", "ratio_ema90", "ema200", "target", "target_weight"):
        a = full_ind["SHARED"][col].iloc[:cut].reset_index(drop=True)
        b = prefix_ind["SHARED"][col].reset_index(drop=True)
        pd.testing.assert_series_equal(a, b, check_names=False, check_dtype=False)
    assert all(ts.weekday() == 6 for ts in full_ind["SHARED"].index[full_ind["SHARED"]["weekly_decision"]])

def test_cost_scenarios_get_strictly_more_adverse_and_quantity_rounds_down():
    outputs = {}
    for name, cost in study.COSTS.items():
        buy, fee = study._cost_price(Decimal("100"), "BUY", cost)
        sell, _ = study._cost_price(Decimal("102"), "SELL", cost)
        outputs[name] = sell*(Decimal("1")-fee)/(buy*(Decimal("1")+fee))-Decimal("1")
    assert outputs["BASE"] > outputs["STRESS_1"] > outputs["STRESS_2"]
    assert study._floor_qty(Decimal("0.123459"), Decimal("0.00001000")) == Decimal("0.12345000")
    assert study._floor_qty(Decimal("0.000009"), Decimal("0.00001000")) == Decimal("0E-8")

def test_atr_weights_are_bounded_and_decrease_when_volatility_rises():
    quiet = synthetic_frames()
    high_vol = synthetic_frames()
    for symbol in ("BTCUSDT", "ETHUSDT"):
        d = high_vol[symbol].copy()
        mid = d.close.to_numpy()
        d["high"] = np.maximum(d.open.to_numpy(), mid)*1.05
        d["low"] = np.minimum(d.open.to_numpy(), mid)*0.95
        high_vol[symbol] = d
    a = study.compute_indicators(quiet)
    b = study.compute_indicators(high_vol)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        base = a[symbol]["atr14"] / a[symbol]["close"]
        other = b[symbol]["atr14"] / b[symbol]["close"]
        valid = base.notna() & other.notna() & (base > 0) & (other > 0)
        w1 = np.minimum(study.MAX_WEIGHT, study.ATR_TARGET/(base[valid]*math.sqrt(365.0)))
        w2 = np.minimum(study.MAX_WEIGHT, study.ATR_TARGET/(other[valid]*math.sqrt(365.0)))
        assert (w1 > 0).all() and (w1 <= study.MAX_WEIGHT).all()
        assert (w2 <= w1 + 1e-12).all()

def test_next_open_fills_one_position_and_terminal_liquidation():
    indicators = study.compute_indicators(synthetic_frames())
    result = study.simulate_window(indicators, "RECENT_2023_2026_YTD", Decimal("60"), "STRESS_2", study.COSTS["STRESS_2"])
    assert result["status"] == "COMPLETED"
    assert math.isfinite(float(result["net_pnl_usdt"]))
    assert math.isfinite(float(result["max_drawdown_pct"]))
    assert all(float(x["equity_net_liquidation_usdt"]) >= 0 for x in result["equity"])
    trades = result["trades"]
    for trade in trades:
        signal_close = pd.Timestamp(trade["signal_bar_utc"])
        entry = pd.Timestamp(trade["entry_bar_utc"])
        exit_ = pd.Timestamp(trade["exit_bar_utc"])
        assert entry > signal_close
        assert signal_close.weekday() == 6
        assert exit_ >= entry
        assert Decimal(trade["quantity"]) > 0
    for previous, current in zip(trades, trades[1:]):
        assert pd.Timestamp(current["entry_bar_utc"]) >= pd.Timestamp(previous["exit_bar_utc"])
    assert not trades or trades[-1]["terminal_liquidation"] is True
