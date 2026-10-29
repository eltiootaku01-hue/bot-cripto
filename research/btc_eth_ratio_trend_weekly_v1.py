from __future__ import annotations
import argparse, csv, hashlib, json, math
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

STRATEGY = "BTC_ETH_DAILY_RATIO_TREND_WEEKLY_V1"
EXPECTED_CANDLES_SHA256 = "584eb746f0cae21ad688b762c715fd55568bdbb4a27ca017a90867bd17b4e8a6"
EXPECTED_MANIFEST_SHA256 = "4ef8547f3605a51e905cebc94b8da956dc0f57228ac0e5bef047c939764d76f6"
ROWS_PER_ASSET = 2851
CAPITALS = (Decimal("60"), Decimal("100"))
LOT_STEP = {"BTCUSDT": Decimal("0.00001000"), "ETHUSDT": Decimal("0.00010000")}
MIN_QTY = {"BTCUSDT": Decimal("0.00001000"), "ETHUSDT": Decimal("0.00010000")}
MIN_NOTIONAL = {"BTCUSDT": Decimal("5.00000000"), "ETHUSDT": Decimal("5.00000000")}
MAX_WEIGHT, ATR_TARGET, EMA_RATIO, EMA_BTC_REGIME, ATR_PERIOD = 0.50, 0.15, 90, 200, 14
COSTS = {
 "BASE":{"fee_rate":Decimal("0.001"),"spread_bps":Decimal("10"),"slippage_bps":Decimal("5")},
 "STRESS_1":{"fee_rate":Decimal("0.001"),"spread_bps":Decimal("25"),"slippage_bps":Decimal("10")},
 "STRESS_2":{"fee_rate":Decimal("0.002"),"spread_bps":Decimal("50"),"slippage_bps":Decimal("20")},
}
WINDOWS = {
 "FULL_2020_2026_YTD":(pd.Timestamp("2020-01-01T00:00:00Z"),pd.Timestamp("2026-10-22T00:00:00Z")),
 "EARLIER_2020_2022":(pd.Timestamp("2020-01-01T00:00:00Z"),pd.Timestamp("2023-01-01T00:00:00Z")),
 "RECENT_2023_2026_YTD":(pd.Timestamp("2023-01-01T00:00:00Z"),pd.Timestamp("2026-10-22T00:00:00Z")),
 "COMBINED_2025_2026_YTD":(pd.Timestamp("2025-01-01T00:00:00Z"),pd.Timestamp("2026-10-22T00:00:00Z")),
 "2025":(pd.Timestamp("2025-01-01T00:00:00Z"),pd.Timestamp("2026-01-01T00:00:00Z")),
 "2026_YTD":(pd.Timestamp("2026-01-01T00:00:00Z"),pd.Timestamp("2026-10-22T00:00:00Z")),
}
BASELINE_15 = {
 ("COMBINED_2025_2026_YTD",Decimal("60")):Decimal("9.99129"),
 ("COMBINED_2025_2026_YTD",Decimal("100")):Decimal("16.79469"),
 ("2026_YTD",Decimal("60")):Decimal("3.76917"),
 ("2026_YTD",Decimal("100")):Decimal("6.23236"),
}

class ReplayError(ValueError): pass

def sha256_bytes(blob: bytes) -> str:
 return hashlib.sha256(blob).hexdigest()

def canonical_sha(value: Any) -> str:
 return sha256_bytes(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8"))

def load_history(candles_path: Path, manifest_path: Path):
 raw=candles_path.read_bytes(); raw_sha=sha256_bytes(raw)
 manifest_raw=manifest_path.read_bytes(); manifest_sha=sha256_bytes(manifest_raw)
 if raw_sha!=EXPECTED_CANDLES_SHA256: raise ReplayError("PINNED_CANDLES_SHA256_MISMATCH")
 if manifest_sha!=EXPECTED_MANIFEST_SHA256: raise ReplayError("PINNED_MANIFEST_SHA256_MISMATCH")
 manifest=json.loads(manifest_raw)
 grouped={"BTCUSDT":[],"ETHUSDT":[]}
 with candles_path.open("r",newline="",encoding="utf-8") as f:
  reader=csv.DictReader(f)
  if reader.fieldnames!=["symbol","open_ms","timestamp","close_ms","open","high","low","close","volume"]:
   raise ReplayError("UNEXPECTED_CSV_SCHEMA")
  for r in reader:
   if r["symbol"] not in grouped: raise ReplayError("UNEXPECTED_SYMBOL")
   grouped[r["symbol"]].append(r)
 frames={}; own_hashes={}
 for sym,records in grouped.items():
  if len(records)!=ROWS_PER_ASSET: raise ReplayError(f"WRONG_ROW_COUNT:{sym}:{len(records)}")
  rows=[]; timestamps=[]; canonical=[]
  for r in records:
   ts=pd.Timestamp(r["timestamp"])
   if ts.tzinfo is None: raise ReplayError(f"NAIVE_TIMESTAMP:{sym}")
   open_ms=int(r["open_ms"]); close_ms=int(r["close_ms"])
   if int(ts.timestamp()*1000)!=open_ms or close_ms!=open_ms+86_399_999: raise ReplayError(f"BAD_TIME_CONTRACT:{sym}:{r['timestamp']}")
   o,h,l,c,v=[Decimal(r[k]) for k in ("open","high","low","close","volume")]
   if min(o,h,l,c)<=0 or v<0 or h<max(o,c,l) or l>min(o,c,h): raise ReplayError(f"BAD_OHLCV:{sym}:{r['timestamp']}")
   timestamps.append(ts)
   canonical.append({k:r[k] for k in ("timestamp","open_ms","close_ms","open","high","low","close","volume")})
   rows.append({"timestamp":ts,"open":float(o),"high":float(h),"low":float(l),"close":float(c),"volume":float(v),
     "open_s":r["open"],"high_s":r["high"],"low_s":r["low"],"close_s":r["close"]})
  if timestamps!=sorted(timestamps) or len(set(timestamps))!=len(timestamps): raise ReplayError(f"UNSORTED_OR_DUPLICATE:{sym}")
  if any(b-a!=pd.Timedelta(days=1) for a,b in zip(timestamps,timestamps[1:])): raise ReplayError(f"DAILY_GAP:{sym}")
  own_hashes[sym]=canonical_sha(canonical)
  frames[sym]=pd.DataFrame(rows).set_index("timestamp",drop=False)
 if not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index): raise ReplayError("PAIR_TIMESTAMPS_MISMATCH")
 if frames["BTCUSDT"].index[0]!=pd.Timestamp("2019-01-01T00:00:00Z") or frames["BTCUSDT"].index[-1]!=pd.Timestamp("2026-10-21T00:00:00Z"):
  raise ReplayError("UNEXPECTED_DATE_RANGE")
 source={"candles_csv_sha256":raw_sha,"manifest_sha256":manifest_sha,"own_canonical_sha256_by_symbol":own_hashes,
  "manifest_canonical_sha256_by_symbol":manifest.get("canonical_dataset_sha256_by_symbol",{}),
  "paired_timestamp_series_sha256":manifest.get("paired_timestamp_series_sha256","UNKNOWN"),
  "rows_per_asset":{s:len(frames[s]) for s in frames},"first_timestamp":frames["BTCUSDT"].index[0].isoformat(),
  "last_timestamp":frames["BTCUSDT"].index[-1].isoformat(),
  "point_in_time_availability":manifest.get("point_in_time_availability","UNKNOWN"),
  "historical_filter_equivalence":manifest.get("historical_exchange_filter_equivalence","UNKNOWN"),
  "historical_spread_slippage":manifest.get("historical_spread_slippage","UNKNOWN")}
 return frames,manifest,source

def compute_indicators(frames: dict[str,pd.DataFrame]) -> dict[str,pd.DataFrame]:
 if not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index): raise ReplayError("PAIR_TIMESTAMPS_MISMATCH")
 out={s:frames[s].copy() for s in ("BTCUSDT","ETHUSDT")}
 for s in ("BTCUSDT","ETHUSDT"):
  d=out[s]; prev=d.close.shift(1)
  tr=pd.concat([d.high-d.low,(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1)
  d["atr14"]=tr.rolling(ATR_PERIOD,min_periods=ATR_PERIOD).mean()
 out["BTCUSDT"]["ema200"]=out["BTCUSDT"].close.ewm(span=EMA_BTC_REGIME,adjust=False,min_periods=EMA_BTC_REGIME).mean()
 ratio=out["ETHUSDT"].close/out["BTCUSDT"].close
 shared=out["BTCUSDT"].copy()
 shared["ratio_eth_btc"]=ratio
 shared["ratio_ema90"]=ratio.ewm(span=EMA_RATIO,adjust=False,min_periods=EMA_RATIO).mean()
 shared["btc_risk_on"]=shared.close>shared.ema200
 shared["weekly_decision"]=[ts.weekday()==6 for ts in shared.index]
 shared["target"]=None
 shared["target_weight"]=np.nan
 for i,ts in enumerate(shared.index):
  if not bool(shared.iloc[i]["weekly_decision"]): continue
  row=shared.iloc[i]
  if pd.isna(row["ema200"]) or pd.isna(row["ratio_ema90"]): continue
  if float(row["close"])<=float(row["ema200"]): target=None
  else: target="ETHUSDT" if float(row["ratio_eth_btc"])>float(row["ratio_ema90"]) else "BTCUSDT"
  shared.at[ts,"target"]=target
  if target is not None:
   tr=out[target].iloc[i]
   if pd.isna(tr["atr14"]) or float(tr["atr14"])<=0 or float(tr["close"])<=0:
    shared.at[ts,"target"]=None; continue
   vol=(float(tr["atr14"])/float(tr["close"]))*math.sqrt(365.0)
   weight=min(MAX_WEIGHT,ATR_TARGET/vol) if vol>0 else 0.0
   if not math.isfinite(weight) or weight<=0: shared.at[ts,"target"]=None
   else: shared.at[ts,"target_weight"]=weight
 out["SHARED"]=shared
 return out

def _cost_price(reference: Decimal,side: str,cost: dict[str,Decimal]) -> tuple[Decimal,Decimal]:
 if not reference.is_finite() or reference<=0: raise ReplayError("INVALID_FILL_REFERENCE")
 adverse=(cost["spread_bps"]/Decimal("2")+cost["slippage_bps"])/Decimal("10000")
 if side=="BUY": return reference*(Decimal("1")+adverse),cost["fee_rate"]
 if side=="SELL": return reference*(Decimal("1")-adverse),cost["fee_rate"]
 raise ReplayError("BAD_SIDE")

def _floor_qty(q: Decimal,step: Decimal) -> Decimal:
 if not q.is_finite() or q<0: raise ReplayError("INVALID_QUANTITY")
 return (q/step).to_integral_value(rounding=ROUND_DOWN)*step

def _close(pos,reference,ts,reason,cash,cost,trades):
 fill,fee_rate=_cost_price(reference,"SELL",cost); gross=pos["quantity"]*fill; fee=gross*fee_rate; net=gross-fee
 trades.append({"symbol":pos["symbol"],"signal_bar_utc":pos["signal_bar"].isoformat(),"entry_bar_utc":pos["entry_time"].isoformat(),
  "exit_bar_utc":ts.isoformat(),"entry_reference_open":str(pos["entry_reference"]),"entry_fill_price":str(pos["entry_fill"]),
  "exit_reference_price":str(reference),"exit_fill_price":str(fill),"quantity":str(pos["quantity"]),
  "entry_cash_debit_usdt":str(pos["entry_debit"]),"entry_fee_usdt":str(pos["entry_fee"]),"exit_fee_usdt":str(fee),
  "net_pnl_usdt":str(net-pos["entry_debit"]),"net_trade_return_pct":str((net/pos["entry_debit"]-Decimal("1"))*100),
  "entry_weight":str(pos["weight"]),"exit_reason":reason,"terminal_liquidation":reason=="TERMINAL_CLOSE"})
 return cash+net

def _mark(cash,pos,indicators,ts,cost):
 if pos is None: return cash
 reference=Decimal(indicators[pos["symbol"]].loc[ts,"close_s"]); sell,fee=_cost_price(reference,"SELL",cost)
 return cash+pos["quantity"]*sell*(Decimal("1")-fee)

def simulate_window(indicators,window,capital,scenario_name,cost):
 start,end=WINDOWS[window]; shared=indicators["SHARED"]; all_ts=shared.index
 selected=all_ts[(all_ts>=start)&(all_ts<end)]
 if len(selected)<30: raise ReplayError(f"INSUFFICIENT_WINDOW:{window}")
 cash=capital; pos=None; pending=None; trades=[]; fills=[]; equity=[{"timestamp":selected[0].isoformat(),"equity_net_liquidation_usdt":str(cash),"cash_usdt":str(cash),"position_symbol":""}]
 rejected=0; decisions=0
 for local_i,ts in enumerate(selected):
  if pending is not None:
   target=pending["target"]
   current=pos["symbol"] if pos is not None else None
   if target!=current:
    if pos is not None:
     sym=pos["symbol"]; ref=Decimal(indicators[sym].loc[ts,"open_s"]); before=cash
     cash=_close(pos,ref,ts,"WEEKLY_TARGET_SWITCH_NEXT_OPEN",cash,cost,trades)
     fills.append({"timestamp":ts.isoformat(),"symbol":sym,"side":"SELL","reference_price":str(ref),"cash_before_usdt":str(before),"cash_after_usdt":str(cash),"reason":"WEEKLY_TARGET_SWITCH_NEXT_OPEN"})
     pos=None
    if target is not None:
     sym=target; ref=Decimal(indicators[sym].loc[ts,"open_s"]); fill,fee_rate=_cost_price(ref,"BUY",cost)
     weight=Decimal(str(pending["weight"])); budget=cash*min(Decimal(str(MAX_WEIGHT)),max(Decimal("0"),weight))
     qty=_floor_qty(budget/(fill*(Decimal("1")+fee_rate)),LOT_STEP[sym]); notional=qty*fill; debit=notional*(Decimal("1")+fee_rate)
     if qty<MIN_QTY[sym] or notional<MIN_NOTIONAL[sym] or debit>cash: rejected+=1
     else:
      fee=notional*fee_rate; before=cash; cash-=debit
      pos={"symbol":sym,"quantity":qty,"entry_debit":debit,"entry_fee":fee,"entry_reference":ref,"entry_fill":fill,
           "entry_time":ts,"signal_bar":pending["signal_bar"],"weight":weight}
      fills.append({"timestamp":ts.isoformat(),"symbol":sym,"side":"BUY","reference_price":str(ref),"fill_price":str(fill),"quantity":str(qty),
        "notional_usdt":str(notional),"fee_usdt":str(fee),"cash_before_usdt":str(before),"cash_after_usdt":str(cash),
        "requested_weight":str(weight),"reason":"SUNDAY_RATIO_TREND_SIGNAL_NEXT_OPEN"})
   pending=None
  marked=_mark(cash,pos,indicators,ts,cost)
  equity.append({"timestamp":ts.isoformat(),"equity_net_liquidation_usdt":str(marked),"cash_usdt":str(cash),"position_symbol":pos["symbol"] if pos else ""})
  if bool(shared.loc[ts,"weekly_decision"]) and local_i<len(selected)-1:
   target=shared.loc[ts,"target"]
   if pd.isna(target): target=None
   weight=0.0 if target is None else float(shared.loc[ts,"target_weight"])
   pending={"target":target,"weight":weight,"signal_bar":ts+pd.Timedelta(days=1)-pd.Timedelta(milliseconds=1)}
   decisions+=1
 if pos is not None:
  last=selected[-1]; ref=Decimal(indicators[pos["symbol"]].loc[last,"close_s"]); before=cash
  cash=_close(pos,ref,last,"TERMINAL_CLOSE",cash,cost,trades)
  fills.append({"timestamp":last.isoformat(),"symbol":pos["symbol"],"side":"SELL","reference_price":str(ref),"cash_before_usdt":str(before),"cash_after_usdt":str(cash),"reason":"TERMINAL_CLOSE"})
  equity[-1]["equity_net_liquidation_usdt"]=str(cash); equity[-1]["cash_usdt"]=str(cash); equity[-1]["position_symbol"]=""
 curve=np.asarray([float(x["equity_net_liquidation_usdt"]) for x in equity]); peaks=np.maximum.accumulate(curve); dd=np.where(peaks>0,curve/peaks-1,0)
 fees=sum((Decimal(t["entry_fee_usdt"])+Decimal(t["exit_fee_usdt"]) for t in trades),Decimal("0"))
 final=Decimal(str(curve[-1]))
 return {"status":"COMPLETED","window":window,"initial_usdt":str(capital),"cost_scenario":scenario_name,"bars":len(selected),
  "start":selected[0].isoformat(),"end_exclusive":end.isoformat(),"net_pnl_usdt":str(final-capital),
  "return_pct":str((final/capital-Decimal("1"))*Decimal("100")),"max_drawdown_pct":float(dd.min()*100),
  "round_trips":len(trades),"fees_usdt":str(fees),"weekly_decisions":decisions,"rejected_min_notional":rejected,
  "trades":trades,"fills":fills,"equity":equity}

def simulate_bh(frame,symbol,window,capital,scenario_name,cost):
 start,end=WINDOWS[window]; d=frame.loc[(frame.index>=start)&(frame.index<end)]
 if d.empty: raise ReplayError(f"NO_BH_DATA:{window}:{symbol}")
 first,last=d.iloc[0],d.iloc[-1]; ref=Decimal(first["open_s"]); buy,fee=_cost_price(ref,"BUY",cost)
 budget=capital*Decimal("0.95"); qty=_floor_qty(budget/(buy*(Decimal("1")+fee)),LOT_STEP[symbol]); notional=qty*buy; debit=notional*(Decimal("1")+fee)
 if qty<MIN_QTY[symbol] or notional<MIN_NOTIONAL[symbol] or debit>capital: final=capital; fees=Decimal("0")
 else:
  sell,fee2=_cost_price(Decimal(last["close_s"]),"SELL",cost); gross=qty*sell; exit_fee=gross*fee2; final=capital-debit+gross-exit_fee; fees=notional*fee+exit_fee
 return {"status":"COMPLETED","window":window,"symbol":symbol,"initial_usdt":str(capital),"cost_scenario":scenario_name,
  "net_pnl_usdt":str(final-capital),"return_pct":str((final/capital-Decimal("1"))*Decimal("100")),"fees_usdt":str(fees)}

def run(candles_path,manifest_path,output_dir,source_sha):
 frames,source_manifest,source=load_history(candles_path,manifest_path); indicators=compute_indicators(frames); output_dir.mkdir(parents=True,exist_ok=True)
 strategy=[]; benchmarks=[]; trades_all=[]; fills_all=[]; equity_all=[]; result_objects=[]
 for cost_name,cost in COSTS.items():
  for win in WINDOWS:
   for capital in CAPITALS:
    r=simulate_window(indicators,win,capital,cost_name,cost); result_objects.append(r)
    strategy.append({k:r.get(k) for k in ("status","window","initial_usdt","cost_scenario","bars","start","end_exclusive","net_pnl_usdt","return_pct","max_drawdown_pct","round_trips","fees_usdt","weekly_decisions","rejected_min_notional")})
    trades_all += [{**x,"window":win,"initial_usdt":str(capital),"cost_scenario":cost_name} for x in r["trades"]]
    fills_all += [{**x,"window":win,"initial_usdt":str(capital),"cost_scenario":cost_name} for x in r["fills"]]
    equity_all += [{**x,"window":win,"initial_usdt":str(capital),"cost_scenario":cost_name,"strategy":STRATEGY} for x in r["equity"]]
    for sym in ("BTCUSDT","ETHUSDT"): benchmarks.append(simulate_bh(frames[sym],sym,win,capital,cost_name,cost))
    benchmarks.append({"status":"CASH_BENCHMARK","window":win,"symbol":"CASH","initial_usdt":str(capital),"cost_scenario":cost_name,"net_pnl_usdt":"0","return_pct":"0","fees_usdt":"0"})
 for (win,capital),pnl in BASELINE_15.items():
  benchmarks.append({"status":"REPORTED_IN_ISSUE_181","window":win,"symbol":"ISSUE_181_ATR15_ROTATION","initial_usdt":str(capital),"cost_scenario":"STRESS_2","net_pnl_usdt":str(pnl),"return_pct":str(pnl/capital*100),"source_url":"https://github.com/eltiootaku01-hue/bot-cripto/issues/181"})
 def metric(win,cap): return next(x for x in result_objects if x["window"]==win and Decimal(x["initial_usdt"])==cap and x["cost_scenario"]=="STRESS_2")
 def bench(win,cap,sym): return next(x for x in benchmarks if x.get("window")==win and Decimal(x["initial_usdt"])==cap and x.get("cost_scenario")=="STRESS_2" and x.get("symbol")==sym)
 gates=[]
 for cap in CAPITALS:
  for win in WINDOWS:
   r=metric(win,cap); pnl=Decimal(r["net_pnl_usdt"])
   gates.append({"gate":"positive_net_pnl","window":win,"capital":str(cap),"pass":pnl>0,"observed":str(pnl)})
   gates.append({"gate":"max_drawdown_ge_minus_15pct","window":win,"capital":str(cap),"pass":r["max_drawdown_pct"]>=-15.0,"observed":r["max_drawdown_pct"]})
  for win in ("FULL_2020_2026_YTD","RECENT_2023_2026_YTD","COMBINED_2025_2026_YTD"):
   r=metric(win,cap); gates.append({"gate":"minimum_5_round_trips","window":win,"capital":str(cap),"pass":r["round_trips"]>=5,"observed":r["round_trips"]})
  r=metric("2026_YTD",cap); gates.append({"gate":"minimum_3_round_trips","window":"2026_YTD","capital":str(cap),"pass":r["round_trips"]>=3,"observed":r["round_trips"]})
  for win in ("COMBINED_2025_2026_YTD","2026_YTD"):
   r=metric(win,cap); pnl=Decimal(r["net_pnl_usdt"]); ref=BASELINE_15[(win,cap)]
   gates.append({"gate":"beat_issue_181_atr15","window":win,"capital":str(cap),"pass":pnl>ref,"observed":str(pnl),"reference_pnl":str(ref)})
  ytd=metric("2026_YTD",cap); ytd_pnl=Decimal(ytd["net_pnl_usdt"])
  for sym in ("CASH","BTCUSDT","ETHUSDT"):
   ref=Decimal(bench("2026_YTD",cap,sym)["net_pnl_usdt"])
   gates.append({"gate":"2026_YTD_beat_"+sym,"window":"2026_YTD","capital":str(cap),"pass":ytd_pnl>ref,"observed":str(ytd_pnl),"reference_pnl":str(ref)})
 gate_summary={"passed":sum(bool(g["pass"]) for g in gates),"failed":sum(not g["pass"] for g in gates),"total":len(gates)}
 decision="PASS_HISTORICAL_SHADOW_ONLY" if gate_summary["failed"]==0 else "FAIL_NO_TRADE"
 config={"strategy":STRATEGY,"signal":"Sunday UTC daily close; BTC close > EMA200; choose ETH if ETH/BTC > EMA90(ETH/BTC), otherwise BTC, else cash",
  "execution":"next daily open; switch only when target changes","sizing":"min(50%,15%/((ATR14/close)*sqrt(365))) frozen until target changes",
  "costs":{n:{k:str(v) for k,v in c.items()} for n,c in COSTS.items()},
  "windows":{k:[v[0].isoformat(),v[1].isoformat()] for k,v in WINDOWS.items()},"capitals":[str(x) for x in CAPITALS]}
 config_sha=canonical_sha(config)
 signals_sha=canonical_sha({"timestamps":[x.isoformat() for x in indicators["SHARED"].index],
  "ratio":[None if pd.isna(x) else float(x) for x in indicators["SHARED"]["ratio_eth_btc"]],
  "ratio_ema90":[None if pd.isna(x) else float(x) for x in indicators["SHARED"]["ratio_ema90"]],
  "btc_ema200":[None if pd.isna(x) else float(x) for x in indicators["SHARED"]["ema200"]],
  "targets":[None if pd.isna(x) else x for x in indicators["SHARED"]["target"]]})
 result={"strategy":STRATEGY,"decision":decision,"status":"HISTORICAL_REPLAY_NOT_PROSPECTIVE_HOLDOUT","source_git_sha":source_sha,"source_data":source,
  "configuration":config,"configuration_sha256":config_sha,"indicators_signals_sha256":signals_sha,
  "scenario_results":strategy,"benchmarks":benchmarks,"gates":gates,"gate_summary":gate_summary,
  "data_limits":{"point_in_time_availability":"UNKNOWN","historical_filter_equivalence":"UNKNOWN","historical_executable_quotes":"UNKNOWN","costs":"MODELED_ASSUMPTIONS_ONLY","ai_or_ml_used":False}}
 def write_json(n,obj): (output_dir/n).write_text(json.dumps(obj,sort_keys=True,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else None)+"\n",encoding="utf-8")
 def write_csv(n,rows):
  keys=sorted({k for row in rows for k in row})
  with (output_dir/n).open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=keys,extrasaction="ignore");w.writeheader()
   for row in rows: w.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})
 write_json("result.json",result);write_json("gate_results.json",{"decision":decision,"summary":gate_summary,"gates":gates})
 write_csv("summary.csv",strategy);write_csv("benchmarks.csv",benchmarks);write_csv("fills.csv",fills_all);write_csv("round_trips.csv",trades_all);write_csv("equity.csv",equity_all)
 lines=["# BTC/ETH daily ETH/BTC ratio trend rotation V1 — no AI","",f"Decision: {decision}","Historical replay only; a PASS permits shadow consideration only, never PAPER/LIVE.",
  f"Source git SHA: {source_sha}",f"Candles CSV SHA-256: {source['candles_csv_sha256']}",f"Manifest SHA-256: {source['manifest_sha256']}",
  f"Configuration SHA-256: {config_sha}",f"Indicators/signals SHA-256: {signals_sha}",f"Rows per asset: {json.dumps(source['rows_per_asset'],sort_keys=True)}",
  f"Point-in-time availability: {source['point_in_time_availability']}; historic filter equivalence: {source['historical_filter_equivalence']}; executable quotes: UNKNOWN.","",
  "## STRESS_2 results","","| Window | Capital | Net PnL | Return | Max DD | Round trips | Fees |","|---|---:|---:|---:|---:|---:|---:|"]
 for r in strategy:
  if r["cost_scenario"]=="STRESS_2": lines.append(f"| {r['window']} | {r['initial_usdt']} | {float(r['net_pnl_usdt']):.4f} | {float(r['return_pct']):.3f}% | {float(r['max_drawdown_pct']):.3f}% | {r['round_trips']} | {float(r['fees_usdt']):.4f} |")
 lines+=["",f"Gates: {gate_summary['passed']} pass / {gate_summary['total']} total; {gate_summary['failed']} fail.","","| Gate | Window | Capital | Outcome | Observed | Reference |","|---|---|---:|---|---:|---:|"]
 for g in gates: lines.append(f"| {g['gate']} | {g['window']} | {g['capital']} | {'PASS' if g['pass'] else 'FAIL'} | {g['observed']} | {g.get('reference_pnl','')} |")
 lines+=["","Limits: results are retrospective; cost assumptions are modeled; historic filter equivalence, point-in-time availability and executable quotes remain UNKNOWN.",
  "NO_TRADE stays active; no AI, leverage, shorts, derivatives, PAPER activation or live orders."]
 (output_dir/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
 files={}
 for p in sorted(output_dir.iterdir()):
  if p.is_file() and p.name!="manifest.json":
   b=p.read_bytes();files[p.name]={"size_bytes":len(b),"sha256":sha256_bytes(b)}
 write_json("manifest.json",{"strategy":STRATEGY,"decision":decision,"source_git_sha":source_sha,"candles_csv_sha256":source["candles_csv_sha256"],
  "manifest_sha256":source["manifest_sha256"],"configuration_sha256":config_sha,"indicators_signals_sha256":signals_sha,"files":files})
 print("STRATEGY:",STRATEGY);print("DECISION:",decision);print("GATES:",json.dumps(gate_summary,sort_keys=True))
 print("DATASET_SHA256:",source["candles_csv_sha256"]);print("CONFIG_SHA256:",config_sha);print("INDICATORS_SHA256:",signals_sha)
 for r in strategy:
  if r["cost_scenario"]=="STRESS_2": print("STRESS_2",r["window"],r["initial_usdt"],r["net_pnl_usdt"],r["return_pct"],r["max_drawdown_pct"],r["round_trips"])
 return result

def main():
 p=argparse.ArgumentParser(description="Frozen rule-based daily BTC/ETH ratio trend rotation, no AI")
 p.add_argument("--candles",type=Path,required=True);p.add_argument("--dataset-manifest",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--source-sha",default="UNKNOWN")
 a=p.parse_args();run(a.candles,a.dataset_manifest,a.output_dir,a.source_sha);return 0
if __name__=="__main__": raise SystemExit(main())
