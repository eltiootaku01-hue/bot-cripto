
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

EXPECTED_DATASET_SHA256 = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
EXPECTED_CONFIGURATION_SHA256 = "2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a"
EVALUATION_START = pd.Timestamp("2026-01-01T00:00:00Z")
HORIZONS = (4, 12, 24)
WAVE_THRESHOLD = 0.005
ABSTAIN = "ABSTAIN"
DIRECTIONS = {"UP", "DOWN"}
STRATEGIES = (
    "ALWAYS_UP", "ALWAYS_DOWN", "MOMENTUM_4H", "MOMENTUM_12H",
    "MEAN_REVERSION_4H", "EMA_TREND_12_36", "RSI14_EXTREMES",
    "DONCHIAN_24H", "SWING_STRUCTURE_12H", "BTC_LEADS_ETH_4H",
)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_bundle(path: Path) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("dataset_sha256") != EXPECTED_DATASET_SHA256:
        raise ValueError("pinned dataset hash does not match preregistration")
    if bundle.get("configuration_sha256") != EXPECTED_CONFIGURATION_SHA256:
        raise ValueError("pinned configuration hash does not match preregistration")
    if _canonical_sha256(bundle["data"]) != EXPECTED_DATASET_SHA256:
        raise ValueError("dataset logical SHA-256 mismatch")
    if _canonical_sha256(bundle["configuration"]) != EXPECTED_CONFIGURATION_SHA256:
        raise ValueError("configuration logical SHA-256 mismatch")

    frames = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        rows = [r["candle"] for r in bundle["data"]["history"][symbol]]
        frame = pd.DataFrame({
            "start": [r["start"] for r in rows], "end": [r["end"] for r in rows],
            "open": [r["open"] for r in rows], "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows], "close": [r["close"] for r in rows],
            "volume": [r["volume"] for r in rows],
        })
        frame["start"] = pd.to_datetime(frame["start"], utc=True, errors="raise")
        frame["end"] = pd.to_datetime(frame["end"], utc=True, errors="raise")
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        if frame.empty or frame["start"].duplicated().any() or not frame["start"].is_monotonic_increasing:
            raise ValueError(f"{symbol}: empty, duplicate, or unordered timestamps")
        if not (frame[["open", "high", "low", "close"]] > 0).all().all():
            raise ValueError(f"{symbol}: non-positive OHLC price")
        if not (frame["high"] >= frame[["open", "close", "low"]].max(axis=1)).all():
            raise ValueError(f"{symbol}: inconsistent high")
        if not (frame["low"] <= frame[["open", "close", "high"]].min(axis=1)).all():
            raise ValueError(f"{symbol}: inconsistent low")
        if not (frame["start"].diff().dropna() == pd.Timedelta(hours=1)).all():
            raise ValueError(f"{symbol}: missing or misaligned hourly candle")
        if any(r.get("candle_state") != "CLOSED" for r in rows):
            raise ValueError(f"{symbol}: non-closed candle in archived dataset")
        frames[symbol] = frame
    if len(frames["BTCUSDT"]) != len(frames["ETHUSDT"]):
        raise ValueError("BTC/ETH row counts differ")
    if not frames["BTCUSDT"]["start"].equals(frames["ETHUSDT"]["start"]):
        raise ValueError("BTC/ETH timestamps are not aligned")
    return frames, bundle


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Every feature at row i uses only candle data through row i."""
    out = frame.copy()
    close, high, low = out["close"], out["high"], out["low"]
    out["ret4"] = close / close.shift(4) - 1.0
    out["ret12"] = close / close.shift(12) - 1.0
    out["ema12"] = close.ewm(span=12, adjust=False, min_periods=12).mean()
    out["ema36"] = close.ewm(span=36, adjust=False, min_periods=36).mean()
    delta = close.diff()
    gains, losses = delta.clip(lower=0.0), -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    avg_loss = losses.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out["rsi14"] = 100.0 - 100.0 / (1.0 + rs)
    out.loc[(avg_loss == 0.0) & (avg_gain > 0.0), "rsi14"] = 100.0
    out.loc[(avg_gain == 0.0) & (avg_loss > 0.0), "rsi14"] = 0.0
    out.loc[(avg_gain == 0.0) & (avg_loss == 0.0), "rsi14"] = 50.0
    out["prior_high24"] = high.shift(1).rolling(24, min_periods=24).max()
    out["prior_low24"] = low.shift(1).rolling(24, min_periods=24).min()
    out["recent_high12"] = high.rolling(12, min_periods=12).max()
    out["recent_low12"] = low.rolling(12, min_periods=12).min()
    out["previous_high12"] = high.shift(12).rolling(12, min_periods=12).max()
    out["previous_low12"] = low.shift(12).rolling(12, min_periods=12).min()
    return out


def signal_series(features: pd.DataFrame, btc_features: pd.DataFrame | None = None) -> dict[str, np.ndarray]:
    """Frozen deterministic rules; no fitting, learning, or persistent state."""
    c, r4, r12 = features["close"], features["ret4"], features["ret12"]
    ema12, ema36, rsi = features["ema12"], features["ema36"], features["rsi14"]

    def choose(up: pd.Series, down: pd.Series) -> np.ndarray:
        values = np.full(len(features), ABSTAIN, dtype=object)
        values[up.fillna(False).to_numpy(dtype=bool)] = "UP"
        values[down.fillna(False).to_numpy(dtype=bool)] = "DOWN"
        return values

    result = {
        "ALWAYS_UP": np.full(len(features), "UP", dtype=object),
        "ALWAYS_DOWN": np.full(len(features), "DOWN", dtype=object),
        "MOMENTUM_4H": choose(r4 >= 0.0025, r4 <= -0.0025),
        "MOMENTUM_12H": choose(r12 >= 0.0040, r12 <= -0.0040),
        "MEAN_REVERSION_4H": choose(r4 <= -0.0075, r4 >= 0.0075),
        "EMA_TREND_12_36": choose(
            (c > ema12) & (ema12 > ema36) & (ema12 > ema12.shift(4)),
            (c < ema12) & (ema12 < ema36) & (ema12 < ema12.shift(4)),
        ),
        "RSI14_EXTREMES": choose(rsi <= 30.0, rsi >= 70.0),
        "DONCHIAN_24H": choose(c > features["prior_high24"], c < features["prior_low24"]),
        "SWING_STRUCTURE_12H": choose(
            (features["recent_high12"] > features["previous_high12"])
            & (features["recent_low12"] > features["previous_low12"]),
            (features["recent_high12"] < features["previous_high12"])
            & (features["recent_low12"] < features["previous_low12"]),
        ),
    }
    if btc_features is not None:
        btc_r4 = btc_features["ret4"]
        result["BTC_LEADS_ETH_4H"] = choose(btc_r4 >= 0.0050, btc_r4 <= -0.0050)
    return result


def future_wave_label(forward_return: float, threshold: float = WAVE_THRESHOLD) -> str:
    if forward_return >= threshold:
        return "UP"
    if forward_return <= -threshold:
        return "DOWN"
    return "NO_WAVE"


def _pct(n: int, d: int) -> float | None:
    return 100.0 * n / d if d else None


def _feature_text(frame: pd.DataFrame, column: str, i: int) -> str:
    v = frame[column].iloc[i]
    return "" if pd.isna(v) else f"{float(v):.10f}"


def run(dataset_path: Path, output_dir: Path) -> dict[str, Any]:
    frames, _bundle = _load_bundle(dataset_path)
    features = {symbol: build_features(frame) for symbol, frame in frames.items()}
    signal_map = {
        "BTCUSDT": signal_series(features["BTCUSDT"]),
        "ETHUSDT": signal_series(features["ETHUSDT"], features["BTCUSDT"]),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, monthly = [], []
    prediction_path = output_dir / "predictions.csv"
    fields = [
        "decision_time_utc", "asset", "strategy", "horizon_hours", "prediction",
        "decision_close", "return_4h_pct", "return_12h_pct", "rsi14", "ema12", "ema36",
        "btc_return_4h_pct", "target_time_utc", "future_return_pct", "target_wave",
        "direction_correct", "wave_hit", "actual_wave",
    ]
    first_index = int(frames["ETHUSDT"]["start"].searchsorted(EVALUATION_START))
    first_start = frames["ETHUSDT"]["start"].iloc[first_index]
    if first_start != EVALUATION_START:
        raise ValueError("archive does not have the exact 2026-01-01 00:00 UTC start")
    source_end = frames["ETHUSDT"]["end"].iloc[-1]
    records_written = 0

    with prediction_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for asset in ("BTCUSDT", "ETHUSDT"):
            frame, feat = frames[asset], features[asset]
            close = frame["close"].to_numpy(dtype=float)
            pmap = signal_map[asset]
            for strategy in STRATEGIES:
                if strategy not in pmap:
                    continue
                forecast = pmap[strategy]
                for horizon in HORIZONS:
                    last_i = len(frame) - horizon - 1
                    indices = range(first_index, last_i + 1)
                    per_month = defaultdict(lambda: {
                        "decisions": 0, "calls": 0, "up": 0, "down": 0,
                        "correct": 0, "hits": 0, "actual_waves": 0,
                        "false_no_wave": 0, "signed_sum": 0.0,
                    })
                    decisions = calls = ups = downs = correct = hits = actual_waves = false_no_wave = 0
                    signed_values = []
                    for i in indices:
                        pred = str(forecast[i])
                        fwd = close[i + horizon] / close[i] - 1.0
                        wave = future_wave_label(fwd)
                        active = pred in DIRECTIONS
                        is_wave = wave in DIRECTIONS
                        is_correct = active and ((pred == "UP" and fwd > 0.0) or (pred == "DOWN" and fwd < 0.0))
                        is_hit = active and pred == wave
                        month = frame["start"].iloc[i].strftime("%Y-%m")
                        m = per_month[month]
                        decisions += 1
                        m["decisions"] += 1
                        records_written += 1
                        if is_wave:
                            actual_waves += 1
                            m["actual_waves"] += 1
                        aligned = None
                        if active:
                            calls += 1
                            m["calls"] += 1
                            if pred == "UP":
                                ups += 1
                                m["up"] += 1
                                aligned = fwd
                            else:
                                downs += 1
                                m["down"] += 1
                                aligned = -fwd
                            signed_values.append(aligned)
                            m["signed_sum"] += aligned
                            if is_correct:
                                correct += 1
                                m["correct"] += 1
                            if is_hit:
                                hits += 1
                                m["hits"] += 1
                            if not is_wave:
                                false_no_wave += 1
                                m["false_no_wave"] += 1
                        writer.writerow({
                            "decision_time_utc": frame["end"].iloc[i].isoformat(),
                            "asset": asset, "strategy": strategy, "horizon_hours": horizon,
                            "prediction": pred, "decision_close": f"{close[i]:.10f}",
                            "return_4h_pct": _feature_text(feat, "ret4", i),
                            "return_12h_pct": _feature_text(feat, "ret12", i),
                            "rsi14": _feature_text(feat, "rsi14", i),
                            "ema12": _feature_text(feat, "ema12", i),
                            "ema36": _feature_text(feat, "ema36", i),
                            "btc_return_4h_pct": _feature_text(features["BTCUSDT"], "ret4", i),
                            "target_time_utc": frame["end"].iloc[i + horizon].isoformat(),
                            "future_return_pct": f"{fwd * 100.0:.8f}",
                            "target_wave": wave,
                            "direction_correct": "" if not active else str(bool(is_correct)).lower(),
                            "wave_hit": "" if not active else str(bool(is_hit)).lower(),
                            "actual_wave": str(is_wave).lower(),
                        })
                    if decisions != len(indices):
                        raise AssertionError("prediction ledger has missing decision ticks")
                    summary.append({
                        "asset": asset, "strategy": strategy, "horizon_hours": horizon,
                        "first_decision_utc": frame["end"].iloc[first_index].isoformat(),
                        "last_decision_utc": frame["end"].iloc[last_i].isoformat(),
                        "last_target_utc": frame["end"].iloc[last_i + horizon].isoformat(),
                        "decisions": decisions, "calls": calls, "abstentions": decisions - calls,
                        "coverage_pct": _pct(calls, decisions), "up_calls": ups, "down_calls": downs,
                        "direction_correct": correct, "direction_accuracy_pct": _pct(correct, calls),
                        "wave_hits": hits, "wave_hit_pct": _pct(hits, calls),
                        "actual_waves": actual_waves, "wave_capture_pct": _pct(hits, actual_waves),
                        "false_calls_no_wave": false_no_wave,
                        "false_call_no_wave_pct": _pct(false_no_wave, calls),
                        "mean_signed_forward_return_pct": float(np.mean(signed_values)) * 100.0 if signed_values else None,
                        "median_signed_forward_return_pct": float(np.median(signed_values)) * 100.0 if signed_values else None,
                    })
                    for month, m in sorted(per_month.items()):
                        monthly.append({
                            "month_utc": month, "asset": asset, "strategy": strategy, "horizon_hours": horizon,
                            "decisions": m["decisions"], "calls": m["calls"],
                            "coverage_pct": _pct(m["calls"], m["decisions"]),
                            "up_calls": m["up"], "down_calls": m["down"],
                            "direction_correct": m["correct"], "direction_accuracy_pct": _pct(m["correct"], m["calls"]),
                            "wave_hits": m["hits"], "wave_hit_pct": _pct(m["hits"], m["calls"]),
                            "actual_waves": m["actual_waves"], "wave_capture_pct": _pct(m["hits"], m["actual_waves"]),
                            "false_calls_no_wave": m["false_no_wave"],
                            "false_call_no_wave_pct": _pct(m["false_no_wave"], m["calls"]),
                            "mean_signed_forward_return_pct": m["signed_sum"] / m["calls"] * 100.0 if m["calls"] else None,
                        })

    def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    summary_path, monthly_path = output_dir / "summary.csv", output_dir / "monthly_summary.csv"
    write_csv(summary_path, summary)
    write_csv(monthly_path, monthly)
    ranked = sorted(
        (r for r in summary if r["calls"] >= 100 and r["wave_hit_pct"] is not None),
        key=lambda r: (r["wave_hit_pct"], r["direction_accuracy_pct"] or 0.0, r["coverage_pct"] or 0.0),
        reverse=True,
    )
    fmt = lambda v: "NA" if v is None else f"{v:.3f}"
    lines = [
        "# Stateless candle-wave forecast accuracy V1", "",
        "**Decision: NO_TRADE.** Historical directional diagnostics, not proof of profitable execution.", "",
        f"- Dataset logical SHA-256: {EXPECTED_DATASET_SHA256}",
        f"- Configuration SHA-256: {EXPECTED_CONFIGURATION_SHA256}",
        f"- Source candles end: {source_end.isoformat()}",
        f"- Forecast window begins: {first_start.isoformat()}",
        f"- Significant wave: future close-to-close move of at least {WAVE_THRESHOLD * 100:.2f}% in either direction.",
        "- Horizons: 4h, 12h, 24h; all rules use the same source candles for a given asset and horizon.",
        "- wave_hit_pct = successful significant directional waves / directional calls.",
        "- direction_accuracy_pct = calls with the correct sign / all directional calls.",
        "- coverage_pct = directional calls / all decision opportunities.",
        "- Fixed stateless rules; no model training, memory updates, or parameter search.",
        "- Forecasts overlap and are dependent. Historical available_at/finality is UNKNOWN.",
        "- Prior research examined this archive; this report is exploratory and not an untouched holdout.", "",
        "## Highest observed wave-hit rates (descriptive only; at least 100 calls)", "",
        "| Asset | Rule | Horizon | Calls | Coverage | Sign accuracy | Wave hit rate | Wave capture |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in ranked[:15]:
        lines.append(
            f"| {r['asset']} | {r['strategy']} | {r['horizon_hours']}h | {r['calls']} | "
            f"{fmt(r['coverage_pct'])}% | {fmt(r['direction_accuracy_pct'])}% | "
            f"{fmt(r['wave_hit_pct'])}% | {fmt(r['wave_capture_pct'])}% |"
        )
    lines += [
        "", "The highest row is not a promoted tactic. Review all rows and monthly consistency, including coverage and false-call rates.",
        "", "This experiment does not model commissions, spread, slippage, or a portfolio. Accuracy alone is not a trading edge. The decision remains NO_TRADE.", "",
    ]
    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    result = {
        "schema_version": 1,
        "status": "EXPLORATORY_HISTORICAL_FORECAST_ACCURACY_NOT_PROSPECTIVE_HOLDOUT",
        "decision": "NO_TRADE",
        "dataset_sha256": EXPECTED_DATASET_SHA256,
        "configuration_sha256": EXPECTED_CONFIGURATION_SHA256,
        "evaluation_start_utc": EVALUATION_START.isoformat(),
        "source_data_end_utc": source_end.isoformat(),
        "wave_threshold_return": WAVE_THRESHOLD,
        "horizons_hours": list(HORIZONS),
        "strategies": list(STRATEGIES),
        "asset_scope": ["BTCUSDT", "ETHUSDT"],
        "forecast_records_written": records_written,
        "summary": summary,
        "month_summary_rows": len(monthly),
        "anti_lookahead": {
            "status": "CAUSAL_FEATURES_BY_CONSTRUCTION",
            "future_candles_passed_to_signal": 0,
            "note": "Index-causal calculations; historical source available_at/finality remains UNKNOWN.",
        },
        "limitations": [
            "Pinned archive ends on 2026-10-17; later October candles are unavailable.",
            "This period was previously inspected by repository research, not an untouched holdout.",
            "Overlapping predictions are statistically dependent.",
            "Accuracy does not account for trading costs and is not profitability.",
        ],
    }
    json_path = output_dir / "summary.json"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    output_files = [prediction_path, summary_path, monthly_path, report_path, json_path]
    manifest = {
        "schema_version": 1, "decision": "NO_TRADE",
        "dataset_sha256": EXPECTED_DATASET_SHA256,
        "configuration_sha256": EXPECTED_CONFIGURATION_SHA256,
        "files": {p.name: {"size_bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in output_files},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("DATASET_SHA256:", EXPECTED_DATASET_SHA256)
    print("CONFIGURATION_SHA256:", EXPECTED_CONFIGURATION_SHA256)
    print("SOURCE_END_UTC:", source_end.isoformat())
    print("EVALUATION_START_UTC:", first_start.isoformat())
    print("SUMMARY_ROWS:", len(summary), "MONTHLY_ROWS:", len(monthly), "FORECAST_RECORDS:", records_written)
    print("TOP_DESCRIPTIVE_ROWS:")
    for r in ranked[:10]:
        print(
            f"asset={r['asset']} strategy={r['strategy']} horizon={r['horizon_hours']}h calls={r['calls']} "
            f"coverage_pct={fmt(r['coverage_pct'])} direction_accuracy_pct={fmt(r['direction_accuracy_pct'])} "
            f"wave_hit_pct={fmt(r['wave_hit_pct'])} wave_capture_pct={fmt(r['wave_capture_pct'])}"
        )
    print("ANTI_LOOKAHEAD: CAUSAL_FEATURES_BY_CONSTRUCTION")
    print("DECISION: NO_TRADE")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate fixed stateless candle-wave prediction rules.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.dataset, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
