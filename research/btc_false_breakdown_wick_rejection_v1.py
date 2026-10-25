"""Frozen BTC false-breakdown lower-wick rejection replay; public data only, no orders."""
from __future__ import annotations
import argparse, csv, json, os, sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import btc_volume_shock_reversal_v1 as core

STRATEGY = "FALSE_BREAKDOWN_WICK_REJECTION"
PROTOCOL = "BTC_FALSE_BREAKDOWN_WICK_REJECTION_V1"
EMA_PERIOD = 100
SWEEP_LOOKBACK = 5
MIN_LOWER_WICK_FRACTION = Decimal("0.55")
MIN_CLOSE_LOCATION = Decimal("0.75")
TAKE_PROFIT = Decimal("1.04")
STOP_LOSS = Decimal("0.975")
MAX_HOLD = 7
ENTRY_ALLOC = Decimal("0.20")

_EMA_CACHE_ROWS: Sequence[dict[str, Any]] | None = None
_EMA_CACHE_VALUES: list[Decimal] = []

def ema_series(rows: Sequence[dict[str, Any]]) -> list[Decimal]:
    """Recursive EMA(alpha=2/(100+1)); only closes in rows are consumed."""
    global _EMA_CACHE_ROWS, _EMA_CACHE_VALUES
    if _EMA_CACHE_ROWS is rows and len(_EMA_CACHE_VALUES) == len(rows):
        return _EMA_CACHE_VALUES
    if not rows:
        return []
    alpha = Decimal(2) / Decimal(EMA_PERIOD + 1)
    complement = Decimal(1) - alpha
    values: list[Decimal] = [core.D(rows[0]["close"])]
    for i in range(1, len(rows)):
        close = core.D(rows[i]["close"])
        values.append(alpha * close + complement * values[-1])
    _EMA_CACHE_ROWS = rows
    _EMA_CACHE_VALUES = values
    return values

def lower_wick_rejection_signal(rows: Sequence[dict[str, Any]], i: int) -> bool:
    """Signal at close i: trend filter plus five-day low sweep, bullish reclaim and lower wick."""
    if i < max(EMA_PERIOD - 1, SWEEP_LOOKBACK):
        return False
    op, high, low, close = (core.D(rows[i][k]) for k in ("open", "high", "low", "close"))
    ema = ema_series(rows)[i]
    if close <= ema:
        return False
    prior_low = min(core.D(rows[j]["low"]) for j in range(i - SWEEP_LOOKBACK, i))
    if low >= prior_low:
        return False
    candle_range = high - low
    if candle_range <= 0 or close < op:
        return False
    lower_wick = min(op, close) - low
    if lower_wick < candle_range * MIN_LOWER_WICK_FRACTION:
        return False
    if (close - low) / candle_range < MIN_CLOSE_LOCATION:
        return False
    return True

def activate_frozen_rules() -> None:
    # Reuse the already-reviewed Decimal accounting, costs, data validator and execution engine.
    core.entry_signal = lower_wick_rejection_signal
    core.ALLOC = ENTRY_ALLOC
    core.TP = TAKE_PROFIT
    core.SL = STOP_LOSS
    core.MAX_HOLD = MAX_HOLD

def _blocked(outdir: Path, error: str, manifest: dict[str, Any]) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    manifest["status"] = "BLOCKED"
    manifest["blocker"] = error
    (outdir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (outdir / "report.md").write_text(
        "# BTC false-breakdown wick-rejection V1\n\nStatus: BLOCKED. No economic result is valid.\n\n"
        f"Blocker: {error}\n\nNo candle was interpolated or silently removed. Operational decision: NO_TRADE.\n",
        encoding="utf-8",
    )
    print("BLOCKED:" + error, file=sys.stderr)
    return 2

def _cash_result(cash: Decimal, rows: Sequence[dict[str, Any]], a: int, b: int) -> dict[str, Any]:
    return {
        "method": "CASH", "final": cash, "pnl": Decimal(0), "return_pct": Decimal(0),
        "mdd_pct": Decimal(0), "trades": 0, "wins": 0, "losses": 0, "fees": Decimal(0),
        "friction": Decimal(0), "rejects": 0, "fills": [],
        "curve": [{"timestamp_utc": rows[a]["timestamp"], "phase": "START", "equity_usdt": core.ds(cash)}]
        + [{"timestamp_utc": rows[i]["timestamp"], "phase": "CLOSE", "equity_usdt": core.ds(cash)} for i in range(a, b)],
    }

def run(outdir: Path) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    activate_frozen_rules()
    manifest: dict[str, Any] = {
        "protocol": PROTOCOL,
        "preregistration_issue": 159,
        "symbol": "BTCUSDT",
        "interval": "1d",
        "start_utc": core.START.isoformat(),
        "end_exclusive_utc": core.END.isoformat(),
        "point_in_time_availability": "UNKNOWN",
        "historical_exchange_filter_equivalence": "UNKNOWN",
        "historical_spread_slippage": "MODELED_ASSUMPTIONS_ONLY",
        "main_changed": False,
        "operational_decision": "NO_TRADE",
        "reused_engine_origin_commit": "d2d26aa7604ba357fe3b4f8422afa64b97b080e0",
        "frozen_rule": {
            "ema_period": EMA_PERIOD,
            "ema_alpha": "2/(100+1)",
            "false_breakdown_prior_low_bars": SWEEP_LOOKBACK,
            "current_low_must_break_prior_low": True,
            "bullish_or_flat_body": True,
            "minimum_lower_wick_fraction": str(MIN_LOWER_WICK_FRACTION),
            "minimum_close_location_fraction": str(MIN_CLOSE_LOCATION),
            "entry_fraction_of_available_cash": str(ENTRY_ALLOC),
            "take_profit_multiple": str(TAKE_PROFIT),
            "stop_loss_multiple": str(STOP_LOSS),
            "max_holding_daily_candles": MAX_HOLD,
            "execution": "signal after completed daily close; entry/exit at next daily open; terminal close liquidation",
        },
    }
    try:
        rows, pages = core.fetch_history()
        quality = core.validate_candles(rows)
        manifest.update({
            "quality": quality, "row_count": len(rows), "raw_pages": pages,
            "canonical_dataset_sha256": core.canonical_data_hash(rows),
        })
        if not quality["ok"]:
            return _blocked(outdir, "DATA_INTEGRITY_GATE_FAILED", manifest)
        filters, filter_evidence = core.fetch_filters()
        manifest.update({
            "exchange_info": filter_evidence,
            "filters_current": {k: core.ds(v) for k, v in filters.items()},
            "first_open_utc": rows[0]["timestamp"], "last_open_utc": rows[-1]["timestamp"],
        })
        with (outdir / "candles.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["open_ms", "timestamp", "close_ms", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "open_ms": row["open_ms"], "timestamp": row["timestamp"], "close_ms": row["close_ms"],
                    **{k: core.ds(row[k]) for k in ("open", "high", "low", "close", "volume")},
                })
        index = {row["open_ms"]: i for i, row in enumerate(rows)}
        metrics: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        equities: list[dict[str, Any]] = []
        for window_name, start, end in core.WINDOWS:
            if core.to_ms(start) not in index:
                raise core.DataIntegrityError("WINDOW_START_MISSING:" + window_name)
            a = index[core.to_ms(start)]
            b = index.get(core.to_ms(end), len(rows))
            if b <= a:
                raise core.DataIntegrityError("WINDOW_ORDER_INVALID:" + window_name)
            for capital in core.CAPITALS:
                for cost_name in core.COSTS:
                    for method in (STRATEGY, "BTC_BUY_AND_HOLD", "CASH"):
                        if method == STRATEGY:
                            result = core.simulate(rows, a, b, capital, cost_name, filters, window_name, "VOLUME_SHOCK_REVERSAL")
                            result["method"] = STRATEGY
                            for fill in result["fills"]:
                                fill["method"] = STRATEGY
                        elif method == "BTC_BUY_AND_HOLD":
                            result = core.simulate(rows, a, b, capital, cost_name, filters, window_name, "BTC_BUY_AND_HOLD")
                        else:
                            result = _cash_result(capital, rows, a, b)
                        metrics.append(core.metric_row(window_name, start, end, capital, cost_name, result))
                        fills.extend(result["fills"])
                        equities.extend({
                            "window": window_name, "capital": core.ds(capital), "cost": cost_name,
                            "method": result["method"], **point,
                        } for point in result["curve"])
        metric_fields = [
            "window", "start_utc", "end_exclusive_utc", "capital", "cost", "method", "final_equity_usdt",
            "net_pnl_usdt", "return_pct", "max_drawdown_pct", "closed_roundtrips", "wins", "losses",
            "fees_usdt", "spread_slippage_usdt", "rejected_orders",
        ]
        fill_fields = [
            "window", "capital", "cost", "method", "trade_id", "side", "reason", "signal_utc", "execution_utc",
            "execution_type", "raw_price", "fill_price", "qty_btc", "notional_usdt", "fee_usdt", "friction_usdt", "cash_after",
        ]
        equity_fields = ["window", "capital", "cost", "method", "timestamp_utc", "phase", "equity_usdt"]
        core.csv_write(outdir / "summary.csv", metrics, metric_fields)
        core.csv_write(outdir / "fills.csv", fills, fill_fields)
        core.csv_write(outdir / "equity.csv", equities, equity_fields)
        gate_metrics = [
            {**row, "method": "VOLUME_SHOCK_REVERSAL"} if row["method"] == STRATEGY else row
            for row in metrics
        ]
        gates = core.gate_results(gate_metrics, True)
        manifest["status"] = "COMPLETED"
        manifest["strategy_gate"] = gates["overall"]
        manifest["output_counts"] = {"summary_rows": len(metrics), "fill_rows": len(fills), "equity_rows": len(equities)}
        (outdir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        (outdir / "gate_results.json").write_text(json.dumps(gates, indent=2, sort_keys=True), encoding="utf-8")
        lines = [
            "# BTC false-breakdown wick-rejection V1", "",
            "Frozen economic gate: " + gates["overall"], "",
            "Dataset SHA-256: " + manifest["canonical_dataset_sha256"],
            "Candle count: " + str(manifest["row_count"]),
            "Continuity validation: PASS; timestamp, close-time and OHLCV contracts passed.",
            "Point-in-time data availability: UNKNOWN; historic lot filters: UNKNOWN.",
            "Spread/slippage are modeled assumptions, not observed historic quotes.", "",
            "## Strategy results across costs", "",
            "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | Fees USDT | Friction USDT |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
        for window_name, _, _ in core.WINDOWS:
            for capital in core.CAPITALS:
                for cost_name in core.COSTS:
                    row = next(x for x in metrics if x["window"] == window_name and x["capital"] == core.ds(capital)
                               and x["cost"] == cost_name and x["method"] == STRATEGY)
                    lines.append(
                        f"| {window_name} | {int(capital)} | {cost_name} | {row['net_pnl_usdt']} | {row['return_pct']} | "
                        f"{row['max_drawdown_pct']} | {row['closed_roundtrips']} | {row['fees_usdt']} | {row['spread_slippage_usdt']} |"
                    )
        lines += ["", "## Frozen gate outcomes", "", "Passed: " + str(gates.get("passed", 0)) + "; failed: " + str(gates.get("failed", 0)), "",
                  "| Gate | Status | Details |", "|---|---|---|"]
        lines += [f"| {item['gate']} | {item['status']} | {item.get('details', '')} |" for item in gates["checks"]]
        lines += ["", "Workflow success means the program completed, not that the strategy is profitable.",
                  "This historical archive has been inspected previously; any historical pass would require prospective shadow validation.",
                  "No real orders, credentials, leverage or main changes. Operational decision: NO_TRADE."]
        (outdir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": "COMPLETED", "rows": len(rows), "dataset_sha256": manifest["canonical_dataset_sha256"],
            "strategy_gate": gates["overall"], "passed": gates.get("passed"), "failed": gates.get("failed"),
            "main_changed": False,
        }, sort_keys=True))
        return 0
    except Exception as exc:
        return _blocked(outdir, type(exc).__name__ + ":" + str(exc), manifest)

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="wick-rejection-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__ == "__main__":
    raise SystemExit(main())
