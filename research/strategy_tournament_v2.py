from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

INITIAL = 31.0
FRACTION = 0.95
MAX_ENTRIES_30D = 5
BARS_30D = 30 * 24
WARMUP = 90 * 24
VALIDATION_BARS = 60 * 24
EVALUATION_BARS = 365 * 24
EXPECTED_DATASET_SHA256 = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
EXPECTED_CONFIGURATION_SHA256 = "2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a"
COSTS = (
    {"name": "BASE", "fee": 0.001, "spread_bps": 10.0, "slippage_bps": 5.0},
    {"name": "STRESS_1", "fee": 0.001, "spread_bps": 25.0, "slippage_bps": 10.0},
    {"name": "STRESS_2", "fee": 0.002, "spread_bps": 50.0, "slippage_bps": 20.0},
)
EMAS = (5, 6, 8, 10, 12, 16, 20, 24, 30, 36, 48, 50, 60, 72, 96, 100, 120, 144, 150, 168, 200)
MOMENTUM_LOOKBACKS = (24, 48, 72, 96, 120, 168, 240, 336, 504)
DONCHIAN_ENTRIES = (12, 18, 24, 36, 48, 72, 96)
DONCHIAN_EXITS = (4, 6, 10, 12, 18, 24, 36)
RSI_PERIODS = (7, 10, 14, 21)
BOOTSTRAP_BLOCK_DAYS = 5


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_dataset(path: Path) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("dataset_sha256") != EXPECTED_DATASET_SHA256:
        raise ValueError("PINNED_DATASET_SHA256_MISMATCH")
    if bundle.get("configuration_sha256") != EXPECTED_CONFIGURATION_SHA256:
        raise ValueError("PINNED_CONFIGURATION_SHA256_MISMATCH")
    if canonical_sha256(bundle.get("configuration")) != bundle["configuration_sha256"]:
        raise ValueError("CONFIGURATION_HASH_RECOMPUTE_FAILED")
    if canonical_sha256(bundle.get("data")) != bundle["dataset_sha256"]:
        raise ValueError("DATASET_HASH_RECOMPUTE_FAILED")
    frames: dict[str, pd.DataFrame] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        records = bundle["data"]["history"][symbol]
        rows = [{**item["candle"], "timestamp": item["candle"]["start"]} for item in records]
        frame = pd.DataFrame(rows)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
        for col in ("open", "high", "low", "close", "volume"):
            frame[col] = pd.to_numeric(frame[col], errors="raise")
        if len(frame) != 15360:
            raise ValueError(f"UNEXPECTED_BAR_COUNT:{symbol}:{len(frame)}")
        if not frame.timestamp.is_monotonic_increasing or not frame.timestamp.is_unique:
            raise ValueError(f"INVALID_CHRONOLOGY:{symbol}")
        if not (frame.timestamp.diff().dropna() == pd.Timedelta(hours=1)).all():
            raise ValueError(f"HOURLY_GAP:{symbol}")
        if not (frame[["open", "high", "low", "close"]] > 0).all().all():
            raise ValueError(f"NON_POSITIVE_OHLC:{symbol}")
        if not (frame.high >= frame[["open", "close", "low"]].max(axis=1)).all():
            raise ValueError(f"INVALID_HIGH:{symbol}")
        if not (frame.low <= frame[["open", "close", "high"]].min(axis=1)).all():
            raise ValueError(f"INVALID_LOW:{symbol}")
        frames[symbol] = frame
    if not frames["BTCUSDT"].timestamp.equals(frames["ETHUSDT"].timestamp):
        raise ValueError("BTC_ETH_HISTORY_NOT_ALIGNED")
    return bundle, frames


def rsi_wilder(close: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(close, dtype=np.float64)
    out = np.full(len(x), np.nan, dtype=np.float64)
    if len(x) <= period:
        return out
    delta = np.diff(x)
    gains, losses = np.maximum(delta, 0.0), np.maximum(-delta, 0.0)
    avg_g, avg_l = float(gains[:period].mean()), float(losses[:period].mean())

    def value(gain: float, loss: float) -> float:
        if loss == 0.0:
            return 100.0 if gain > 0 else 50.0
        if gain == 0.0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out[period] = value(avg_g, avg_l)
    for i in range(period + 1, len(x)):
        avg_g = ((period - 1) * avg_g + gains[i - 1]) / period
        avg_l = ((period - 1) * avg_l + losses[i - 1]) / period
        out[i] = value(avg_g, avg_l)
    return out


def compute_features(frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    f: dict[str, Any] = {"frames": frames, "ema": {}, "rsi": {}, "mom": {}, "donchian_high": {}, "donchian_low": {}}
    for symbol, frame in frames.items():
        close = frame.close
        f["ema"][symbol] = {
            p: close.ewm(span=p, adjust=False, min_periods=p).mean().to_numpy(dtype=np.float64)
            for p in EMAS
        }
        f["rsi"][symbol] = {p: rsi_wilder(close.to_numpy(dtype=np.float64), p) for p in RSI_PERIODS}
        f["mom"][symbol] = {p: (close / close.shift(p) - 1.0).to_numpy(dtype=np.float64) for p in MOMENTUM_LOOKBACKS}
        f["donchian_high"][symbol] = {
            p: frame.high.rolling(p, min_periods=p).max().shift(1).to_numpy(dtype=np.float64)
            for p in DONCHIAN_ENTRIES
        }
        f["donchian_low"][symbol] = {
            p: frame.low.rolling(p, min_periods=p).min().shift(1).to_numpy(dtype=np.float64)
            for p in DONCHIAN_EXITS
        }
    return f


def build_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ema_fast = (5, 6, 8, 10, 12, 16, 20, 24)
    ema_slow = (24, 30, 36, 48, 60, 72, 96, 120, 144, 168, 200)
    entry_rsi = (30, 35, 40, 45, 50, 55)
    exit_rsi = (55, 60, 65, 70, 75, 80)
    for fast in ema_fast:
        for slow in ema_slow:
            if fast >= slow:
                continue
            for entry in entry_rsi:
                for exit_ in exit_rsi:
                    if entry < exit_:
                        candidates.append({"family": "EMA_CROSS_RSI", "params": {"fast_ema": fast, "slow_ema": slow, "entry_rsi_min": entry, "exit_rsi_at": exit_}})
    for lookback in MOMENTUM_LOOKBACKS:
        for trend_ema in (None, 20, 50, 100, 150, 200):
            for btc_gate_ema in (None, 50, 100, 200):
                for entry in (0.00, 0.01, 0.02, 0.05):
                    for exit_ in (-0.05, -0.02, 0.00):
                        if exit_ <= entry:
                            candidates.append({"family": "TSMOM_TREND", "params": {"lookback_hours": lookback, "trend_ema": trend_ema, "btc_gate_ema": btc_gate_ema, "entry_momentum_min": entry, "exit_momentum_at": exit_}})
    for entry_period in DONCHIAN_ENTRIES:
        for exit_period in DONCHIAN_EXITS:
            if exit_period >= entry_period:
                continue
            for trend_ema in (None, 50, 100, 150, 200):
                candidates.append({"family": "DONCHIAN_BREAKOUT", "params": {"entry_window": entry_period, "exit_window": exit_period, "trend_ema": trend_ema}})
    for period in RSI_PERIODS:
        for lower in (15, 20, 25, 30, 35):
            for exit_ in (45, 50, 55, 60, 65):
                for trend_ema in (None, 100, 150, 200):
                    candidates.append({"family": "RSI_MEAN_REVERSION", "params": {"rsi_period": period, "entry_rsi_at_or_below": lower, "exit_rsi_at": exit_, "trend_ema": trend_ema}})
    for i, candidate in enumerate(candidates):
        candidate["candidate_id"] = f"C{i+1:05d}"
    return candidates


def signals_for(candidate: dict[str, Any], features: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    n = len(features["frames"]["ETHUSDT"])
    p = candidate["params"]
    close = features["frames"]["ETHUSDT"].close.to_numpy(dtype=np.float64)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    family = candidate["family"]
    if family == "EMA_CROSS_RSI":
        fast = features["ema"]["ETHUSDT"][p["fast_ema"]]
        slow = features["ema"]["ETHUSDT"][p["slow_ema"]]
        rsi = features["rsi"]["ETHUSDT"][14]
        buy[1:] = (fast[:-1] <= slow[:-1]) & (fast[1:] > slow[1:]) & (rsi[1:] >= p["entry_rsi_min"])
        sell[1:] = ((fast[:-1] >= slow[:-1]) & (fast[1:] < slow[1:])) | (rsi[1:] >= p["exit_rsi_at"])
    elif family == "TSMOM_TREND":
        mom = features["mom"]["ETHUSDT"][p["lookback_hours"]]
        entry_ok = mom > p["entry_momentum_min"]
        exit_bad = mom <= p["exit_momentum_at"]
        if p["trend_ema"] is not None:
            trend = features["ema"]["ETHUSDT"][p["trend_ema"]]
            entry_ok &= close > trend
            exit_bad |= close < trend
        if p["btc_gate_ema"] is not None:
            btc_close = features["frames"]["BTCUSDT"].close.to_numpy(dtype=np.float64)
            btc_trend = features["ema"]["BTCUSDT"][p["btc_gate_ema"]]
            entry_ok &= btc_close > btc_trend
            exit_bad |= btc_close < btc_trend
        buy = entry_ok & np.isfinite(mom)
        sell = exit_bad & np.isfinite(mom)
    elif family == "DONCHIAN_BREAKOUT":
        upper = features["donchian_high"]["ETHUSDT"][p["entry_window"]]
        lower = features["donchian_low"]["ETHUSDT"][p["exit_window"]]
        entry_ok = close > upper
        exit_bad = close < lower
        if p["trend_ema"] is not None:
            trend = features["ema"]["ETHUSDT"][p["trend_ema"]]
            entry_ok &= close > trend
            exit_bad |= close < trend
        buy = entry_ok & np.isfinite(upper)
        sell = exit_bad & np.isfinite(lower)
    elif family == "RSI_MEAN_REVERSION":
        rsi = features["rsi"]["ETHUSDT"][p["rsi_period"]]
        trend_ok = np.ones(n, dtype=bool)
        exit_bad = rsi >= p["exit_rsi_at"]
        if p["trend_ema"] is not None:
            trend = features["ema"]["ETHUSDT"][p["trend_ema"]]
            trend_ok = close > trend
            exit_bad |= close < trend
        buy[1:] = (rsi[:-1] > p["entry_rsi_at_or_below"]) & (rsi[1:] <= p["entry_rsi_at_or_below"]) & trend_ok[1:]
        sell = exit_bad & np.isfinite(rsi)
    else:
        raise ValueError(f"unknown family {family}")
    # NaNs cannot create admissible instructions. Every decision remains long-only Spot.
    buy &= np.isfinite(close)
    sell &= np.isfinite(close)
    return buy, sell


def effective_eth_filters(bundle: dict[str, Any]) -> dict[str, float]:
    raw = bundle["data"]["filters"]["ETHUSDT"]
    normal_step = float(raw["step_size"]) if float(raw["step_size"]) > 0 else None
    market_step = float(raw["market_step_size"]) if float(raw["market_step_size"]) > 0 else None
    step = market_step if market_step is not None else normal_step
    return {
        "min_qty": float(raw["market_min_qty"]) if float(raw["market_min_qty"]) > 0 else float(raw["min_qty"]),
        "step": float(step) if step is not None else 0.0,
        "max_qty": float(raw["market_max_qty"]) if float(raw["market_max_qty"]) > 0 else float(raw["max_qty"]),
        "min_notional": float(raw["min_notional"]),
    }


def simulate_batch(
    buys: np.ndarray,
    sells: np.ndarray,
    frame: pd.DataFrame,
    start: int,
    end: int,
    cost: dict[str, Any],
    filters: dict[str, float],
) -> dict[str, np.ndarray]:
    """Exact discrete-fill research replay for a candidate batch using V1 conventions.

    Signals at candle close t are attempted at candle open t+1. The account starts
    flat for each fold. It uses 95% quote spend, current captured quantity filters,
    fee/spread/slippage assumptions and a five-entry/rolling-30-day limit.
    """
    count = buys.shape[0]
    opens = frame.open.to_numpy(dtype=np.float64)
    closes = frame.close.to_numpy(dtype=np.float64)
    fee_rate = float(cost["fee"])
    adverse = (float(cost["slippage_bps"]) + float(cost["spread_bps"]) / 2.0) / 10000.0
    cash = np.full(count, INITIAL, dtype=np.float64)
    qty = np.zeros(count, dtype=np.float64)
    recent_buys = np.full((count, MAX_ENTRIES_30D), -100000, dtype=np.int32)
    next_slot = np.zeros(count, dtype=np.int32)
    buy_count = np.zeros(count, dtype=np.int32)
    sell_count = np.zeros(count, dtype=np.int32)
    total_fees = np.zeros(count, dtype=np.float64)
    peak_liq = np.full(count, INITIAL, dtype=np.float64)
    max_dd = np.zeros(count, dtype=np.float64)
    daily_samples = (end - start) // 24
    daily_book = np.zeros((count, daily_samples), dtype=np.float64)
    daily_n = 0
    for offset, bar_i in enumerate(range(start, end)):
        if offset > 0:
            sig_i = bar_i - 1
            was_in = qty > 0.0
            sell_mask = was_in & sells[:, sig_i]
            if np.any(sell_mask):
                fill_px = opens[bar_i] * (1.0 - adverse)
                proceeds = qty[sell_mask] * fill_px
                fees = proceeds * fee_rate
                cash[sell_mask] += proceeds - fees
                qty[sell_mask] = 0.0
                total_fees[sell_mask] += fees
                sell_count[sell_mask] += 1
            buy_mask = (~was_in) & buys[:, sig_i]
            if np.any(buy_mask):
                idx = np.flatnonzero(buy_mask)
                recent_count = np.sum(recent_buys[idx] >= (bar_i - BARS_30D), axis=1)
                allowed = recent_count < MAX_ENTRIES_30D
                idx = idx[allowed]
                if idx.size:
                    fill_px = opens[bar_i] * (1.0 + adverse)
                    spend = cash[idx] * FRACTION
                    raw_qty = spend / (fill_px * (1.0 + fee_rate))
                    step = filters["step"]
                    if step > 0.0:
                        buy_qty = np.floor(raw_qty / step + 1e-10) * step
                    else:
                        buy_qty = raw_qty
                    notional = buy_qty * fill_px
                    valid = (
                        (buy_qty > 0.0)
                        & (buy_qty >= filters["min_qty"])
                        & (buy_qty <= filters["max_qty"])
                        & (notional >= filters["min_notional"])
                        & np.isfinite(buy_qty)
                        & np.isfinite(notional)
                    )
                    idx_valid = idx[valid]
                    q_valid = buy_qty[valid]
                    notion_valid = notional[valid]
                    if idx_valid.size:
                        fees = notion_valid * fee_rate
                        cash[idx_valid] -= notion_valid + fees
                        qty[idx_valid] = q_valid
                        total_fees[idx_valid] += fees
                        buy_count[idx_valid] += 1
                        slot = next_slot[idx_valid] % MAX_ENTRIES_30D
                        recent_buys[idx_valid, slot] = bar_i
                        next_slot[idx_valid] += 1
        book_equity = cash + qty * closes[bar_i]
        liquidation_equity = cash + qty * closes[bar_i] * (1.0 - adverse) * (1.0 - fee_rate)
        peak_liq = np.maximum(peak_liq, liquidation_equity)
        max_dd = np.minimum(max_dd, np.where(peak_liq > 0, liquidation_equity / peak_liq - 1.0, 0.0))
        if (offset + 1) % 24 == 0:
            daily_book[:, daily_n] = book_equity
            daily_n += 1
    # Same terminal-liquidation convention as the existing V1 research replay.
    if end > start:
        terminal_px = closes[end - 1] * (1.0 - adverse)
        active = qty > 0.0
        if np.any(active):
            proceeds = qty[active] * terminal_px
            fees = proceeds * fee_rate
            cash[active] += proceeds - fees
            total_fees[active] += fees
            sell_count[active] += 1
            qty[active] = 0.0
        final = cash.copy()
        if daily_samples:
            daily_book[:, -1] = final
        peak_liq = np.maximum(peak_liq, final)
        max_dd = np.minimum(max_dd, np.where(peak_liq > 0, final / peak_liq - 1.0, 0.0))
    else:
        final = cash + qty * closes[end - 1]
    # Convert sampled account-equity values into daily log returns for the bootstrap.
    daily_log = np.zeros_like(daily_book)
    if daily_samples:
        daily_log[:, 0] = np.log(np.maximum(daily_book[:, 0], 1e-12) / INITIAL)
        if daily_samples > 1:
            daily_log[:, 1:] = np.log(np.maximum(daily_book[:, 1:], 1e-12) / np.maximum(daily_book[:, :-1], 1e-12))
    return {
        "final_usdt": final,
        "return_pct": (final / INITIAL - 1.0) * 100.0,
        "max_drawdown_pct": max_dd * 100.0,
        "buys": buy_count,
        "sells": sell_count,
        "fills": buy_count + sell_count,
        "fees_usdt": total_fees,
        "daily_log_returns": daily_log,
    }


def buy_hold_benchmark(frame: pd.DataFrame, start: int, end: int, cost: dict[str, Any]) -> dict[str, Any]:
    fee_rate = float(cost["fee"])
    adverse = (float(cost["slippage_bps"]) + float(cost["spread_bps"]) / 2.0) / 10000.0
    closes = frame.close.to_numpy(dtype=np.float64)[start:end]
    entry_price = closes[0] * (1.0 + adverse)
    qty = INITIAL / (entry_price * (1.0 + fee_rate))
    book = qty * closes.copy()
    final = closes[-1] * qty * (1.0 - adverse) * (1.0 - fee_rate)
    book[-1] = final
    daily_book = book[23::24]
    if len(daily_book) != (end - start) // 24:
        raise ValueError("BENCHMARK_DAILY_SAMPLING_MISMATCH")
    daily_log = np.zeros(len(daily_book), dtype=np.float64)
    if len(daily_book):
        daily_log[0] = math.log(max(daily_book[0], 1e-12) / INITIAL)
        if len(daily_book) > 1:
            daily_log[1:] = np.log(np.maximum(daily_book[1:], 1e-12) / np.maximum(daily_book[:-1], 1e-12))
    peak = INITIAL
    drawdown = 0.0
    for close in closes:
        liquidation = qty * close * (1.0 - adverse) * (1.0 - fee_rate)
        peak = max(peak, liquidation)
        if peak > 0:
            drawdown = min(drawdown, liquidation / peak - 1.0)
    drawdown = min(drawdown, final / peak - 1.0 if peak > 0 else 0.0)
    return {"return_pct": (final / INITIAL - 1.0) * 100.0, "max_drawdown_pct": drawdown * 100.0, "final_usdt": final, "daily_log_returns": daily_log}


def bootstrap_max_mean_excess(
    candidate_daily_log_returns: np.ndarray,
    benchmark_daily_log_returns: np.ndarray,
    *,
    replicates: int,
    seed: int,
    block_days: int = BOOTSTRAP_BLOCK_DAYS,
) -> dict[str, Any]:
    """Exploratory family-wise max-statistic block bootstrap against one benchmark.

    Samples are stratified within each historical 60-day fold using circular
    blocks. Each candidate is centered separately within each fold under the null;
    the max over the entire parameter family accounts for the search multiplicity.
    This is not an independent holdout and must not authorize operation.
    """
    if candidate_daily_log_returns.ndim != 3:
        raise ValueError("candidate_daily_log_returns must be candidate × fold × day")
    count, folds, days = candidate_daily_log_returns.shape
    if folds != 3 or days != 60 or benchmark_daily_log_returns.shape != (3, 60):
        raise ValueError("BOOTSTRAP_REQUIRES_THREE_60_DAY_FOLDS")
    excess = candidate_daily_log_returns - benchmark_daily_log_returns[None, :, :]
    observed_means = excess.mean(axis=(1, 2))
    observed_max = float(np.max(observed_means))
    centered = excess - excess.mean(axis=2, keepdims=True)
    rng = np.random.default_rng(seed)
    blocks_per_fold = days // block_days
    offsets = np.arange(block_days, dtype=np.int32)
    max_stats = np.empty(replicates, dtype=np.float64)
    for rep in range(replicates):
        sampled: list[np.ndarray] = []
        for fold in range(folds):
            starts = rng.integers(0, days, size=blocks_per_fold, endpoint=False)
            indices = ((starts[:, None] + offsets[None, :]) % days).reshape(-1)
            sampled.append(centered[:, fold, indices])
        bootstrap_samples = np.concatenate(sampled, axis=1)
        max_stats[rep] = float(np.max(bootstrap_samples.mean(axis=1)))
    p_value = float((1 + np.count_nonzero(max_stats >= observed_max)) / (replicates + 1))
    return {
        "benchmark": "cash_or_passive_reference",
        "observed_max_mean_daily_log_excess": observed_max,
        "unadjusted_candidate_count": int(count),
        "bootstrap_replicates": replicates,
        "block_days": block_days,
        "folds": folds,
        "days_per_fold": days,
        "random_seed": seed,
        "exploratory_familywise_p_value": p_value,
        "method": "fold-stratified_circular_moving-block_bootstrap_centered_null_max_mean_excess",
        "limitation": "historical selection data are reused and market available_at/finality are unknown; not a live holdout",
    }


def anti_lookahead_smoke(candidate: dict[str, Any], features: dict[str, Any], cutoff: int) -> bool:
    """Perturb only a future suffix and assert decision signals through cutoff do not change."""
    frame_copy = {k: v.copy() for k, v in features["frames"].items()}
    for symbol, multiplier in (("BTCUSDT", 0.73), ("ETHUSDT", 1.37)):
        frame = frame_copy[symbol].copy()
        for column in ("open", "high", "low", "close"):
            frame.loc[cutoff + 1 :, column] = frame.loc[cutoff + 1 :, column] * multiplier
        frame_copy[symbol] = frame
    altered = compute_features(frame_copy)
    old_buy, old_sell = signals_for(candidate, features)
    new_buy, new_sell = signals_for(candidate, altered)
    return np.array_equal(old_buy[: cutoff + 1], new_buy[: cutoff + 1]) and np.array_equal(old_sell[: cutoff + 1], new_sell[: cutoff + 1])


def run_tournament(dataset_path: Path, output_dir: Path, *, chunk_size: int = 256, bootstrap_replicates: int = 2000, seed: int = 20261019) -> dict[str, Any]:
    started = time.monotonic()
    bundle, frames = read_dataset(dataset_path)
    features = compute_features(frames)
    candidates = build_candidates()
    if len(candidates) < 5000:
        raise ValueError(f"GRID_TOO_SMALL:{len(candidates)}")
    n_candidates = len(candidates)
    n_folds = 3
    n_costs = len(COSTS)
    returns = np.zeros((n_candidates, n_folds, n_costs), dtype=np.float64)
    drawdowns = np.zeros_like(returns)
    fills = np.zeros((n_candidates, n_folds, n_costs), dtype=np.int32)
    buys_count = np.zeros_like(fills)
    sells_count = np.zeros_like(fills)
    fees = np.zeros_like(returns)
    daily_base = np.zeros((n_candidates, n_folds, 60), dtype=np.float64)
    legacy_eval_returns = np.full((n_candidates, n_costs), np.nan, dtype=np.float64)
    legacy_eval_drawdowns = np.full_like(legacy_eval_returns, np.nan)
    legacy_eval_fills = np.full((n_candidates, n_costs), -1, dtype=np.int32)
    legacy_eval_fees = np.full_like(legacy_eval_returns, np.nan)
    scopes = [("VALIDATION_1", WARMUP, WARMUP + VALIDATION_BARS),
              ("VALIDATION_2", WARMUP + VALIDATION_BARS, WARMUP + 2 * VALIDATION_BARS),
              ("VALIDATION_3", WARMUP + 2 * VALIDATION_BARS, WARMUP + 3 * VALIDATION_BARS)]
    legacy_eval_start = WARMUP + 3 * VALIDATION_BARS
    legacy_eval_end = legacy_eval_start + EVALUATION_BARS
    if legacy_eval_end > len(frames["ETHUSDT"]):
        raise ValueError("LEGACY_EVALUATION_WINDOW_EXCEEDS_DATASET")
    legacy_eval_benchmarks: dict[tuple[int, str], dict[str, Any]] = {}
    filters = effective_eth_filters(bundle)
    benchmarks: dict[tuple[int, int, str], dict[str, Any]] = {}
    metric_rows: list[dict[str, Any]] = []
    for fold_i, (scope_name, start, end) in enumerate(scopes):
        if (end - start) != VALIDATION_BARS:
            raise ValueError("VALIDATION_FOLD_LENGTH_MISMATCH")
        for cost_i, cost in enumerate(COSTS):
            benchmarks[(fold_i, cost_i, "CASH")] = {
                "return_pct": 0.0, "max_drawdown_pct": 0.0, "final_usdt": INITIAL,
                "daily_log_returns": np.zeros(60, dtype=np.float64),
            }
            for symbol in ("BTCUSDT", "ETHUSDT"):
                benchmarks[(fold_i, cost_i, f"BUY_HOLD_{symbol}")] = buy_hold_benchmark(frames[symbol], start, end, cost)
            for offset in range(0, n_candidates, chunk_size):
                limit = min(n_candidates, offset + chunk_size)
                chunk = candidates[offset:limit]
                buy_matrix = np.empty((len(chunk), len(frames["ETHUSDT"])), dtype=bool)
                sell_matrix = np.empty_like(buy_matrix)
                for j, candidate in enumerate(chunk):
                    b, s = signals_for(candidate, features)
                    buy_matrix[j] = b
                    sell_matrix[j] = s
                result = simulate_batch(buy_matrix, sell_matrix, frames["ETHUSDT"], start, end, cost, filters)
                sl = slice(offset, limit)
                returns[sl, fold_i, cost_i] = result["return_pct"]
                drawdowns[sl, fold_i, cost_i] = result["max_drawdown_pct"]
                fills[sl, fold_i, cost_i] = result["fills"]
                buys_count[sl, fold_i, cost_i] = result["buys"]
                sells_count[sl, fold_i, cost_i] = result["sells"]
                fees[sl, fold_i, cost_i] = result["fees_usdt"]
                if cost_i == 0:
                    daily_base[sl, fold_i, :] = result["daily_log_returns"]
            for symbol in ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT"):
                b = benchmarks[(fold_i, cost_i, symbol)]
                metric_rows.append({"candidate_id": symbol, "family": "BENCHMARK", "params": {}, "fold": scope_name, "cost": cost["name"], **{k: b[k] for k in ("return_pct", "max_drawdown_pct", "final_usdt")}})
    base_benchmark_daily = {
        symbol: np.stack([benchmarks[(fold_i, 0, symbol)]["daily_log_returns"] for fold_i in range(3)], axis=0)
        for symbol in ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT")
    }
    # Calculate multi-comparison robust summary for each candidate using only the three pre-existing validation folds.
    summary: list[dict[str, Any]] = []
    benchmark_return_base = {symbol: np.array([[benchmarks[(fold_i, cost_i, symbol)]["return_pct"] for cost_i in range(3)] for fold_i in range(3)], dtype=np.float64)
                             for symbol in ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT")}
    for i, candidate in enumerate(candidates):
        fold_returns = returns[i]
        base_returns = fold_returns[:, 0]
        stress1_returns = fold_returns[:, 1]
        stress2_returns = fold_returns[:, 2]
        min_all_scenarios = float(np.min(fold_returns))
        all_base_pos = bool(np.all(base_returns > 0.0))
        all_base_stress1_pos = bool(np.all(base_returns > 0.0) and np.all(stress1_returns > 0.0))
        all_three_costs_nonnegative = bool(np.all(fold_returns >= 0.0))
        beats_all_base_passives_each_fold = bool(
            np.all(base_returns > benchmark_return_base["CASH"][:, 0])
            and np.all(base_returns > benchmark_return_base["BUY_HOLD_BTCUSDT"][:, 0])
            and np.all(base_returns > benchmark_return_base["BUY_HOLD_ETHUSDT"][:, 0])
        )
        summary.append({
            "candidate_id": candidate["candidate_id"],
            "family": candidate["family"],
            "params": candidate["params"],
            "validation_base_returns_pct": [round(float(x), 8) for x in base_returns],
            "validation_stress1_returns_pct": [round(float(x), 8) for x in stress1_returns],
            "validation_stress2_returns_pct": [round(float(x), 8) for x in stress2_returns],
            "validation_min_return_all_folds_costs_pct": round(min_all_scenarios, 8),
            "validation_median_base_return_pct": round(float(np.median(base_returns)), 8),
            "validation_worst_drawdown_pct": round(float(np.min(drawdowns[i])), 8),
            "validation_total_fills": int(np.sum(fills[i])),
            "validation_total_fees_usdt": round(float(np.sum(fees[i])), 8),
            "all_three_base_folds_positive": all_base_pos,
            "positive_base_and_stress1_in_all_folds": all_base_stress1_pos,
            "nonnegative_in_all_folds_and_cost_scenarios": all_three_costs_nonnegative,
            "beats_cash_and_both_buy_hold_in_base_each_fold": beats_all_base_passives_each_fold,
        })
        for fold_i, (scope_name, _, _) in enumerate(scopes):
            for cost_i, cost in enumerate(COSTS):
                metric_rows.append({
                    "candidate_id": candidate["candidate_id"], "family": candidate["family"], "params": candidate["params"],
                    "fold": scope_name, "cost": cost["name"],
                    "return_pct": round(float(returns[i, fold_i, cost_i]), 8),
                    "max_drawdown_pct": round(float(drawdowns[i, fold_i, cost_i]), 8),
                    "final_usdt": round(float(INITIAL * (1.0 + returns[i, fold_i, cost_i] / 100.0)), 8),
                    "fills": int(fills[i, fold_i, cost_i]), "buys": int(buys_count[i, fold_i, cost_i]),
                    "sells": int(sells_count[i, fold_i, cost_i]), "fees_usdt": round(float(fees[i, fold_i, cost_i]), 8),
                })
    # Test strategy-signal causality for representatives from all families at multiple temporal cuts.
    representative_ids: dict[str, str] = {}
    for candidate in candidates:
        representative_ids.setdefault(candidate["family"], candidate["candidate_id"])
    candidate_by_id = {c["candidate_id"]: c for c in candidates}
    cuts = (WARMUP + 17, WARMUP + VALIDATION_BARS - 3, WARMUP + 2 * VALIDATION_BARS + 29)
    lookahead_checks = []
    for family, candidate_id in representative_ids.items():
        for cut in cuts:
            lookahead_checks.append({"candidate_id": candidate_id, "family": family, "cutoff_index": cut,
                                     "status": "PASS" if anti_lookahead_smoke(candidate_by_id[candidate_id], features, cut) else "FAIL"})
    # Family-wise block bootstrap against every registered benchmark, over all searched variants.
    bootstrap: list[dict[str, Any]] = []
    for symbol, benchmark_daily in base_benchmark_daily.items():
        test = bootstrap_max_mean_excess(daily_base, benchmark_daily, replicates=bootstrap_replicates, seed=seed, block_days=BOOTSTRAP_BLOCK_DAYS)
        test["benchmark"] = symbol
        bootstrap.append(test)
    # Freeze the primary ranking using validation folds only, then replay only the top 100
    # validation-consistent finalists on the old, already-inspected evaluation slice.
    # Those reference results cannot promote a strategy or alter the primary ranking.
    summary.sort(key=lambda r: (r["validation_min_return_all_folds_costs_pct"], r["validation_median_base_return_pct"]), reverse=True)
    validation_robust_count = sum(1 for r in summary if r["positive_base_and_stress1_in_all_folds"] and r["nonnegative_in_all_folds_and_cost_scenarios"])
    strict_gate_count = sum(1 for r in summary if r["positive_base_and_stress1_in_all_folds"] and r["nonnegative_in_all_folds_and_cost_scenarios"] and r["beats_cash_and_both_buy_hold_in_base_each_fold"])
    finalists = [r for r in summary if r["positive_base_and_stress1_in_all_folds"] and r["nonnegative_in_all_folds_and_cost_scenarios"]][:100]
    candidate_by_id = {c["candidate_id"]: (i, c) for i, c in enumerate(candidates)}
    finalist_indices = [candidate_by_id[r["candidate_id"]][0] for r in finalists]
    evaluated_ids = {r["candidate_id"] for r in finalists}
    legacy_eval_benchmarks: dict[tuple[int, str], dict[str, Any]] = {}
    for cost_i, cost in enumerate(COSTS):
        legacy_eval_benchmarks[(cost_i, "CASH")] = {"return_pct": 0.0, "max_drawdown_pct": 0.0, "final_usdt": INITIAL, "daily_log_returns": np.zeros(EVALUATION_BARS // 24, dtype=np.float64)}
        for symbol in ("BTCUSDT", "ETHUSDT"):
            legacy_eval_benchmarks[(cost_i, f"BUY_HOLD_{symbol}")] = buy_hold_benchmark(frames[symbol], legacy_eval_start, legacy_eval_end, cost)
        for group_start in range(0, len(finalist_indices), min(chunk_size, 100)):
            ids = finalist_indices[group_start:group_start + min(chunk_size, 100)]
            chunk = [candidates[i] for i in ids]
            buy_matrix = np.empty((len(chunk), len(frames["ETHUSDT"])), dtype=bool)
            sell_matrix = np.empty_like(buy_matrix)
            for j, candidate in enumerate(chunk):
                b, s = signals_for(candidate, features)
                buy_matrix[j] = b
                sell_matrix[j] = s
            result = simulate_batch(buy_matrix, sell_matrix, frames["ETHUSDT"], legacy_eval_start, legacy_eval_end, cost, filters)
            legacy_eval_returns[ids, cost_i] = result["return_pct"]
            legacy_eval_drawdowns[ids, cost_i] = result["max_drawdown_pct"]
            legacy_eval_fills[ids, cost_i] = result["fills"]
            legacy_eval_fees[ids, cost_i] = result["fees_usdt"]
        for symbol in ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT"):
            b = legacy_eval_benchmarks[(cost_i, symbol)]
            metric_rows.append({"candidate_id": symbol, "family": "BENCHMARK", "params": {}, "fold": "LEGACY_EVALUATION_REFERENCE_ONLY", "cost": cost["name"], **{k: b[k] for k in ("return_pct", "max_drawdown_pct", "final_usdt")}})
    for row in summary:
        ci = candidate_by_id[row["candidate_id"]][0]
        if row["candidate_id"] not in evaluated_ids:
            row.update({"legacy_evaluation_reference_returns_pct": None, "legacy_evaluation_reference_worst_drawdown_pct": None, "legacy_evaluation_reference_fills": None, "legacy_evaluation_reference_positive_in_all_costs": None, "legacy_evaluation_reference_beats_cash_and_both_buy_hold_base": None})
            continue
        eval_returns = legacy_eval_returns[ci]
        row["legacy_evaluation_reference_returns_pct"] = [round(float(x), 8) for x in eval_returns]
        row["legacy_evaluation_reference_worst_drawdown_pct"] = round(float(np.min(legacy_eval_drawdowns[ci])), 8)
        row["legacy_evaluation_reference_fills"] = int(np.sum(legacy_eval_fills[ci]))
        row["legacy_evaluation_reference_positive_in_all_costs"] = bool(np.all(eval_returns > 0.0))
        row["legacy_evaluation_reference_beats_cash_and_both_buy_hold_base"] = bool(
            eval_returns[0] > legacy_eval_benchmarks[(0, "CASH")]["return_pct"]
            and eval_returns[0] > legacy_eval_benchmarks[(0, "BUY_HOLD_BTCUSDT")]["return_pct"]
            and eval_returns[0] > legacy_eval_benchmarks[(0, "BUY_HOLD_ETHUSDT")]["return_pct"]
        )
        for cost_i, cost in enumerate(COSTS):
            metric_rows.append({"candidate_id": row["candidate_id"], "family": row["family"], "params": row["params"], "fold": "LEGACY_EVALUATION_REFERENCE_ONLY", "cost": cost["name"], "return_pct": round(float(eval_returns[cost_i]), 8), "max_drawdown_pct": round(float(legacy_eval_drawdowns[ci, cost_i]), 8), "final_usdt": round(float(legacy_eval_returns[ci, cost_i] + 100.0) * INITIAL / 100.0, 8), "fills": int(legacy_eval_fills[ci, cost_i]), "fees_usdt": round(float(legacy_eval_fees[ci, cost_i]), 8)})
    legacy_eval_top_base = sorted([r for r in summary if r["legacy_evaluation_reference_returns_pct"] is not None], key=lambda r: r["legacy_evaluation_reference_returns_pct"][0], reverse=True)
    legacy_reference_crosscheck_count = sum(1 for r in summary if r["legacy_evaluation_reference_returns_pct"] is not None and r["positive_base_and_stress1_in_all_folds"] and r["nonnegative_in_all_folds_and_cost_scenarios"] and r["legacy_evaluation_reference_positive_in_all_costs"] and r["legacy_evaluation_reference_beats_cash_and_both_buy_hold_base"])
    out = {
        "schema_version": 1,
        "study_id": "BTC_ETH_STRATEGY_TOURNAMENT_V2_HISTORICAL_SCREEN",
        "status": "EXPLORATORY_HISTORICAL_SCREEN_NOT_A_LIVE_HOLDOUT",
        "decision": "NO_TRADE",
        "candidate_policy": "No historical parameter winner is eligible for promotion; independent point-in-time prospective validation is still required.",
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        "execution_provenance": {
            "git_sha": os.environ.get("GITHUB_SHA"),
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "research_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "source_workflow_run": 39062915950,
        "source_artifact": "btc-eth-follow-365d-f8b9c36768f68ee754f1cb217f920dea25ef445e",
        "dataset_sha256": bundle["dataset_sha256"],
        "configuration_sha256": bundle["configuration_sha256"],
        "dataset_bars_per_symbol": len(frames["ETHUSDT"]),
        "source_timestamps": {"start": frames["ETHUSDT"].timestamp.iloc[0].isoformat(), "end_inclusive": frames["ETHUSDT"].timestamp.iloc[-1].isoformat()},
        "data_provenance_warning": {
            "available_at_unknown_bars_BTC": sum(x.get("available_at") is None for x in bundle["data"]["history"]["BTCUSDT"]),
            "available_at_unknown_bars_ETH": sum(x.get("available_at") is None for x in bundle["data"]["history"]["ETHUSDT"]),
            "finality_unknown_bars_BTC": sum(x["candle"].get("finality") == "UNKNOWN" for x in bundle["data"]["history"]["BTCUSDT"]),
            "finality_unknown_bars_ETH": sum(x["candle"].get("finality") == "UNKNOWN" for x in bundle["data"]["history"]["ETHUSDT"]),
            "meaning": "The hash proves dataset identity only; historical availability/finality are unknown, so no historical score proves an executable point-in-time edge.",
        },
        "frozen_method": {
            "families": ["EMA_CROSS_RSI", "TSMOM_TREND", "DONCHIAN_BREAKOUT", "RSI_MEAN_REVERSION"],
            "candidate_count": n_candidates,
            "candidate_grid_sha256": canonical_sha256([{ "family": c["family"], "params": c["params"] } for c in candidates]),
            "initial_virtual_usdt": INITIAL,
            "spot_long_only": True,
            "leverage": False,
            "max_entries_per_rolling_30d": MAX_ENTRIES_30D,
            "signal_time": "closed hourly candle t",
            "fill_time": "next hourly open",
            "account_reset": "cash at start of each 60-day fold",
            "position_fraction": FRACTION,
            "cost_scenarios": list(COSTS),
            "parameter_search_scope": "three fixed pre-existing 60-day historical validation folds only; the legacy 365-day evaluation split was not used to rank candidates",
            "note": "Validation folds have already been examined in prior V1 research; this is therefore exploratory, not a fresh independent holdout.",
        },
        "validation_folds": [{"name": name, "start_index": start, "end_index_exclusive": end, "bars": end-start,
                              "start": frames["ETHUSDT"].timestamp.iloc[start].isoformat(),
                              "end_exclusive": (frames["ETHUSDT"].timestamp.iloc[end-1] + pd.Timedelta(hours=1)).isoformat()}
                             for name, start, end in scopes],
        "search_counts": {
            "candidates_evaluated": n_candidates,
            "legacy_evaluation_finalist_count": len(evaluated_ids),
            "validation_candidate_replays": n_candidates * len(scopes) * len(COSTS),
            "legacy_evaluation_reference_candidate_replays": len(evaluated_ids) * len(COSTS),
            "fold_cost_candidate_replays": n_candidates * len(scopes) * len(COSTS) + len(evaluated_ids) * len(COSTS),
            "metric_rows_including_benchmarks": len(metric_rows),
            "validation_consistency_candidates_positive_base_stress1_and_nonnegative_stress2": validation_robust_count,
            "strict_pre_screen_pass_count_including_passives_every_validation_fold": strict_gate_count,
            "legacy_evaluation_reference_candidates_that_also_meet_validation_and_reference_conditions": legacy_reference_crosscheck_count,
            "all_three_base_folds_positive_count": sum(1 for r in summary if r["all_three_base_folds_positive"]),
            "positive_base_and_stress1_all_folds_count": sum(1 for r in summary if r["positive_base_and_stress1_in_all_folds"]),
            "nonnegative_all_folds_costs_count": sum(1 for r in summary if r["nonnegative_in_all_folds_and_cost_scenarios"]),
            "beats_all_base_passive_benchmarks_each_fold_count": sum(1 for r in summary if r["beats_cash_and_both_buy_hold_in_base_each_fold"]),
        },
        "benchmark_base_returns_by_fold": {
            symbol: [round(float(benchmarks[(fold_i, 0, symbol)]["return_pct"]), 8) for fold_i in range(3)]
            for symbol in ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT")
        },
        "legacy_evaluation_reference_only": {
            "start_index": legacy_eval_start, "end_index_exclusive": legacy_eval_end, "bars": legacy_eval_end - legacy_eval_start,
            "start": frames["ETHUSDT"].timestamp.iloc[legacy_eval_start].isoformat(),
            "end_exclusive": (frames["ETHUSDT"].timestamp.iloc[legacy_eval_end - 1] + pd.Timedelta(hours=1)).isoformat(),
            "ranking_used": False,
            "warning": "This historical period was already inspected in earlier V1 research; these additional grid results are diagnostic only and cannot be called an independent holdout.",
            "benchmark_base_returns_pct": {symbol: round(float(legacy_eval_benchmarks[(0, symbol)]["return_pct"]), 8) for symbol in ("CASH", "BUY_HOLD_BTCUSDT", "BUY_HOLD_ETHUSDT")},
            "top_10_by_base_return_descriptive_only": legacy_eval_top_base[:10],
        },
        "multiple_testing_bootstrap": bootstrap,
        "anti_lookahead_representative_tests": {"status": "PASS" if all(x["status"] == "PASS" for x in lookahead_checks) else "FAIL", "cutoffs": list(cuts), "tests": lookahead_checks,
                                                    "limitation": "representative strategy tests are not proof of real historical availability; they test suffix invariance of the implementation"},
        "top_50_by_robust_floor": summary[:50],
        "all_candidate_summary": summary,
        "all_fold_cost_metrics": metric_rows,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "final_interpretation": "No historical grid leader is called a winner. A promotion candidate requires real point-in-time BTC/ETH data, a preregistered future holdout, costs based on observed quotes, and independent review.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tournament-results.json").write_text(json.dumps(out, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Compact flat CSV for spreadsheet inspection.
    pd.DataFrame(metric_rows).to_csv(output_dir / "tournament-metrics.csv", index=False)
    (output_dir / "tournament-summary.md").write_text(render_summary(out), encoding="utf-8")
    manifest_files = {}
    for name in ("tournament-results.json", "tournament-metrics.csv", "tournament-summary.md"):
        payload = (output_dir / name).read_bytes()
        manifest_files[name] = {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}
    manifest = {
        "schema_version": 1,
        "study_id": out["study_id"],
        "git_sha": out["execution_provenance"]["git_sha"],
        "github_run_id": out["execution_provenance"]["github_run_id"],
        "dataset_sha256": out["dataset_sha256"],
        "configuration_sha256": out["configuration_sha256"],
        "research_code_sha256": out["execution_provenance"]["research_code_sha256"],
        "candidate_grid_sha256": out["frozen_method"]["candidate_grid_sha256"],
        "files": manifest_files,
    }
    (output_dir / "tournament-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"CANDIDATES: {n_candidates}")
    print(f"FOLD_COST_CANDIDATE_REPLAYS: {out['search_counts']['fold_cost_candidate_replays']}")
    print(f"VALIDATION_CONSISTENT_CANDIDATES: {validation_robust_count}")
    print(f"LEGACY_REFERENCE_FINALISTS: {len(evaluated_ids)}")
    print(f"LEGACY_REFERENCE_CROSSCHECK_COUNT: {legacy_reference_crosscheck_count}")
    print(f"LOOKAHEAD_SMOKE: {out['anti_lookahead_representative_tests']['status']} ({len(lookahead_checks)} checks)")
    for item in bootstrap:
        print(f"FAMILYWISE_BOOTSTRAP {item['benchmark']}: p={item['exploratory_familywise_p_value']:.5f}; max_mean_log_excess={item['observed_max_mean_daily_log_excess']:.8g}")
    print("TOP 10 (validation folds only; exploratory):")
    for row in summary[:10]:
        print(f"{row['candidate_id']} {row['family']} params={row['params']} robust_floor={row['validation_min_return_all_folds_costs_pct']:.4f}% median_base={row['validation_median_base_return_pct']:.4f}% folds={row['validation_base_returns_pct']} stress1={row['validation_stress1_returns_pct']} stress2={row['validation_stress2_returns_pct']}")
    print("DECISION: NO_TRADE; historical grid results are not a prospective winner")
    print(f"OUTPUT: {output_dir}")
    return out


def render_summary(out: dict[str, Any]) -> str:
    lines = [
        "# BTC/ETH Strategy Tournament V2 — result record",
        "",
        f"**Status:** `{out['status']}`  ",
        f"**Decision:** `{out['decision']}`  ",
        f"**Dataset SHA-256:** `{out['dataset_sha256']}`  ",
        f"**Grid SHA-256:** `{out['frozen_method']['candidate_grid_sha256']}`  ",
        "",
        "## Trial volume",
        "",
        f"- Unique parameterized candidates: **{out['search_counts']['candidates_evaluated']:,}**.",
        f"- Fold × cost × candidate economic replays: **{out['search_counts']['fold_cost_candidate_replays']:,}** (including the legacy evaluation reference, not used for ranking).",
        f"- Candidate metric rows plus benchmarks: **{out['search_counts']['metric_rows_including_benchmarks']:,}**.",
        f"- Candidates positive in BASE and STRESS_1 for all three validation folds and nonnegative in STRESS_2: **{out['search_counts']['validation_consistency_candidates_positive_base_stress1_and_nonnegative_stress2']}**.",
        f"- Candidates also meeting the cross-fold passive benchmark pre-screen: **{out['search_counts']['strict_pre_screen_pass_count_including_passives_every_validation_fold']}**.",
        f"- Candidates passing the descriptive legacy-evaluation cross-check too: **{out['search_counts']['legacy_evaluation_reference_candidates_that_also_meet_validation_and_reference_conditions']}**.",
        "",
        "## Top candidates (ranking uses only the three fixed validation folds)",
        "",
        "Ranking is descriptive. The three folds were already examined in earlier V1 work; this search is not an untouched holdout, and its leader is not a trading recommendation.",
        "",
        "| ID | Family | Worst fold/cost return | Median BASE fold return | BASE validation returns | STRESS_1 returns | STRESS_2 returns |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for row in out["top_50_by_robust_floor"][:20]:
        lines.append(f"| {row['candidate_id']} | {row['family']} | {row['validation_min_return_all_folds_costs_pct']:.4f}% | {row['validation_median_base_return_pct']:.4f}% | {row['validation_base_returns_pct']} | {row['validation_stress1_returns_pct']} | {row['validation_stress2_returns_pct']} |")
    lines += ["", "## Legacy evaluation reference (not used to rank candidates)", "", "Only the top 100 candidates selected using validation consistency were replayed on the 365-day legacy slice. That slice was already inspected in earlier V1 research; results are diagnostic only and neither ranked candidates nor qualify as a new holdout.", "", "| Benchmark | BASE return |", "|---|---:|"]
    for symbol, value in out["legacy_evaluation_reference_only"]["benchmark_base_returns_pct"].items():
        lines.append(f"| {symbol} | {value:.4f}% |")
    lines += ["", "## Multiplicity correction", "", "Family-wise exploratory p-values use a fold-stratified circular moving-block bootstrap (5-day blocks) with a max-statistic over the entire parameter grid. The historical folds have already been inspected and all candles have `available_at`/finality provenance gaps, so these p-values do not substitute for an independent prospective holdout.", "", "| Benchmark | Bootstrap replicates | Family-wise p-value |", "|---|---:|---:|"]
    for item in out["multiple_testing_bootstrap"]:
        lines.append(f"| {item['benchmark']} | {item['bootstrap_replicates']:,} | {item['exploratory_familywise_p_value']:.5f} |")
    lines += ["", "## Reproducibility and safety", "", f"- Python `{out['runtime']['python']}`, NumPy `{out['runtime']['numpy']}`, pandas `{out['runtime']['pandas']}`.", f"- Representative suffix-invariance smoke: `{out['anti_lookahead_representative_tests']['status']}` across {len(out['anti_lookahead_representative_tests']['tests'])} family/cut checks.", "- Data scope: historical Binance Spot BTCUSDT and ETHUSDT hourly bars; 31 hypothetical USDT; long-only, no leverage, max five buys per rolling 30 days.", "- Fill convention: signal at close t, virtual fill at next hourly open, three fixed fee/spread/slippage scenarios, terminal liquidation.", "- Historical `available_at` and candle finality are unknown; no backtest fills can be asserted to have been executable at the time.", "- **Final decision: `NO_TRADE`. No strategy is promoted from this historical search.** The next qualifying test must use a real, accepted point-in-time capture and a preregistered future holdout; do not retune after T0.", ""]
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a reproducible multi-thousand-variant BTC/ETH historical strategy screen; no trading.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=256)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20261019)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.chunk_size < 16 or args.chunk_size > 2048:
        parser.error("--chunk-size must be in [16, 2048]")
    if args.bootstrap_replicates < 1000 or args.bootstrap_replicates > 10000:
        parser.error("--bootstrap-replicates must be in [1000, 10000]")
    try:
        run_tournament(args.dataset, args.output_dir, chunk_size=args.chunk_size, bootstrap_replicates=args.bootstrap_replicates, seed=args.seed)
    except Exception as exc:
        print(f"TOURNAMENT_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())