"""Frozen BTC bullish-regime pullback and EMA20 reclaim replay; research only."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_BULL_PULLBACK_EMA20_RECLAIM_EMA50_EXIT_ATR14"
PROTOCOL = "BTC_PULLBACK_EMA20_RECLAIM_ATR_V1"
FAST_PERIOD, MEDIUM_PERIOD, SLOW_PERIOD = 20, 50, 200
PULLBACK_MIN = Decimal("-0.12")
PULLBACK_MAX = Decimal("-0.04")
HIGH_LOOKBACK = 30
ZERO, ONE = Decimal("0"), Decimal("1")

def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    if not values:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    complement = ONE - alpha
    current = values[0]
    output: list[Decimal | None] = [None] * len(values)
    for i, value in enumerate(values):
        if i:
            current = alpha * value + complement * current
        if i >= period - 1:
            output[i] = current
    return output

def pullback_fraction_at(closes: Sequence[Decimal], highs: Sequence[Decimal], i: int,
                         lookback: int = HIGH_LOOKBACK) -> Decimal | None:
    """Pullback from prior high channel, explicitly excluding the current candle."""
    if len(closes) != len(highs) or i < lookback or i >= len(closes):
        return None
    previous_high = max(highs[i-lookback:i])
    if previous_high <= ZERO:
        return None
    return closes[i] / previous_high - ONE

def entry_condition_at(closes: Sequence[Decimal], highs: Sequence[Decimal],
                       ema20: Sequence[Decimal | None], ema50: Sequence[Decimal | None],
                       ema200: Sequence[Decimal | None], i: int) -> bool:
    if i < max(SLOW_PERIOD-1, HIGH_LOOKBACK, 1) or i >= len(closes):
        return False
    if any(i >= len(values) for values in (highs, ema20, ema50, ema200)):
        return False
    prev20, current20 = ema20[i-1], ema20[i]
    mid50, long200 = ema50[i], ema200[i]
    if prev20 is None or current20 is None or mid50 is None or long200 is None:
        return False
    pullback = pullback_fraction_at(closes, highs, i)
    if pullback is None or not (PULLBACK_MIN <= pullback <= PULLBACK_MAX):
        return False
    return (
        closes[i-1] <= prev20
        and closes[i] > current20
        and closes[i] > mid50
        and closes[i] > long200
    )

def exit_condition_at(closes: Sequence[Decimal], ema50: Sequence[Decimal | None],
                      ema200: Sequence[Decimal | None], i: int) -> bool:
    if i >= len(closes) or i >= len(ema50) or i >= len(ema200) or i < 0:
        return False
    if ema50[i] is None or ema200[i] is None:
        return False
    return closes[i] < ema50[i] or closes[i] < ema200[i]

def pullback_signals_and_weights(items: Sequence[Any]):
    closes = [row.payload.close for row in items]
    highs = [row.payload.high for row in items]
    lows = [row.payload.low for row in items]
    count = len(items)
    ema20 = ema_series(closes, FAST_PERIOD)
    ema50 = ema_series(closes, MEDIUM_PERIOD)
    ema200 = ema_series(closes, SLOW_PERIOD)

    true_ranges: list[Decimal] = []
    for i in range(count):
        tr = highs[i]-lows[i] if i == 0 else max(
            highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])
        )
        true_ranges.append(tr)
    atr14: list[Decimal | None] = [None] * count
    for i in range(13, count):
        atr14[i] = sum(true_ranges[i-13:i+1], ZERO) / Decimal(14)

    entries = [False] * count
    exits = [False] * count
    weights: list[Decimal | None] = [None] * count
    pullbacks: list[Decimal | None] = [None] * count
    annual_target = Decimal("0.20")
    max_weight = Decimal("0.95")
    annualization = Decimal(365).sqrt()

    for i in range(count):
        pullbacks[i] = pullback_fraction_at(closes, highs, i)
        entries[i] = entry_condition_at(closes, highs, ema20, ema50, ema200, i)
        exits[i] = exit_condition_at(closes, ema50, ema200, i)
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denom = (atr14[i] / closes[i]) * annualization
            if denom > ZERO:
                weights[i] = min(max_weight, annual_target / denom)
    return entries, exits, weights, atr14, ema20, ema50, ema200, pullbacks

def frozen_gate(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    stress = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    lookup = {(r["window"], r["capital_initial_usdt"]): r for r in stress}
    checks: list[dict[str, str]] = []
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    annual = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    dd_windows = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", *annual)
    def add(name: str, passed: bool, details: str) -> None:
        checks.append({"gate": name, "status": "PASS" if passed else "FAIL", "details": details})
    for capital in engine.CAPITALS:
        cap = str(capital)
        for window in principal:
            row = lookup.get((window, cap))
            add(f"positive_pnl_{window}_{cap}", row is not None and Decimal(row["net_pnl_usdt"]) > ZERO,
                "missing result" if row is None else f"PnL={row['net_pnl_usdt']} USDT")
            add(f"five_roundtrips_{window}_{cap}", row is not None and int(row["completed_round_trips"]) >= 5,
                "missing result" if row is None else f"round trips={row['completed_round_trips']}")
        for window in dd_windows:
            row = lookup.get((window, cap))
            add(f"drawdown_limit_{window}_{cap}", row is not None and Decimal(row["max_drawdown_pct"]) >= Decimal("-15"),
                "missing result" if row is None else f"max drawdown={row['max_drawdown_pct']}%")
        row22 = lookup.get(("2022", cap))
        add(f"2022_nonnegative_{cap}", row22 is not None and Decimal(row22["net_pnl_usdt"]) >= ZERO,
            "missing result" if row22 is None else f"PnL={row22['net_pnl_usdt']} USDT")
        positive = sum(lookup.get((window, cap)) is not None and Decimal(lookup[(window, cap)]["net_pnl_usdt"]) > ZERO for window in annual)
        add(f"four_positive_annual_windows_{cap}", positive >= 4, f"positive windows={positive}/7")
        recent = lookup.get(("RECENT_2023_2026", cap))
        add(f"recent_beats_cash_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")
    failed = sum(x["status"] != "PASS" for x in checks)
    return {
        "overall": "PASS" if failed == 0 else "FAIL",
        "passed": len(checks)-failed,
        "failed": failed,
        "checks": checks,
        "candidate_origin": "Bullish-regime pullback reclaim preregistered in Issue #167; ATR sizing reuses Issue #143 formula unchanged.",
        "promotion_note": "Previously inspected archive, not a pristine holdout; prospective shadow testing would still be required. NO_TRADE.",
    }

def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, ema20, ema50, ema200, pullbacks = pullback_signals_and_weights(items)
    indicator_rows = []
    for i, item in enumerate(items):
        indicator_rows.append({
            "index": i,
            "timestamp_utc": item.payload.start.isoformat(),
            "close": str(item.payload.close),
            "ema20": None if ema20[i] is None else str(ema20[i]),
            "ema50": None if ema50[i] is None else str(ema50[i]),
            "ema200": None if ema200[i] is None else str(ema200[i]),
            "prior_high_30": None if i < HIGH_LOOKBACK else str(max(x.payload.high for x in items[i-HIGH_LOOKBACK:i])),
            "pullback_fraction": None if pullbacks[i] is None else str(pullbacks[i]),
            "atr14": None if atr14[i] is None else str(atr14[i]),
            "risk_weight": None if weights[i] is None else str(weights[i]),
            "entry_reclaim_signal": entries[i],
            "exit_signal": exits[i],
        })
    indicator_sha = hashlib.sha256(json.dumps(indicator_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    evidence = {
        "protocol": PROTOCOL,
        "preregistration_issue": 167,
        "candidate": CANDIDATE,
        "symbol": engine.SYMBOL,
        "interval": engine.INTERVAL,
        "evaluation_start": engine.FULL_START.isoformat(),
        "evaluation_end_exclusive": engine.END.isoformat(),
        "warmup_start": engine.WARMUP_START.isoformat(),
        "warmup_bars": engine.WARMUP_BARS,
        "expected_bars": engine.EXPECTED_BARS,
        "observed_bars": len(items),
        "dataset_sha256": data_sha,
        "indicator_series_sha256": indicator_sha,
        "entry_signal_count": sum(entries),
        "exit_signal_count": sum(exits),
        "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot_sha256": rules["sha256"],
            "scope": "current public filter only; historical filters not established",
        },
        "frozen_rule": {
            "ema_periods": [FAST_PERIOD, MEDIUM_PERIOD, SLOW_PERIOD],
            "prior_high_lookback": HIGH_LOOKBACK,
            "prior_high_excludes_current": True,
            "pullback_fraction_bounds": [str(PULLBACK_MIN), str(PULLBACK_MAX)],
            "entry": "close crosses above EMA20; close > EMA50 and EMA200; prior-high drawdown within -12% to -4%",
            "exit": "close < EMA50 or close < EMA200",
            "execution": "signal after completed daily close; execute next daily open; terminal close liquidation",
            "atr14": "simple mean of 14 true ranges",
            "risk_sizing": "min(95%, 20%/((ATR14/close)*sqrt(365))); frozen at entry, no intratrade rebalance",
            "max_entries_rolling_30d": engine.MAX_ENTRIES_30D,
        },
        "cost_scenarios": [
            {"name": name, "fee_per_fill": str(fee), "assumed_full_spread_bps": str(spread),
             "assumed_slippage_bps_per_fill": str(slippage)}
            for name, fee, spread, slippage in engine.SCENARIOS
        ],
        "retrospective_selection_warning": True,
        "data": records,
        "indicator_rows": indicator_rows,
    }
    (output/"dataset-evidence.json").write_text(json.dumps(evidence,sort_keys=True,indent=2),encoding="utf-8")

    results, all_fills, all_curves = [], [], []
    for window, start, end in engine.WINDOWS:
        for capital in engine.CAPITALS:
            for cost, fee, spread, slippage in engine.SCENARIOS:
                result, fills, curve = engine._simulate(
                    items, rules, entries, exits, weights, atr14, ema200,
                    window, start, end, capital, cost, fee, spread, slippage
                )
                for fill in fills:
                    if fill.get("side") in ("BUY","REJECTED_BUY"):
                        fill["reason"] = "Pullback 4-12% below prior 30-day high plus EMA20 reclaim in EMA50/EMA200 regime; ATR14 sizing frozen at signal close"
                    elif fill.get("side") == "SELL":
                        fill["reason"] = "Close below EMA50 or EMA200; next-open fill"
                bnh, bnh_fees = engine._buy_hold(items,rules,start,end,capital,fee,spread,slippage)
                result.update({
                    "btc_buy_hold_final_equity_usdt": str(bnh),
                    "btc_buy_hold_net_pnl_usdt": str(bnh-capital),
                    "btc_buy_hold_fees_usdt": str(bnh_fees),
                    "cash_final_equity_usdt": str(capital),
                    "dataset_sha256": data_sha,
                })
                results.append(result)
                all_fills.extend({"capital_initial_usdt":str(capital),"cost_scenario":cost,**fill} for fill in fills)
                all_curves.extend({"capital_initial_usdt":str(capital),"cost_scenario":cost,**point} for point in curve)

    for filename, rows in (("summary.csv",results),("fills.csv",all_fills),("equity.csv",all_curves)):
        with (output/filename).open("w",newline="",encoding="utf-8") as handle:
            if rows:
                writer=csv.DictWriter(handle,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            else:
                handle.write("\n")
    gates=frozen_gate(results)
    evidence["strategy_gate"]=gates["overall"]
    evidence["output_counts"]={"summary_rows":len(results),"fill_rows":len(all_fills),"equity_rows":len(all_curves)}
    (output/"dataset-evidence.json").write_text(json.dumps(evidence,sort_keys=True,indent=2),encoding="utf-8")
    (output/"gate_results.json").write_text(json.dumps(gates,sort_keys=True,indent=2)+"\n",encoding="utf-8")

    report=[
        "# BTC bullish-regime pullback EMA20 reclaim + EMA50 exit + ATR14",
        "", "Frozen economic gate: "+gates["overall"], "",
        f"Daily candles={len(items)}; dataset SHA-256={data_sha}; indicator SHA-256={indicator_sha}.",
        f"Entry signals={sum(entries)}; exit-condition bars={sum(exits)}.",
        f"Market completeness UNKNOWN={unknown_comp}; candle finality UNKNOWN={unknown_final}.",
        "Historical filters, point-in-time availability and executable historic quotes remain UNKNOWN.",
        "Spread/slippage are assumptions, not observed historical order-book quotes.", "",
        "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | BTC B&H PnL | Fees USDT |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        report.append(f"| {row['window']} | {row['capital_initial_usdt']} | {row['cost_scenario']} | {Decimal(row['net_pnl_usdt']):+.4f} | {Decimal(row['return_pct']):+.2f}% | {Decimal(row['max_drawdown_pct']):.2f}% | {row['completed_round_trips']} | {Decimal(row['btc_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(row['total_fees_usdt']):.4f} |")
    report.extend(["",f"Passed: {gates['passed']}; failed: {gates['failed']}.","",
                   "| Gate | Status | Details |","|---|---|---|"])
    report.extend([f"| {g['gate']} | {g['status']} | {g['details']} |" for g in gates["checks"]])
    report.extend(["","Archive was inspected previously and is not a pristine holdout. A historic pass still needs a prospective shadow test.","No live orders, private endpoints, leverage or main changes. NO_TRADE.",""])
    (output/"report.md").write_text("\n".join(report),encoding="utf-8")
    print(json.dumps({"status":"COMPLETED","rows":len(items),"dataset_sha256":data_sha,
                      "entries":sum(entries),"exits":sum(exits),"gate":gates["overall"],
                      "passed":gates["passed"],"failed":gates["failed"],"main_changed":False},sort_keys=True))
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",default="pullback-reclaim-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__=="__main__":
    raise SystemExit(main())
