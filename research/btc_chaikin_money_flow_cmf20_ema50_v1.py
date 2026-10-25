"""Frozen BTC Chaikin Money Flow accumulation crossover replay; research only."""
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

STRATEGY = "BTC_CMF20_EMA50_ACCUMULATION_CROSS"
PROTOCOL = "BTC_CHAIKIN_MONEY_FLOW_CMF20_EMA50_V1"
CMF_PERIOD = 20
EMA_PERIOD = 50
ENTRY_ALLOC = Decimal("0.20")
TAKE_PROFIT = Decimal("1.04")
STOP_LOSS = Decimal("0.97")
MAX_HOLD = 10
ENTRY_SIGNALS: list[bool] = []

def ema_series(rows: Sequence[dict[str, Any]], period: int = EMA_PERIOD) -> list[Decimal]:
    """Recursive EMA seeded from the first close; no future candles are used."""
    if not rows:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    complement = Decimal(1) - alpha
    values = [core.D(rows[0]["close"])]
    for row in rows[1:]:
        values.append(alpha * core.D(row["close"]) + complement * values[-1])
    return values

def money_flow_multiplier(row: dict[str, Any]) -> Decimal:
    high, low, close = (core.D(row[k]) for k in ("high", "low", "close"))
    candle_range = high - low
    if candle_range == 0:
        return Decimal(0)
    return ((close - low) - (high - close)) / candle_range

def cmf_at(rows: Sequence[dict[str, Any]], i: int, period: int = CMF_PERIOD) -> Decimal | None:
    """CMF over completed candles [i-period+1, i], inclusive of i and no future data."""
    if period <= 0 or i < period - 1 or i >= len(rows):
        return None
    mfv = Decimal(0)
    total_volume = Decimal(0)
    for row in rows[i-period+1:i+1]:
        volume = core.D(row["volume"])
        if volume < 0:
            raise ValueError("NEGATIVE_VOLUME")
        mfv += money_flow_multiplier(row) * volume
        total_volume += volume
    if total_volume <= 0:
        return None
    return mfv / total_volume

def entry_signal_at(rows: Sequence[dict[str, Any]], i: int,
                    ema: Sequence[Decimal] | None = None) -> bool:
    if i < max(EMA_PERIOD - 1, CMF_PERIOD) or i >= len(rows):
        return False
    ema_values = ema if ema is not None else ema_series(rows)
    if len(ema_values) != len(rows):
        raise ValueError("EMA_LENGTH_MISMATCH")
    if core.D(rows[i]["close"]) <= ema_values[i]:
        return False
    previous = cmf_at(rows, i-1)
    current = cmf_at(rows, i)
    return previous is not None and current is not None and previous <= 0 and current > 0

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

def _tag_result(result: dict[str, Any], method: str) -> dict[str, Any]:
    result["method"] = method
    for fill in result["fills"]:
        fill["method"] = method
        if "qty_btc" in fill:
            fill["qty_asset"] = fill.pop("qty_btc")
    return result

def gate_results(metrics: Sequence[dict[str, str]], data_ok: bool) -> dict[str, Any]:
    if not data_ok:
        return {"overall": "BLOCKED", "passed": 0, "failed": 1,
                "checks": [{"gate": "DATA_INTEGRITY", "status": "BLOCKED"}]}
    aliased = [
        {**row, "method": "VOLUME_SHOCK_REVERSAL"} if row["method"] == STRATEGY else row
        for row in metrics
    ]
    return core.gate_results(aliased, True)

def _blocked(outdir: Path, error: str, manifest: dict[str, Any]) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    manifest["status"] = "BLOCKED"
    manifest["blocker"] = error
    (outdir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (outdir / "report.md").write_text(
        "# BTC Chaikin Money Flow CMF20/EMA50 V1\n\nStatus: BLOCKED. No economic result is valid.\n\n"
        f"Blocker: {error}\n\nNo candle was interpolated or silently removed. Operational decision: NO_TRADE.\n",
        encoding="utf-8")
    print("BLOCKED:" + error, file=sys.stderr)
    return 2

def run(outdir: Path) -> int:
    global ENTRY_SIGNALS
    original_symbol = core.SYMBOL
    outdir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "protocol": PROTOCOL, "preregistration_issue": 162, "symbol": "BTCUSDT", "interval": "1d",
        "start_utc": core.START.isoformat(), "end_exclusive_utc": core.END.isoformat(),
        "point_in_time_availability": "UNKNOWN", "historical_exchange_filter_equivalence": "UNKNOWN",
        "historical_spread_slippage": "MODELED_ASSUMPTIONS_ONLY", "main_changed": False,
        "operational_decision": "NO_TRADE",
        "reused_engine_origin_commit": "d2d26aa7604ba357fe3b4f8422afa64b97b080e0",
        "frozen_rule": {
            "cmf_period": CMF_PERIOD, "ema_period": EMA_PERIOD, "ema_alpha": "2/(50+1)",
            "cmf_formula": "sum(MFM*volume)/sum(volume), MFM=((close-low)-(high-close))/(high-low)",
            "zero_range_rule": "MFM=0", "signal": "CMF20[t-1]<=0 and CMF20[t]>0 and close[t]>EMA50[t]",
            "entry_fraction": str(ENTRY_ALLOC), "take_profit_multiple": str(TAKE_PROFIT),
            "stop_loss_multiple": str(STOP_LOSS), "max_holding_daily_candles": MAX_HOLD,
            "execution": "completed daily-close signal; next daily open fill; terminal close liquidation",
        },
        "costs": {k: {f: core.ds(v) for f, v in d.items()} for k,d in core.COSTS.items()},
    }
    try:
        core.SYMBOL = "BTCUSDT"
        rows, pages = core.fetch_history()
        quality = core.validate_candles(rows)
        manifest.update({
            "quality": quality, "row_count": len(rows), "raw_pages": pages,
            "canonical_dataset_sha256": core.canonical_data_hash(rows),
        })
        if not quality["ok"]:
            return _blocked(outdir, "DATA_INTEGRITY_GATE_FAILED", manifest)
        filters, evidence = core.fetch_filters()
        manifest["exchange_info"] = evidence
        manifest["filters_current"] = {k: core.ds(v) for k,v in filters.items()}
        manifest["first_open_utc"] = rows[0]["timestamp"]
        manifest["last_open_utc"] = rows[-1]["timestamp"]
        ema = ema_series(rows)
        cmf_values = [cmf_at(rows, i) for i in range(len(rows))]
        ENTRY_SIGNALS = [entry_signal_at(rows, i, ema) for i in range(len(rows))]
        manifest["signal_count_over_archive"] = sum(ENTRY_SIGNALS)
        manifest["cmf_non_missing_bars"] = sum(x is not None for x in cmf_values)
        manifest["ema_method"] = "recursive EMA50 seeded at first close"
        manifest["cmf_current_inclusive"] = True
        manifest["cmf_reference_window"] = "current plus prior 19 completed daily bars"
        manifest["indicator_data_sha256"] = core.sha(core.canon([
            {"timestamp": rows[i]["timestamp"], "cmf20": None if cmf_values[i] is None else core.ds(cmf_values[i]),
             "ema50": core.ds(ema[i]), "entry_signal": ENTRY_SIGNALS[i]}
            for i in range(len(rows))
        ]))
        with (outdir / "candles.csv").open("w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=["open_ms","timestamp","close_ms","open","high","low","close","volume"])
            w.writeheader()
            for row in rows:
                w.writerow({"open_ms":row["open_ms"],"timestamp":row["timestamp"],"close_ms":row["close_ms"],
                            **{k:core.ds(row[k]) for k in ("open","high","low","close","volume")}})

        index = {int(row["open_ms"]): i for i,row in enumerate(rows)}
        summary: list[dict[str, Any]] = []
        fill_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        for window,start,end in core.WINDOWS:
            if core.to_ms(start) not in index:
                raise core.DataIntegrityError("WINDOW_START_MISSING:"+window)
            a = index[core.to_ms(start)]
            b = index.get(core.to_ms(end), len(rows))
            if b <= a:
                raise core.DataIntegrityError("WINDOW_BOUNDARY_INVALID:"+window)
            for capital in core.CAPITALS:
                for cost_name in core.COSTS:
                    core.entry_signal = _entry_signal
                    core.ALLOC = ENTRY_ALLOC
                    core.TP = TAKE_PROFIT
                    core.SL = STOP_LOSS
                    core.MAX_HOLD = MAX_HOLD
                    strategy = core.simulate(rows,a,b,capital,cost_name,filters,window,"VOLUME_SHOCK_REVERSAL")
                    strategy = _tag_result(strategy,STRATEGY)
                    benchmark = core.simulate(rows,a,b,capital,cost_name,filters,window,"BTC_BUY_AND_HOLD")
                    benchmark = _tag_result(benchmark,"BTC_BUY_AND_HOLD")
                    cash = _cash_result(capital,rows,a,b)
                    for result in (strategy,benchmark,cash):
                        summary.append(core.metric_row(window,start,end,capital,cost_name,result))
                        fill_rows.extend(result["fills"])
                        equity_rows.extend({"window":window,"capital":core.ds(capital),"cost":cost_name,
                                            "method":result["method"],**point} for point in result["curve"])
        gates = gate_results(summary, True)
        manifest.update({
            "status": "COMPLETED", "strategy_gate": gates["overall"],
            "output_counts":{"summary_rows":len(summary),"fill_rows":len(fill_rows),"equity_rows":len(equity_rows)},
        })
        core.csv_write(outdir/"summary.csv",summary,[
            "window","start_utc","end_exclusive_utc","capital","cost","method","final_equity_usdt","net_pnl_usdt",
            "return_pct","max_drawdown_pct","closed_roundtrips","wins","losses","fees_usdt","spread_slippage_usdt","rejected_orders"])
        core.csv_write(outdir/"fills.csv",fill_rows,[
            "window","capital","cost","method","trade_id","side","reason","signal_utc","execution_utc","execution_type",
            "raw_price","fill_price","qty_asset","notional_usdt","fee_usdt","friction_usdt","cash_after"])
        core.csv_write(outdir/"equity.csv",equity_rows,[
            "window","capital","cost","method","timestamp_utc","phase","equity_usdt"])
        (outdir/"dataset_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding="utf-8")
        (outdir/"gate_results.json").write_text(json.dumps(gates,indent=2,sort_keys=True),encoding="utf-8")
        lines = [
            "# BTC Chaikin Money Flow CMF20/EMA50 V1", "", "Frozen economic gate: "+gates["overall"], "",
            "Dataset SHA-256: "+manifest["canonical_dataset_sha256"], "Candle count: "+str(len(rows)),
            "Continuity validation: PASS; exact daily timestamps and OHLCV contracts passed.",
            "Historical point-in-time availability: UNKNOWN; historical filters: UNKNOWN.",
            f"Signal count over archive: {manifest['signal_count_over_archive']}.",
            "Spread/slippage are modeled assumptions, not historic order-book observations.", "",
            "## Results at STRESS_2", "",
            "| Window | Capital | Net PnL USDT | Return % | Max DD % | Round trips | Fees | Friction |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for window,_,_ in core.WINDOWS:
            for capital in core.CAPITALS:
                row=next(x for x in summary if x["window"]==window and x["capital"]==core.ds(capital)
                         and x["cost"]=="STRESS_2" and x["method"]==STRATEGY)
                lines.append(f"| {window} | {int(capital)} | {row['net_pnl_usdt']} | {row['return_pct']} | {row['max_drawdown_pct']} | "
                             f"{row['closed_roundtrips']} | {row['fees_usdt']} | {row['spread_slippage_usdt']} |")
        lines += ["", "## Frozen gates", "", f"Passed: {gates['passed']}; failed: {gates['failed']}", "",
                  "| Gate | Status | Details |", "|---|---|---|"]
        lines += [f"| {g['gate']} | {g['status']} | {g.get('details','')} |" for g in gates["checks"]]
        lines += ["", "Workflow success certifies technical execution, not profitability.",
                  "The historical archive was inspected previously and is not a pristine holdout.",
                  "No private credentials, real orders, leverage or main-branch changes. Decision: NO_TRADE."]
        (outdir/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
        print(json.dumps({"status":"COMPLETED","rows":len(rows),"dataset_sha256":manifest["canonical_dataset_sha256"],
                          "signals":manifest["signal_count_over_archive"],"gate":gates["overall"],
                          "passed":gates["passed"],"failed":gates["failed"],"main_changed":False},sort_keys=True))
        return 0
    except Exception as exc:
        return _blocked(outdir,type(exc).__name__+":"+str(exc),manifest)
    finally:
        core.SYMBOL = original_symbol

def main(argv: Sequence[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",default="btc-cmf20-ema50-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__=="__main__":
    raise SystemExit(main())
