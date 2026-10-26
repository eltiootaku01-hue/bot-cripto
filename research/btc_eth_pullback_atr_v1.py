from __future__ import annotations
import argparse, csv, hashlib, json, math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from research import trend_mode_practice_v1 as shared

STRATEGY = "BTC_ETH_4H_PULLBACK_CONTINUATION_ATR_V1"
DATA_SHA = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
CONFIG_SHA = "2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a"
RAW_SHA = "3851a560aafbaa44f7d57081bd12f7dfdae070c2abff3d65e122ad8fcbd6df36"
CAPITALS = (60.0, 100.0)
MAX_WEIGHT = 0.50
LOT_STEP, MIN_NOTIONAL = 0.00001, 5.0
COSTS = {
    "BASE": {"fee_rate": .001, "spread_bps": 10.0, "slippage_bps": 5.0},
    "STRESS_1": {"fee_rate": .001, "spread_bps": 25.0, "slippage_bps": 10.0},
    "STRESS_2": {"fee_rate": .002, "spread_bps": 50.0, "slippage_bps": 20.0},
}
WINDOWS = {
    "COMBINED_2025_2026_YTD": (pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2026-10-22T00:00:00Z")),
    "2025": (pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2026-01-01T00:00:00Z")),
    "2026_YTD": (pd.Timestamp("2026-01-01T00:00:00Z"), pd.Timestamp("2026-10-22T00:00:00Z")),
}
ROTATION_173 = {
    ("COMBINED_2025_2026_YTD", 60.0): 6.3591898993,
    ("COMBINED_2025_2026_YTD", 100.0): 11.0848945852,
    ("2025", 60.0): 4.6571872070, ("2025", 100.0): 8.2739321487,
    ("2026_YTD", 60.0): 2.5174311773, ("2026_YTD", 100.0): 4.2053856003,
}

class ResearchError(ValueError):
    pass

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def canonical_sha256(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())

def cost_price(ref: float, cost: dict[str, float], side: str) -> tuple[float, float]:
    if not math.isfinite(ref) or ref <= 0:
        raise ResearchError("INVALID_FILL_REFERENCE_PRICE")
    adverse = (cost["spread_bps"] / 2 + cost["slippage_bps"]) / 10000
    if side == "BUY":
        return ref * (1 + adverse), cost["fee_rate"]
    if side == "SELL":
        return ref * (1 - adverse), cost["fee_rate"]
    raise ResearchError("UNKNOWN_ORDER_SIDE")

def aggregate_daily(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, group in frame.groupby(frame.index.floor("D"), sort=True):
        expected = pd.date_range(day, periods=6, freq="4h")
        if len(group) != 6 or not group.index.equals(expected):
            continue
        rows.append({"timestamp": day, "open": float(group.open.iloc[0]), "high": float(group.high.max()),
                     "low": float(group.low.min()), "close": float(group.close.iloc[-1]), "volume": float(group.volume.sum())})
    if not rows:
        raise ResearchError("NO_COMPLETE_DAILY_BARS")
    result = pd.DataFrame(rows)
    result["timestamp"] = pd.to_datetime(result.timestamp, utc=True)
    result = result.set_index("timestamp", drop=False)
    if not (result.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all():
        raise ResearchError("GAP_IN_COMPLETE_DAILY_BARS")
    result["ema200"] = result.close.ewm(span=200, adjust=False, min_periods=200).mean()
    return result

def add_indicators(frame: pd.DataFrame, btc_daily: pd.DataFrame) -> pd.DataFrame:
    d = frame.copy()
    if not (d.index.to_series().diff().dropna() == pd.Timedelta(hours=4)).all():
        raise ResearchError("GAP_IN_4H_BARS")
    prior = d.close.shift(1)
    tr = pd.concat([d.high-d.low, (d.high-prior).abs(), (d.low-prior).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14, min_periods=14).mean()
    d["ema20"] = d.close.ewm(span=20, adjust=False, min_periods=20).mean()
    d["ema50"] = d.close.ewm(span=50, adjust=False, min_periods=50).mean()
    d["ema200"] = d.close.ewm(span=200, adjust=False, min_periods=200).mean()
    d["momentum42"] = d.close / d.close.shift(42) - 1
    d["chandelier"] = d.high.rolling(22, min_periods=22).max() - 3 * d.atr14
    # The 4h candle timestamp is its open. A signal is known at open+4h;
    # the most recent completed daily candle is floor(close_time, UTC day) - one day.
    decision_times = d.index + pd.Timedelta(hours=4)
    last_daily = decision_times.floor("D") - pd.Timedelta(days=1)
    d["macro_btc_close"] = btc_daily.close.reindex(last_daily).to_numpy()
    d["macro_btc_ema200"] = btc_daily.ema200.reindex(last_daily).to_numpy()
    d["macro_btc_risk_on"] = d["macro_btc_close"] > d["macro_btc_ema200"]
    d["buy_signal"] = (
        (d.low <= d.ema20) & (d.close > d.ema20) & (d.close > d.ema50) &
        (d.ema50 > d.ema200) & (d.momentum42 > 0) &
        d.macro_btc_risk_on.fillna(False) & d.atr14.notna() & d.chandelier.notna()
    )
    d["exit_signal"] = (d.close <= d.ema50) | (d.close <= d.chandelier)
    d["weight"] = (0.10 / ((d.atr14 / d.close) * math.sqrt(365*6))).clip(upper=0.50)
    d.loc[~np.isfinite(d.weight) | (d.weight <= 0), "weight"] = np.nan
    return d

def prepare_indicators(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    if set(frames) != {"BTCUSDT", "ETHUSDT"} or not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index):
        raise ResearchError("BTC_ETH_HISTORY_NOT_ALIGNED")
    daily = aggregate_daily(frames["BTCUSDT"])
    out = {s: add_indicators(frames[s], daily) for s in ("BTCUSDT", "ETHUSDT")}
    common = out["BTCUSDT"].index.intersection(out["ETHUSDT"].index)
    if len(common) != len(out["BTCUSDT"]) or len(common) != len(out["ETHUSDT"]):
        raise ResearchError("COMMON_INDEX_MISMATCH")
    return {s: out[s].loc[common].copy() for s in out}

def floor_qty(q: float) -> float:
    if not math.isfinite(q) or q < 0:
        raise ResearchError("INVALID_QUANTITY")
    return math.floor(q / LOT_STEP + 1e-10) * LOT_STEP

def close_position(pos: dict[str, Any], ref: float, ts: pd.Timestamp, reason: str, cash: float,
                   cost: dict[str, float], trades: list[dict[str, Any]]) -> float:
    fill, fee_rate = cost_price(ref, cost, "SELL")
    gross = pos["quantity"] * fill
    exit_fee = gross * fee_rate
    net = gross - exit_fee
    trades.append({
        "symbol": pos["symbol"], "signal_close_time": pos["signal_time"].isoformat(),
        "entry_time": pos["entry_time"].isoformat(), "exit_time": ts.isoformat(),
        "entry_reference_open": pos["entry_reference"], "entry_fill_price": pos["entry_fill"],
        "exit_reference_price": ref, "exit_fill_price": fill, "quantity": pos["quantity"],
        "entry_debit_usdt": pos["debit"], "entry_fee_usdt": pos["entry_fee"],
        "exit_fee_usdt": exit_fee, "net_pnl_usdt": net - pos["debit"],
        "net_trade_return_pct": (net / pos["debit"] - 1) * 100, "entry_weight": pos["weight"],
        "exit_reason": reason, "terminal_liquidation": reason == "TERMINAL_CLOSE",
    })
    return cash + net

def mark_equity(cash: float, pos: dict[str, Any] | None, frames: dict[str, pd.DataFrame],
                ts: pd.Timestamp, cost: dict[str, float]) -> float:
    if pos is None:
        return cash
    px, fee = cost_price(float(frames[pos["symbol"]].loc[ts, "close"]), cost, "SELL")
    return cash + pos["quantity"] * px * (1-fee)

def simulate_window(indicators: dict[str, pd.DataFrame], window: str, initial: float,
                    cost_name: str, cost: dict[str, float]) -> dict[str, Any]:
    start, end = WINDOWS[window]
    common = indicators["BTCUSDT"].index
    selected = common[(common >= start) & (common < end)]
    if len(selected) < 20:
        return {"status":"INCONCLUSIVE_NO_DATA","window":window,"initial_usdt":initial,"cost_scenario":cost_name,
                "bars":len(selected),"net_pnl_usdt":None,"return_pct":None,"max_drawdown_pct":None,
                "round_trips":0,"fees_usdt":None,"trades":[],"equity":[],"rejected_min_notional":0}
    cash = float(initial)
    pos = None
    trades: list[dict[str, Any]] = []
    equity = [{"timestamp": selected[0].isoformat(),"equity_net_liquidation_usdt":cash,"cash_usdt":cash,"position_symbol":""}]
    rejected = 0
    for ts in selected:
        i = common.get_loc(ts)
        if i > 0:
            signal_ts = common[i-1]
            if pos is not None:
                prior_pos_row = indicators[pos["symbol"]].loc[signal_ts]
                if i-1 >= pos["entry_bar_index"] and bool(prior_pos_row.exit_signal):
                    ref = float(indicators[pos["symbol"]].loc[ts, "open"])
                    cash = close_position(pos, ref, ts, "EMA50_OR_CHANDELIER_NEXT_OPEN", cash, cost, trades)
                    pos = None
            if pos is None:
                candidates = []
                for symbol in ("BTCUSDT","ETHUSDT"):
                    row = indicators[symbol].loc[signal_ts]
                    if bool(row.buy_signal):
                        candidates.append((float(row.momentum42),symbol,row))
                candidates.sort(key=lambda x:(-x[0],x[1]))
                if candidates:
                    momentum,symbol,row = candidates[0]
                    ref = float(indicators[symbol].loc[ts,"open"])
                    fill,fee_rate = cost_price(ref,cost,"BUY")
                    weight = float(row.weight) if math.isfinite(float(row.weight)) else 0.0
                    budget = cash * min(0.50,max(0.0,weight))
                    qty = floor_qty(budget/(fill*(1+fee_rate))) if budget > 0 else 0.0
                    notional = qty*fill
                    debit = notional*(1+fee_rate)
                    if qty <= 0 or notional < MIN_NOTIONAL or debit > cash + 1e-8:
                        rejected += 1
                    else:
                        entry_fee = notional*fee_rate
                        cash -= debit
                        pos = {"symbol":symbol,"signal_time":signal_ts+pd.Timedelta(hours=4),"entry_time":ts,
                               "entry_reference":ref,"entry_fill":fill,"quantity":qty,"debit":debit,
                               "entry_fee":entry_fee,"weight":weight,"entry_bar_index":i,"momentum42":momentum}
        equity.append({"timestamp":ts.isoformat(),"equity_net_liquidation_usdt":mark_equity(cash,pos,indicators,ts,cost),
                       "cash_usdt":cash,"position_symbol":pos["symbol"] if pos else ""})
    if pos is not None:
        last=selected[-1]
        cash=close_position(pos,float(indicators[pos["symbol"]].loc[last,"close"]),last,"TERMINAL_CLOSE",cash,cost,trades)
        equity[-1]["equity_net_liquidation_usdt"]=cash
        equity[-1]["cash_usdt"]=cash
        equity[-1]["position_symbol"]=""
    curve=np.array([float(r["equity_net_liquidation_usdt"]) for r in equity])
    peak=np.maximum.accumulate(curve)
    dd=np.where(peak>0,curve/peak-1,0)
    total_fees=sum(float(t["entry_fee_usdt"])+float(t["exit_fee_usdt"]) for t in trades)
    final=float(curve[-1])
    return {"status":"COMPLETED","window":window,"initial_usdt":initial,"cost_scenario":cost_name,"bars":len(selected),
            "start":selected[0].isoformat(),"end_exclusive":end.isoformat(),"net_pnl_usdt":final-initial,
            "return_pct":(final/initial-1)*100,"max_drawdown_pct":float(dd.min()*100),"round_trips":len(trades),
            "fees_usdt":total_fees,"rejected_min_notional":rejected,"trades":trades,"equity":equity}

def simulate_buy_hold(frame: pd.DataFrame, symbol: str, window: str, initial: float,
                      cost_name: str, cost: dict[str,float]) -> dict[str,Any]:
    start,end=WINDOWS[window]
    d=frame.loc[(frame.index>=start)&(frame.index<end)]
    if d.empty:
        return {"status":"INCONCLUSIVE_NO_DATA","window":window,"symbol":symbol,"initial_usdt":initial,
                "cost_scenario":cost_name,"net_pnl_usdt":None,"return_pct":None}
    first,last=d.iloc[0],d.iloc[-1]
    buy,fee=cost_price(float(first.open),cost,"BUY")
    qty=floor_qty(initial*.95/(buy*(1+fee)))
    notional=qty*buy; debit=notional*(1+fee)
    if notional<MIN_NOTIONAL or debit>initial:
        final=initial
    else:
        sell,sfee=cost_price(float(last.close),cost,"SELL")
        final=initial-debit+qty*sell*(1-sfee)
    return {"status":"COMPLETED","window":window,"symbol":symbol,"initial_usdt":initial,
            "cost_scenario":cost_name,"net_pnl_usdt":final-initial,"return_pct":(final/initial-1)*100,
            "start":d.index[0].isoformat(),"end_exclusive":end.isoformat()}

def run(dataset: Path, output_dir: Path, source_sha: str) -> dict[str,Any]:
    bundle,hourly,raw_sha,missing_availability,unknown_finality=shared.load_dataset(dataset)
    if raw_sha!=RAW_SHA or bundle.get("dataset_sha256")!=DATA_SHA or bundle.get("configuration_sha256")!=CONFIG_SHA:
        raise ResearchError("PINNED_SOURCE_HASH_MISMATCH")
    bars={s:shared.aggregate_4h(hourly[s]) for s in ("BTCUSDT","ETHUSDT")}
    common=bars["BTCUSDT"].index.intersection(bars["ETHUSDT"].index)
    if len(common)!=len(bars["BTCUSDT"]) or len(common)!=len(bars["ETHUSDT"]):
        raise ResearchError("BTC_ETH_COMMON_HISTORY_MISMATCH")
    bars={s:bars[s].loc[common].copy() for s in bars}
    indicators=prepare_indicators(bars)
    config={
        "strategy":STRATEGY,"macro":"latest completed UTC BTC daily close > daily EMA200",
        "entry":"low<=EMA20; close>EMA20; close>EMA50>EMA200; 42-bar return>0",
        "selection":"highest positive 42-bar return among qualifying BTC/ETH pullback signals",
        "exit":"close<=EMA50 OR close<=highest(high,22)-3*ATR14; fill next 4h open",
        "sizing":"min(50%, 10% / ((ATR14/close)*sqrt(365*6))) frozen at signal",
        "costs":COSTS,"windows":{k:[v[0].isoformat(),v[1].isoformat()] for k,v in WINDOWS.items()},
        "capitals":CAPITALS,"lot_step_assumption":LOT_STEP,"min_notional_assumption":MIN_NOTIONAL}
    config_hash=canonical_sha256(config)
    indicator_hash=canonical_sha256({s:[{"t":ts.isoformat(),"ema20":None if pd.isna(r.ema20) else float(r.ema20),
        "ema50":None if pd.isna(r.ema50) else float(r.ema50),"ema200":None if pd.isna(r.ema200) else float(r.ema200),
        "atr14":None if pd.isna(r.atr14) else float(r.atr14),"mom":None if pd.isna(r.momentum42) else float(r.momentum42),
        "macro":None if pd.isna(r.macro_btc_ema200) else float(r.macro_btc_ema200),
        "buy":bool(r.buy_signal),"exit":bool(r.exit_signal)} for ts,r in indicators[s].iterrows()] for s in indicators})
    output_dir.mkdir(parents=True,exist_ok=True)
    results=[]; summary=[]; benchmarks=[]; all_trades=[]; all_equity=[]
    for cost_name,cost in COSTS.items():
        for window in WINDOWS:
            for capital in CAPITALS:
                r=simulate_window(indicators,window,capital,cost_name,cost)
                results.append(r)
                summary.append({k:r.get(k) for k in ("status","window","initial_usdt","cost_scenario","bars","start","end_exclusive",
                    "net_pnl_usdt","return_pct","max_drawdown_pct","round_trips","fees_usdt","rejected_min_notional")})
                all_trades += [{**t,"window":window,"initial_usdt":capital,"cost_scenario":cost_name} for t in r.get("trades",[])]
                all_equity += [{**e,"window":window,"initial_usdt":capital,"cost_scenario":cost_name,"strategy":STRATEGY} for e in r.get("equity",[])]
                for symbol in ("BTCUSDT","ETHUSDT"):
                    benchmarks.append(simulate_buy_hold(bars[symbol],symbol,window,capital,cost_name,cost))
    for (window,capital),pnl in ROTATION_173.items():
        benchmarks.append({"status":"REPORTED_IN_ISSUE_173","window":window,"symbol":"ISSUE_173_ATR_ROTATION",
                           "initial_usdt":capital,"cost_scenario":"STRESS_2","net_pnl_usdt":pnl,"return_pct":pnl/capital*100,
                           "source_url":"https://github.com/eltiootaku01-hue/bot-cripto/issues/173"})
    def metric(window,capital):
        return next(x for x in results if x["window"]==window and x["initial_usdt"]==capital and x["cost_scenario"]=="STRESS_2")
    def bmetric(window,capital,symbol):
        return next(x for x in benchmarks if x.get("window")==window and x.get("initial_usdt")==capital and x.get("cost_scenario")=="STRESS_2" and x.get("symbol")==symbol)
    gates=[]
    for capital in CAPITALS:
        for window in WINDOWS:
            r=metric(window,capital)
            gates.append({"gate":"positive_net_pnl","window":window,"capital":capital,"pass":r.get("net_pnl_usdt") is not None and r["net_pnl_usdt"]>0,"observed":r.get("net_pnl_usdt")})
            gates.append({"gate":"max_drawdown_ge_minus_15pct","window":window,"capital":capital,"pass":r.get("max_drawdown_pct") is not None and r["max_drawdown_pct"]>=-15,"observed":r.get("max_drawdown_pct")})
        for window in ("COMBINED_2025_2026_YTD","2026_YTD"):
            r=metric(window,capital); reference=ROTATION_173[(window,capital)]
            gates.append({"gate":"minimum_5_round_trips","window":window,"capital":capital,"pass":r.get("round_trips",0)>=5,"observed":r.get("round_trips",0)})
            gates.append({"gate":"beat_issue_173","window":window,"capital":capital,"pass":r.get("net_pnl_usdt") is not None and r["net_pnl_usdt"]>reference,"observed":r.get("net_pnl_usdt"),"reference_pnl":reference})
        r=metric("2026_YTD",capital)
        gates.append({"gate":"2026_YTD_beat_cash","window":"2026_YTD","capital":capital,"pass":r.get("net_pnl_usdt") is not None and r["net_pnl_usdt"]>0,"observed":r.get("net_pnl_usdt"),"reference_pnl":0.0})
        for symbol in ("BTCUSDT","ETHUSDT"):
            b=bmetric("2026_YTD",capital,symbol)
            gates.append({"gate":"2026_YTD_beat_"+symbol+"_buy_hold","window":"2026_YTD","capital":capital,
                "pass":r.get("net_pnl_usdt") is not None and b.get("net_pnl_usdt") is not None and r["net_pnl_usdt"]>b["net_pnl_usdt"],
                "observed":r.get("net_pnl_usdt"),"reference_pnl":b.get("net_pnl_usdt")})
    decision="PASS_HISTORICAL_SHADOW_ONLY" if gates and all(x["pass"] for x in gates) else "FAIL_NO_TRADE"
    gate_summary={"passed":sum(bool(x["pass"]) for x in gates),"failed":sum(not x["pass"] for x in gates),"total":len(gates)}
    result={"strategy":STRATEGY,"decision":decision,"status":"HISTORICAL_REPLAY_NOT_PROSPECTIVE_HOLDOUT",
        "source_git_sha":source_sha,"source_raw_dataset_sha256":raw_sha,"dataset_sha256":bundle["dataset_sha256"],
        "dataset_configuration_sha256":bundle["configuration_sha256"],"strategy_configuration_sha256":config_hash,
        "indicators_and_signals_sha256":indicator_hash,"bar_counts_4h":{s:len(bars[s]) for s in bars},
        "bar_start_4h":{s:bars[s].index[0].isoformat() for s in bars},"bar_end_4h":{s:bars[s].index[-1].isoformat() for s in bars},
        "source_missing_available_at_count":missing_availability,"source_unknown_finality_count":unknown_finality,
        "filters":{"lot_step_assumption":LOT_STEP,"min_notional_assumption_usdt":MIN_NOTIONAL,
            "historical_filter_equivalence":"UNKNOWN","historical_executable_quotes":"UNKNOWN"},
        "strategy_configuration":config,"scenario_results":summary,"benchmarks":benchmarks,"gates":gates,"gate_summary":gate_summary}
    def write_json(name,obj):
        (output_dir/name).write_text(json.dumps(obj,sort_keys=True,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else None)+"\n",encoding="utf-8")
    def write_csv(name,rows):
        keys=sorted({k for row in rows for k in row})
        with (output_dir/name).open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=keys,extrasaction="ignore");writer.writeheader()
            for row in rows:
                writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    write_json("result.json",result); write_json("gate_results.json",{"decision":decision,"summary":gate_summary,"gates":gates})
    write_csv("summary.csv",summary); write_csv("benchmarks.csv",benchmarks); write_csv("fills.csv",all_trades); write_csv("equity.csv",all_equity)
    report=["# BTC/ETH 4h pullback continuation + ATR trailing exit V1","",
        "Decision: "+decision,"Status: historical replay only; PASS allows shadow prospective consideration, not PAPER/LIVE.",
        "Raw data SHA-256: "+raw_sha,"Canonical dataset SHA-256: "+str(bundle["dataset_sha256"]),
        "Configuration SHA-256: "+config_hash,"Indicators/signals SHA-256: "+indicator_hash,
        "Source code SHA: "+source_sha,"Historical executable quotes and historical filter equivalence: UNKNOWN.",
        "Source availability counts: "+json.dumps(missing_availability,sort_keys=True),
        "Source unknown-finality counts: "+json.dumps(unknown_finality,sort_keys=True),"",
        "## STRESS_2 strategy results","","| Window | Capital | PnL USDT | Return % | Max DD % | Round trips | Rejected min-notional |","|---|---:|---:|---:|---:|---:|---:|"]
    for x in summary:
        if x["cost_scenario"]=="STRESS_2":
            fmt=lambda v:"UNKNOWN" if v is None else f"{float(v):.4f}"
            report.append(f"| {x['window']} | {x['initial_usdt']:.0f} | {fmt(x['net_pnl_usdt'])} | {fmt(x['return_pct'])} | {fmt(x['max_drawdown_pct'])} | {x['round_trips']} | {x['rejected_min_notional']} |")
    report += ["","## Gates","",f"Passed {gate_summary['passed']} / {gate_summary['total']}; failed {gate_summary['failed']}.",
        "| Gate | Window | Capital | Result | Observed | Reference |","|---|---|---:|---|---:|---:|"]
    for g in gates:
        report.append(f"| {g['gate']} | {g['window']} | {g['capital']:.0f} | {'PASS' if g['pass'] else 'FAIL'} | {g.get('observed')} | {g.get('reference_pnl','')} |")
    report += ["","## Limitations","","Costs are modeled assumptions, not historical bid/ask fills. Lot-size and min-notional values are current assumptions; historic equivalence is UNKNOWN.",
        "The source has UNKNOWN point-in-time availability and candle-finality metadata. This is retrospective, not a pristine holdout.",
        "Spot long/cash only. No leverage, shorts, PAPER activation or live orders. NO_TRADE remains outside any future shadow test."]
    (output_dir/"report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    files={}
    for p in sorted(output_dir.iterdir()):
        if p.is_file() and p.name!="manifest.json":
            data=p.read_bytes();files[p.name]={"size_bytes":len(data),"sha256":sha256_bytes(data)}
    write_json("manifest.json",{"strategy":STRATEGY,"decision":decision,"source_git_sha":source_sha,
        "source_dataset_sha256":raw_sha,"dataset_sha256":bundle["dataset_sha256"],"configuration_sha256":config_hash,
        "indicators_and_signals_sha256":indicator_hash,"files":files})
    return result

def main():
    p=argparse.ArgumentParser(description="Frozen BTC/ETH 4h pullback-continuation virtual research")
    p.add_argument("--dataset",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--source-sha",default="UNKNOWN");a=p.parse_args()
    r=run(a.dataset,a.output_dir,a.source_sha)
    print("STRATEGY:",r["strategy"]);print("DECISION:",r["decision"])
    print("DATASET_SHA256:",r["dataset_sha256"]);print("CONFIG_SHA256:",r["strategy_configuration_sha256"])
    print("INDICATORS_SHA256:",r["indicators_and_signals_sha256"]);print("GATES:",json.dumps(r["gate_summary"],sort_keys=True))
    for row in r["scenario_results"]:
        if row["cost_scenario"]=="STRESS_2":
            print("STRESS_2",row["window"],row["initial_usdt"],row["net_pnl_usdt"],row["return_pct"],row["max_drawdown_pct"],row["round_trips"])
    return 0

if __name__=="__main__":
    raise SystemExit(main())
