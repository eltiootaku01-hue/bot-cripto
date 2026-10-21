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
import sys

# When invoked as `python research/script.py`, Python otherwise places only
# the research/ folder on sys.path. Add the repository root for sibling imports.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from research.tp_sl_barrier_backtest_v1 import (
    CAPITALS,
    COSTS,
    EVAL_START,
    SOURCE_ASSETS,
    SOURCE_STRATEGIES,
    SOURCE_HORIZONS,
    _read_dataset,
    load_frozen_predictions,
    max_drawdown_pct,
    pct,
    simulate_portfolio,
)

EXPECTED_DATASET_SHA256 = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
EXPECTED_FORECAST_SHA256 = "b8f555028791d0a9d2f93dc428acf3882d5ec3488ac56221df3136e4841c3d00"
EXPECTED_COSTED_SUMMARY_SHA256 = "0f9f76ad8f94042b304fd6a9b65d62171aa4f8311900aaafab5f2209eea0c25a"
EXPECTED_COSTED_TRADES_SHA256 = "e54e2e0094ef898716da0e9cca10b753cf821c4f6f5ff6c2127a692e2405f550"
ZERO_COST = {"fee_rate": 0.0, "spread_bps": 0.0, "slippage_bps": 0.0}
OUTCOMES = ("TP", "SL", "TIME_EXIT")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in records:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def read_and_verify_prior_artifacts(summary_path: Path, trades_path: Path, manifest_path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("decision") != "NO_TRADE":
        raise ValueError("costed baseline decision metadata is not NO_TRADE")
    expected = {
        "summary.csv": (summary_path, EXPECTED_COSTED_SUMMARY_SHA256),
        "trades.csv": (trades_path, EXPECTED_COSTED_TRADES_SHA256),
    }
    for name, (path, pinned_hash) in expected.items():
        meta = manifest.get("files", {}).get(name)
        if not meta:
            raise ValueError(f"source manifest missing {name}")
        digest = file_sha256(path)
        if digest != pinned_hash or digest != meta.get("sha256") or path.stat().st_size != meta.get("size_bytes"):
            raise ValueError(f"source {name} hash/size failed verification")
    with summary_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 342:
        raise ValueError(f"expected 342 costed summary rows, got {len(rows)}")
    return rows, manifest


def empty_trade_stats() -> dict[str, Any]:
    return {
        "trades": 0, "tp": 0, "sl": 0, "time": 0,
        "net_pnl_usdt": 0.0, "gross_price_pnl_proxy_usdt": 0.0,
        "execution_friction_residual_usdt": 0.0, "fees_usdt": 0.0,
        "gross_return_pct_sum": 0.0, "net_trade_return_pct_sum": 0.0,
        "net_profitable_trades": 0, "gross_positive_price_trades": 0,
        "sl_gap": 0, "both_barriers_same_candle": 0,
        "time_exit_net_winners": 0, "time_exit_net_losers": 0,
        "time_exit_net_pnl_usdt": 0.0, "time_exit_gross_proxy_usdt": 0.0,
        "tp_net_pnl_usdt": 0.0, "sl_net_pnl_usdt": 0.0,
        "tp_gross_proxy_usdt": 0.0, "sl_gross_proxy_usdt": 0.0,
    }


def add_trade_stats(stats: dict[str, Any], row: dict[str, str]) -> None:
    outcome = row["outcome"]
    reason = row["exit_reason"]
    qty = float(row["quantity"])
    gross = qty * (float(row["exit_trigger_raw"]) - float(row["entry_open_raw"]))
    net = float(row["net_pnl_usdt"])
    fee = float(row["total_fees_usdt"])
    gross_return = float(row["gross_price_return_pct"])
    net_return = float(row["net_return_on_entry_outlay_pct"])
    stats["trades"] += 1
    stats["net_pnl_usdt"] += net
    stats["gross_price_pnl_proxy_usdt"] += gross
    stats["execution_friction_residual_usdt"] += gross - net
    stats["fees_usdt"] += fee
    stats["gross_return_pct_sum"] += gross_return
    stats["net_trade_return_pct_sum"] += net_return
    stats["net_profitable_trades"] += int(net > 0)
    stats["gross_positive_price_trades"] += int(gross > 0)
    stats["tp"] += int(outcome == "TP")
    stats["sl"] += int(outcome == "SL")
    stats["time"] += int(outcome == "TIME_EXIT")
    stats["sl_gap"] += int(reason == "SL_GAP")
    stats["both_barriers_same_candle"] += int(reason == "SL_BOTH_TOUCHED_CONSERVATIVE")
    if outcome == "TP":
        stats["tp_net_pnl_usdt"] += net
        stats["tp_gross_proxy_usdt"] += gross
    elif outcome == "SL":
        stats["sl_net_pnl_usdt"] += net
        stats["sl_gross_proxy_usdt"] += gross
    elif outcome == "TIME_EXIT":
        stats["time_exit_net_pnl_usdt"] += net
        stats["time_exit_gross_proxy_usdt"] += gross
        stats["time_exit_net_winners"] += int(net > 0)
        stats["time_exit_net_losers"] += int(net <= 0)


def aggregate_costed_trade_ledger(trades_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    by_cost: dict[str, dict[str, Any]] = defaultdict(empty_trade_stats)
    by_rule: dict[tuple[str, str, str, int], dict[str, Any]] = defaultdict(empty_trade_stats)
    by_reason: dict[tuple[str, str], dict[str, Any]] = defaultdict(empty_trade_stats)
    by_outcome: dict[tuple[str, str], dict[str, Any]] = defaultdict(empty_trade_stats)
    row_count = 0
    with trades_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            row_count += 1
            scenario, outcome, reason = row["cost_scenario"], row["outcome"], row["exit_reason"]
            asset, strategy, horizon = row["asset"], row["strategy"], int(row["horizon_hours"])
            add_trade_stats(by_cost[scenario], row)
            add_trade_stats(by_rule[(scenario, asset, strategy, horizon)], row)
            add_trade_stats(by_reason[(scenario, reason)], row)
            add_trade_stats(by_outcome[(scenario, outcome)], row)

    overall = []
    for scenario in ("BASE", "STRESS_1", "STRESS_2"):
        s = by_cost[scenario]
        overall.append({
            "cost_scenario": scenario,
            **s,
            "tp_all_trades_pct": pct(s["tp"], s["trades"]),
            "tp_barrier_resolved_pct": pct(s["tp"], s["tp"] + s["sl"]),
            "net_profitable_trade_pct": pct(s["net_profitable_trades"], s["trades"]),
            "gross_positive_price_trade_pct": pct(s["gross_positive_price_trades"], s["trades"]),
            "mean_gross_price_return_pct": s["gross_return_pct_sum"] / s["trades"] if s["trades"] else None,
            "mean_net_trade_return_pct": s["net_trade_return_pct_sum"] / s["trades"] if s["trades"] else None,
            "estimated_spread_slippage_residual_usdt": s["execution_friction_residual_usdt"] - s["fees_usdt"],
            "sl_gap_pct_all_trades": pct(s["sl_gap"], s["trades"]),
            "same_candle_both_hit_pct_all_trades": pct(s["both_barriers_same_candle"], s["trades"]),
            "time_exits_net_winner_pct": pct(s["time_exit_net_winners"], s["time"]),
        })

    by_rule_rows = []
    for (scenario, asset, strategy, horizon), s in sorted(by_rule.items()):
        by_rule_rows.append({
            "cost_scenario": scenario, "asset": asset, "strategy": strategy, "horizon_hours": horizon,
            **s,
            "tp_all_trades_pct": pct(s["tp"], s["trades"]),
            "tp_barrier_resolved_pct": pct(s["tp"], s["tp"] + s["sl"]),
            "net_profitable_trade_pct": pct(s["net_profitable_trades"], s["trades"]),
            "gross_positive_price_trade_pct": pct(s["gross_positive_price_trades"], s["trades"]),
            "mean_gross_price_return_pct": s["gross_return_pct_sum"] / s["trades"] if s["trades"] else None,
            "mean_net_trade_return_pct": s["net_trade_return_pct_sum"] / s["trades"] if s["trades"] else None,
            "estimated_spread_slippage_residual_usdt": s["execution_friction_residual_usdt"] - s["fees_usdt"],
        })

    by_reason_rows = []
    for (scenario, reason), s in sorted(by_reason.items()):
        by_reason_rows.append({
            "cost_scenario": scenario, "exit_reason": reason, **s,
            "net_pnl_usdt": s["net_pnl_usdt"], "gross_price_pnl_proxy_usdt": s["gross_price_pnl_proxy_usdt"],
        })
    # Add precise outcome-level totals and reason totals to the same CSV with explicit grouping labels.
    for (scenario, outcome), s in sorted(by_outcome.items()):
        by_reason_rows.append({
            "cost_scenario": scenario, "exit_reason": f"OUTCOME_TOTAL:{outcome}", **s,
            "net_pnl_usdt": s["net_pnl_usdt"], "gross_price_pnl_proxy_usdt": s["gross_price_pnl_proxy_usdt"],
        })
    return overall, by_rule_rows, by_reason_rows, row_count


def summary_aggregate(rows: list[dict[str, Any]], scenario: str, label: str) -> dict[str, Any]:
    capitals = [float(r["initial_capital_usdt"]) for r in rows]
    returns = [float(r["return_pct"]) for r in rows]
    pnl = [float(r["net_pnl_usdt"]) for r in rows]
    trades = sum(int(r["executed_trades"]) for r in rows)
    tp = sum(int(r["tp_exits"]) for r in rows)
    sl = sum(int(r["sl_exits"]) for r in rows)
    tm = sum(int(r["time_exits"]) for r in rows)
    fees = sum(float(r["total_fees_usdt"]) for r in rows)
    known_profit_count_estimate = 0.0
    known_profit_rate = False
    for row in rows:
        row_trades = int(row["executed_trades"])
        rate = row.get("net_profitable_trade_rate_pct")
        if row_trades and rate not in (None, ""):
            known_profit_count_estimate += float(rate) * row_trades / 100.0
            known_profit_rate = True
    return {
        "scenario": label,
        "matched_cost_scenario": scenario,
        "portfolio_count": len(rows),
        "initial_capital_total_usdt": sum(capitals),
        "active_portfolios": sum(int(r["executed_trades"]) > 0 for r in rows),
        "profitable_portfolios": sum(float(r["return_pct"]) > 0 for r in rows),
        "losing_portfolios": sum(float(r["return_pct"]) < 0 for r in rows),
        "trades": trades, "tp": tp, "sl": sl, "time_exits": tm,
        "tp_all_trades_pct": pct(tp, trades),
        "tp_barrier_resolved_pct": pct(tp, tp + sl),
        "net_profitable_trade_pct": pct(known_profit_count_estimate, trades) if known_profit_rate else None,
        "mean_portfolio_return_pct": sum(returns) / len(returns) if returns else None,
        "median_portfolio_return_pct": float(pd.Series(returns).median()) if returns else None,
        "sum_net_pnl_usdt": sum(pnl),
        "portfolio_ensemble_return_on_initial_capital_pct": pct(sum(pnl), sum(capitals)),
        "total_fees_usdt": fees,
        "mean_max_drawdown_hourly_close_pct": sum(float(r["max_drawdown_hourly_close_pct"]) for r in rows) / len(rows) if rows else None,
        "trades_per_portfolio": trades / len(rows) if rows else None,
    }

def run(dataset_path: Path, predictions_path: Path, prediction_manifest_path: Path,
        costed_summary_path: Path, costed_trades_path: Path, costed_manifest_path: Path,
        output_dir: Path) -> dict[str, Any]:
    prior_rows, prior_manifest = read_and_verify_prior_artifacts(
        costed_summary_path, costed_trades_path, costed_manifest_path
    )
    dataset_rows = _read_dataset(dataset_path)
    forecasts, forecast_sha = load_frozen_predictions(predictions_path, prediction_manifest_path)
    if forecast_sha != EXPECTED_FORECAST_SHA256:
        raise ValueError("frozen prediction ledger SHA-256 mismatch")

    costed_lookup: dict[tuple[str, str, int, float, str], dict[str, str]] = {}
    for row in prior_rows:
        key = (row["asset"], row["strategy"], int(row["horizon_hours"]),
               float(row["initial_capital_usdt"]), row["cost_scenario"])
        if key in costed_lookup:
            raise ValueError(f"duplicate costed summary key: {key}")
        costed_lookup[key] = row
    expected_key_count = 57 * 2 * 3
    if len(costed_lookup) != expected_key_count:
        raise ValueError(f"expected {expected_key_count} unique costed summary keys, got {len(costed_lookup)}")

    end_index = {pd.Timestamp(row["end"]): i for i, row in enumerate(dataset_rows["ETHUSDT"])}
    eval_start_idx = next(
        (i for i, row in enumerate(dataset_rows["ETHUSDT"]) if pd.Timestamp(row["start"]) == EVAL_START),
        None,
    )
    if eval_start_idx is None:
        raise ValueError("source dataset lacks frozen evaluation start")
    groups = sorted(set(zip(forecasts["asset"].astype(str), forecasts["strategy"].astype(str),
                            forecasts["horizon_hours"].astype(int))))
    if len(groups) != 57:
        raise ValueError(f"expected 57 frozen signal groups, got {len(groups)}")

    zero_cost_rows: list[dict[str, Any]] = []
    for asset, strategy, horizon in groups:
        bars = dataset_rows[asset]
        for capital in CAPITALS:
            metrics, _trades, _months = simulate_portfolio(
                asset, strategy, horizon, bars, forecasts, capital, "COSTLESS", ZERO_COST,
                end_index, eval_start_idx,
            )
            zero_cost_rows.append(metrics)
    zero_lookup = {
        (r["asset"], r["strategy"], int(r["horizon_hours"]), float(r["initial_capital_usdt"])): r
        for r in zero_cost_rows
    }
    if len(zero_lookup) != 114:
        raise ValueError("costless counterfactual did not create exactly 114 unique portfolios")

    comparison_rows: list[dict[str, Any]] = []
    for key, costed in sorted(costed_lookup.items()):
        asset, strategy, horizon, capital, scenario = key
        zero = zero_lookup[(asset, strategy, horizon, capital)]
        costed_pnl = float(costed["net_pnl_usdt"])
        zero_pnl = float(zero["net_pnl_usdt"])
        comparison_rows.append({
            "asset": asset, "strategy": strategy, "horizon_hours": horizon,
            "initial_capital_usdt": capital, "cost_scenario": scenario,
            "costless_return_pct": float(zero["return_pct"]),
            "costed_return_pct": float(costed["return_pct"]),
            "return_change_vs_costless_percentage_points": float(costed["return_pct"]) - float(zero["return_pct"]),
            "costless_net_pnl_usdt": zero_pnl, "costed_net_pnl_usdt": costed_pnl,
            "net_pnl_change_vs_costless_usdt": costed_pnl - zero_pnl,
            "costless_trades": int(zero["executed_trades"]), "costed_trades": int(costed["executed_trades"]),
            "trade_count_change_vs_costless": int(costed["executed_trades"]) - int(zero["executed_trades"]),
            "costless_tp": int(zero["tp_exits"]), "costed_tp": int(costed["tp_exits"]),
            "costless_sl": int(zero["sl_exits"]), "costed_sl": int(costed["sl_exits"]),
            "costless_time_exits": int(zero["time_exits"]), "costed_time_exits": int(costed["time_exits"]),
            "costless_tp_barrier_pct": zero["barrier_resolved_win_rate_pct"],
            "costed_tp_barrier_pct": costed["barrier_resolved_win_rate_pct"],
            "costed_total_fees_usdt": float(costed["total_fees_usdt"]),
            "costed_max_drawdown_hourly_close_pct": float(costed["max_drawdown_hourly_close_pct"]),
        })

    aggregate_rows: list[dict[str, Any]] = []
    aggregate_rows.append(summary_aggregate(zero_cost_rows, "COSTLESS", "COSTLESS"))
    for scenario in ("BASE", "STRESS_1", "STRESS_2"):
        rows = [r for r in prior_rows if r["cost_scenario"] == scenario]
        aggregate_rows.append(summary_aggregate(rows, scenario, scenario))
    aggregates_by_label = {r["scenario"]: r for r in aggregate_rows}
    zero_pnl = float(aggregates_by_label["COSTLESS"]["sum_net_pnl_usdt"])
    zero_return = float(aggregates_by_label["COSTLESS"]["mean_portfolio_return_pct"])
    for row in aggregate_rows:
        row["net_pnl_change_vs_costless_usdt"] = float(row["sum_net_pnl_usdt"]) - zero_pnl
        row["mean_return_change_vs_costless_pp"] = float(row["mean_portfolio_return_pct"]) - zero_return

    # Per-family and per-horizon performance uses one 60-USDT portfolio per
    # asset/rule/horizon to avoid double-counting the same proportional signal
    # path at both initial capital levels.
    family_rows: list[dict[str, Any]] = []
    horizon_rows: list[dict[str, Any]] = []
    asset_rows: list[dict[str, Any]] = []
    costless_cap_rows = [r for r in zero_cost_rows if float(r["initial_capital_usdt"]) == 60.0]
    for family in SOURCE_STRATEGIES:
        subset = [r for r in costless_cap_rows if r["strategy"] == family]
        if subset:
            stat = summary_aggregate(subset, "COSTLESS", family)
            stat.update({"scenario": "COSTLESS", "strategy": family, "capital_usdt": 60})
            family_rows.append(stat)
    for horizon in SOURCE_HORIZONS:
        subset = [r for r in costless_cap_rows if int(r["horizon_hours"]) == horizon]
        stat = summary_aggregate(subset, "COSTLESS", str(horizon))
        active = [r for r in subset if int(r["executed_trades"]) > 0]
        stat.update({
            "scenario": "COSTLESS", "horizon_hours": horizon,
            "active_portfolios": len(active),
            "mean_return_active_portfolios_pct": sum(float(r["return_pct"]) for r in active) / len(active) if active else None,
            "capital_usdt": 60,
        })
        horizon_rows.append(stat)
    for asset in SOURCE_ASSETS:
        subset = [r for r in costless_cap_rows if r["asset"] == asset]
        stat = summary_aggregate(subset, "COSTLESS", asset)
        stat.update({"scenario": "COSTLESS", "asset": asset, "capital_usdt": 60})
        asset_rows.append(stat)

    for scenario in ("BASE", "STRESS_1", "STRESS_2"):
        cap_rows = [r for r in prior_rows if r["cost_scenario"] == scenario and float(r["initial_capital_usdt"]) == 60.0]
        for family in SOURCE_STRATEGIES:
            subset = [r for r in cap_rows if r["strategy"] == family]
            if not subset:
                continue
            stat = summary_aggregate(subset, scenario, family)
            stat.update({"scenario": scenario, "strategy": family, "capital_usdt": 60})
            family_rows.append(stat)
        for horizon in SOURCE_HORIZONS:
            subset = [r for r in cap_rows if int(r["horizon_hours"]) == horizon]
            stat = summary_aggregate(subset, scenario, str(horizon))
            active = [r for r in subset if int(r["executed_trades"]) > 0]
            stat.update({
                "scenario": scenario, "horizon_hours": horizon,
                "active_portfolios": len(active),
                "mean_return_active_portfolios_pct": sum(float(r["return_pct"]) for r in active) / len(active) if active else None,
                "capital_usdt": 60,
            })
            horizon_rows.append(stat)
        for asset in SOURCE_ASSETS:
            subset = [r for r in cap_rows if r["asset"] == asset]
            stat = summary_aggregate(subset, scenario, asset)
            stat.update({"scenario": scenario, "asset": asset, "capital_usdt": 60})
            asset_rows.append(stat)

    trade_overall, trade_by_rule, exit_breakdown, trade_count = aggregate_costed_trade_ledger(costed_trades_path)
    if trade_count != sum(int(r["executed_trades"]) for r in prior_rows):
        raise ValueError("trade ledger count does not reconcile with costed portfolio summary")
    for aggregate in trade_overall:
        scenario = aggregate["cost_scenario"]
        summary_rows = [r for r in prior_rows if r["cost_scenario"] == scenario]
        if aggregate["trades"] != sum(int(r["executed_trades"]) for r in summary_rows):
            raise ValueError(f"trade count mismatch for {scenario}")
        if aggregate["tp"] != sum(int(r["tp_exits"]) for r in summary_rows):
            raise ValueError(f"TP count mismatch for {scenario}")
        if aggregate["sl"] != sum(int(r["sl_exits"]) for r in summary_rows):
            raise ValueError(f"SL count mismatch for {scenario}")
        if aggregate["time"] != sum(int(r["time_exits"]) for r in summary_rows):
            raise ValueError(f"time-exit count mismatch for {scenario}")

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "costless_counterfactual.csv": zero_cost_rows,
        "cost_vs_costless_by_portfolio.csv": comparison_rows,
        "scenario_aggregate.csv": aggregate_rows,
        "signal_family_decomposition.csv": family_rows,
        "horizon_decomposition.csv": horizon_rows,
        "asset_decomposition.csv": asset_rows,
        "trade_execution_decomposition.csv": trade_overall,
        "trade_rule_decomposition.csv": trade_by_rule,
        "exit_reason_decomposition.csv": exit_breakdown,
    }
    for name, rows in paths.items():
        write_csv(output_dir / name, rows)

    base_family = {r["strategy"]: r for r in family_rows if r["scenario"] == "BASE"}
    zero_family = {r["strategy"]: r for r in family_rows if r["scenario"] == "COSTLESS"}
    family_names = sorted(base_family, key=lambda name: float(base_family[name]["mean_portfolio_return_pct"] or 0), reverse=True)
    lines = [
        "# TP/SL V1 — diagnosis: signal vs transaction costs", "",
        "**Operational decision: NO_TRADE. This is a post-hoc diagnostic; no signal or parameter has been changed.**", "",
        f"- Source dataset SHA-256: {EXPECTED_DATASET_SHA256}",
        f"- Frozen V1 forecast ledger SHA-256: {forecast_sha}",
        f"- Costed baseline summary SHA-256: {EXPECTED_COSTED_SUMMARY_SHA256}",
        f"- Costed baseline trade ledger SHA-256: {EXPECTED_COSTED_TRADES_SHA256}",
        f"- Costed trade rows reconciled: {trade_count}",
        f"- Costless matched portfolios: {len(zero_cost_rows)}; source costed portfolio rows: {len(prior_rows)}.",
        "- Same archived UP/DOWN/ABSTAIN calls; only fee, spread, and slippage set to zero for the costless counterfactual.",
        "- The 114 portfolios use a combined starting capital of 9,120 USDT (57 rule/asset/horizon groups at each of 60 and 100 USDT). Aggregate PnL sums independent simulation accounts; it is not one account's balance.",
        "- Costless is a counterfactual accounting diagnostic, not an executable trading scenario; historical point-in-time candle availability remains UNKNOWN.", "",
        "## Costless signal path vs modeled friction", "",
        "| Scenario | Portfolios | Trades | TP | SL | Time exits | TP/(TP+SL) | Net-profitable trades | Mean return / portfolio | Aggregate PnL | PnL change vs costless | Fees | Mean max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in aggregate_rows:
        win_rate = r["net_profitable_trade_pct"]
        lines.append(
            f"| {r['scenario']} | {r['portfolio_count']} | {r['trades']} | {r['tp']} | {r['sl']} | {r['time_exits']} | "
            f"{'NA' if r['tp_barrier_resolved_pct'] is None else f'{r["tp_barrier_resolved_pct"]:.2f}%'} | "
            f"{'NA' if win_rate is None else f'{win_rate:.2f}%'} | "
            f"{r['mean_portfolio_return_pct']:.2f}% | {r['sum_net_pnl_usdt']:.2f} USDT | "
            f"{r['net_pnl_change_vs_costless_usdt']:.2f} USDT | {r['total_fees_usdt']:.2f} USDT | "
            f"{r['mean_max_drawdown_hourly_close_pct']:.2f}% |"
        )
    lines.extend([
        "",
        f"**Signal vs friction conclusion:** without any modeled fee, spread, or slippage, the frozen signals and TP/SL contract still lost {abs(aggregates_by_label['COSTLESS']['sum_net_pnl_usdt']):.2f} USDT in aggregate (-{abs(zero_return):.2f}% mean return per portfolio). Compared with this counterfactual, the costed BASE case deteriorated by another {abs(aggregates_by_label['BASE']['net_pnl_change_vs_costless_usdt']):.2f} USDT ({abs(aggregates_by_label['BASE']['mean_return_change_vs_costless_pp']):.2f} percentage points in mean portfolio return). The difference includes both direct friction and the fact that fill-relative barriers shift, changing exits and subsequent trade frequency; it is not a pure additive fee estimate.",
        "",
        "## Signal-family decomposition: zero-cost vs BASE (60-USDT portfolios)", "",
        "| Signal family | Zero-cost mean return | BASE mean return | BASE trades | TP | SL | Time exits | TP/(TP+SL) | BASE fees | BASE max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for name in family_names:
        base_row = base_family[name]
        zero_row = zero_family.get(name, {})
        wr = base_row["tp_barrier_resolved_pct"]
        zero_ret = zero_row.get("mean_portfolio_return_pct")
        lines.append(
            f"| {name} | {'NA' if zero_ret is None else f'{zero_ret:.2f}%'} | "
            f"{base_row['mean_portfolio_return_pct']:.2f}% | {base_row['trades']} | "
            f"{base_row['tp']} | {base_row['sl']} | {base_row['time_exits']} | "
            f"{'NA' if wr is None else f'{wr:.2f}%'} | {base_row['total_fees_usdt']:.2f} USDT | "
            f"{base_row['mean_max_drawdown_hourly_close_pct']:.2f}% |"
        )
    lines.extend([
        "",
        "## Horizon decomposition (60-USDT portfolios)", "",
        "| Cost scenario | Horizon | Portfolios | Trades | Trades/portfolio | TP | SL | Time exits | TP/(TP+SL) | Mean return, all portfolios | Mean return, active portfolios |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for r in horizon_rows:
        lines.append(
            f"| {r['scenario']} | {r['horizon_hours']}h | {r['portfolio_count']} | {r['trades']} | "
            f"{r['trades_per_portfolio']:.1f} | {r['tp']} | {r['sl']} | {r['time_exits']} | "
            f"{'NA' if r['tp_barrier_resolved_pct'] is None else f'{r["tp_barrier_resolved_pct"]:.2f}%'} | "
            f"{r['mean_portfolio_return_pct']:.2f}% | "
            f"{'NA' if r.get('mean_return_active_portfolios_pct') is None else f'{r["mean_return_active_portfolios_pct"]:.2f}%'} |"
        )
    lines.extend([
        "",
        "## Asset decomposition (60-USDT portfolios)", "",
        "| Cost scenario | Asset | Portfolios | Trades | TP | SL | Time exits | TP/(TP+SL) | Mean portfolio return | Total PnL across these portfolios |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for r in asset_rows:
        lines.append(
            f"| {r['scenario']} | {r['asset']} | {r['portfolio_count']} | {r['trades']} | "
            f"{r['tp']} | {r['sl']} | {r['time_exits']} | "
            f"{'NA' if r['tp_barrier_resolved_pct'] is None else f'{r["tp_barrier_resolved_pct"]:.2f}%'} | "
            f"{r['mean_portfolio_return_pct']:.2f}% | {r['sum_net_pnl_usdt']:.2f} USDT |"
        )
    lines.extend(["", "## Stop, time-exit, and execution-cost observations", ""])
    for r in trade_overall:
        lines.append(
            f"- **{r['cost_scenario']}**: {r['sl_gap']} gap-through-stop exits; "
            f"{r['both_barriers_same_candle']} candles touched both barriers and were resolved as stop; "
            f"{r['time']} time exits, with {r['time_exit_net_winners']} net-positive and "
            f"{r['time_exit_net_losers']} non-positive; time-exit net PnL {r['time_exit_net_pnl_usdt']:.2f} USDT. "
            f"Gross-price PnL proxy {r['gross_price_pnl_proxy_usdt']:.2f} USDT vs net PnL {r['net_pnl_usdt']:.2f} USDT; "
            f"friction residual {r['execution_friction_residual_usdt']:.2f} USDT including {r['fees_usdt']:.2f} USDT fees."
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "1. The costless counterfactual is the closest available separation of fixed signal quality from modeled transaction costs. If its aggregate/mean portfolio return is negative, the signal path itself fails before any fee/spread/slippage is charged.",
        "2. The costed-minus-costless PnL difference captures the total effect of the modeled friction under this simulator, including shifted entry-fill-relative barrier prices, changes in exit timing, and subsequent trade frequency. It is not a pure linear subtraction because the simulator is sequential and compounds available capital.",
        "3. Gross-price PnL proxy vs net PnL is a ledger accounting identity on the costed realized paths; it is not the costless counterfactual.",
        "4. No SL_GAP in the historical OHLC ledger does not guarantee future or live absence of gaps. Same-candle both-hit counts are conservative stop-first classifications, not observed intrabar sequence.",
        "5. The 4h/12h/24h signal outputs overlap in time and share the same historical period; they are not independent samples. The period was previously researched and is not an untouched holdout.",
        "6. No parameter search or signal adjustment is permitted from this report. main remains unchanged and the operational gate stays NO_TRADE.", "",
    ])
    report = output_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    summary_json = {
        "schema_version": 1,
        "status": "POST_HOC_DIAGNOSTIC_FIXED_SIGNALS_COSTLESS_COUNTERFACTUAL",
        "decision": "NO_TRADE",
        "dataset_sha256": EXPECTED_DATASET_SHA256,
        "forecast_ledger_sha256": forecast_sha,
        "source_costed_summary_sha256": EXPECTED_COSTED_SUMMARY_SHA256,
        "source_costed_trades_sha256": EXPECTED_COSTED_TRADES_SHA256,
        "source_trade_count_reconciled": trade_count,
        "costless_portfolios": len(zero_cost_rows),
        "costed_portfolios": len(prior_rows),
        "aggregate_rows": aggregate_rows,
        "limitations": [
            "The zero-cost counterfactual changes entry fill and therefore the exact levels of fill-relative TP/SL triggers.",
            "Historical available_at/finality remains UNKNOWN.",
            "This is a reused historical window, not a new independent holdout.",
            "OHLC bars do not identify intrabar sequencing; the existing stop-first policy remains fixed.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary_json, indent=2, sort_keys=True), encoding="utf-8")
    output_paths = [output_dir / name for name in paths] + [report, output_dir / "summary.json"]
    manifest = {
        "schema_version": 1, "decision": "NO_TRADE",
        "dataset_sha256": EXPECTED_DATASET_SHA256, "forecast_ledger_sha256": forecast_sha,
        "source_costed_summary_sha256": EXPECTED_COSTED_SUMMARY_SHA256,
        "source_costed_trades_sha256": EXPECTED_COSTED_TRADES_SHA256,
        "files": {p.name: {"size_bytes": p.stat().st_size, "sha256": file_sha256(p)} for p in output_paths},
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print("COSTED_TRADE_ROWS_RECONCILED:", trade_count)
    print("COSTLESS_PORTFOLIOS:", len(zero_cost_rows))
    print("COSTED_PORTFOLIOS:", len(prior_rows))
    for r in aggregate_rows:
        print(
            f"SCENARIO={r['scenario']} trades={r['trades']} tp={r['tp']} sl={r['sl']} time={r['time_exits']} "
            f"tp_barrier_pct={r['tp_barrier_resolved_pct']} mean_return_pct={r['mean_portfolio_return_pct']} "
            f"sum_pnl_usdt={r['sum_net_pnl_usdt']} cost_change_vs_zero={r['net_pnl_change_vs_costless_usdt']} "
            f"fees={r['total_fees_usdt']} mean_mdd_pct={r['mean_max_drawdown_hourly_close_pct']}"
        )
    print("SL_GAP_COUNTS:", {r["cost_scenario"]: r["sl_gap"] for r in trade_overall})
    print("BOTH_BARRIERS_COUNTS:", {r["cost_scenario"]: r["both_barriers_same_candle"] for r in trade_overall})
    print("DECISION: NO_TRADE")
    return summary_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Decompose frozen TP/SL V1 signal losses and modeled transaction-cost effects.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--prediction-manifest", type=Path, required=True)
    parser.add_argument("--costed-summary", type=Path, required=True)
    parser.add_argument("--costed-trades", type=Path, required=True)
    parser.add_argument("--costed-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.dataset, args.predictions, args.prediction_manifest,
        args.costed_summary, args.costed_trades, args.costed_manifest, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
