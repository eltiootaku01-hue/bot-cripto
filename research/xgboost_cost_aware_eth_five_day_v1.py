"""Frozen cost-aware walk-forward XGBoost ETH return filter; research only."""
from __future__ import annotations
import argparse, csv, hashlib, json, math, os, statistics, sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from xgboost import XGBRegressor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import btc_volume_shock_reversal_v1 as core

STRATEGY = "XGBOOST_COST_AWARE_ETH_5D"
PROTOCOL = "COST_AWARE_XGBOOST_ETH_5D_V1"
HORIZON = 5                    # five open-to-open daily intervals
LABEL_EXIT_OFFSET = HORIZON + 1
CADENCE = HORIZON
MIN_TRAINING_ROWS = 80
MAX_TRAINING_ROWS = 180
EMA_PERIODS = (20, 50, 200)
RET_LAGS = (1, 3, 7, 14, 30)
RELATIVE_LAGS = (3, 7, 14, 30)
VOL_WINDOWS = (7, 30)
RATIO_Z_WINDOW = 30
VOLUME_LOOKBACK = 20
ENTRY_ALLOC = Decimal("0.20")
EDGE_BUFFER = Decimal("0.0025")  # fixed 25 bps above exact modeled round-trip break-even
TAKE_PROFIT_UNUSED = Decimal("1000000000")
STOP_LOSS_UNUSED = Decimal("0.000000001")
RISK_SEED = 20261025

FEATURE_NAMES = tuple(
    [f"eth_return_{n}" for n in RET_LAGS]
    + [f"btc_return_{n}" for n in RET_LAGS]
    + [f"relative_return_{n}" for n in RELATIVE_LAGS]
    + [f"{asset}_distance_ema{period}" for asset in ("eth", "btc") for period in EMA_PERIODS]
    + [f"{asset}_realized_vol_{n}" for asset in ("eth", "btc") for n in VOL_WINDOWS]
    + ["eth_btc_ratio_z30", "eth_volume_over_prior_median20"]
)

XGB_PARAMS = {
    "objective": "reg:squarederror", "n_estimators": 80, "max_depth": 2,
    "learning_rate": 0.03, "min_child_weight": 10, "subsample": 0.8,
    "colsample_bytree": 0.8, "reg_lambda": 10.0, "random_state": RISK_SEED,
    "n_jobs": 1, "tree_method": "hist", "verbosity": 0,
}

ENTRY_SIGNALS: list[bool] = []

def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """Recursive EMA seeded from the first close; each element only uses past/current values."""
    if not values:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    complement = Decimal(1) - alpha
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + complement * out[-1])
    return out

def _return(values: Sequence[Decimal], i: int, lag: int) -> float | None:
    if i < lag:
        return None
    return float(values[i] / values[i-lag] - Decimal(1))

def _realized_vol(values: Sequence[Decimal], i: int, window: int) -> float | None:
    if i < window:
        return None
    returns = [float(values[j] / values[j-1] - Decimal(1)) for j in range(i-window+1, i+1)]
    return statistics.pstdev(returns)

def ratio_zscore(eth_close: Sequence[Decimal], btc_close: Sequence[Decimal], i: int,
                 window: int = RATIO_Z_WINDOW) -> float | None:
    """Current ETH/BTC ratio against the preceding completed ratios, excluding current i."""
    if len(eth_close) != len(btc_close) or i < window or i >= len(eth_close):
        return None
    ratios = [eth_close[j] / btc_close[j] for j in range(i-window, i)]
    mean = sum(ratios, Decimal(0)) / Decimal(window)
    variance = sum(((value-mean) ** 2 for value in ratios), Decimal(0)) / Decimal(window)
    if variance <= 0:
        return None
    std = variance.sqrt()
    if std <= 0:
        return None
    current = eth_close[i] / btc_close[i]
    return float((current-mean) / std)

def make_features(eth_rows: Sequence[dict[str, Any]], btc_rows: Sequence[dict[str, Any]]) -> list[list[float] | None]:
    if len(eth_rows) != len(btc_rows):
        raise ValueError("FEATURE_DATASETS_NOT_PAIRED")
    if [x["open_ms"] for x in eth_rows] != [x["open_ms"] for x in btc_rows]:
        raise ValueError("FEATURE_TIMESTAMPS_NOT_PAIRED")
    closes = {
        "eth": [core.D(row["close"]) for row in eth_rows],
        "btc": [core.D(row["close"]) for row in btc_rows],
    }
    volumes = [core.D(row["volume"]) for row in eth_rows]
    emas = {asset: {p: ema_series(closes[asset], p) for p in EMA_PERIODS} for asset in ("eth", "btc")}
    features: list[list[float] | None] = [None] * len(eth_rows)
    warmup = max(max(RET_LAGS), max(EMA_PERIODS)-1, max(VOL_WINDOWS), RATIO_Z_WINDOW, VOLUME_LOOKBACK)
    for i in range(warmup, len(eth_rows)):
        eth_rets = {n: _return(closes["eth"], i, n) for n in RET_LAGS}
        btc_rets = {n: _return(closes["btc"], i, n) for n in RET_LAGS}
        rel_rets = {n: eth_rets[n]-btc_rets[n] for n in RELATIVE_LAGS}
        eth_vol = {n: _realized_vol(closes["eth"], i, n) for n in VOL_WINDOWS}
        btc_vol = {n: _realized_vol(closes["btc"], i, n) for n in VOL_WINDOWS}
        z = ratio_zscore(closes["eth"], closes["btc"], i)
        volume_baseline = core.median_decimal(volumes[i-VOLUME_LOOKBACK:i])
        if (any(v is None for v in eth_rets.values()) or any(v is None for v in btc_rets.values())
                or any(v is None for v in rel_rets.values())
                or any(v is None for v in eth_vol.values()) or any(v is None for v in btc_vol.values())
                or z is None or volume_baseline <= 0):
            continue
        row = (
            [eth_rets[n] for n in RET_LAGS] + [btc_rets[n] for n in RET_LAGS]
            + [rel_rets[n] for n in RELATIVE_LAGS]
            + [float(closes[asset][i] / emas[asset][p][i] - Decimal(1))
               for asset in ("eth", "btc") for p in EMA_PERIODS]
            + [eth_vol[n] for n in VOL_WINDOWS] + [btc_vol[n] for n in VOL_WINDOWS]
            + [z, float(volumes[i] / volume_baseline)]
        )
        if len(row) != len(FEATURE_NAMES) or not all(math.isfinite(float(x)) for x in row):
            continue
        features[i] = [float(x) for x in row]
    return features

def mature_training_indices(features: Sequence[list[float] | None], decision_index: int) -> list[int]:
    """Phase-aligned labels only; the exit open at j+6 must already be observed by decision i."""
    if decision_index < 0 or decision_index >= len(features):
        return []
    candidates = [
        j for j in range(max(0, decision_index-LABEL_EXIT_OFFSET+1))
        if j % CADENCE == CADENCE-1 and features[j] is not None and j+LABEL_EXIT_OFFSET <= decision_index
    ]
    return candidates[-MAX_TRAINING_ROWS:]

def fit_predict_xgb(training_x: Sequence[Sequence[float]], training_y: Sequence[float],
                    current_x: Sequence[float]) -> float:
    if len(training_x) != len(training_y) or not training_x:
        raise ValueError("XGB_TRAINING_SHAPE_INVALID")
    x = np.asarray(training_x, dtype=np.float32)
    y = np.asarray(training_y, dtype=np.float32)
    current = np.asarray([current_x], dtype=np.float32)
    if x.ndim != 2 or x.shape[1] != len(current_x) or not np.isfinite(x).all() or not np.isfinite(y).all() or not np.isfinite(current).all():
        raise ValueError("XGB_NONFINITE_OR_BAD_FEATURE_SHAPE")
    model = XGBRegressor(**XGB_PARAMS)
    model.fit(x, y, verbose=False)
    prediction = float(model.predict(current)[0])
    if not math.isfinite(prediction):
        raise ValueError("XGB_NONFINITE_PREDICTION")
    return prediction

def break_even_threshold(cost_name: str) -> Decimal:
    cost = core.COSTS[cost_name]
    adverse = cost["slippage_bps"] / Decimal(10000) + cost["spread_bps"] / Decimal(20000)
    fee = cost["fee"]
    break_even = ((Decimal(1)+adverse)*(Decimal(1)+fee) /
                  ((Decimal(1)-adverse)*(Decimal(1)-fee))) - Decimal(1)
    return break_even + EDGE_BUFFER

def _entry_signal(_: Sequence[dict[str, Any]], i: int) -> bool:
    return 0 <= i < len(ENTRY_SIGNALS) and ENTRY_SIGNALS[i]

def _cash_result(cash: Decimal, rows: Sequence[dict[str, Any]], a: int, b: int) -> dict[str, Any]:
    return {
        "method": "CASH", "final": cash, "pnl": Decimal(0), "return_pct": Decimal(0),
        "mdd_pct": Decimal(0), "trades": 0, "wins": 0, "losses": 0, "fees": Decimal(0),
        "friction": Decimal(0), "rejects": 0, "fills": [],
        "curve": [{"timestamp_utc": rows[a]["timestamp"], "phase": "START", "equity_usdt": core.ds(cash)}]
        + [{"timestamp_utc": rows[i]["timestamp"], "phase": "CLOSE", "equity_usdt": core.ds(cash)} for i in range(a, b)],
    }

def _tag_result(result: dict[str, Any], method: str, symbol: str) -> dict[str, Any]:
    result["method"] = method
    for fill in result["fills"]:
        fill["method"] = method
        fill["asset"] = symbol
        if "qty_btc" in fill:
            fill["qty_asset"] = fill.pop("qty_btc")
    return result

def gate_results(metrics: Sequence[dict[str, str]], data_ok: bool) -> dict[str, Any]:
    if not data_ok:
        return {"overall": "BLOCKED", "passed": 0, "failed": 1,
                "checks": [{"gate": "PAIRED_DATA_INTEGRITY", "status": "BLOCKED"}]}
    checks = []
    def val(window: str, capital: Decimal, cost: str, method: str, field: str) -> Decimal:
        return core.metric_value(metrics, window, capital, cost, method, field)
    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"gate": name, "status": "PASS" if ok else "FAIL", "details": detail})
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    annual = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    for capital in core.CAPITALS:
        cap = str(int(capital))
        for win in principal:
            pnl = val(win, capital, "STRESS_2", STRATEGY, "net_pnl_usdt")
            trades = int(val(win, capital, "STRESS_2", STRATEGY, "closed_roundtrips"))
            add(f"positive_pnl_{win}_{cap}", pnl > 0, f"PnL={pnl} USDT")
            add(f"five_roundtrips_{win}_{cap}", trades >= 5, f"closed={trades}")
        for win, _, _ in core.WINDOWS:
            dd = val(win, capital, "STRESS_2", STRATEGY, "max_drawdown_pct")
            add(f"drawdown_at_least_minus15_{win}_{cap}", dd >= -15, f"max drawdown={dd}%")
        p22 = val("2022", capital, "STRESS_2", STRATEGY, "net_pnl_usdt")
        add(f"2022_nonnegative_{cap}", p22 >= 0, f"PnL={p22} USDT")
        positive = sum(val(w, capital, "STRESS_2", STRATEGY, "net_pnl_usdt") > 0 for w in annual)
        add(f"four_positive_annual_windows_{cap}", positive >= 4, f"positive={positive}/7")
        recent = val("RECENT_2023_2026", capital, "STRESS_2", STRATEGY, "net_pnl_usdt")
        cash = val("RECENT_2023_2026", capital, "STRESS_2", "CASH", "net_pnl_usdt")
        eth_bh = val("RECENT_2023_2026", capital, "STRESS_2", "ETH_BUY_AND_HOLD", "net_pnl_usdt")
        add(f"recent_beats_cash_{cap}", recent > cash, f"strategy={recent}; cash={cash}")
        add(f"recent_beats_eth_buy_hold_{cap}", recent > eth_bh, f"strategy={recent}; ETH_BH={eth_bh}")
    failed = sum(item["status"] != "PASS" for item in checks)
    return {"overall": "PASS" if failed == 0 else "FAIL", "passed": len(checks)-failed, "failed": failed,
            "checks": checks,
            "promotion_note": "Previously inspected archive, not a pristine holdout; any historical pass requires independent prospective shadow validation."}

def _blocked(outdir: Path, error: str, manifest: dict[str, Any]) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    manifest["status"] = "BLOCKED"
    manifest["blocker"] = error
    (outdir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (outdir / "report.md").write_text("# Cost-aware XGBoost ETH five-day replay\n\nStatus: BLOCKED. No economic result is valid.\n\n"
                                       f"Blocker: {error}\n\nNo candle was interpolated or silently removed. NO_TRADE.\n", encoding="utf-8")
    print("BLOCKED:" + error, file=sys.stderr)
    return 2

def run(outdir: Path) -> int:
    global ENTRY_SIGNALS
    outdir.mkdir(parents=True, exist_ok=True)
    original_symbol = core.SYMBOL
    manifest: dict[str, Any] = {
        "protocol": PROTOCOL, "preregistration_issue": 161, "symbols": ["BTCUSDT", "ETHUSDT"],
        "interval": "1d", "start_utc": core.START.isoformat(), "end_exclusive_utc": core.END.isoformat(),
        "point_in_time_availability": "UNKNOWN", "historical_exchange_filter_equivalence": "UNKNOWN",
        "historical_spread_slippage": "MODELED_ASSUMPTIONS_ONLY", "main_changed": False,
        "operational_decision": "NO_TRADE", "model": "xgboost.XGBRegressor",
        "model_params": XGB_PARAMS, "feature_names": FEATURE_NAMES,
        "cadence": f"decision only when index % {CADENCE} == {CADENCE-1}",
        "label": "ETH open[j+6] / ETH open[j+1] - 1; label mature only if j+6 <= decision index",
        "training": {"latest_mature_phase_aligned_rows": MAX_TRAINING_ROWS, "minimum_rows": MIN_TRAINING_ROWS,
                     "phase_alignment": CADENCE, "horizon_days": HORIZON, "random_state": RISK_SEED},
        "fixed_edge_buffer": str(EDGE_BUFFER),
        "costs": {k: {f: core.ds(v) for f, v in d.items()} for k, d in core.COSTS.items()},
        "reused_engine_origin_commit": "d2d26aa7604ba357fe3b4f8422afa64b97b080e0",
    }
    try:
        datasets: dict[str, list[dict[str, Any]]] = {}
        pages_by_symbol, quality_by_symbol, hashes_by_symbol = {}, {}, {}
        filters_by_symbol, filter_evidence = {}, {}
        for symbol in ("BTCUSDT", "ETHUSDT"):
            core.SYMBOL = symbol
            rows, pages = core.fetch_history()
            quality = core.validate_candles(rows)
            datasets[symbol], pages_by_symbol[symbol], quality_by_symbol[symbol] = rows, pages, quality
            hashes_by_symbol[symbol] = core.canonical_data_hash(rows)
            if not quality["ok"]:
                manifest.update({"quality_by_symbol": quality_by_symbol, "row_counts": {s: len(v) for s, v in datasets.items()},
                                 "raw_pages_by_symbol": pages_by_symbol, "canonical_dataset_sha256_by_symbol": hashes_by_symbol})
                return _blocked(outdir, "DATA_INTEGRITY_GATE_FAILED:" + symbol, manifest)
            filters, evidence = core.fetch_filters()
            filters_by_symbol[symbol], filter_evidence[symbol] = filters, evidence

        btc_rows, eth_rows = datasets["BTCUSDT"], datasets["ETHUSDT"]
        btc_times = [int(row["open_ms"]) for row in btc_rows]
        eth_times = [int(row["open_ms"]) for row in eth_rows]
        if btc_times != eth_times:
            manifest.update({"quality_by_symbol": quality_by_symbol,
                             "row_counts": {s: len(v) for s, v in datasets.items()},
                             "raw_pages_by_symbol": pages_by_symbol,
                             "canonical_dataset_sha256_by_symbol": hashes_by_symbol})
            return _blocked(outdir, "BTC_ETH_DAILY_TIMESTAMP_PAIR_MISMATCH", manifest)
        core.SYMBOL = "ETHUSDT"

        features = make_features(eth_rows, btc_rows)
        eth_open = [core.D(row["open"]) for row in eth_rows]
        predictions: dict[int, float] = {}
        prediction_audit: list[dict[str, Any]] = []
        training_audit: list[dict[str, Any]] = []
        input_features_hash = core.sha(core.canon([{ "index": i, "features": row} for i, row in enumerate(features) if row is not None]))
        for i in range(len(eth_rows)):
            if i % CADENCE != CADENCE-1 or features[i] is None:
                continue
            training_indices = mature_training_indices(features, i)
            if len(training_indices) < MIN_TRAINING_ROWS:
                prediction_audit.append({
                    "decision_index": i, "decision_timestamp_utc": eth_rows[i]["timestamp"],
                    "training_rows": len(training_indices), "latest_training_label_exit_index": max((j+LABEL_EXIT_OFFSET for j in training_indices), default=""),
                    "prediction_gross_return": "", "status": "CASH_INSUFFICIENT_MATURE_LABELS",
                })
                continue
            latest_exit = max(j+LABEL_EXIT_OFFSET for j in training_indices)
            if latest_exit > i:
                raise RuntimeError(f"LOOKAHEAD_GUARD:{latest_exit}>{i}")
            training_x = [features[j] for j in training_indices]
            training_y = [float(eth_open[j+LABEL_EXIT_OFFSET] / eth_open[j+1] - Decimal(1)) for j in training_indices]
            prediction = fit_predict_xgb(training_x, training_y, features[i])
            predictions[i] = prediction
            training_indices_sha = core.sha(core.canon(training_indices))
            y_sha = core.sha(core.canon([format(v, ".17g") for v in training_y]))
            prediction_audit.append({
                "decision_index": i, "decision_timestamp_utc": eth_rows[i]["timestamp"],
                "training_rows": len(training_indices), "first_training_index": training_indices[0],
                "last_training_index": training_indices[-1], "latest_training_label_exit_index": latest_exit,
                "prediction_gross_return": prediction, "prediction_gross_return_pct": prediction*100.0,
                "features_json": json.dumps(dict(zip(FEATURE_NAMES, features[i])), sort_keys=True),
                "training_indices_sha256": training_indices_sha, "training_targets_sha256": y_sha,
                "status": "PREDICTION_READY",
            })
            for j, target in zip(training_indices, training_y):
                training_audit.append({
                    "decision_index": i, "decision_timestamp_utc": eth_rows[i]["timestamp"],
                    "sample_index": j, "sample_feature_timestamp_utc": eth_rows[j]["timestamp"],
                    "label_entry_index": j+1, "label_exit_index": j+LABEL_EXIT_OFFSET,
                    "label_exit_mature_at_decision": j+LABEL_EXIT_OFFSET <= i,
                    "label_gross_return": target,
                })

        thresholds = {cost: break_even_threshold(cost) for cost in core.COSTS}
        manifests_signals = {}
        strategy_results_signals = {}
        summary: list[dict[str, Any]] = []
        fills_out: list[dict[str, Any]] = []
        equity_out: list[dict[str, Any]] = []
        index = {int(row["open_ms"]): i for i, row in enumerate(eth_rows)}
        for window, start, end in core.WINDOWS:
            if core.to_ms(start) not in index:
                raise core.DataIntegrityError("WINDOW_START_MISSING:" + window)
            a = index[core.to_ms(start)]
            b = index.get(core.to_ms(end), len(eth_rows))
            if b <= a:
                raise core.DataIntegrityError("WINDOW_BOUNDARY_INVALID:" + window)
            for capital in core.CAPITALS:
                for cost_name in core.COSTS:
                    ENTRY_SIGNALS = [
                        (i in predictions and Decimal(str(predictions[i])) > thresholds[cost_name])
                        for i in range(len(eth_rows))
                    ]
                    manifests_signals[cost_name] = int(sum(ENTRY_SIGNALS))
                    core.entry_signal = _entry_signal
                    core.ALLOC = ENTRY_ALLOC
                    core.TP = TAKE_PROFIT_UNUSED
                    core.SL = STOP_LOSS_UNUSED
                    core.MAX_HOLD = HORIZON
                    strategy = core.simulate(eth_rows, a, b, capital, cost_name,
                                              filters_by_symbol["ETHUSDT"], window, "VOLUME_SHOCK_REVERSAL")
                    strategy = _tag_result(strategy, STRATEGY, "ETHUSDT")
                    eth_bh = core.simulate(eth_rows, a, b, capital, cost_name,
                                           filters_by_symbol["ETHUSDT"], window, "BTC_BUY_AND_HOLD")
                    eth_bh = _tag_result(eth_bh, "ETH_BUY_AND_HOLD", "ETHUSDT")
                    btc_bh = core.simulate(btc_rows, a, b, capital, cost_name,
                                           filters_by_symbol["BTCUSDT"], window, "BTC_BUY_AND_HOLD")
                    btc_bh = _tag_result(btc_bh, "BTC_BUY_AND_HOLD", "BTCUSDT")
                    cash = _cash_result(capital, eth_rows, a, b)
                    for result in (strategy, eth_bh, btc_bh, cash):
                        summary.append(core.metric_row(window, start, end, capital, cost_name, result))
                        for fill in result["fills"]:
                            fills_out.append({**fill, "asset": fill.get("asset", "")})
                        equity_out.extend({"window": window, "capital": core.ds(capital), "cost": cost_name,
                                           "method": result["method"], **point} for point in result["curve"])

        # Preserve one signal-decision row per model decision and scenario; costs never enter model features/training.
        for row in prediction_audit:
            index_ = int(row["decision_index"])
            for cost_name, threshold in thresholds.items():
                row[f"threshold_{cost_name.lower()}"] = str(threshold)
                row[f"trade_{cost_name.lower()}"] = bool(index_ in predictions and Decimal(str(predictions[index_])) > threshold)
        signal_fields = ["decision_index", "decision_timestamp_utc", "training_rows", "first_training_index",
                         "last_training_index", "latest_training_label_exit_index", "prediction_gross_return",
                         "prediction_gross_return_pct", "status", "features_json", "training_indices_sha256",
                         "training_targets_sha256", "threshold_base", "threshold_stress_1", "threshold_stress_2",
                         "trade_base", "trade_stress_1", "trade_stress_2"]
        training_fields = ["decision_index", "decision_timestamp_utc", "sample_index", "sample_feature_timestamp_utc",
                           "label_entry_index", "label_exit_index", "label_exit_mature_at_decision", "label_gross_return"]
        candle_fields = ["symbol", "open_ms", "timestamp", "close_ms", "open", "high", "low", "close", "volume"]
        with (outdir / "candles.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=candle_fields)
            writer.writeheader()
            for symbol, rows in datasets.items():
                for row in rows:
                    writer.writerow({"symbol": symbol, "open_ms": row["open_ms"], "timestamp": row["timestamp"],
                                     "close_ms": row["close_ms"], **{k: core.ds(row[k]) for k in ("open", "high", "low", "close", "volume")}})
        core.csv_write(outdir / "predictions.csv", prediction_audit, signal_fields)
        core.csv_write(outdir / "training_audit.csv", training_audit, training_fields)
        core.csv_write(outdir / "summary.csv", summary, ["window", "start_utc", "end_exclusive_utc", "capital", "cost", "method",
                      "final_equity_usdt", "net_pnl_usdt", "return_pct", "max_drawdown_pct", "closed_roundtrips",
                      "wins", "losses", "fees_usdt", "spread_slippage_usdt", "rejected_orders"])
        core.csv_write(outdir / "fills.csv", fills_out, ["window", "capital", "cost", "method", "asset", "trade_id", "side", "reason",
                      "signal_utc", "execution_utc", "execution_type", "raw_price", "fill_price", "qty_asset", "notional_usdt",
                      "fee_usdt", "friction_usdt", "cash_after"])
        core.csv_write(outdir / "equity.csv", equity_out, ["window", "capital", "cost", "method", "timestamp_utc", "phase", "equity_usdt"])

        gates = gate_results(summary, True)
        manifest.update({
            "quality_by_symbol": quality_by_symbol, "row_counts": {s: len(v) for s,v in datasets.items()},
            "first_open_utc_by_symbol": {s: v[0]["timestamp"] for s,v in datasets.items()},
            "last_open_utc_by_symbol": {s: v[-1]["timestamp"] for s,v in datasets.items()},
            "raw_pages_by_symbol": pages_by_symbol, "canonical_dataset_sha256_by_symbol": hashes_by_symbol,
            "paired_timestamp_series_sha256": core.sha(core.canon(btc_times)),
            "exchange_info_by_symbol": filter_evidence,
            "filters_current_by_symbol": {s: {k: core.ds(v) for k,v in f.items()} for s,f in filters_by_symbol.items()},
            "features_sha256": input_features_hash, "usable_feature_rows": sum(v is not None for v in features),
            "prediction_rows": len(prediction_audit), "ready_prediction_rows": len(predictions),
            "training_audit_rows": len(training_audit), "scenario_thresholds": {k: str(v) for k,v in thresholds.items()},
            "signals_above_threshold_per_scenario": {
                cost: sum(i in predictions and Decimal(str(predictions[i])) > thresholds[cost] for i in range(len(eth_rows)))
                for cost in core.COSTS
            },
            "strategy_gate": gates["overall"], "output_counts": {
                "summary_rows": len(summary), "fill_rows": len(fills_out), "equity_rows": len(equity_out),
                "training_audit_rows": len(training_audit), "prediction_rows": len(prediction_audit),
            },
            "status": "COMPLETED",
        })
        (outdir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        (outdir / "gate_results.json").write_text(json.dumps(gates, indent=2, sort_keys=True), encoding="utf-8")
        lines = [
            "# Cost-aware walk-forward XGBoost ETH 5-day filter V1", "", "Frozen gate: " + gates["overall"], "",
            "| Dataset | Bars | SHA-256 |", "|---|---:|---|",
        ]
        for symbol in ("BTCUSDT", "ETHUSDT"):
            lines.append(f"| {symbol} | {len(datasets[symbol])} | {hashes_by_symbol[symbol]} |")
        lines += ["", "Paired timestamp SHA-256: " + manifest["paired_timestamp_series_sha256"],
                  f"Features usable: {manifest['usable_feature_rows']}; predictions ready: {len(predictions)}; model decisions: {len(prediction_audit)}.",
                  f"Feature vector SHA-256: {input_features_hash}.",
                  "Dataset integrity: PASS; both series have exact daily timestamps and no OHLCV validation failures.",
                  "Historical data availability and historical instrument-filter equivalence: UNKNOWN.",
                  "Spread/slippage are modeled assumptions, not historical order-book observations.", "",
                  "## Frozen thresholds", "",
                  "| Cost | Minimum gross predicted return |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in thresholds.items()]
        lines += ["", "## Net STRESS_2 results", "",
                  "| Window | Capital | Net PnL USDT | Return % | Max DD % | Round trips | Fees | Spread/slippage |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for window, _, _ in core.WINDOWS:
            for capital in core.CAPITALS:
                row = next(x for x in summary if x["window"] == window and x["capital"] == core.ds(capital)
                           and x["cost"] == "STRESS_2" and x["method"] == STRATEGY)
                lines.append(f"| {window} | {int(capital)} | {row['net_pnl_usdt']} | {row['return_pct']} | {row['max_drawdown_pct']} | "
                             f"{row['closed_roundtrips']} | {row['fees_usdt']} | {row['spread_slippage_usdt']} |")
        lines += ["", "## Frozen gates", "", f"Passed: {gates['passed']}; failed: {gates['failed']}", "",
                  "| Gate | Status | Details |", "|---|---|---|"]
        lines += [f"| {g['gate']} | {g['status']} | {g.get('details','')} |" for g in gates["checks"]]
        lines += ["", "Workflow success certifies technical execution, not profitable expectancy.",
                  "The historical archive is not an untouched holdout. Any historical pass requires prospective shadow validation.",
                  "No private API, keys, live orders, leverage or main changes. Operational decision: NO_TRADE."]
        (outdir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({"status": "COMPLETED", "rows": len(eth_rows), "signals_ready": len(predictions),
                          "signal_count_base": manifest["signals_above_threshold_per_scenario"]["BASE"],
                          "signal_count_stress2": manifest["signals_above_threshold_per_scenario"]["STRESS_2"],
                          "gate": gates["overall"], "passed": gates["passed"], "failed": gates["failed"], "main_changed": False}, sort_keys=True))
        return 0
    except Exception as exc:
        return _blocked(outdir, type(exc).__name__ + ":" + str(exc), manifest)
    finally:
        core.SYMBOL = original_symbol

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="xgboost-cost-aware-eth-5d-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__ == "__main__":
    raise SystemExit(main())
