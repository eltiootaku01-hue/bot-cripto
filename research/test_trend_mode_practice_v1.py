from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from research import trend_mode_practice_v1 as study

def synthetic_hourly(groups: int = 360, drop_last_hours: int = 0) -> dict[str, pd.DataFrame]:
    index = pd.date_range("2024-01-01T00:00:00Z", periods=groups*4, freq="h")
    frames = {}
    i = np.arange(groups, dtype=float)
    series = {
        "BTCUSDT": 100.0*np.exp(0.0010*i + 0.035*np.sin(i/8.0) + 0.008*np.sin(i/2.8)),
        "ETHUSDT": 50.0*np.exp(0.0007*i + 0.041*np.sin(i/9.0+0.3) + 0.009*np.sin(i/3.2)),
    }
    for symbol, targets in series.items():
        prev = float(targets[0]*0.998)
        rows = []
        for group, target in enumerate(targets):
            hourly_close = np.linspace(prev, float(target), 4)
            for j, close in enumerate(hourly_close):
                opn = prev if j == 0 else float(hourly_close[j-1])
                rows.append({"timestamp": index[group*4+j], "open": opn,
                    "high": max(opn, float(close))*(1.0008 + 0.0001*(j%2)),
                    "low": min(opn, float(close))*(0.9992 - 0.0001*(j%2)),
                    "close": float(close), "volume": 10.0})
            prev = float(target)
        df = pd.DataFrame(rows).set_index("timestamp", drop=False)
        if drop_last_hours: df = df.iloc[:-drop_last_hours]
        frames[symbol] = df
    return frames

def test_aggregate_only_accepts_complete_contiguous_utc_four_hour_buckets():
    aggregated = study.aggregate_4h(synthetic_hourly(groups=20, drop_last_hours=2)["BTCUSDT"])
    assert len(aggregated) == 19
    assert (aggregated.source_hour_count == 4).all()
    assert (aggregated.index.to_series().diff().dropna() == pd.Timedelta(hours=4)).all()

def test_indicators_exclude_current_bar_from_donchian_channels():
    frame = study.aggregate_4h(synthetic_hourly()["BTCUSDT"])
    baseline = study.add_indicators(frame)
    changed = frame.copy()
    target = frame.index[280]
    changed.loc[target, "high"] = float(changed.loc[target, "high"])*5
    altered = study.add_indicators(changed)
    assert baseline.loc[target, "donchian_entry_high"] == altered.loc[target, "donchian_entry_high"]
    assert baseline.loc[target, "donchian_exit_low"] == altered.loc[target, "donchian_exit_low"]

def test_indicator_and_signal_prefixes_are_invariant_to_future_suffix():
    frame = study.aggregate_4h(synthetic_hourly(groups=370)["ETHUSDT"])
    full = study.add_indicators(frame)
    cutoff = 300
    prefix = study.add_indicators(frame.iloc[:cutoff].copy())
    pd.testing.assert_series_equal(full.buy_signal.iloc[:cutoff].reset_index(drop=True), prefix.buy_signal.reset_index(drop=True))
    pd.testing.assert_series_equal(full.donchian_entry_high.iloc[:cutoff].reset_index(drop=True), prefix.donchian_entry_high.reset_index(drop=True))
    pd.testing.assert_series_equal(full.ema200.iloc[:cutoff].reset_index(drop=True), prefix.ema200.reset_index(drop=True))

def test_stop_fill_never_beats_stop_or_gap_open_and_no_fill_without_breach():
    assert study._stop_reference_fill(100.0, 94.0, 95.0) == 95.0
    assert study._stop_reference_fill(92.0, 90.0, 95.0) == 92.0
    assert study._stop_reference_fill(100.0, 96.0, 95.0) is None
    with pytest.raises(study.TrendReplayError, match="INVALID_STOP_INPUT"):
        study._stop_reference_fill(100.0, 94.0, 0.0)

def test_modeled_cost_scenarios_are_strictly_more_adverse():
    returns = {}
    for name, cost in study.COSTS.items():
        buy, fee = study.cost_prices(100.0, cost, "BUY")
        sell, _ = study.cost_prices(102.0, cost, "SELL")
        returns[name] = sell*(1-fee)/(buy*(1+fee))-1
    assert returns["BASE"] > returns["STRESS_1"] > returns["STRESS_2"]
    assert study.cost_prices(100.0, study.COSTS["BASE"], "BUY")[0] > 100
    assert study.cost_prices(100.0, study.COSTS["BASE"], "SELL")[0] < 100

def test_simulation_uses_one_position_and_next_four_hour_open_fills():
    result = study.simulate(synthetic_hourly(groups=370), study.COSTS["BASE"])
    trades = result["trades"]
    assert trades
    indicators = result["indicator_data"]
    for trade in trades:
        entry = pd.Timestamp(trade["entry_time"])
        signal_time = pd.Timestamp(trade["signal_time"])
        assert signal_time == entry
        bars = indicators[trade["symbol"]]
        assert trade["entry_reference_open"] == pytest.approx(float(bars.loc[entry, "open"]))
        signal_bar_time = entry-pd.Timedelta(hours=4)
        assert bool(bars.loc[signal_bar_time, "buy_signal"])
        assert pd.Timestamp(trade["exit_time"]) >= entry
    for earlier, later in zip(trades, trades[1:]):
        assert pd.Timestamp(later["entry_time"]) >= pd.Timestamp(earlier["exit_time"])
    assert not result["metrics"]["open_position_at_end"]
    assert len(result["equity"]) == len(result["indicator_data"]["BTCUSDT"])+1
    assert min(x["equity_net_liquidation_usdt"] for x in result["equity"]) >= 0
    assert result["metrics"]["rejected_signals"]["position_open"] >= 0

def test_trade_entries_never_exceed_rolling_30_day_cap():
    result = study.simulate(synthetic_hourly(groups=370), study.COSTS["BASE"])
    entries = sorted(pd.Timestamp(t["entry_time"]) for t in result["trades"])
    for start in entries:
        assert sum(start-pd.Timedelta(days=30) < x <= start for x in entries) <= study.MAX_ENTRIES_ROLLING_30D

def test_pinned_archive_integrity_and_unknown_point_in_time_flags():
    archive = Path("/mnt/data/btc-eth-historical/btc-eth-follow-365d-dataset.json")
    if not archive.is_file(): pytest.skip("the pinned archived dataset is fully verified by the dedicated GitHub Actions replay")
    bundle, frames, raw_sha, missing, unknown = study.load_dataset(archive)
    assert raw_sha == study.EXPECTED_RAW_SHA256
    assert all(len(frame) == 15360 for frame in frames.values())
    assert missing == {"BTCUSDT": 15360, "ETHUSDT": 15360}
    assert unknown == {"BTCUSDT": 15360, "ETHUSDT": 15360}

def test_invalid_raw_sha_is_rejected(tmp_path: Path):
    bad = tmp_path/"bad.json"
    bad.write_text(json.dumps({"dataset_sha256": study.EXPECTED_DATASET_SHA256,
        "configuration_sha256": study.EXPECTED_CONFIG_SHA256}), encoding="utf-8")
    with pytest.raises(study.TrendReplayError, match="PINNED_RAW_DATASET_SHA256_MISMATCH"):
        study.load_dataset(bad)
