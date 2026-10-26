"""Frozen BTC RSI(2) oversold mean-reversion + EMA200 regime replay."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_RSI2_OVERSOLD_EMA200_BULL_MEAN_REVERSION"
PROTOCOL = "BTC_RSI2_EMA200_MEAN_REVERSION_V1"
RSI_PERIOD, EMA_PERIOD, SMA_PERIOD = 2, 200, 5
ENTRY_RSI_MAX, EXIT_RSI_MIN = Decimal("10"), Decimal("70")
ZERO, ONE, HUNDRED = Decimal("0"), Decimal("1"), Decimal("100")

def ema_series(values: Sequence[Decimal], period: int = EMA_PERIOD) -> list[Decimal | None]:
    if not values: return []
    alpha = Decimal(2) / Decimal(period + 1)
    complement = ONE - alpha
    current = values[0]
    result: list[Decimal | None] = [None] * len(values)
    for i, value in enumerate(values):
        if i: current = alpha * value + complement * current
        if i >= period - 1: result[i] = current
    return result

def sma_series(values: Sequence[Decimal], period: int = SMA_PERIOD) -> list[Decimal | None]:
    if period <= 0: raise ValueError("SMA_PERIOD_MUST_BE_POSITIVE")
    result: list[Decimal | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i-period+1:i+1], ZERO) / Decimal(period)
    return result

def rsi_wilder(values: Sequence[Decimal], period: int = RSI_PERIOD) -> list[Decimal | None]:
    if period <= 0: raise ValueError("RSI_PERIOD_MUST_BE_POSITIVE")
    n = len(values)
    result: list[Decimal | None] = [None] * n
    if n <= period: return result
    gains: list[Decimal] = [ZERO] * n
    losses: list[Decimal] = [ZERO] * n
    for i in range(1, n):
        delta = values[i] - values[i-1]
        gains[i], losses[i] = max(delta, ZERO), max(-delta, ZERO)
    avg_gain = sum(gains[1:period+1], ZERO) / Decimal(period)
    avg_loss = sum(losses[1:period+1], ZERO) / Decimal(period)
    def value(gain: Decimal, loss: Decimal) -> Decimal:
        if loss == ZERO and gain == ZERO: return Decimal("50")
        if loss == ZERO: return HUNDRED
        if gain == ZERO: return ZERO
        rs = gain / loss
        return HUNDRED - HUNDRED / (ONE + rs)
    result[period] = value(avg_gain, avg_loss)
    for i in range(period+1, n):
        avg_gain = (avg_gain * Decimal(period-1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period-1) + losses[i]) / Decimal(period)
        result[i] = value(avg_gain, avg_loss)
    return result

def entry_condition_at(closes, rsi2, ema200, i: int) -> bool:
    if i < 0 or i >= len(closes) or i >= len(rsi2) or i >= len(ema200): return False
    if rsi2[i] is None or ema200[i] is None: return False
    return rsi2[i] <= ENTRY_RSI_MAX and closes[i] > ema200[i]

def exit_condition_at(closes, rsi2, sma5, ema200, i: int) -> bool:
    if i < 0 or i >= len(closes) or any(i >= len(v) for v in (rsi2, sma5, ema200)): return False
    if rsi2[i] is None or sma5[i] is None or ema200[i] is None: return False
    return closes[i] > sma5[i] or rsi2[i] >= EXIT_RSI_MIN or closes[i] < ema200[i]

def signals_and_weights(items: Sequence[Any]):
    closes = [x.payload.close for x in items]
    highs = [x.payload.high for x in items]
    lows = [x.payload.low for x in items]
    n = len(items)
    rsi2, ema200, sma5 = rsi_wilder(closes, RSI_PERIOD), ema_series(closes, EMA_PERIOD), sma_series(closes, SMA_PERIOD)
    tr: list[Decimal] = []
    for i in range(n):
        if i == 0: tr.append(highs[i] - lows[i])
        else: tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr14: list[Decimal | None] = [None] * n
    for i in range(13, n): atr14[i] = sum(tr[i-13:i+1], ZERO) / Decimal(14)
    entries, exits = [False]*n, [False]*n
    weights: list[Decimal | None] = [None] * n
    annual_target, max_weight, annualization = Decimal("0.20"), Decimal("0.95"), Decimal(365).sqrt()
    for i in range(n):
        entries[i] = entry_condition_at(closes, rsi2, ema200, i)
        exits[i] = exit_condition_at(closes, rsi2, sma5, ema200, i)
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denom = (atr14[i] / closes[i]) * annualization
            if denom > ZERO: weights[i] = min(max_weight, annual_target / denom)
    return entries, exits, weights, atr14, ema200, rsi2, sma5

def frozen_gate(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    lookup = {(r["window"], r["capital_initial_usdt"]): r for r in rows}
    checks = []
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    annual = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    dd_windows = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", *annual)
    def add(name, passed, detail): checks.append({"gate":name,"status":"PASS" if passed else "FAIL","details":detail})
    for capital in engine.CAPITALS:
        cap = str(capital)
        for window in principal:
            row = lookup.get((window,cap))
            add(f"positive_pnl_{window}_{cap}", row is not None and Decimal(row["net_pnl_usdt"]) > ZERO,
                "missing result" if row is None else f"PnL={row['net_pnl_usdt']} USDT")
            add(f"five_roundtrips_{window}_{cap}", row is not None and int(row["completed_round_trips"]) >= 5,
                "missing result" if row is None else f"round trips={row['completed_round_trips']}")
        for window in dd_windows:
            row = lookup.get((window,cap))
            add(f"drawdown_limit_{window}_{cap}", row is not None and Decimal(row["max_drawdown_pct"]) >= Decimal("-15"),
                "missing result" if row is None else f"max DD={row['max_drawdown_pct']}%")
        row22 = lookup.get(("2022",cap))
        add(f"2022_nonnegative_{cap}", row22 is not None and Decimal(row22["net_pnl_usdt"]) >= ZERO,
            "missing result" if row22 is None else f"PnL={row22['net_pnl_usdt']} USDT")
        positives = sum(lookup.get((w,cap)) is not None and Decimal(lookup[(w,cap)]["net_pnl_usdt"]) > ZERO for w in annual)
        add(f"four_positive_annual_windows_{cap}", positives >= 4, f"positive windows={positives}/7")
        recent = lookup.get(("RECENT_2023_2026",cap))
        add(f"recent_beats_cash_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")
    failed = sum(c["status"] != "PASS" for c in checks)
    return {"overall":"PASS" if failed == 0 else "FAIL","passed":len(checks)-failed,"failed":failed,"checks":checks,
            "candidate_origin":"RSI2 mean-reversion hypothesis preregistered in Issue #172; ATR sizing unchanged.",
            "promotion_note":"Historical archive previously inspected; any pass still needs prospective shadow validation. NO_TRADE."}

def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, ema200, rsi2, sma5 = signals_and_weights(items)
    indicators = [{"index":i,"timestamp_utc":x.payload.start.isoformat(),"close":str(x.payload.close),
                   "rsi2_wilder":None if rsi2[i] is None else str(rsi2[i]),
                   "ema200":None if ema200[i] is None else str(ema200[i]),
                   "sma5":None if sma5[i] is None else str(sma5[i]),
                   "atr14_simple_mean":None if atr14[i] is None else str(atr14[i]),
                   "risk_weight":None if weights[i] is None else str(weights[i]),
                   "entry_signal":entries[i],"exit_condition":exits[i]} for i,x in enumerate(items)]
    indicator_sha = hashlib.sha256(json.dumps(indicators,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    evidence = {"protocol":PROTOCOL,"preregistration_issue":172,"candidate":CANDIDATE,"symbol":engine.SYMBOL,"interval":engine.INTERVAL,
      "evaluation_start":engine.FULL_START.isoformat(),"evaluation_end_exclusive":engine.END.isoformat(),
      "warmup_start":engine.WARMUP_START.isoformat(),"warmup_bars":engine.WARMUP_BARS,
      "expected_bars":engine.EXPECTED_BARS,"observed_bars":len(items),"dataset_sha256":data_sha,
      "indicator_series_sha256":indicator_sha,"entry_signal_count":sum(entries),"exit_condition_count":sum(exits),
      "rsi2_available_count":sum(v is not None for v in rsi2),"sma5_available_count":sum(v is not None for v in sma5),
      "ema200_available_count":sum(v is not None for v in ema200),"market_data_completeness_unknown_count":unknown_comp,
      "candle_finality_unknown_count":unknown_final,
      "current_exchange_filters":{"step_size":str(rules["step"]),"min_qty":str(rules["min_qty"]),
         "min_notional":str(rules["min_notional"]),"snapshot_sha256":rules["sha256"],"scope":"current public filters only; historical filters unknown"},
      "frozen_rule":{"rsi_period":RSI_PERIOD,"rsi_smoothing":"Wilder smoothing; seed first two changes",
         "entry":"RSI2<=10 and close>EMA200","exit":"close>SMA5 or RSI2>=70 or close<EMA200",
         "execution":"signal at completed close, fill next daily open; terminal close liquidation",
         "atr14_sizing":"simple 14-bar mean TR; min(95%,20%/((ATR14/close)*sqrt(365))) frozen at entry",
         "max_entries_rolling_30d":engine.MAX_ENTRIES_30D},
      "cost_scenarios":[{"name":name,"fee_per_fill":str(fee),"assumed_full_spread_bps":str(spread),
          "assumed_slippage_bps_per_fill":str(slip)} for name,fee,spread,slip in engine.SCENARIOS],
      "retrospective_selection_warning":True,"data":records,"indicator_rows":indicators}
    (output/"dataset-evidence.json").write_text(json.dumps(evidence,sort_keys=True,indent=2),encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for window,start,end in engine.WINDOWS:
      for capital in engine.CAPITALS:
       for cost,fee,spread,slip in engine.SCENARIOS:
        result,fills,curve=engine._simulate(items,rules,entries,exits,weights,atr14,ema200,window,start,end,capital,cost,fee,spread,slip)
        for fill in fills:
          if fill.get("side") in ("BUY","REJECTED_BUY"): fill["reason"]="RSI2<=10 with close>EMA200; ATR risk weight frozen at signal close"
          elif fill.get("side")=="SELL": fill["reason"]="close>SMA5 or RSI2>=70 or close<EMA200; next-open or terminal close"
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
    report=["# BTC RSI(2) oversold mean-reversion + EMA200 regime","",
      "Frozen economic gate: "+gates["overall"],"",
      f"Daily candles={len(items)}; dataset SHA-256={data_sha}; indicator SHA-256={indicator_sha}.",
      f"Entry signals={sum(entries)}; exit-condition bars={sum(exits)}.",
      f"RSI2 ready={evidence['rsi2_available_count']}; SMA5 ready={evidence['sma5_available_count']}; EMA200 ready={evidence['ema200_available_count']}.",
      f"Market completeness UNKNOWN={unknown_comp}; candle finality UNKNOWN={unknown_final}.",
      "Historical filters, point-in-time availability and executable historic quotes remain UNKNOWN.",
      "Spread/slippage are assumptions, not observed historical quotes.","",
      "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | BTC B&H PnL | Fees USDT |",
      "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
    for r in results:
      report.append(f"| {r['window']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | {Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | {Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(r['total_fees_usdt']):.4f} |")
    report.extend(["",f"Passed: {gates['passed']}; failed: {gates['failed']}.","","| Gate | Status | Details |","|---|---|---|"])
    report.extend([f"| {x['gate']} | {x['status']} | {x['details']} |" for x in gates["checks"]])
    report.extend(["","Historical archive previously inspected; not a pristine holdout. Any historic pass still requires prospective shadow validation.",
      "No real orders, leverage, private endpoints or main changes. NO_TRADE.",""])
    (output/"report.md").write_text("\n".join(report),encoding="utf-8")
    print(json.dumps({"status":"COMPLETED","rows":len(items),"dataset_sha256":data_sha,"entries":sum(entries),
      "exits":sum(exits),"gate":gates["overall"],"passed":gates["passed"],"failed":gates["failed"],"main_changed":False},sort_keys=True))
    return 0

def main(argv: Sequence[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",default="rsi2-ema200-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__=="__main__":
    raise SystemExit(main())
