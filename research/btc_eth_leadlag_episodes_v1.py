from __future__ import annotations
import argparse, hashlib, json, math, os, platform
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

DATASET_SHA256 = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
CONFIG_SHA256 = "2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a"
COSTS = {
 "BASE": {"fee": .001, "spread_bps": 10., "slippage_bps": 5.},
 "STRESS_1": {"fee": .001, "spread_bps": 25., "slippage_bps": 10.},
 "STRESS_2": {"fee": .002, "spread_bps": 50., "slippage_bps": 20.},
}
REPLICATES, SEED, MONTH_BLOCK, DAY_BLOCK = 10000, 20261019, 3, 90

def sha256(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def canonical_sha256(x: Any) -> str:
 return sha256(json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())

def load_data(path: Path):
 raw = path.read_bytes()
 bundle = json.loads(raw)
 if bundle.get("dataset_sha256") != DATASET_SHA256: raise ValueError("PINNED_DATASET_SHA256_MISMATCH")
 if bundle.get("configuration_sha256") != CONFIG_SHA256: raise ValueError("PINNED_CONFIGURATION_SHA256_MISMATCH")
 if canonical_sha256(bundle.get("data")) != DATASET_SHA256: raise ValueError("DATASET_HASH_RECOMPUTE_FAILED")
 if canonical_sha256(bundle.get("configuration")) != CONFIG_SHA256: raise ValueError("CONFIGURATION_HASH_RECOMPUTE_FAILED")
 frames = {}
 for symbol in ("BTCUSDT", "ETHUSDT"):
  records = bundle["data"]["history"][symbol]
  d = pd.DataFrame([{**x["candle"], "timestamp": x["candle"]["start"], "available_at": x.get("available_at"),
                     "finality": x["candle"].get("finality")} for x in records])
  d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, errors="raise")
  for col in ("open", "high", "low", "close", "volume"): d[col] = pd.to_numeric(d[col], errors="raise")
  if len(d) != 15360 or not d.timestamp.is_unique or not d.timestamp.is_monotonic_increasing: raise ValueError("INVALID_BAR_COUNT_OR_CHRONOLOGY:"+symbol)
  if not (d.timestamp.diff().dropna() == pd.Timedelta(hours=1)).all(): raise ValueError("HOURLY_GAP:"+symbol)
  if not (d[["open","high","low","close"]] > 0).all().all(): raise ValueError("INVALID_OHLC:"+symbol)
  if not (d.high >= d[["open","close","low"]].max(axis=1)).all(): raise ValueError("INVALID_HIGH:"+symbol)
  if not (d.low <= d[["open","close","high"]].min(axis=1)).all(): raise ValueError("INVALID_LOW:"+symbol)
  frames[symbol] = d.set_index("timestamp", drop=False)
 if not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index): raise ValueError("BTC_ETH_HISTORY_NOT_ALIGNED")
 return bundle, frames, sha256(raw)

def net_return(entry: float, exit_: float, cost: dict[str,float]) -> float:
 if not all(math.isfinite(x) and x > 0 for x in (entry, exit_)): raise ValueError("INVALID_MID_PRICE")
 half = cost["spread_bps"]/20000
 slip = cost["slippage_bps"]/10000
 buy, sell = entry*(1+half+slip), exit_*(1-half-slip)
 if buy <= 0 or sell <= 0: raise ValueError("NON_POSITIVE_EXECUTION_PRICE")
 return float(sell/buy*(1-cost["fee"])**2-1)

def episodes_for(frames):
 btc, eth = frames["BTCUSDT"], frames["ETHUSDT"]
 br, er = btc.close.pct_change(4), eth.close.pct_change(4)
 ema = eth.close.ewm(span=100, adjust=False, min_periods=100).mean()
 signal = ((br >= .015) & (er >= -.005) & (er <= .008) & (eth.close > ema)).fillna(False).to_numpy()
 opens, idx = eth.open.to_numpy(float), eth.index
 events=[]; overlap=tail=0; next_allowed=0
 for i, fire in enumerate(signal):
  if not fire: continue
  ent, ex = i+1, i+25
  if ex >= len(eth): tail += 1; continue
  if i < next_allowed: overlap += 1; continue
  close_time=idx[i]+pd.Timedelta(hours=1)
  if close_time != idx[ent] or idx[ex]-idx[ent] != pd.Timedelta(hours=24): raise ValueError("EPISODE_TIMING_CONTRACT_FAILED")
  ev={"episode_id":f"E{len(events)+1:04d}","signal_bar_start":idx[i].isoformat(),"signal_close_time":close_time.isoformat(),
      "entry_time":idx[ent].isoformat(),"exit_time":idx[ex].isoformat(),"exit_month":idx[ex].strftime("%Y-%m"),
      "btc_r4_pct":100*float(br.iloc[i]),"eth_r4_pct":100*float(er.iloc[i]),"eth_close":float(eth.close.iloc[i]),
      "eth_ema100":float(ema.iloc[i]),"entry_mid":float(opens[ent]),"exit_mid":float(opens[ex]),
      "gross_return_pct":100*(float(opens[ex])/float(opens[ent])-1)}
  for name,cost in COSTS.items(): ev[f"net_return_{name.lower()}_pct"]=100*net_return(ev["entry_mid"],ev["exit_mid"],cost)
  events.append(ev); next_allowed=i+24
 counts={"raw_signal_count":int(np.count_nonzero(signal)),"accepted_episode_count":len(events),
         "overlapping_signals_skipped":overlap,"signals_rejected_incomplete_horizon":tail}
 if counts["raw_signal_count"] != counts["accepted_episode_count"]+overlap+tail: raise ValueError("SIGNAL_ACCOUNTING_INVARIANT_FAILED")
 return events, counts

def month_rows(events, frames):
 idx=frames["ETHUSDT"].index; first,last=idx[0],idx[-1]
 periods=pd.period_range(first.tz_localize(None).to_period("M"),last.tz_localize(None).to_period("M"),freq="M")
 grouped={}
 for e in events: grouped.setdefault(e["exit_month"],[]).append(e)
 rows=[]
 def find(t):
  i=int(idx.searchsorted(t,side="left")); return i if i<len(idx) else None
 for period in periods:
  mo=str(period); start=pd.Timestamp(mo+"-01T00:00:00Z"); nxt=start+pd.offsets.MonthBegin(1)
  complete=first<=start and last>=nxt; si,ei=find(start),find(nxt)
  for name,cost in COSTS.items():
   evs=grouped.get(mo,[])
   rr=[float(e[f"net_return_{name.lower()}_pct"])/100 for e in evs]
   strat=100*(float(np.prod(1+np.asarray(rr)))-1) if rr else 0.0
   refs={}
   for sym in ("BTCUSDT","ETHUSDT"):
    refs[sym]=None if si is None or ei is None else 100*net_return(float(frames[sym].open.iloc[si]),float(frames[sym].open.iloc[ei]),cost)
   rows.append({"month":mo,"cost_scenario":name,"is_complete_calendar_month":bool(complete),"episodes_closed_in_month":len(evs),
    "strategy_net_return_pct":strat,"cash_return_pct":0.0,"btc_isolated_monthly_buy_hold_net_return_pct":refs["BTCUSDT"],
    "eth_isolated_monthly_buy_hold_net_return_pct":refs["ETHUSDT"],
    "benchmark_status":"VALID_FULL_MONTH" if complete else ("PARTIAL_ARCHIVE_MONTH" if si is not None and ei is not None else "MISSING_MONTH_END_BOUNDARY")})
 return rows

def full_benchmarks(frames):
 out=[]
 for sym,fr in frames.items():
  for name,cost in COSTS.items():
   out.append({"benchmark":sym+"_CONTINUOUS_BUY_HOLD","cost_scenario":name,"start":fr.index[0].isoformat(),"end":fr.index[-1].isoformat(),
               "round_trips":1,"net_return_pct":100*net_return(float(fr.open.iloc[0]),float(fr.open.iloc[-1]),cost)})
 for name in COSTS:
  out.append({"benchmark":"CASH","cost_scenario":name,"start":frames["ETHUSDT"].index[0].isoformat(),"end":frames["ETHUSDT"].index[-1].isoformat(),"round_trips":0,"net_return_pct":0.0})
 return out

def logret(p):
 if p is None or not math.isfinite(float(p)) or float(p)<=-100: raise ValueError("INVALID_RETURN_FOR_LOG")
 return math.log1p(float(p)/100)

def bootstrap(x, block, reps, seed):
 x=np.asarray(x,float)
 if x.ndim!=1 or len(x)<block or not np.isfinite(x).all() or block<1 or reps<100: raise ValueError("INVALID_BOOTSTRAP_SERIES_OR_CONFIGURATION")
 n=len(x); obs=float(x.mean()); nblocks=math.ceil(n/block); offsets=np.arange(block,dtype=np.int64)
 rng,rng0=np.random.default_rng(seed),np.random.default_rng(seed+1); boot=np.empty(reps); nul=np.empty(reps); centered=x-obs
 for start in range(0,reps,256):
  size=min(256,reps-start); starts=rng.integers(0,n,(size,nblocks)); ids=(starts[:,:,None]+offsets[None,None,:])%n
  boot[start:start+size]=x[ids.reshape(size,-1)[:,:n]].mean(axis=1)
  starts0=rng0.integers(0,n,(size,nblocks)); ids0=(starts0[:,:,None]+offsets[None,None,:])%n
  nul[start:start+size]=centered[ids0.reshape(size,-1)[:,:n]].mean(axis=1)
 lo,hi=np.quantile(boot,[.025,.975])
 return {"n_observations":n,"block_length":block,"bootstrap_replicates":reps,"seed":seed,
  "mean_log_excess_per_observation":obs,"percentile_95_ci_low":float(lo),"percentile_95_ci_high":float(hi),
  "one_sided_null_centered_p_value_mean_greater_zero":(1+int(np.count_nonzero(nul>=obs)))/(reps+1)}

def infer(months, events, reps, seed):
 m=pd.DataFrame(months); complete=sorted(m.loc[(m.cost_scenario=="BASE")&m.is_complete_calendar_month,"month"].unique().tolist())
 b=m[(m.cost_scenario=="BASE")&m.month.isin(complete)].set_index("month"); primary=[]
 for mo in complete:
  s=logret(float(b.loc[mo,"strategy_net_return_pct"])); btc=logret(float(b.loc[mo,"btc_isolated_monthly_buy_hold_net_return_pct"]))
  eth=logret(float(b.loc[mo,"eth_isolated_monthly_buy_hold_net_return_pct"])); primary.append(s-max(0.0,btc,eth))
 prim=bootstrap(primary,MONTH_BLOCK,reps,seed)
 prim.update({"endpoint":"monthly strategy BASE log return minus strongest of cash/BTC/ETH monthly references BASE","months_used":complete,
  "primary_hypothesis_supported_by_historical_screen":bool(prim["mean_log_excess_per_observation"]>0 and prim["percentile_95_ci_low"]>0 and prim["one_sided_null_centered_p_value_mean_greater_zero"]<.05),"promotion_eligible":False})
 refs={"CASH":"cash_return_pct","BTC_MONTHLY_BH":"btc_isolated_monthly_buy_hold_net_return_pct","ETH_MONTHLY_BH":"eth_isolated_monthly_buy_hold_net_return_pct"}
 secondary=[]; ci=0
 for scen in COSTS:
  d=m[(m.cost_scenario==scen)&m.month.isin(complete)].set_index("month")
  for bn,col in refs.items():
   diff=[logret(float(d.loc[mo,"strategy_net_return_pct"]))-logret(float(d.loc[mo,col])) for mo in complete]
   val=bootstrap(diff,MONTH_BLOCK,reps,seed+100+ci); ci+=1
   secondary.append({"comparison":"STRATEGY_MINUS_"+bn,"cost_scenario":scen,"months_used":len(complete),**val,
                     "adjusted_for_secondary_test_family":False,"interpretation":"EXPLORATORY_ONLY_NO_WINNER_SELECTION"})
 start=pd.Timestamp(complete[0]+"-01T00:00:00Z"); end=pd.Timestamp(complete[-1]+"-01T00:00:00Z")+pd.offsets.MonthBegin(1)
 days=pd.date_range(start,end-pd.Timedelta(days=1),freq="D"); daily=pd.Series(0.0,index=days)
 for e in events:
  ex=pd.Timestamp(e["exit_time"]).normalize()
  if start<=ex<end and ex in daily.index: daily.loc[ex]+=math.log1p(float(e["net_return_base_pct"])/100)
 db=bootstrap(daily.to_numpy(),DAY_BLOCK,reps,seed+999)
 db.update({"endpoint":"mean daily BASE event net log return; zero on non-event days","daily_series_start":days[0].isoformat(),
  "daily_series_end":days[-1].isoformat(),"events_with_exit_in_series":sum(start<=pd.Timestamp(e["exit_time"])<end for e in events),
  "block_interpretation":"90 consecutive UTC days, circular moving blocks","promotion_eligible":False})
 return {"primary":prim,"secondary_comparisons_exploratory":secondary,"daily_event_series_diagnostic":db,
  "complete_month_count":len(complete),"complete_months":complete,"partial_months_excluded_from_inference":sorted(set(m.month.unique())-set(complete)),
  "bootstrap_note":"Only about 20 full calendar months are available; block-bootstrap inference is approximate and low-power."},secondary

def summarize(events):
 out=[]
 for name in COSTS:
  net=np.asarray([e[f"net_return_{name.lower()}_pct"]/100 for e in events],float); gross=np.asarray([e["gross_return_pct"]/100 for e in events],float)
  mr={}
  for e,r in zip(events,net): mr[e["exit_month"]]=mr.get(e["exit_month"],1.0)*(1+r)
  monthly=[v-1 for v in mr.values()]; eq=np.cumprod(np.r_[1.,1.+net]); dd=eq/np.maximum.accumulate(eq)-1
  out.append({"cost_scenario":name,"accepted_episodes":len(net),"gross_compounded_return_pct":100*(np.prod(1+gross)-1) if len(net) else 0,
   "net_compounded_return_pct":100*(np.prod(1+net)-1) if len(net) else 0,"mean_gross_episode_pct":100*gross.mean() if len(net) else None,
   "mean_net_episode_pct":100*net.mean() if len(net) else None,"median_net_episode_pct":100*np.median(net) if len(net) else None,
   "win_rate_pct":100*np.mean(net>0) if len(net) else None,"positive_months_with_trades":sum(x>0 for x in monthly),
   "months_with_trades":len(monthly),"worst_month_with_trades_pct":100*min(monthly) if monthly else None,
   "max_drawdown_trade_sequence_pct":100*float(dd.min()) if len(dd) else 0})
 return out

def csvout(path, rows): pd.DataFrame(rows).to_csv(path,index=False,float_format="%.12g")

def build_report(info, months, bench, summ, secondary):
 p=info["inference"]["primary"]; d=info["inference"]["daily_event_series_diagnostic"]; md=pd.DataFrame(months)
 mt=["| Month | Episodes exited | Strategy BASE | Cash | BTC monthly BH BASE | ETH monthly BH BASE |","|---|---:|---:|---:|---:|---:|"]
 for _,r in md[md.cost_scenario=="BASE"].iterrows():
  vals=[r.strategy_net_return_pct,r.cash_return_pct,r.btc_isolated_monthly_buy_hold_net_return_pct,r.eth_isolated_monthly_buy_hold_net_return_pct]
  vv=["—" if pd.isna(v) else f"{float(v):+.3f}%" for v in vals]; mark="" if r.is_complete_calendar_month else " *"
  mt.append(f"| {r.month}{mark} | {int(r.episodes_closed_in_month)} | {vv[0]} | {vv[1]} | {vv[2]} | {vv[3]} |")
 st=["| Costs | Episodes | Gross compounded | Net compounded | Mean net | Median net | Win rate | Positive months / active | Worst active month | Max DD |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for r in summ:
  ff=lambda k:"—" if r[k] is None else f"{r[k]:+.3f}%"
  st.append(f"| {r['cost_scenario']} | {r['accepted_episodes']} | {ff('gross_compounded_return_pct')} | {ff('net_compounded_return_pct')} | {ff('mean_net_episode_pct')} | {ff('median_net_episode_pct')} | {ff('win_rate_pct')} | {r['positive_months_with_trades']}/{r['months_with_trades']} | {ff('worst_month_with_trades_pct')} | {ff('max_drawdown_trade_sequence_pct')} |")
 bh=["| Benchmark | Costs | Full-period net return |","|---|---|---:|"]
 for r in bench: bh.append(f"| {r['benchmark']} | {r['cost_scenario']} | {r['net_return_pct']:+.3f}% |")
 bt=["| Comparison | Costs | Mean monthly log excess | 95% block interval | One-sided p |","|---|---|---:|---:|---:|"]
 for r in secondary: bt.append(f"| {r['comparison']} | {r['cost_scenario']} | {r['mean_log_excess_per_observation']:+.6f} | [{r['percentile_95_ci_low']:+.6f}, {r['percentile_95_ci_high']:+.6f}] | {r['one_sided_null_centered_p_value_mean_greater_zero']:.5f} |")
 supported=bool(p["primary_hypothesis_supported_by_historical_screen"])
 return f"""# BTC→ETH independent episodes V1 — results

**Decision: NO_TRADE**  
**Status:** {info['status']}  
**Source code commit:** {info['source_git_sha']}  
**Preregistration SHA-256:** {info['preregistration_sha256']}  
**Dataset logical SHA-256:** {info['dataset_logical_sha256']}  
**Raw dataset SHA-256:** {info['dataset_raw_file_sha256']}  
**Configuration SHA-256:** {info['dataset_configuration_sha256']}

## Frozen hypothesis

At the close of a closed hourly bar: BTC four-hour return at least +1.50%; ETH four-hour return between -0.50% and +0.80%; ETH close above EMA100 calculated through that bar only. Enter ETH at the next hourly open and exit at the open 24 hours later. Skip overlapping positions. Attribute each realized episode return to its UTC exit month. No parameter grid or retuning.

Modeled costs per round trip: BASE = fee 0.10% each side, total spread 10 bps, slippage 5 bps each side; STRESS_1 = 0.10%, 25 bps, 10 bps; STRESS_2 = 0.20%, 50 bps, 20 bps. These are assumed costs, not measured historical fills.

## Primary contrast

- Raw signal bars: **{info['raw_signal_count']}**; accepted episodes: **{info['accepted_episode_count']}**; overlapping signals skipped: **{info['overlapping_signals_skipped']}**.
- Complete months in inference: **{info['inference']['complete_month_count']}**; partial months excluded: {', '.join(info['inference']['partial_months_excluded_from_inference'])}.
- BASE mean monthly log excess versus the best of cash, BTC monthly buy-and-hold and ETH monthly buy-and-hold: **{p['mean_log_excess_per_observation']:+.6f}**; 95% circular 3-month block interval [{p['percentile_95_ci_low']:+.6f}, {p['percentile_95_ci_high']:+.6f}]; one-sided null-centered p={p['one_sided_null_centered_p_value_mean_greater_zero']:.5f}.
- Historical screen: **{'SUPPORTED, descriptive only' if supported else 'NOT SUPPORTED'}**. This retrospective experiment is never eligible to promote a strategy.
- Daily event diagnostic (zero in no-event days): n={d['n_observations']} days, mean={d['mean_log_excess_per_observation']:+.8f}, 95% 90-day block interval [{d['percentile_95_ci_low']:+.8f}, {d['percentile_95_ci_high']:+.8f}], p={d['one_sided_null_centered_p_value_mean_greater_zero']:.5f}.

## Episode-level return summary

{chr(10).join(st)}

## Continuous buy-and-hold over the full archive (one round trip)

{chr(10).join(bh)}

## Month-by-month results

Asterisk means the archive covers only part of that calendar month. Partial months are retained descriptively but excluded from bootstrap inference. Cash is 0%. Monthly BTC/ETH references are separate one-month round trips with the same modeled costs, not continuously held accounts; full-period continuous references are shown above.

{chr(10).join(mt)}

## Secondary paired block-bootstrap comparisons — exploratory, unadjusted for the family of nine

{chr(10).join(bt)}

## Limitations and decision

Bootstrap blocks retain some serial dependence, but only {info['inference']['complete_month_count']} complete monthly units are available. Secondary p-values are not multiplicity-adjusted. The previous diagnostic already exposed this archive to investigation, so this is not a clean untouched holdout. All 30,720 candles have unknown historical available_at and finality; costs are modeled, not observed. Regardless of the statistics, keep NO_TRADE and do not enable PAPER automation or LIVE.
"""

def run(data_path: Path,out: Path,source_sha: str,reps: int,seed: int):
 if out.exists() and any(out.iterdir()): raise ValueError("OUTPUT_DIRECTORY_NOT_EMPTY")
 out.mkdir(parents=True,exist_ok=True)
 bundle,frames,raw_sha=load_data(data_path); events,counts=episodes_for(frames); months=month_rows(events,frames)
 bench=full_benchmarks(frames); inference,secondary=infer(months,events,reps,seed); summ=summarize(events)
 pp=Path("docs/research/BTC_ETH_LEADLAG_EPISODES_V1_PREREGISTRATION.md")
 if not pp.is_file(): raise ValueError("PREREGISTRATION_FILE_MISSING")
 info={"schema_version":1,"study":"BTC_ETH_LEADLAG_EPISODES_V1","status":"EXPLORATORY_HISTORICAL_FIXED_HYPOTHESIS_SCREEN_NOT_PROSPECTIVE_HOLDOUT",
  "decision":"NO_TRADE","source_git_sha":source_sha,"dataset_logical_sha256":bundle["dataset_sha256"],
  "dataset_configuration_sha256":bundle["configuration_sha256"],"dataset_raw_file_sha256":raw_sha,
  "preregistration_sha256":sha256(pp.read_bytes()),"signal_rule":{"conditions":["BTC 4h return >= +1.50%","ETH 4h return -0.50% to +0.80%","ETH close > EMA100"],"entry":"next hourly open proxy","exit":"24h after entry"},
  "costs":COSTS,**counts,"bar_count_per_asset":{s:len(frames[s]) for s in frames},
  "availability_known_per_asset":{s:int(frames[s].available_at.notna().sum()) for s in frames},
  "finality_known_per_asset":{s:int((frames[s].finality.astype(str).str.lower()=="final").sum()) for s in frames},
  "start":frames["ETHUSDT"].index[0].isoformat(),"end":frames["ETHUSDT"].index[-1].isoformat(),
  "python_version":platform.python_version(),"numpy_version":np.__version__,"pandas_version":pd.__version__,
  "bootstrap_replicates":reps,"bootstrap_seed":seed,"bootstrap_block_months":MONTH_BLOCK,"bootstrap_daily_block_days":DAY_BLOCK,
  "inference":inference,"strategy_summary":summ,
  "limitations":["Historical available_at/finality unknown for all archived candles.","Hypothesis chosen after prior diagnostic exposure; not an untouched holdout.","Costs are scenarios, not actual historical fills.","Decision remains NO_TRADE."]}
 (out/"episode-results.json").write_text(json.dumps(info,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
 csvout(out/"episodes.csv",events); csvout(out/"monthly-returns.csv",months); csvout(out/"bootstrap-comparisons.csv",secondary)
 csvout(out/"full-period-benchmarks.csv",bench); csvout(out/"strategy-summary.csv",summ)
 (out/"REPORT.md").write_text(build_report(info,months,bench,summ,secondary),encoding="utf-8")
 filedata={}
 for f in sorted(out.iterdir()):
  if f.is_file(): filedata[f.name]={"size_bytes":f.stat().st_size,"sha256":sha256(f.read_bytes())}
 manifest={"schema_version":1,"study":info["study"],"status":info["status"],"decision":"NO_TRADE",
  "source_git_sha":source_sha,"dataset_logical_sha256":bundle["dataset_sha256"],"dataset_raw_file_sha256":raw_sha,
  "dataset_configuration_sha256":bundle["configuration_sha256"],"preregistration_sha256":info["preregistration_sha256"],
  "bar_count_per_asset":info["bar_count_per_asset"],**counts,"complete_month_count":inference["complete_month_count"],
  "bootstrap_replicates":reps,"files":filedata}
 (out/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
 for n,v in filedata.items():
  b=(out/n).read_bytes()
  if len(b)!=v["size_bytes"] or sha256(b)!=v["sha256"]: raise ValueError("OUTPUT_INTEGRITY_FAILED:"+n)
 return {"info":info,"manifest":manifest}

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--dataset",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True)
 p.add_argument("--run-sha",default=os.environ.get("GITHUB_SHA","LOCAL_UNCOMMITTED"))
 p.add_argument("--bootstrap-replicates",type=int,default=REPLICATES); p.add_argument("--seed",type=int,default=SEED)
 a=p.parse_args()
 if a.bootstrap_replicates<REPLICATES: p.error("frozen protocol requires at least 10000 bootstrap replicates")
 x=run(a.dataset,a.output_dir,a.run_sha,a.bootstrap_replicates,a.seed)
 i=x["info"]; print("RAW_SIGNALS:",i["raw_signal_count"]); print("ACCEPTED_EPISODES:",i["accepted_episode_count"])
 print("OVERLAP_SKIPPED:",i["overlapping_signals_skipped"]); print("COMPLETE_MONTHS:",i["inference"]["complete_month_count"])
 print("PRIMARY_MEAN_LOG_EXCESS:",i["inference"]["primary"]["mean_log_excess_per_observation"])
 print("PRIMARY_BOOTSTRAP_P:",i["inference"]["primary"]["one_sided_null_centered_p_value_mean_greater_zero"])
 print("DECISION: NO_TRADE"); return 0
if __name__=="__main__": raise SystemExit(main())
