from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research import strategy_tournament_v2 as tournament


def _synthetic_frames(length: int = 720) -> dict[str, pd.DataFrame]:
    index = np.arange(length, dtype=np.float64)
    timestamps = pd.date_range("2024-01-01T00:00:00Z", periods=length, freq="h")
    frames: dict[str, pd.DataFrame] = {}
    for symbol, base, phase in (("BTCUSDT", 40000.0, 0.0), ("ETHUSDT", 2000.0, 0.5)):
        close = base * np.exp(0.00005 * index + 0.018 * np.sin(index / 19.0 + phase) + 0.006 * np.sin(index / 5.7))
        opn = close * (1.0 + 0.001 * np.sin(index / 3.1 + phase))
        high = np.maximum(opn, close) * 1.003
        low = np.minimum(opn, close) * 0.997
        frames[symbol] = pd.DataFrame({
            "timestamp": timestamps,
            "open": opn,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(length, 100.0),
        })
    return frames


def test_grid_is_deterministic_and_contains_thousands_of_unique_variants() -> None:
    first = tournament.build_candidates()
    second = tournament.build_candidates()

    assert len(first) == 6232
    assert [item["candidate_id"] for item in first] == [item["candidate_id"] for item in second]
    assert len({(item["family"], json.dumps(item["params"], sort_keys=True)) for item in first}) == len(first)
    assert {item["family"] for item in first} == {
        "EMA_CROSS_RSI", "TSMOM_TREND", "DONCHIAN_BREAKOUT", "RSI_MEAN_REVERSION"
    }
    assert tournament.canonical_sha256([{"family": c["family"], "params": c["params"]} for c in first]) == (
        "76b6abf4f61bc05135ea613c813dce2f5c1a44c4869ed42ed3faa18cdf625eae"
    )


def test_rsi_wilder_is_bounded_and_reproducible() -> None:
    close = 100.0 + np.arange(80, dtype=np.float64) + 3.0 * np.sin(np.arange(80) / 3.0)
    result = tournament.rsi_wilder(close, 14)

    assert np.isnan(result[:14]).all()
    assert np.isfinite(result[14:]).all()
    assert np.all((result[14:] >= 0.0) & (result[14:] <= 100.0))
    np.testing.assert_array_equal(result, tournament.rsi_wilder(close, 14))


def test_representative_signals_are_suffix_invariant_for_each_family() -> None:
    features = tournament.compute_features(_synthetic_frames())
    representatives = [
        {"candidate_id": "EMA", "family": "EMA_CROSS_RSI", "params": {"fast_ema": 8, "slow_ema": 24, "entry_rsi_min": 30, "exit_rsi_at": 70}},
        {"candidate_id": "MOM", "family": "TSMOM_TREND", "params": {"lookback_hours": 24, "trend_ema": 50, "btc_gate_ema": 50, "entry_momentum_min": 0.01, "exit_momentum_at": 0.0}},
        {"candidate_id": "DON", "family": "DONCHIAN_BREAKOUT", "params": {"entry_window": 18, "exit_window": 6, "trend_ema": None}},
        {"candidate_id": "RSI", "family": "RSI_MEAN_REVERSION", "params": {"rsi_period": 7, "entry_rsi_at_or_below": 30, "exit_rsi_at": 55, "trend_ema": None}},
    ]

    for candidate in representatives:
        buy, sell = tournament.signals_for(candidate, features)
        assert buy.dtype == np.bool_ and sell.dtype == np.bool_
        assert tournament.anti_lookahead_smoke(candidate, features, cutoff=550)


def test_signal_timestamp_executes_at_next_open_and_costs_reduce_constant_price_result() -> None:
    count = 48
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01T00:00:00Z", periods=count, freq="h"),
        "open": np.full(count, 100.0),
        "high": np.full(count, 101.0),
        "low": np.full(count, 99.0),
        "close": np.full(count, 100.0),
        "volume": np.full(count, 100.0),
    })
    buys = np.zeros((1, count), dtype=bool)
    sells = np.zeros((1, count), dtype=bool)
    buys[0, 3] = True  # signal at bar 3 close; entry is attempted at bar 4 open.
    sells[0, 10] = True  # exit is attempted at bar 11 open.
    filters = {"min_qty": 0.0001, "step": 0.0001, "max_qty": 3500.0, "min_notional": 5.0}

    result_by_cost = [
        tournament.simulate_batch(buys, sells, frame, 0, count, cost, filters)
        for cost in tournament.COSTS
    ]

    assert [int(result["buys"][0]) for result in result_by_cost] == [1, 1, 1]
    assert [int(result["sells"][0]) for result in result_by_cost] == [1, 1, 1]
    assert result_by_cost[0]["return_pct"][0] > result_by_cost[1]["return_pct"][0]
    assert result_by_cost[1]["return_pct"][0] > result_by_cost[2]["return_pct"][0]
    assert result_by_cost[0]["return_pct"][0] < 0.0


def test_buy_hold_has_three_scenarios_and_consistent_daily_series() -> None:
    frame = _synthetic_frames(length=1440)["ETHUSDT"].iloc[:1440].reset_index(drop=True)
    result = tournament.buy_hold_benchmark(frame, 0, len(frame), tournament.COSTS[0])

    assert len(result["daily_log_returns"]) == 60
    assert np.isfinite(result["daily_log_returns"]).all()
    assert np.isfinite(result["return_pct"])


def test_bootstrap_max_statistic_is_reproducible_and_adjusted_for_family_size() -> None:
    rng = np.random.default_rng(1234)
    candidate_returns = rng.normal(0.0001, 0.01, size=(12, 3, 60))
    benchmark_returns = rng.normal(0.00005, 0.008, size=(3, 60))
    kwargs = {"replicates": 120, "seed": 555, "block_days": 5}

    one = tournament.bootstrap_max_mean_excess(candidate_returns, benchmark_returns, **kwargs)
    two = tournament.bootstrap_max_mean_excess(candidate_returns, benchmark_returns, **kwargs)

    assert one == two
    assert one["unadjusted_candidate_count"] == 12
    assert 0.0 < one["exploratory_familywise_p_value"] <= 1.0
    assert one["method"].startswith("fold-stratified_circular_moving-block_bootstrap")


def test_read_dataset_refuses_an_unpinned_hash(tmp_path: Path) -> None:
    path = tmp_path / "forged.json"
    path.write_text(json.dumps({"dataset_sha256": "wrong", "configuration_sha256": "wrong"}), encoding="utf-8")

    with pytest.raises(ValueError, match="PINNED_DATASET_SHA256_MISMATCH"):
        tournament.read_dataset(path)


def test_cli_rejects_too_few_bootstrap_replicates() -> None:
    with pytest.raises(SystemExit) as exc:
        tournament.main(["--dataset", "missing.json", "--output-dir", "out", "--bootstrap-replicates", "10"])
    assert exc.value.code == 2