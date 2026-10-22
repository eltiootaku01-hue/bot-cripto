"""Historical-only BTCUSDT ML walk-forward; uses official checksummed Binance Spot hourly Klines.

Historical Klines have no historic Spot bid/ask. This script therefore reports a
separately labelled candle-close proxy with assumed spread/slippage/fees, not exact
historic fills or prospective evidence. It never connects to an exchange account.
"""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, io, json, os, re, time, urllib.request, zipfile
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from research import cost_aware_ml_return_filter_v1 as c

BASE="https://data.binance.vision"
SYMBOL="BTCUSDT"; INTERVAL="1h"
START=date(2023,1,1); END_EXCLUSIVE=date(2026,10,22)
CAP=Decimal("100"); ALLOC=Decimal("0.95"); HORIZON=24
STEP=Decimal("0.000001"); MIN_QTY=Decimal("0.00001"); MIN_NOTIONAL=Decimal("5")
COLS=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_base","taker_quote","ignore"]
SHA=re.compile(r"(?i)\b([0-9a-f]{64})\b")
FOLDS=[
 ("2024",pd.Timestamp("2024-01-01T00:00:00Z"),pd.Timestamp("2025-01-01T00:00:00Z")),
 ("2025",pd.Timestamp("2025-01-01T00:00:00Z"),pd.Timestamp("2026-01-01T00:00:00Z")),
 ("2026_YTD",pd.Timestamp("2026-01-01T00:00:00Z"),pd.Timestamp("2026-10-22T00:00:00Z")),
]
class HistoricalCost:
    def __init__(self,name,fee,slippage,spread):
        self.name=name; self.fee=Decimal(fee); self.slippage=Decimal(slippage); self.spread=Decimal(spread)
    @property
    def half_spread(self): return self.spread/Decimal("20000")
    @property
    def slip(self): return self.slippage/Decimal("10000")
    @property
    def friction(self): return self.spread/Decimal("10000")+2*self.fee+2*self.slip
COSTS=[HistoricalCost("BASE","0.001","5","10"),HistoricalCost("STRESS_1","0.001","10","20"),HistoricalCost("STRESS_2","0.002","20","40")]

def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return sha(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode())
def next_month(d): return date(d.year+1,1,1) if d.month==12 else date(d.year,d.month+1,1)
def archive_plan(start,end):
    current=date(end.year,end.month,1); out=[]; m=date(start.year,start.month,1)
    while m<current and m<end:
        p=f"{m.year:04d}-{m.month:02d}"; f=f"{SYMBOL}-{INTERVAL}-{p}.zip"
        out.append((p,f"{BASE}/data/spot/monthly/klines/{SYMBOL}/{INTERVAL}/{f}")); m=next_month(m)
    d=max(start,current)
    while d<end:
        p=d.isoformat(); f=f"{SYMBOL}-{INTERVAL}-{p}.zip"
        out.append((p,f"{BASE}/data/spot/daily/klines/{SYMBOL}/{INTERVAL}/{f}")); d+=timedelta(days=1)
    return out
def download(url, retries=4):
    last=None
    for i in range(retries):
        try:
            q=urllib.request.Request(url,headers={"User-Agent":"bot-cripto-historical-research/1.0"})
            with urllib.request.urlopen(q,timeout=45) as r:
                if r.status!=200: raise RuntimeError(f"HTTP:{r.status}:{url}")
                return r.read()
        except Exception as e:
            last=e
            if i+1<retries: time.sleep(i+1)
    raise RuntimeError(f"DOWNLOAD_FAILED:{url}:{last}") from last
def fetch_archive(period,url):
    b=download(url); checksum_bytes=download(url+".CHECKSUM"); checksum=checksum_bytes.decode().strip(); match=SHA.search(checksum)
    if not match: raise RuntimeError(f"BAD_CHECKSUM_FILE:{period}")
    digest=sha(b)
    if digest!=match.group(1).lower(): raise RuntimeError(f"CHECKSUM_MISMATCH:{period}")
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1: raise RuntimeError(f"CSV_MEMBER_COUNT:{period}:{names}")
        raw=z.read(names[0])
    f=pd.read_csv(io.BytesIO(raw),header=None,names=COLS,dtype=str,keep_default_na=False)
    f=f.loc[f.open_time.astype(str).str.fullmatch(r"\d+")].copy()
    if f.empty: raise RuntimeError(f"EMPTY_KLINES:{period}")
    op=pd.to_numeric(f.open_time,errors="raise").astype("int64")
    unit="us" if int(op.iloc[0])>=100_000_000_000_000 else "ms"
    f["timestamp"]=pd.to_datetime(op,unit=unit,utc=True,errors="raise")
    for col in ("open","high","low","close","volume"): f[col]=pd.to_numeric(f[col],errors="raise").astype("float64")
    frame=f[["timestamp","open","high","low","close","volume"]].sort_values("timestamp").reset_index(drop=True)
    rec={"period":period,"url":url,"zip_sha256":digest,"published_sha256":match.group(1).lower(),"checksum_file_sha256":sha(checksum_bytes),"rows":len(frame)}
    return frame,rec

def load_history(outdir):
    plan=archive_plan(START,END_EXCLUSIVE); frames=[]; sources=[]
    print(f"Planned {len(plan)} Binance Kline archives; checksums required.",flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fs=[pool.submit(fetch_archive,p,u) for p,u in plan]
        for i,future in enumerate(concurrent.futures.as_completed(fs),1):
            frame,rec=future.result(); frames.append(frame); sources.append(rec)
            if i%10==0 or i==len(fs): print(f"Verified archives {i}/{len(fs)}",flush=True)
    bars=pd.concat(frames,ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    if bars.timestamp.duplicated().any(): raise RuntimeError("DUPLICATE_HOURLY_CANDLE")
    expected=pd.date_range(pd.Timestamp(START.isoformat(),tz="UTC"),pd.Timestamp(END_EXCLUSIVE.isoformat(),tz="UTC")-pd.Timedelta(hours=1),freq="h")
    observed=pd.DatetimeIndex(bars.timestamp)
    if not observed.equals(expected):
        missing=expected.difference(observed); extra=observed.difference(expected)
        raise RuntimeError(f"DATA_GAP_OR_EXTRA:rows={len(bars)} expected={len(expected)} missing={len(missing)} extra={len(extra)} examples={[str(x) for x in missing[:5]]}")
    bars=c.validate_candles(bars); sources.sort(key=lambda x:x["period"])
    manifest={"exchange":"Binance Spot public archive","symbol":SYMBOL,"interval":INTERVAL,"rows":len(bars),
      "first_open_utc":bars.timestamp.iloc[0].isoformat(),"last_open_utc":bars.timestamp.iloc[-1].isoformat(),
      "archive_count":len(sources),"archives":sources,"archive_manifest_sha256":canonical(sources),
      "canonical_candle_data_sha256":c.canonical_dataset_sha256(bars),"checksum_policy":"each ZIP compared to published .CHECKSUM"}
    outdir.mkdir(parents=True,exist_ok=True)
    (outdir/"historical-data-manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return bars,manifest

def fill_price(ref,cost,buy):
    friction=cost.half_spread+cost.slip
    return ref*(Decimal("1")+friction if buy else Decimal("1")-friction)
def size_for(cash,close,cost):
    px=fill_price(close,cost,True); qty=(cash*ALLOC/(px*(1+cost.fee))/STEP).to_integral_value(rounding=ROUND_FLOOR)*STEP
    notional=qty*px
    if qty<MIN_QTY or notional<MIN_NOTIONAL or notional*(1+cost.fee)>cash: return Decimal("0"),px,Decimal("0")
    return qty,px,notional*cost.fee
def mark(cash,pos,close,cost):
    if pos is None:return cash
    value=pos["qty"]*fill_price(close,cost,False)
    return cash+value-value*cost.fee
def stats(final,equity,fees,trades,inmarket,total):
    peak=CAP; dd=Decimal("0")
    for x in equity:
        peak=max(peak,x)
        if peak>0: dd=min(dd,(x/peak-1)*100)
    pnl=final-CAP
    return {"final_equity_usdt":str(final.quantize(Decimal("0.00000001"))),"net_pnl_usdt":str(pnl.quantize(Decimal("0.00000001"))),
      "return_pct":str((pnl/CAP*100).quantize(Decimal("0.000001"))),"max_drawdown_pct":str(dd.quantize(Decimal("0.000001"))),
      "total_fees_usdt":str(fees.quantize(Decimal("0.00000001"))),"trades":len(trades),
      "win_rate_pct":str((Decimal(sum(Decimal(t["net_pnl_usdt"])>0 for t in trades))/Decimal(len(trades))*100).quantize(Decimal("0.000001"))) if trades else "0",
      "time_in_market_pct":str((Decimal(inmarket)/Decimal(max(total,1))*100).quantize(Decimal("0.000001"))),"trade_log":trades}
def cash_metrics(hours):
    return {"final_equity_usdt":"100","net_pnl_usdt":"0","return_pct":"0","max_drawdown_pct":"0","total_fees_usdt":"0","trades":0,"win_rate_pct":"0","time_in_market_pct":"0","trade_log":[],"equity_curve_hours":hours}
def replay(bars,predictions,start,end,cost):
    closes=bars.timestamp+pd.Timedelta(hours=1); full=bars.assign(close_time=closes)
    sub=full[(full.close_time>=start)&(full.close_time<=end)].reset_index(drop=True)
    if len(sub)<HORIZON+2 or sub.close_time.iloc[0]!=start or sub.close_time.iloc[-1]!=end: raise RuntimeError("BAD_EVAL_PRICE_WINDOW")
    prices=[Decimal(str(x)) for x in sub.close]; times=pd.DatetimeIndex(sub.close_time)
    signals={pd.Timestamp(x.decision_at):float(x.predicted_return) for x in predictions.itertuples(index=False)}
    cash=CAP; pos=None; trades=[]; fees=Decimal("0"); equity=[]; inmarket=0; last_entry=len(sub)-1-HORIZON
    for i,(stamp,close) in enumerate(zip(times,prices)):
        if pos is not None and i>=pos["exit_i"]:
            sell=fill_price(close,cost,False); amount=pos["qty"]*sell; exit_fee=amount*cost.fee; proceeds=amount-exit_fee
            pnl=proceeds-pos["entry_spend"]; cash+=proceeds; fees+=exit_fee
            trades.append({"entry_at":pos["at"].isoformat(),"exit_at":stamp.isoformat(),"quantity":str(pos["qty"]),
              "entry_price":str(pos["entry_px"]),"exit_price":str(sell),"entry_fee_usdt":str(pos["entry_fee"]),
              "exit_fee_usdt":str(exit_fee),"total_fees_usdt":str(pos["entry_fee"]+exit_fee),
              "net_pnl_usdt":str(pnl.quantize(Decimal("0.00000001"))),"exit_reason":"HORIZON_24H"})
            pos=None
        equity.append(mark(cash,pos,close,cost))
        if pos is not None: inmarket+=1
        pred=signals.get(stamp)
        if pos is not None or pred is None or i>last_entry: continue
        if pred<=float(Decimal("1.5")*cost.friction): continue
        qty,px,fee=size_for(cash,close,cost)
        if qty<=0: continue
        spend=qty*px+fee; cash-=spend; fees+=fee
        pos={"at":stamp,"qty":qty,"entry_px":px,"entry_fee":fee,"entry_spend":spend,"exit_i":i+HORIZON}
    if pos is not None: raise RuntimeError("OPEN_POSITION_AT_END")
    equity[-1]=cash
    result=stats(cash,equity,fees,trades,inmarket,len(sub)); result["equity_curve_hours"]=len(equity); return result
def hold(bars,start,end,cost):
    ct=bars.timestamp+pd.Timedelta(hours=1); sub=bars[(ct>=start)&(ct<=end)].copy(); sub["close_time"]=ct.loc[sub.index]
    sub=sub.sort_values("close_time")
    if sub.empty or sub.close_time.iloc[0]!=start or sub.close_time.iloc[-1]!=end: raise RuntimeError("BAD_HOLD_WINDOW")
    prices=[Decimal(str(x)) for x in sub.close]; qty,px,entry_fee=size_for(CAP,prices[0],cost)
    if qty<=0: raise RuntimeError("BUY_HOLD_BELOW_MIN")
    reserve=CAP-qty*px-entry_fee; fees=entry_fee
    curve=[mark(reserve,{"qty":qty},price,cost) for price in prices]
    sell=fill_price(prices[-1],cost,False); amount=qty*sell; exit_fee=amount*cost.fee; fees+=exit_fee; final=reserve+amount-exit_fee; curve[-1]=final
    trade={"entry_at":start.isoformat(),"exit_at":end.isoformat(),"quantity":str(qty),"entry_price":str(px),"exit_price":str(sell),
       "entry_fee_usdt":str(entry_fee),"exit_fee_usdt":str(exit_fee),"total_fees_usdt":str(entry_fee+exit_fee),
       "net_pnl_usdt":str((final-CAP).quantize(Decimal("0.00000001"))),"exit_reason":"WINDOW_END"}
    return stats(final,curve,fees,[trade],len(curve),len(curve))
def train(bars,cutoff):
    trainbars=bars[bars.timestamp<cutoff].copy().reset_index(drop=True)
    if trainbars.empty or trainbars.timestamp.iloc[-1]+pd.Timedelta(hours=1)>cutoff: raise RuntimeError("TRAIN_CUTOFF_VIOLATION")
    ds=c.build_training_set(trainbars,cutoff); model=c.make_regressor(); model.fit(ds.features,ds.target)
    return model,{"cutoff_utc":cutoff.isoformat(),"first_training_candle":trainbars.timestamp.iloc[0].isoformat(),
      "last_training_candle":trainbars.timestamp.iloc[-1].isoformat(),"training_candles":len(trainbars),
      "mature_rows":len(ds.features),"training_rows_sha256":c._training_rows_sha256(ds),
      "training_data_sha256":c.canonical_dataset_sha256(trainbars),"feature_schema_sha256":c.FEATURE_SCHEMA_SHA256}
def predict(bars,model,start,end):
    ft=c.compute_features(bars); ready=c._feature_ready_mask(ft); times=ft.timestamp+pd.Timedelta(hours=1)
    use=ready&(times>=start).to_numpy()&(times+pd.Timedelta(hours=HORIZON)<=end).to_numpy()
    x=ft.loc[use,list(c.FEATURE_COLUMNS)].reset_index(drop=True); decisions=times.loc[use].reset_index(drop=True)
    values=np.asarray(model.predict(x),dtype=float)
    if values.shape!=(len(x),) or not np.isfinite(values).all(): raise RuntimeError("BAD_PREDICTIONS")
    return pd.DataFrame({"decision_at":decisions,"predicted_return":values}),len(x)
def row(fold,scenario,method,m):
    return {"fold":fold,"scenario":scenario,"method":method,**{k:m[k] for k in ("final_equity_usdt","net_pnl_usdt","return_pct","max_drawdown_pct","total_fees_usdt","trades","win_rate_pct","time_in_market_pct")}}
def report(summary):
    lines=["# Historical BTCUSDT contrast: cost-aware ML return filter","",
      f"Generated UTC: {summary['generated_at_utc']}",f"Source commit: {summary['source_commit_sha']}",
      f"Market rows: {summary['market_data']['rows']} | archives checksum-verified: {summary['market_data']['archive_count']}",
      f"First/last candle open: {summary['market_data']['first_open_utc']} / {summary['market_data']['last_open_utc']}",
      f"Canonical data SHA-256: {summary['market_data']['canonical_candle_data_sha256']}",
      f"Archive manifest SHA-256: {summary['market_data']['archive_manifest_sha256']}","",
      "Walk-forward folds: train 2023/test 2024; train through 2024/test 2025; train through 2025/test 2026-01-01 to 2026-10-22 00:00 UTC. Each model is fit once before its test fold and held fixed within that fold.",
      "Execution is a historical candle-close proxy, not historical Spot bid/ask. Assumed spreads are explicit inputs, not measured quote evidence.","",
      "## Fixed cost assumptions","","| Scenario | Fee per side | Slippage per side | Assumed full spread | Round-trip friction | Entry threshold |","|---|---:|---:|---:|---:|---:|"]
    for x in COSTS:
        bps=x.friction*10000; lines.append(f"| {x.name} | {x.fee*100:.3f}% | {x.slippage} bp | {x.spread} bp | {bps:.2f} bp | {bps*Decimal('1.5'):.2f} bp |")
    lines += ["","## Results on 100 USDT initial capital","","| Fold | Scenario | Method | Final USDT | Net PnL | Return % | Max DD % | Fees | Trades | Win % | Time in market % |","|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summary["results"]:
        lines.append("| "+" | ".join(str(r[k]) for k in ("fold","scenario","method","final_equity_usdt","net_pnl_usdt","return_pct","max_drawdown_pct","total_fees_usdt","trades","win_rate_pct","time_in_market_pct"))+" |")
    lines += ["","## Gate versus cash, buy-and-hold, +50 USDT and drawdown",""]
    for g in summary["gates"]:
        lines.append(f"- {g['fold']} / {g['scenario']}: beat cash={g['beats_cash']}; beat BTC buy-and-hold={g['beats_buy_hold']}; net PnL at least +50={g['target_plus_50']}; drawdown limit={g['drawdown_ok']}; combined={g['combined_pass']}.")
    lines += ["","## Limits","",
      "The 2026 YTD fold is shorter than 365 days. No historic Spot bookTicker prices are available in this Kline dataset, so fixed spread/slippage is an assumption. A pass would justify more validation, not prove future profit. Operational authorization remains NO_TRADE.",""]
    return "\n".join(lines)
def run(outdir):
    runtime=c.require_pinned_runtime(); bars,manifest=load_history(outdir); rows=[]; folds=[]; gates=[]
    for name,start,end in FOLDS:
        print(f"Training fold {name}; cutoff={start.isoformat()} end={end.isoformat()}",flush=True)
        model,trainmeta=train(bars,start); preds,n=predict(bars,model,start,end)
        details={"fold":name,"start_utc":start.isoformat(),"end_utc":end.isoformat(),"prediction_rows":n,"training":trainmeta,"scenario_results":{}}
        cash=cash_metrics(int((end-start)/pd.Timedelta(hours=1))+1)
        for cost in COSTS:
            strat=replay(bars,preds,start,end,cost); bh=hold(bars,start,end,cost)
            rows += [row(name,cost.name,"COST_AWARE_ML_PROXY",strat),row(name,cost.name,"BTC_BUY_AND_HOLD",bh),row(name,cost.name,"CASH",cash)]
            limit=Decimal("20") if cost.name=="BASE" else Decimal("25")
            gate={"fold":name,"scenario":cost.name,"beats_cash":Decimal(strat["net_pnl_usdt"])>0,
              "beats_buy_hold":Decimal(strat["final_equity_usdt"])>Decimal(bh["final_equity_usdt"]),
              "target_plus_50":Decimal(strat["net_pnl_usdt"])>=50,"drawdown_ok":Decimal(strat["max_drawdown_pct"])>=-limit}
            gate["combined_pass"]=gate["beats_cash"] and gate["beats_buy_hold"] and gate["target_plus_50"] and gate["drawdown_ok"]; gates.append(gate)
            details["scenario_results"][cost.name]={"candidate":{k:v for k,v in strat.items() if k!="trade_log"},"buy_and_hold":{k:v for k,v in bh.items() if k!="trade_log"},"cash":cash}
        folds.append(details)
    modsha=sha(Path(c.__file__).read_bytes())
    summary={"schema":"cost-aware-ml-historical-contrast-v1","generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "source_commit_sha":os.environ.get("GITHUB_SHA","UNKNOWN"),"candidate_module_sha256":modsha,"runtime":runtime,
      "market_data":{"symbol":SYMBOL,"interval":INTERVAL,"rows":len(bars),"first_open_utc":bars.timestamp.iloc[0].isoformat(),
        "last_open_utc":bars.timestamp.iloc[-1].isoformat(),"archive_count":manifest["archive_count"],
        "archive_manifest_sha256":manifest["archive_manifest_sha256"],"canonical_candle_data_sha256":manifest["canonical_candle_data_sha256"],"source_manifest":manifest},
      "method":{"capital_usdt":str(CAP),"horizon_hours":HORIZON,"parameters":c.FIXED_MODEL_PARAMETERS,"allocation":str(ALLOC),
        "entry_gate":"predicted gross return > 1.5 * (assumed full spread + 2*fee + 2*slippage)",
        "execution":"hourly candle-close proxy; not actual quote fills",
        "cost_scenarios":[{"name":x.name,"fee_per_side":str(x.fee),"slippage_bps":str(x.slippage),"assumed_spread_bps":str(x.spread),"friction_bps":str(x.friction*10000)} for x in COSTS]},
      "folds":folds,"results":rows,"gates":gates,
      "overall_historical_gate":"PASS" if gates and all(g["combined_pass"] for g in gates) else "FAIL",
      "operational_authorization":"NO_TRADE"}
    rendered=report(summary)
    (outdir/"historical-contrast-report.md").write_text(rendered,encoding="utf-8")
    (outdir/"historical-contrast-summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True,default=str)+"\n",encoding="utf-8")
    (outdir/"historical-contrast-results.csv").write_text(pd.DataFrame(rows).to_csv(index=False),encoding="utf-8")
    print("HISTORICAL_REPORT_BEGIN",flush=True); print(rendered,flush=True); print("HISTORICAL_REPORT_END",flush=True)
    print("HISTORICAL_SUMMARY_JSON_BEGIN",flush=True)
    print(json.dumps({"source_commit_sha":summary["source_commit_sha"],"runtime":runtime,"market_data":{k:v for k,v in summary["market_data"].items() if k!="source_manifest"},"overall_historical_gate":summary["overall_historical_gate"],"results":rows,"gates":gates,"operational_authorization":"NO_TRADE"},indent=2,sort_keys=True,default=str),flush=True)
    print("HISTORICAL_SUMMARY_JSON_END",flush=True)
    return summary
def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--output-dir",type=Path,default=Path("historical-output")); p.add_argument("--end-date-exclusive",default=END_EXCLUSIVE.isoformat()); a=p.parse_args()
    if a.end_date_exclusive!=END_EXCLUSIVE.isoformat(): raise RuntimeError("END_DATE_IS_PREREGISTERED:"+END_EXCLUSIVE.isoformat())
    s=run(a.output_dir); print(f"Historical gate: {s['overall_historical_gate']}; authorization: NO_TRADE",flush=True); return 0
if __name__=="__main__": raise SystemExit(main())
