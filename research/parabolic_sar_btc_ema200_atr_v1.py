"""Frozen BTC Parabolic SAR reversal + EMA200 regime + ATR14 sizing replay."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_PSAR_AF002_MAX020_EMA200_ATR14"
PROTOCOL = "BTC_PARABOLIC_SAR_EMA200_ATR_V1"
AF_STEP, AF_MAX, EMA_PERIOD = Decimal("0.02"), Decimal("0.20"), 200
ZERO, ONE = Decimal("0"), Decimal("1")


def ema_series(values: Sequence[Decimal], period: int = EMA_PERIOD) -> list[Decimal | None]:
    if not values:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    complement = ONE - alpha
    current = values[0]
    out: list[Decimal | None] = [None] * len(values)
    for i, value in enumerate(values):
        if i:
            current = alpha * value + complement * current
        if i >= period - 1:
            out[i] = current
    return out


def psar_series(items: Sequence[Any]) -> dict[str, list[Decimal | bool | None]]:
    """Parabolic SAR with frozen AF step/max and causal reversal/clamp logic."""
    n = len(items)
    sar: list[Decimal | None] = [None] * n
    ep: list[Decimal | None] = [None] * n
    af: list[Decimal | None] = [None] * n
    state: list[bool | None] = [None] * n
    if n < 2:
        return {"sar": sar, "ep": ep, "af": af, "long": state}
    closes = [x.payload.close for x in items]
    highs = [x.payload.high for x in items]
    lows = [x.payload.low for x in items]
    initial_long = closes[1] >= closes[0]
    state[1] = initial_long
    sar[1] = lows[0] if initial_long else highs[0]
    ep[1] = max(highs[0], highs[1]) if initial_long else min(lows[0], lows[1])
    af[1] = AF_STEP
    for i in range(2, n):
        prior_state = state[i-1]
        prior_sar, prior_ep, prior_af = sar[i-1], ep[i-1], af[i-1]
        if prior_state is None or prior_sar is None or prior_ep is None or prior_af is None:
            continue
        candidate = prior_sar + prior_af * (prior_ep - prior_sar)
        if prior_state:
            candidate = min(candidate, lows[i-1], lows[i-2])
            if lows[i] < candidate:
                state[i] = False
                sar[i] = prior_ep
                ep[i] = lows[i]
                af[i] = AF_STEP
            else:
                state[i] = True
                sar[i] = candidate
                if highs[i] > prior_ep:
                    ep[i] = highs[i]
                    af[i] = min(prior_af + AF_STEP, AF_MAX)
                else:
                    ep[i], af[i] = prior_ep, prior_af
        else:
            candidate = max(candidate, highs[i-1], highs[i-2])
            if highs[i] > candidate:
                state[i] = True
                sar[i] = prior_ep
                ep[i] = highs[i]
                af[i] = AF_STEP
            else:
                state[i] = False
                sar[i] = candidate
                if lows[i] < prior_ep:
                    ep[i] = lows[i]
                    af[i] = min(prior_af + AF_STEP, AF_MAX)
                else:
                    ep[i], af[i] = prior_ep, prior_af
    return {"sar": sar, "ep": ep, "af": af, "long": state}


def entry_condition_at(closes: Sequence[Decimal], long_state: Sequence[bool | None],
                       ema200: Sequence[Decimal | None], i: int) -> bool:
    if i <= 0 or i >= len(closes) or i >= len(long_state) or i >= len(ema200):
        return False
    if long_state[i-1] is None or long_state[i] is None or ema200[i] is None:
        return False
    return long_state[i-1] is False and long_state[i] is True and closes[i] > ema200[i]


def exit_condition_at(closes: Sequence[Decimal], long_state: Sequence[bool | None],
                      ema200: Sequence[Decimal | None], i: int) -> bool:
    if i < 0 or i >= len(closes) or i >= len(long_state) or i >= len(ema200):
        return False
    if long_state[i] is None or ema200[i] is None:
        return False
    sar_bearish_flip = i > 0 and long_state[i-1] is True and long_state[i] is False
    return sar_bearish_flip or closes[i] < ema200[i]


def signals_and_weights(items: Sequence[Any]):
    closes = [x.payload.close for x in items]
    highs = [x.payload.high for x in items]
    lows = [x.payload.low for x in items]
    n = len(items)
    ema200 = ema_series(closes, EMA_PERIOD)
    sar = psar_series(items)
    tr: list[Decimal] = []
    for i in range(n):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr14: list[Decimal | None] = [None] * n
    for i in range(13, n):
        atr14[i] = sum(tr[i-13:i+1], ZERO) / Decimal(14)
    entries, exits = [False]*n, [False]*n
    weights: list[Decimal | None] = [None]*n
    annual_target, max_weight, annualization = Decimal("0.20"), Decimal("0.95"), Decimal(365).sqrt()
    for i in range(n):
        if i >= EMA_PERIOD - 1:
            entries[i] = entry_condition_at(closes, sar["long"], ema200, i)
            exits[i] = exit_condition_at(closes, sar["long"], ema200, i)
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denominator = (atr14[i] / closes[i]) * annualization
            if denominator > ZERO:
                weights[i] = min(max_weight, annual_target / denominator)
    return entries, exits, weights, atr14, ema200, sar


def frozen_gate(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    lookup = {(r["window"], r["capital_initial_usdt"]): r for r in rows}
    checks = []
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    annual = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    dd_windows = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", *annual)
    def add(name, passed, detail):
        checks.append({"gate": name, "status": "PASS" if passed else "FAIL", "details": detail})
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
                "missing result" if row is None else f"max DD={row['max_drawdown_pct']}%")
        row22 = lookup.get(("2022", cap))
        add(f"2022_nonnegative_{cap}", row22 is not None and Decimal(row22["net_pnl_usdt"]) >= ZERO,
            "missing result" if row22 is None else f"PnL={row22['net_pnl_usdt']} USDT")
        positives = sum(lookup.get((window, cap)) is not None and Decimal(lookup[(window, cap)]["net_pnl_usdt"]) > ZERO for window in annual)
        add(f"four_positive_annual_windows_{cap}", positives >= 4, f"positive windows={positives}/7")
        recent = lookup.get(("RECENT_2023_2026", cap))
        add(f"recent_beats_cash_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")
    failed = sum(x["status"] != "PASS" for x in checks)
    return {"overall":"PASS" if failed == 0 else "FAIL","passed":len(checks)-failed,"failed":failed,"checks":checks,
            "candidate_origin":"Parabolic SAR rule frozen in Issue #171; ATR14 sizing reused unchanged from Issue #143.",
            "promotion_note":"Historical archive previously inspected; any historical pass still requires prospective shadow validation. NO_TRADE."}


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, ema200, sar = signals_and_weights(items)
    indicator_rows = []
    for i, item in enumerate(items):
        indicator_rows.append({
            "index": i, "timestamp_utc": item.payload.start.isoformat(), "close": str(item.payload.close),
            "sar": None if sar["sar"][i] is None else str(sar["sar"][i]),
            "extreme_point": None if sar["ep"][i] is None else str(sar["ep"][i]),
            "acceleration_factor": None if sar["af"][i] is None else str(sar["af"][i]),
            "long_state": sar["long"][i],
            "ema200": None if ema200[i] is None else str(ema200[i]),
            "atr14_simple_mean": None if atr14[i] is None else str(atr14[i]),
            "risk_weight": None if weights[i] is None else str(weights[i]),
            "entry_flip": entries[i], "exit_condition": exits[i],
        })
    indicator_sha = hashlib.sha256(json.dumps(indicator_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    evidence = {
        "protocol": PROTOCOL, "preregistration_issue": 171, "candidate": CANDIDATE,
        "symbol": engine.SYMBOL, "interval": engine.INTERVAL,
        "evaluation_start": engine.FULL_START.isoformat(), "evaluation_end_exclusive": engine.END.isoformat(),
        "warmup_start": engine.WARMUP_START.isoformat(), "warmup_bars": engine.WARMUP_BARS,
        "expected_bars": engine.EXPECTED_BARS, "observed_bars": len(items),
        "dataset_sha256": data_sha, "indicator_series_sha256": indicator_sha,
        "entry_signal_count": sum(entries), "exit_condition_count": sum(exits),
        "sar_ready_count": sum(v is not None for v in sar["sar"]),
        "long_state_count": sum(v is True for v in sar["long"]),
        "short_state_count": sum(v is False for v in sar["long"]),
        "market_data_completeness_unknown_count": unknown_comp, "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {"step_size":str(rules["step"]),"min_qty":str(rules["min_qty"]),
            "min_notional":str(rules["min_notional"]),"snapshot_sha256":rules["sha256"],
            "scope":"current public filter only; historical filters not established"},
        "frozen_rule": {
            "acceleration_step":str(AF_STEP),"acceleration_max":str(AF_MAX),
            "initial_state":"close[1]>=close[0]; initial SAR/EP/AF as Issue #171 protocol",
            "long_update":"candidate SAR, clamp to min previous two lows, reverse short if low[t]<candidate",
            "short_update":"candidate SAR, clamp to max previous two highs, reverse long if high[t]>candidate",
            "entry":"short-to-long SAR state transition and close>EMA200",
            "exit":"long-to-short SAR state transition or close<EMA200",
            "execution":"decision after completed daily close; next daily open; terminal close liquidation",
            "atr14_sizing":"simple 14-bar mean TR; min(95%,20%/((ATR14/close)*sqrt(365))) frozen at entry",
            "max_entries_rolling_30d":engine.MAX_ENTRIES_30D},
        "cost_scenarios":[{"name":name,"fee_per_fill":str(fee),"assumed_full_spread_bps":str(spread),
            "assumed_slippage_bps_per_fill":str(slip)} for name,fee,spread,slip in engine.SCENARIOS],
        "retrospective_selection_warning":True,"data":records,"indicator_rows":indicator_rows}
    (output/"dataset-evidence.json").write_text(json.dumps(evidence,sort_keys=True,indent=2),encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for window,start,end in engine.WINDOWS:
      for capital in engine.CAPITALS:
       for cost,fee,spread,slip in engine.SCENARIOS:
        result,fills,curve=engine._simulate(items,rules,entries,exits,weights,atr14,ema200,window,start,end,capital,cost,fee,spread,slip)
        for fill in fills:
          if fill.get("side") in ("BUY","REJECTED_BUY"): fill["reason"]="SAR flips short-to-long and close>EMA200; ATR weight frozen at signal close"
          elif fill.get("side")=="SELL": fill["reason"]="SAR flips long-to-short or close<EMA200; next-open or terminal close"
        bh,bh_fees=engine._buy_hold(items,rules,start,end,capital,fee,spread,slip)
        result.update({"btc_buy_hold_final_equity_usdt":str(bh),"btc_buy_hold_net_pnl_usdt":str(bh-capital),
          "btc_buy_hold_fees_usdt":str(bh_fees),"cash_final_equity_usdt":str(capital),"dataset_sha256":data_sha})
        results.append(result)
        all_fills.extend({"capital_initial_usdt":str(capital),"cost_scenario":cost,**f} for f in fills)
        all_curves.extend({"capital_initial_usdt":str(capital),"cost_scenario":cost,**q} for q in curve)
    for name,rows in (("summary.csv",results),("fills.csv",all_fills),("equity.csv",all_curves)):
      with (output/name).open("w",newline="",encoding="utf-8") as h:
       if rows:
        writer=csv.DictWriter(h,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
       else: h.write("\n")
    gates=frozen_gate(results)
    evidence["strategy_gate"]=gates["overall"]
    evidence["output_counts"]={"summary_rows":len(results),"fill_rows":len(all_fills),"equity_rows":len(all_curves)}
    (output/"dataset-evidence.json").write_text(json.dumps(evidence,sort_keys=True,indent=2),encoding="utf-8")
    (output/"gate_results.json").write_text(json.dumps(gates,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    report=["# BTC Parabolic SAR reversal + EMA200 regime + ATR14 sizing","",
       "Frozen economic gate: "+gates["overall"],"",
       f"Daily candles={len(items)}; dataset SHA-256={data_sha}; indicator SHA-256={indicator_sha}.",
       f"SAR ready={evidence['sar_ready_count']}; bullish bars={evidence['long_state_count']}; bearish bars={evidence['short_state_count']}.",
       f"Entry flips={sum(entries)}; exit-condition bars={sum(exits)}.",
       f"Market completeness UNKNOWN={unknown_comp}; candle finality UNKNOWN={unknown_final}.",
       "Historical filters, point-in-time availability and executable historic quotes remain UNKNOWN.",
       "Spread/slippage are model assumptions, not observed historical quotes.","",
       "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | BTC B&H PnL | Fees USDT |",
       "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
    for r in results:
      report.append(f"| {r['window']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | {Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(r['total_fees_usdt']):.4f} |")
    report.extend(["",f"Passed: {gates['passed']}; failed: {gates['failed']}.","","| Gate | Status | Details |","|---|---|---|"])
    report.extend([f"| {x['gate']} | {x['status']} | {x['details']} |" for x in gates["checks"]])
    report.extend(["","Historical archive and related trend ideas were inspected before; no pristine holdout claim.",
       "Any historical pass still requires prospective shadow validation. No live orders, leverage, private endpoints or main changes. NO_TRADE.",""])
    (output/"report.md").write_text("\n".join(report),encoding="utf-8")
    print(json.dumps({"status":"COMPLETED","rows":len(items),"dataset_sha256":data_sha,
       "entries":sum(entries),"exits":sum(exits),"gate":gates["overall"],"passed":gates["passed"],
       "failed":gates["failed"],"main_changed":False},sort_keys=True))
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",default="parabolic-sar-ema200-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__=="__main__":
    raise SystemExit(main())
