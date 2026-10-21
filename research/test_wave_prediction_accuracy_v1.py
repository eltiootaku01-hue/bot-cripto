
from __future__ import annotations

import numpy as np
import pandas as pd

from research.wave_prediction_accuracy_v1 import (
    ABSTAIN,
    STRATEGIES,
    build_features,
    future_wave_label,
    signal_series,
)


def synthetic_frame(seed: int, n: int = 360) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.00015, 0.005, n)
    close = 100.0 * np.exp(np.cumsum(steps))
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0001, 0.004, n))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0001, 0.004, n))
    start = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "start": start,
        "end": start + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(1.0, 100.0, n),
    })


def test_wave_labels_use_frozen_half_percent_threshold() -> None:
    assert future_wave_label(0.005) == "UP"
    assert future_wave_label(-0.005) == "DOWN"
    assert future_wave_label(0.00499) == "NO_WAVE"
    assert future_wave_label(-0.00499) == "NO_WAVE"


def test_donchian_window_excludes_current_decision_candle() -> None:
    frame = synthetic_frame(17, 80)
    features = build_features(frame)
    idx = 30
    prior = frame["high"].iloc[idx - 24:idx].max()
    assert features["prior_high24"].iloc[idx] == prior
    assert features["prior_high24"].iloc[idx] != frame["high"].iloc[idx] if frame["high"].iloc[idx] > prior else True


def test_signal_series_is_causal_under_future_suffix_perturbation() -> None:
    decision_index = 250
    btc = synthetic_frame(11)
    eth = synthetic_frame(29)
    btc_features = build_features(btc)
    eth_features = build_features(eth)
    original_btc = signal_series(btc_features)
    original_eth = signal_series(eth_features, btc_features)

    altered_btc = btc.copy()
    altered_eth = eth.copy()
    for frame in (altered_btc, altered_eth):
        j = np.arange(len(frame))
        mask = j > decision_index
        factor = np.where(j[mask] % 2 == 0, 1.12, 0.89)
        frame.loc[mask, "open"] = frame.loc[mask, "open"].to_numpy() * factor
        frame.loc[mask, "close"] = frame.loc[mask, "close"].to_numpy() * factor
        frame.loc[mask, "high"] = frame.loc[mask, ["open", "close"]].max(axis=1).to_numpy() * 1.01
        frame.loc[mask, "low"] = frame.loc[mask, ["open", "close"]].min(axis=1).to_numpy() * 0.99

    altered_btc_features = build_features(altered_btc)
    altered_eth_features = build_features(altered_eth)
    new_btc = signal_series(altered_btc_features)
    new_eth = signal_series(altered_eth_features, altered_btc_features)
    assert set(original_btc) == set(new_btc)
    assert set(original_eth) == set(new_eth)
    for name in original_btc:
        assert original_btc[name][decision_index] == new_btc[name][decision_index], name
    for name in original_eth:
        assert original_eth[name][decision_index] == new_eth[name][decision_index], name


def test_only_fixed_direction_or_abstain_outputs_are_emitted() -> None:
    frame = synthetic_frame(4)
    features = build_features(frame)
    outputs = signal_series(features, features)
    assert set(outputs) == set(STRATEGIES)
    allowed = {"UP", "DOWN", ABSTAIN}
    for series in outputs.values():
        assert set(series).issubset(allowed)
        assert len(series) == len(frame)
