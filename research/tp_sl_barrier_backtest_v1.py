
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

EXPECTED_DATASET_SHA256 = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
EXPECTED_CONFIG_SHA256 = "2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a"
EXPECTED_FORECAST_SHA256 = "REPLACE_FROM_V1_ARTIFACT_MANIFEST"
EVAL_START = pd.Timestamp("2026-01-01T00:00:00Z")
CAPITALS = (60.0, 100.0)
POSITION_FRACTION = 0.95
TAKE_PROFIT_PCT = 0.01
STOP_LOSS_PCT = 0.005
COSTS = {
    "BASE": {"fee_rate": 0.001, "spread_bps": 10.0, "slippage_bps": 5.0},
    "STRESS_1": {"fee_rate": 0.001, "spread_bps": 25.0, "slippage_bps": 10.0},
    "STRESS_2": {"fee_rate": 0.002, "spread_bps": 50.0, "slippage_bps": 20.0},
}
SOURCE_STRATEGIES = (
    "ALWAYS_UP", "ALWAYS_DOWN", "MOMENTUM_4H", "MOMENTUM_12H",
    "MEAN_REVERSION_4H", "EMA_TREND_12_36", "RSI14_EXTREMES",
    "DONCHIAN_24H", "SWING_STRUCTURE_12H", "BTC_LEADS_ETH_4H",
)
SOURCE_HORIZONS = (4, 12, 24)
SOURCE_ASSETS = ("BTCUSDT", "ETHUSDT")


def canonical_sha256(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def pct(numerator: float, denominator: float) -> float | None:
    return 100.0 * numerator / denominator if denominator else None


def max_drawdown_pct(equity_values: list[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0.0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def _read_dataset(path: Path) -> dict[str, list[dict[str, Any]]]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if bundle.get("dataset_sha256") != EXPECTED_DATASET_SHA256:
        raise ValueError("dataset SHA-256 does not match frozen V1 source")
    if bundle.get("configuration_sha256") != EXPECTED_CONFIG_SHA256:
        raise ValueError("dataset configuration hash does not match frozen V1 source")
    if canonical_sha256(bundle["data"]) != EXPECTED_DATASET_SHA256:
        raise ValueError("dataset logical hash verification failed")
    if canonical_sha256(bundle["configuration"]) != EXPECTED_CONFIG_SHA256:
        raise ValueError("configuration logical hash verification failed")
    result: dict[str, list[dict[str, Any]]] = {}
    for symbol in SOURCE_ASSETS:
        raw = bundle["data"]["history"][symbol]
        rows = [dict(r["candle"]) for r in raw]
        if not rows or any(r.get("candle_state") != "CLOSED" for r in rows):
            raise ValueError(f"{symbol}: empty data or non-closed candle")
        starts = [pd.Timestamp(r["start"]) for r in rows]
        if starts != sorted(starts) or len(set(starts)) != len(starts):
            raise ValueError(f"{symbol}: duplicate or unordered candle starts")
        if any(starts[i] - starts[i - 1] != pd.Timedelta(hours=1) for i in range(1, len(starts))):
            raise ValueError(f"{symbol}: hourly candle gap detected")
        for r in rows:
            o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
                raise ValueError(f"{symbol}: invalid OHLC at {r['start']}")
        result[symbol] = rows
    if len(result["BTCUSDT"]) != len(result["ETHUSDT"]):
        raise ValueError("BTC and ETH candle counts differ")
    if [r["start"] for r in result["BTCUSDT"]] != [r["start"] for r in result["ETHUSDT"]]:
        raise ValueError("BTC and ETH candle timestamps are not aligned")
    return result


def load_frozen_predictions(path: Path, manifest_path: Path) -> tuple[pd.DataFrame, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    meta = manifest.get("files", {}).get("predictions.csv")
    if not meta:
        raise ValueError("V1 source manifest does not include predictions.csv")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != meta.get("sha256") or path.stat().st_size != meta.get("size_bytes"):
        raise ValueError("V1 prediction ledger hash/size does not match frozen source manifest")
    # Only select columns needed to run trades. Future outcome labels and future returns are not loaded.
    usecols = ["decision_time_utc", "asset", "strategy", "horizon_hours", "prediction"]
    frame = pd.read_csv(path, usecols=usecols, dtype={
        "asset": "string", "strategy": "string", "prediction": "string",
    })
    if frame.empty or frame.duplicated(["decision_time_utc", "asset", "strategy", "horizon_hours"]).any():
        raise ValueError("V1 forecast ledger is empty or has duplicate decision keys")
    frame["decision_time_utc"] = pd.to_datetime(frame["decision_time_utc"], utc=True, errors="raise")
    frame["horizon_hours"] = pd.to_numeric(frame["horizon_hours"], errors="raise").astype(int)
    if not set(frame["asset"].unique()).issubset(set(SOURCE_ASSETS)):
        raise ValueError("unexpected asset in V1 forecasts")
    if not set(frame["horizon_hours"].unique()).issubset(set(SOURCE_HORIZONS)):
        raise ValueError("unexpected horizon in V1 forecasts")
    if not set(frame["prediction"].unique()).issubset({"UP", "DOWN", "ABSTAIN"}):
        raise ValueError("unexpected frozen signal class")
    allowed = set(SOURCE_STRATEGIES)
    if not set(frame["strategy"].unique()).issubset(allowed):
        raise ValueError("unexpected strategy in V1 predictions")
    return frame, digest


def barrier_exit(open_price: float, high: float, low: float, close: float,
                 take_profit: float, stop_loss: float, position_open_before_bar: bool) -> tuple[float, str] | None:
    """Resolve OHLC barriers conservatively. Return raw market exit price and outcome."""
    if position_open_before_bar:
        if open_price <= stop_loss:
            return open_price, "SL_GAP"
        if open_price >= take_profit:
            return take_profit, "TP_GAP_CONSERVATIVE"
    # If one hourly candle crosses both levels and ordering is unknowable, assume stop first.
    touched_stop = low <= stop_loss
    touched_tp = high >= take_profit
    if touched_stop and touched_tp:
        return stop_loss, "SL_BOTH_TOUCHED_CONSERVATIVE"
    if touched_stop:
        return stop_loss, "SL"
    if touched_tp:
        return take_profit, "TP"
    return None


def _finish_trade(active: dict[str, Any], rows: list[dict[str, Any]], exit_idx: int,
                  raw_exit_price: float, reason: str, cash_before_entry: float,
                  cash_after_entry: float, cost: dict[str, float]) -> tuple[float, dict[str, Any]]:
    px_cost = (cost["spread_bps"] / 2.0 + cost["slippage_bps"]) / 10000.0
    exit_fill = raw_exit_price * (1.0 - px_cost)
    if exit_fill <= 0:
        raise ValueError("modeled exit fill price is non-positive")
    qty = active["quantity"]
    exit_notional = qty * exit_fill
    exit_fee = exit_notional * cost["fee_rate"]
    proceeds = exit_notional - exit_fee
    pnl = proceeds - active["entry_cash_outlay"]
    new_cash = cash_after_entry + proceeds
    initial_trade_outlay = active["entry_cash_outlay"]
    outcome = "TP" if reason.startswith("TP") else "SL" if reason.startswith("SL") else "TIME_EXIT"
    return new_cash, {
        "asset": active["asset"], "strategy": active["strategy"], "horizon_hours": active["horizon_hours"],
        "initial_capital_usdt": active["initial_capital"], "cost_scenario": active["cost_scenario"],
        "signal_decision_time_utc": active["decision_time"], "entry_time_utc": rows[active["entry_idx"]]["start"],
        "exit_time_utc": rows[exit_idx]["end"], "entry_index": active["entry_idx"], "exit_index": exit_idx,
        "direction": "LONG_SPOT", "prediction": "UP", "outcome": outcome, "exit_reason": reason,
        "take_profit_pct": TAKE_PROFIT_PCT * 100.0, "stop_loss_pct": -STOP_LOSS_PCT * 100.0,
        "take_profit_trigger": active["take_profit"], "stop_loss_trigger": active["stop_loss"],
        "entry_open_raw": active["entry_open_raw"], "entry_fill": active["entry_fill"],
        "exit_trigger_raw": raw_exit_price, "exit_fill": exit_fill, "quantity": qty,
        "entry_fee_usdt": active["entry_fee"], "exit_fee_usdt": exit_fee,
        "total_fees_usdt": active["entry_fee"] + exit_fee, "entry_cash_outlay_usdt": initial_trade_outlay,
        "gross_price_return_pct": (raw_exit_price / active["entry_open_raw"] - 1.0) * 100.0,
        "net_pnl_usdt": pnl, "net_return_on_entry_outlay_pct": pct(pnl, initial_trade_outlay),
        "duration_hours": exit_idx - active["decision_idx"],
        "cash_before_entry_usdt": cash_before_entry, "cash_after_exit_usdt": new_cash,
    }


def simulate_portfolio(asset: str, strategy: str, horizon: int, rows: list[dict[str, Any]],
                       predictions: pd.DataFrame, initial_capital: float, cost_name: str,
                       cost: dict[str, float], index_by_end: dict[pd.Timestamp, int],
                       eval_start_idx: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    subset = predictions[
        (predictions["asset"] == asset)
        & (predictions["strategy"] == strategy)
        & (predictions["horizon_hours"] == horizon)
    ].sort_values("decision_time_utc")
    signal_by_idx: dict[int, str] = {}
    for row in subset.itertuples(index=False):
        timestamp = pd.Timestamp(row.decision_time_utc)
        if timestamp not in index_by_end:
            raise ValueError(f"forecast decision timestamp not in dataset: {timestamp}")
        signal_idx = index_by_end[timestamp]
        if signal_idx in signal_by_idx:
            raise ValueError("duplicate decision index after timestamp mapping")
        signal_by_idx[signal_idx] = str(row.prediction)
    if not signal_by_idx:
        raise ValueError("no frozen predictions found for a strategy group")
    decision_indices = sorted(signal_by_idx)
    if decision_indices[0] < eval_start_idx:
        raise ValueError("forecast ledger includes decision before preregistered evaluation window")

    counts = {"UP": 0, "DOWN": 0, "ABSTAIN": 0}
    for signal in signal_by_idx.values():
        counts[signal] += 1
    fee = cost["fee_rate"]
    execution_bps = cost["spread_bps"] / 2.0 + cost["slippage_bps"]
    execution_cost = execution_bps / 10000.0
    cash = initial_capital
    active: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    skipped_open = 0
    entries = 0
    peak = initial_capital
    worst_dd = 0.0
    by_month: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "trades": 0, "tp": 0, "sl": 0, "time_exit": 0, "pnl_usdt": 0.0, "fees_usdt": 0.0,
        "wins_net": 0, "losses_net": 0,
    })

    for idx in range(eval_start_idx, len(rows)):
        candle = rows[idx]
        opened_this_bar = False
        cash_before_entry = cash

        if pending is not None and pending["entry_idx"] == idx:
            entry_open = float(candle["open"])
            entry_fill = entry_open * (1.0 + execution_cost)
            allocation = cash * POSITION_FRACTION
            entry_outlay = allocation
            qty = allocation / (entry_fill * (1.0 + fee))
            entry_fee = qty * entry_fill * fee
            actual_outlay = qty * entry_fill + entry_fee
            cash -= actual_outlay
            if qty <= 0 or cash < -1e-8:
                raise ValueError("invalid entry quantity or negative cash balance")
            active = {
                "asset": asset, "strategy": strategy, "horizon_hours": horizon,
                "initial_capital": initial_capital, "cost_scenario": cost_name,
                "decision_idx": pending["decision_idx"], "decision_time": pending["decision_time"],
                "entry_idx": idx, "expiry_idx": pending["decision_idx"] + horizon,
                "entry_open_raw": entry_open, "entry_fill": entry_fill, "quantity": qty,
                "entry_fee": entry_fee, "entry_cash_outlay": actual_outlay,
                "take_profit": entry_fill * (1.0 + TAKE_PROFIT_PCT),
                "stop_loss": entry_fill * (1.0 - STOP_LOSS_PCT),
            }
            pending = None
            opened_this_bar = True
            entries += 1

        if active is not None:
            exit_hit = barrier_exit(
                float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"]),
                active["take_profit"], active["stop_loss"], not opened_this_bar,
            )
            if exit_hit is not None:
                raw_exit, reason = exit_hit
            elif idx >= active["expiry_idx"]:
                raw_exit, reason = float(candle["close"]), "TIME_EXIT"
            else:
                raw_exit = None
                reason = ""
            if raw_exit is not None:
                active_for_month = active
                cash, trade = _finish_trade(
                    active_for_month, rows, idx, raw_exit, reason, cash_before_entry, cash, cost
                )
                trades.append(trade)
                month = pd.Timestamp(trade["exit_time_utc"]).strftime("%Y-%m")
                m = by_month[month]
                m["trades"] += 1
                m[trade["outcome"].lower() if trade["outcome"] != "TIME_EXIT" else "time_exit"] += 1
                m["pnl_usdt"] += trade["net_pnl_usdt"]
                m["fees_usdt"] += trade["total_fees_usdt"]
                if trade["net_pnl_usdt"] > 0:
                    m["wins_net"] += 1
                elif trade["net_pnl_usdt"] < 0:
                    m["losses_net"] += 1
                active = None

        if active is None:
            equity = cash
        else:
            liquidation_fill = float(candle["close"]) * (1.0 - execution_cost)
            equity = cash + active["quantity"] * liquidation_fill * (1.0 - fee)
        peak = max(peak, equity)
        dd = equity / peak - 1.0 if peak > 0.0 else 0.0
        worst_dd = min(worst_dd, dd)
        curve.append({"time": candle["end"], "equity": equity, "drawdown": dd})

        signal = signal_by_idx.get(idx)
        if signal == "UP":
            if active is not None or pending is not None:
                skipped_open += 1
            elif idx + horizon < len(rows) and idx + 1 < len(rows):
                pending = {"decision_idx": idx, "decision_time": candle["end"], "entry_idx": idx + 1}
        # DOWN calls and abstentions intentionally do not open spot shorts or trigger early exits.

    if active is not None or pending is not None:
        raise AssertionError("simulation ended with an unresolved position or entry")
    final_equity = cash
    tps = sum(t["outcome"] == "TP" for t in trades)
    sls = sum(t["outcome"] == "SL" for t in trades)
    timeouts = sum(t["outcome"] == "TIME_EXIT" for t in trades)
    fees_total = sum(t["total_fees_usdt"] for t in trades)
    pnl_total = final_equity - initial_capital
    metrics = {
        "asset": asset, "strategy": strategy, "horizon_hours": horizon,
        "initial_capital_usdt": initial_capital, "cost_scenario": cost_name,
        "decision_opportunities": len(signal_by_idx), "up_signals": counts["UP"],
        "down_signals_no_spot_short": counts["DOWN"], "abstentions": counts["ABSTAIN"],
        "signal_coverage_up_pct": pct(counts["UP"], len(signal_by_idx)),
        "executed_trades": len(trades), "up_signals_skipped_while_position_open": skipped_open,
        "tp_exits": tps, "sl_exits": sls, "time_exits": timeouts,
        "tp_before_sl_rate_all_trades_pct": pct(tps, len(trades)),
        "barrier_resolved_win_rate_pct": pct(tps, tps + sls),
        "net_profitable_trade_rate_pct": pct(sum(t["net_pnl_usdt"] > 0 for t in trades), len(trades)),
        "mean_net_trade_return_pct": (
            sum(t["net_return_on_entry_outlay_pct"] for t in trades) / len(trades) if trades else None
        ),
        "median_net_trade_return_pct": (
            float(pd.Series([t["net_return_on_entry_outlay_pct"] for t in trades]).median()) if trades else None
        ),
        "total_fees_usdt": fees_total, "final_equity_usdt": final_equity,
        "net_pnl_usdt": pnl_total, "return_pct": pct(pnl_total, initial_capital),
        "max_drawdown_hourly_close_pct": worst_dd * 100.0,
        "cash_return_pct": 0.0, "cash_max_drawdown_pct": 0.0,
        "buy_hold_final_equity_usdt": None, "buy_hold_return_pct": None, "buy_hold_max_drawdown_hourly_close_pct": None,
        "beats_cash": final_equity > initial_capital,
        "beats_buy_hold": None,
    }
    months = []
    month_start = pd.Timestamp("2026-01-01T00:00:00Z")
    month_end = pd.Timestamp("2026-11-01T00:00:00Z")
    while month_start < month_end:
        key = month_start.strftime("%Y-%m")
        m = by_month.get(key, {})
        mt = int(m.get("trades", 0))
        tp = int(m.get("tp", 0))
        sl = int(m.get("sl", 0))
        tout = int(m.get("time_exit", 0))
        months.append({
            "month_utc": key, "asset": asset, "strategy": strategy, "horizon_hours": horizon,
            "initial_capital_usdt": initial_capital, "cost_scenario": cost_name,
            "trades": mt, "tp_exits": tp, "sl_exits": sl, "time_exits": tout,
            "tp_before_sl_rate_all_trades_pct": pct(tp, mt),
            "barrier_resolved_win_rate_pct": pct(tp, tp + sl),
            "net_pnl_usdt": float(m.get("pnl_usdt", 0.0)),
            "fees_usdt": float(m.get("fees_usdt", 0.0)),
            "net_profitable_trades": int(m.get("wins_net", 0)),
            "net_losing_trades": int(m.get("losses_net", 0)),
        })
        month_start = month_start + pd.offsets.MonthBegin(1)
    return metrics, trades, months


def simulate_buy_hold(rows: list[dict[str, Any]], eval_start_idx: int, initial: float,
                      cost_name: str, cost: dict[str, float]) -> dict[str, Any]:
    fee = cost["fee_rate"]
    px_cost = (cost["spread_bps"] / 2.0 + cost["slippage_bps"]) / 10000.0
    start_open = float(rows[eval_start_idx]["open"])
    entry_fill = start_open * (1.0 + px_cost)
    allocation = initial * POSITION_FRACTION
    qty = allocation / (entry_fill * (1.0 + fee))
    entry_notional = qty * entry_fill
    entry_fee = entry_notional * fee
    cash = initial - entry_notional - entry_fee
    equity_curve = [initial]
    for idx in range(eval_start_idx, len(rows)):
        close = float(rows[idx]["close"])
        equity_curve.append(cash + qty * close * (1.0 - px_cost) * (1.0 - fee))
    raw_exit = float(rows[-1]["close"])
    exit_fill = raw_exit * (1.0 - px_cost)
    exit_notional = qty * exit_fill
    exit_fee = exit_notional * fee
    final_cash = cash + exit_notional - exit_fee
    return {
        "initial_capital_usdt": initial, "cost_scenario": cost_name,
        "entry_time_utc": rows[eval_start_idx]["start"], "exit_time_utc": rows[-1]["end"],
        "entry_open_raw": start_open, "entry_fill": entry_fill, "exit_close_raw": raw_exit,
        "exit_fill": exit_fill, "quantity": qty, "entry_fee_usdt": entry_fee, "exit_fee_usdt": exit_fee,
        "total_fees_usdt": entry_fee + exit_fee, "final_equity_usdt": final_cash,
        "net_pnl_usdt": final_cash - initial, "return_pct": pct(final_cash - initial, initial),
        "max_drawdown_hourly_close_pct": max_drawdown_pct(equity_curve),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def run(dataset_path: Path, predictions_path: Path, source_manifest: Path, output_dir: Path) -> dict[str, Any]:
    rows_by_asset = _read_dataset(dataset_path)
    forecasts, forecast_sha = load_frozen_predictions(predictions_path, source_manifest)
    eval_start_idx = next(
        (i for i, r in enumerate(rows_by_asset["ETHUSDT"]) if pd.Timestamp(r["start"]) == EVAL_START),
        None,
    )
    if eval_start_idx is None:
        raise ValueError("source archive lacks the exact frozen evaluation start")
    start_idx_by_end = {
        pd.Timestamp(r["end"]): i for i, r in enumerate(rows_by_asset["ETHUSDT"])
    }
    # Same aligned candle timeline is required for each asset.
    index_by_end = start_idx_by_end
    expected_groups = set()
    for asset in SOURCE_ASSETS:
        for strategy in SOURCE_STRATEGIES:
            if asset == "BTCUSDT" and strategy == "BTC_LEADS_ETH_4H":
                continue
            if asset == "ETHUSDT" or strategy != "BTC_LEADS_ETH_4H":
                for horizon in SOURCE_HORIZONS:
                    # The source generator creates BTC_LEADS_ETH only on ETH.
                    expected_groups.add((asset, strategy, horizon))
    actual_groups = set(zip(forecasts["asset"].astype(str), forecasts["strategy"].astype(str), forecasts["horizon_hours"].astype(int)))
    if actual_groups != expected_groups:
        raise ValueError(f"frozen prediction group mismatch; expected {len(expected_groups)}, got {len(actual_groups)}")
    if len(actual_groups) != 57:
        raise ValueError(f"expected 57 frozen signal groups, got {len(actual_groups)}")
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    monthly: list[dict[str, Any]] = []
    benchmarks: list[dict[str, Any]] = []
    benchmark_cache: dict[tuple[str, float, str], dict[str, Any]] = {}
    for asset in SOURCE_ASSETS:
        bars = rows_by_asset[asset]
        for capital in CAPITALS:
            for cost_name, cost in COSTS.items():
                bh = simulate_buy_hold(bars, eval_start_idx, capital, cost_name, cost)
                benchmark_cache[(asset, capital, cost_name)] = bh
                benchmarks.append({"benchmark": "CASH", "asset": asset, "initial_capital_usdt": capital,
                                   "cost_scenario": cost_name, "final_equity_usdt": capital, "net_pnl_usdt": 0.0,
                                   "return_pct": 0.0, "max_drawdown_hourly_close_pct": 0.0, "total_fees_usdt": 0.0})
                benchmarks.append({"benchmark": "BUY_AND_HOLD", "asset": asset, **bh})

    for asset, strategy, horizon in sorted(actual_groups):
        bars = rows_by_asset[asset]
        for capital in CAPITALS:
            for cost_name, cost in COSTS.items():
                metrics, trades, month_rows = simulate_portfolio(
                    asset, strategy, horizon, bars, forecasts, capital, cost_name, cost,
                    index_by_end, eval_start_idx,
                )
                bh = benchmark_cache[(asset, capital, cost_name)]
                metrics["buy_hold_final_equity_usdt"] = bh["final_equity_usdt"]
                metrics["buy_hold_return_pct"] = bh["return_pct"]
                metrics["buy_hold_max_drawdown_hourly_close_pct"] = bh["max_drawdown_hourly_close_pct"]
                metrics["beats_buy_hold"] = metrics["final_equity_usdt"] > bh["final_equity_usdt"]
                summaries.append(metrics)
                for trade in trades:
                    trade["buy_hold_return_pct"] = bh["return_pct"]
                    all_trades.append(trade)
                monthly.extend(month_rows)

    summary_path = output_dir / "summary.csv"
    trades_path = output_dir / "trades.csv"
    monthly_path = output_dir / "monthly_summary.csv"
    benchmark_path = output_dir / "benchmarks.csv"
    write_csv(summary_path, summaries)
    write_csv(trades_path, all_trades)
    write_csv(monthly_path, monthly)
    write_csv(benchmark_path, benchmarks)

    ranked = sorted(summaries, key=lambda r: float(r["return_pct"] or 0.0), reverse=True)
    tp_resolved = sorted(
        (r for r in summaries if r["executed_trades"] >= 20 and r["barrier_resolved_win_rate_pct"] is not None),
        key=lambda r: float(r["barrier_resolved_win_rate_pct"] or 0.0), reverse=True,
    )
    lines = [
        "# TP/SL barrier backtest V1 — frozen V1 forecast signals", "",
        "**Operational decision: NO_TRADE. Research-only; no live/PAPER orders were submitted.**", "",
        f"- Frozen dataset logical SHA-256: {EXPECTED_DATASET_SHA256}",
        f"- Frozen V1 prediction ledger SHA-256: {forecast_sha}",
        f"- Evaluation start: {rows_by_asset['ETHUSDT'][eval_start_idx]['start']}",
        f"- Evaluation end: {rows_by_asset['ETHUSDT'][-1]['end']}",
        "- Source forecasts are consumed with only decision timestamp, asset, strategy, horizon, and UP/DOWN/ABSTAIN class. Future-return and outcome labels are not loaded by the simulator.",
        "- UP forecasts trigger long spot entries at the next candle open; DOWN/ABSTAIN do not open shorts or force exits.",
        f"- Take profit: +{TAKE_PROFIT_PCT * 100:.2f}% from the modeled entry fill; stop loss: -{STOP_LOSS_PCT * 100:.2f}%.",
        "- Entry allocation: 95% of available quote balance; one position per asset/rule/horizon portfolio; overlapping UP signals are skipped while a position remains open.",
        "- Exit horizon: 4/12/24 hourly candles from the decision candle; if no barrier hits, exit at the close of the horizon candle.",
        "- Same-candle TP and SL: STOP first. Stop gap through SL executes at that candle open before modeled execution costs. TP gap does not receive price improvement; target fills at target level before modeled execution costs.",
        "- Every entry and exit pays the scenario's half-spread plus slippage adjustment and fee. Intrabar path is unknowable from OHLC; the tie resolves against the strategy.",
        "- Drawdown uses hourly-close equity marked net of modeled liquidation costs; intrabar drawdown beyond barrier semantics is not reconstructed.",
        "- Costs are assumptions, not historical order-book quotes; historical point-in-time availability/finality stays UNKNOWN.",
        "- No parameter optimization was performed. These V1 signals and thresholds remain frozen; January–October data has prior research exposure and is not an untouched holdout.", "",
        "## Cost scenarios", "",
        "| Scenario | Fee per side | Full spread | Slippage per side | Approximate modeled round trip |",
        "|---|---:|---:|---:|---:|",
        "| BASE | 0.10% | 10 bps | 5 bps | 0.40% |",
        "| STRESS_1 | 0.10% | 25 bps | 10 bps | 0.65% |",
        "| STRESS_2 | 0.20% | 50 bps | 20 bps | 1.30% |", "",
        "## Highest TP-before-SL rates (descriptive; at least 20 trades and at least one barrier exit)", "",
        "| Asset | Strategy | Horizon | Capital | Costs | Trades | TP | SL | Time exits | TP / (TP+SL) | TP / all trades | Net return | Max DD |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in tp_resolved[:15]:
        def f(v: Any) -> str:
            return "NA" if v is None else f"{float(v):.2f}%"
        lines.append(
            f"| {r['asset']} | {r['strategy']} | {r['horizon_hours']}h | {r['initial_capital_usdt']:.0f} | {r['cost_scenario']} | "
            f"{r['executed_trades']} | {r['tp_exits']} | {r['sl_exits']} | {r['time_exits']} | "
            f"{f(r['barrier_resolved_win_rate_pct'])} | {f(r['tp_before_sl_rate_all_trades_pct'])} | "
            f"{f(r['return_pct'])} | {f(r['max_drawdown_hourly_close_pct'])} |"
        )
    lines.extend(["", "## Best final balances (descriptive only)", "",
                  "| Asset | Strategy | Horizon | Capital | Costs | Trades | Final USDT | PnL USDT | Return | Cash return | Buy/hold return | Max DD |",
                  "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|"])
    for r in ranked[:15]:
        def f2(v: Any) -> str:
            return "NA" if v is None else f"{float(v):.3f}%"
        lines.append(
            f"| {r['asset']} | {r['strategy']} | {r['horizon_hours']}h | {r['initial_capital_usdt']:.0f} | "
            f"{r['cost_scenario']} | {r['executed_trades']} | {r['final_equity_usdt']:.4f} | {r['net_pnl_usdt']:.4f} | "
            f"{f2(r['return_pct'])} | 0.000% | {f2(r['buy_hold_return_pct'])} | {f2(r['max_drawdown_hourly_close_pct'])} |"
        )
    lines.extend(["", "## Interpretation guardrails", "",
        "- TP-before-SL is reported two ways: TP / all trades (time exits remain in denominator), and TP / (TP+SL) (only trades where a barrier was hit).",
        "- Time exits may be net winners or losers. Therefore barrier win rate must be read with time exits, net PnL, return, drawdown, and benchmarks.",
        "- A forecast labeled DOWN is not executed as a short: the existing project is spot-oriented. This report only simulates UP forecasts as long entries.",
        "- The highest apparent rate is not a selected or promoted strategy. With 57 signal groups, two capital levels, and three cost scenarios, selecting the best historical cell would be multiple testing.",
        "- No result in this report permits strategy promotion or changes the operational gate: NO_TRADE.", ""])
    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    result = {
        "schema_version": 1, "status": "EXPLORATORY_BARRIER_BACKTEST_NOT_PROSPECTIVE_HOLDOUT",
        "decision": "NO_TRADE", "dataset_sha256": EXPECTED_DATASET_SHA256,
        "forecast_ledger_sha256": forecast_sha, "configuration_sha256": EXPECTED_CONFIG_SHA256,
        "evaluation_start_utc": rows_by_asset["ETHUSDT"][eval_start_idx]["start"],
        "evaluation_end_utc": rows_by_asset["ETHUSDT"][-1]["end"],
        "take_profit_pct": TAKE_PROFIT_PCT * 100.0, "stop_loss_pct": -STOP_LOSS_PCT * 100.0,
        "capital_levels_usdt": list(CAPITALS), "cost_scenarios": COSTS,
        "entry_policy": "UP forecasts only; long spot, next candle open, 95% allocation",
        "same_candle_policy": "SL first if both barriers touch",
        "summary_rows": len(summaries), "trade_count": len(all_trades),
        "benchmark_rows": len(benchmarks), "monthly_rows": len(monthly),
        "summary": summaries,
        "provenance": {
            "prediction_artifact_run_id": 39611639871,
            "prediction_artifact_id": 12350826389,
            "dataset_artifact_run_id": 39062915950,
            "dataset_sha256": EXPECTED_DATASET_SHA256,
        },
        "limitations": [
            "Only UP forecasts open long spot positions; DOWN calls do not open spot shorts.",
            "OHLC cannot reveal intra-candle sequencing; both-hit bars assume stop first.",
            "Intrabar gaps can cause losses beyond the stop threshold; stop exits pay modeled spread/slippage and fee.",
            "Hourly-close drawdown is not full tick-level drawdown.",
            "Historical source available_at and finality are UNKNOWN.",
            "Prior research exposed the same historical window; this is not an untouched holdout.",
        ],
    }
    json_path = output_dir / "summary.json"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    all_outputs = [summary_path, trades_path, monthly_path, benchmark_path, report_path, json_path]
    manifest = {
        "schema_version": 1, "decision": "NO_TRADE",
        "source_dataset_sha256": EXPECTED_DATASET_SHA256, "source_forecast_ledger_sha256": forecast_sha,
        "files": {p.name: {"size_bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in all_outputs},
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print("DATASET_SHA256:", EXPECTED_DATASET_SHA256)
    print("FROZEN_FORECAST_LEDGER_SHA256:", forecast_sha)
    print("SUMMARY_ROWS:", len(summaries))
    print("EXECUTED_TRADES:", len(all_trades))
    print("MONTHLY_ROWS:", len(monthly))
    print("BENCHMARK_ROWS:", len(benchmarks))
    print("TOP_NET_RETURNS:")
    for r in ranked[:12]:
        print(
            f"asset={r['asset']} strategy={r['strategy']} h={r['horizon_hours']} capital={r['initial_capital_usdt']:.0f} "
            f"cost={r['cost_scenario']} trades={r['executed_trades']} tp={r['tp_exits']} sl={r['sl_exits']} "
            f"time={r['time_exits']} tp_before_sl={r['tp_before_sl_rate_all_trades_pct']} "
            f"barrier_win={r['barrier_resolved_win_rate_pct']} return={r['return_pct']} "
            f"buy_hold={r['buy_hold_return_pct']} mdd={r['max_drawdown_hourly_close_pct']}"
        )
    print("DECISION: NO_TRADE")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Conservative TP/SL replay for frozen V1 UP signals.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.dataset, args.predictions, args.source_manifest, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
