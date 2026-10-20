from __future__ import annotations
import argparse, hashlib, itertools, json, math, platform
from collections import deque
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

EXPECTED_DATASET_SHA256 = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
EXPECTED_CONFIG_SHA256 = "2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a"
EXPECTED_RAW_SHA256 = "3851a560aafbaa44f7d57081bd12f7dfdae070c2abff3d65e122ad8fcbd6df36"
INITIAL_USDT = 31.0
FRACTION = 0.95
MAX_ENTRIES_30D = 5
COSTS = {
    "BASE": {"fee": 0.001, "spread_bps": 10.0, "slippage_bps": 5.0},
    "STRESS_1": {"fee": 0.001, "spread_bps": 25.0, "slippage_bps": 10.0},
    "STRESS_2": {"fee": 0.002, "spread_bps": 50.0, "slippage_bps": 20.0},
}
GRID = {
    "entry": (20, 30, 40, 55, 80, 120),
    "exit": (10, 15, 20, 30, 40),
    "ema": (100, 150, 200, 250),
    "slope": (3, 5, 10),
    "atr_stop": (2.0, 3.0, 4.0, 5.0),
    "adx_min": (0.0, 20.0),
}
GRID_COUNT = math.prod(len(v) for v in GRID.values()) # 2,880 fixed configurations
TRAIN_DAYS = 180
OOS_START = pd.Timestamp("2026-01-01T00:00:00Z")
OOS_END = pd.Timestamp("2026-10-18T00:00:00Z")

class WalkForwardError(ValueError): pass

def sha256(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def canonical_sha256(v: Any) -> str:
    return sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
def json_default(v: Any):
    if isinstance(v, np.generic): return v.item()
    if isinstance(v, pd.Timestamp): return v.isoformat()
    raise TypeError(f"Not JSON serializable: {type(v).__name__}")

def load_dataset(path: Path):
    raw = path.read_bytes(); raw_hash = sha256(raw)
    if raw_hash != EXPECTED_RAW_SHA256: raise WalkForwardError("PINNED_RAW_DATASET_SHA256_MISMATCH")
    bundle = json.loads(raw)
    if bundle.get("dataset_sha256") != EXPECTED_DATASET_SHA256: raise WalkForwardError("PINNED_DATASET_SHA256_MISMATCH")
    if bundle.get("configuration_sha256") != EXPECTED_CONFIG_SHA256: raise WalkForwardError("PINNED_CONFIG_SHA256_MISMATCH")
    if canonical_sha256(bundle.get("data")) != EXPECTED_DATASET_SHA256: raise WalkForwardError("DATA_HASH_RECOMPUTE_FAILED")
    if canonical_sha256(bundle.get("configuration")) != EXPECTED_CONFIG_SHA256: raise WalkForwardError("CONFIG_HASH_RECOMPUTE_FAILED")
    frames={}; missing={}; unknown={}
    for sym in ("BTCUSDT", "ETHUSDT"):
        records=bundle["data"]["history"][sym]
        rows=[]
        for it in records:
            c=it["candle"]
            rows.append({"timestamp":c["start"], **{k:c[k] for k in ("open","high","low","close","volume")},
                "available_at":it.get("available_at"),"finality":c.get("finality"),"candle_state":c.get("candle_state"),"completeness":c.get("completeness")})
        d=pd.DataFrame(rows); d["timestamp"]=pd.to_datetime(d.timestamp,utc=True,errors="raise")
        for col in ("open","high","low","close","volume"): d[col]=pd.to_numeric(d[col],errors="raise")
        if len(d)!=15360 or not d.timestamp.is_unique or not d.timestamp.is_monotonic_increasing: raise WalkForwardError(f"INVALID_CHRONOLOGY:{sym}")
        if not (d.timestamp.diff().dropna()==pd.Timedelta(hours=1)).all(): raise WalkForwardError(f"HOURLY_GAP:{sym}")
        if not (d[["open","high","low","close"]]>0).all().all(): raise WalkForwardError(f"INVALID_OHLC:{sym}")
        if not (d.high>=d[["open","close","low"]].max(axis=1)).all(): raise WalkForwardError(f"INVALID_HIGH:{sym}")
        if not (d.low<=d[["open","close","high"]].min(axis=1)).all(): raise WalkForwardError(f"INVALID_LOW:{sym}")
        if not (d.candle_state=="CLOSED").all() or not (d.completeness=="COMPLETE").all(): raise WalkForwardError(f"CANDLE_STATE_CONTRACT_FAILED:{sym}")
        missing[sym]=int(d.available_at.isna().sum()); unknown[sym]=int((d.finality.astype(str).str.upper()!="FINAL").sum())
        frames[sym]=d.set_index("timestamp",drop=False)
    if not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index): raise WalkForwardError("UNALIGNED_SYMBOLS")
    return bundle,frames,raw_hash,missing,unknown

def aggregate_4h(frame: pd.DataFrame)->pd.DataFrame:
    rows=[]
    for bucket,g in frame.groupby(frame.index.floor("4h"),sort=True):
        if len(g)!=4: continue
        expected=pd.date_range(bucket,periods=4,freq="h")
        if not g.index.equals(expected): continue
        rows.append({"timestamp":bucket,"open":float(g.open.iloc[0]),"high":float(g.high.max()),"low":float(g.low.min()),"close":float(g.close.iloc[-1]),"volume":float(g.volume.sum())})
    d=pd.DataFrame(rows)
    if d.empty: raise WalkForwardError("NO_COMPLETE_4H_BARS")
    d["timestamp"]=pd.to_datetime(d.timestamp,utc=True); d=d.set_index("timestamp",drop=False)
    if not (d.index.to_series().diff().dropna()==pd.Timedelta(hours=4)).all(): raise WalkForwardError("GAP_IN_4H_BARS")
    return d

def indicators(frame:pd.DataFrame)->dict[str,Any]:
    close=frame.close.to_numpy(float); high=frame.high.to_numpy(float); low=frame.low.to_numpy(float)
    prev=np.r_[np.nan,close[:-1]]
    tr=np.nanmax(np.vstack([high-low,np.abs(high-prev),np.abs(low-prev)]),axis=0)
    tr[0]=high[0]-low[0]
    atr=pd.Series(tr,index=frame.index).rolling(14,min_periods=14).mean().to_numpy(float)
    em={p:frame.close.ewm(span=p,adjust=False,min_periods=p).mean().to_numpy(float) for p in GRID["ema"]}
    entry={p:frame.high.shift(1).rolling(p,min_periods=p).max().to_numpy(float) for p in GRID["entry"]}
    exit_={p:frame.low.shift(1).rolling(p,min_periods=p).min().to_numpy(float) for p in GRID["exit"]}
    up=np.r_[np.nan,np.diff(high)]; down=np.r_[np.nan,-np.diff(low)]
    plus_dm=np.where((up>down)&(up>0),up,0.0); minus_dm=np.where((down>up)&(down>0),down,0.0)
    atr_s=pd.Series(tr,index=frame.index).ewm(alpha=1/14,adjust=False,min_periods=14).mean().to_numpy(float)
    plus_s=pd.Series(plus_dm,index=frame.index).ewm(alpha=1/14,adjust=False,min_periods=14).mean().to_numpy(float)
    minus_s=pd.Series(minus_dm,index=frame.index).ewm(alpha=1/14,adjust=False,min_periods=14).mean().to_numpy(float)
    with np.errstate(divide="ignore",invalid="ignore"):
        plus_di=100*plus_s/atr_s; minus_di=100*minus_s/atr_s
        dx=100*np.abs(plus_di-minus_di)/(plus_di+minus_di)
    dx=np.where(np.isfinite(dx),dx,np.nan)
    adx=pd.Series(dx,index=frame.index).ewm(alpha=1/14,adjust=False,min_periods=14).mean().to_numpy(float)
    return {"frame":frame,"open":frame.open.to_numpy(float),"high":high,"low":low,"close":close,"atr":atr,
        "ema":em,"entry_high":entry,"exit_low":exit_,"adx":adx}

def make_grid()->list[tuple[int,int,int,int,float,float]]:
    out=list(itertools.product(GRID["entry"],GRID["exit"],GRID["ema"],GRID["slope"],GRID["atr_stop"],GRID["adx_min"]))
    if len(out)!=2880 or len(set(out))!=2880: raise WalkForwardError("GRID_CARDINALITY_INVARIANT_FAILED")
    return out

def make_signal_cache(feat:dict[str,dict[str,Any]])->dict[tuple[str,int,int,int,float],np.ndarray]:
    cache={}
    for sym,f in feat.items():
        close=f["close"]; atr=f["atr"]; adx=f["adx"]
        for entry,ema,slope,adx_min in itertools.product(GRID["entry"],GRID["ema"],GRID["slope"],GRID["adx_min"]):
            ema_v=f["ema"][ema]; previous=np.r_[np.full(slope,np.nan),ema_v[:-slope]]
            mask=(close>f["entry_high"][entry])&(close>ema_v)&(ema_v>previous)&np.isfinite(atr)&np.isfinite(adx)
            if adx_min>0: mask &= adx>=adx_min
            cache[(sym,entry,ema,slope,adx_min)]=np.asarray(mask,dtype=bool)
    return cache

def adverse_price(ref:float,cost:dict[str,float],side:str)->tuple[float,float]:
    if not math.isfinite(ref) or ref<=0: raise WalkForwardError("INVALID_REFERENCE_PRICE")
    adverse=(cost["spread_bps"]/2+cost["slippage_bps"])/10000
    if side=="BUY": return ref*(1+adverse),cost["fee"]
    if side=="SELL": return ref*(1-adverse),cost["fee"]
    raise WalkForwardError("INVALID_SIDE")

def stop_reference_fill(open_price:float,low_price:float,stop_price:float)->float|None:
    if not all(math.isfinite(x) and x>0 for x in (open_price,low_price,stop_price)): raise WalkForwardError("INVALID_STOP_INPUT")
    if open_price<=stop_price: return open_price
    if low_price<=stop_price: return stop_price
    return None

def simulate_slice(feat:dict[str,dict[str,Any]], cache, candidate, start:int,end:int, scenarios=("BASE","STRESS_1"), collect=False):
    entry_w,exit_w,ema_p,slope,stop_mult,adx_min=candidate
    idx=feat["BTCUSDT"]["frame"].index
    cash={s:INITIAL_USDT for s in scenarios}; pos=None; pending=False; entry_times=deque(); trades=[]
    eq={s:[] for s in scenarios}; rejects={"while_open":0,"rolling_cap":0,"overlap_signal":0}
    for i in range(start,end):
        exited=False
        if pos is not None and pending:
            exit_ref=float(feat[pos["symbol"]]["open"][i]); old=pos
            for scen in scenarios:
                px,fee=adverse_price(exit_ref,COSTS[scen],"SELL"); cash[scen]+=old["qty"][scen]*px*(1-fee)
            trades.append({"symbol":old["symbol"],"signal_time":old["signal_time"].isoformat(),"entry_time":old["entry_time"].isoformat(),"exit_time":idx[i].isoformat(),
                "entry_ref":old["entry_ref"],"exit_ref":exit_ref,"stop_ref":old["stop_ref"],"exit_reason":"DONCHIAN_NEXT_OPEN","candidate":old["candidate"]})
            pos=None; pending=False; exited=True
        if i>start:
            candidates=[]; j=i-1
            for sym in ("BTCUSDT","ETHUSDT"):
                f=feat[sym]; key=(sym,entry_w,ema_p,slope,adx_min)
                if cache[key][j] and math.isfinite(f["atr"][j]) and f["atr"][j]>0:
                    strength=(f["close"][j]-f["entry_high"][entry_w][j])/f["atr"][j]
                    candidates.append((float(strength),sym))
        else: candidates=[]
        if pos is not None and candidates: rejects["while_open"]+=len(candidates)
        elif exited and candidates: rejects["overlap_signal"]+=len(candidates)
        elif pos is None and not exited and candidates:
            candidates.sort(key=lambda z:(-z[0],z[1])); strength,sym=candidates[0]
            while entry_times and idx[i]-entry_times[0]>pd.Timedelta(days=30): entry_times.popleft()
            if len(entry_times)>=MAX_ENTRIES_30D: rejects["rolling_cap"]+=len(candidates)
            else:
                reference=float(feat[sym]["open"][i]); quantities={}
                for scen in scenarios:
                    buy,fee=adverse_price(reference,COSTS[scen],"BUY"); budget=cash[scen]*FRACTION
                    quantities[scen]=budget/(buy*(1+fee)); cash[scen]-=budget
                stop=reference-stop_mult*float(feat[sym]["atr"][j])
                if stop<=0: raise WalkForwardError("NONPOSITIVE_STOP")
                pos={"symbol":sym,"entry_time":idx[i],"signal_time":idx[j]+pd.Timedelta(hours=4),"entry_ref":reference,"stop_ref":stop,
                    "qty":quantities,"candidate":candidate,"exit_w":exit_w,"atr_signal":float(feat[sym]["atr"][j])}
                entry_times.append(idx[i])
        if pos is not None:
            op=float(feat[pos["symbol"]]["open"][i]); lo=float(feat[pos["symbol"]]["low"][i]); stop=float(pos["stop_ref"])
            stop_ref=stop_reference_fill(op,lo,stop)
            if stop_ref is not None:
                old=pos
                for scen in scenarios:
                    px,fee=adverse_price(float(stop_ref),COSTS[scen],"SELL"); cash[scen]+=old["qty"][scen]*px*(1-fee)
                trades.append({"symbol":old["symbol"],"signal_time":old["signal_time"].isoformat(),"entry_time":old["entry_time"].isoformat(),
                    "exit_time":(idx[i]+pd.Timedelta(hours=4)).isoformat(),"entry_ref":old["entry_ref"],"exit_ref":float(stop_ref),"stop_ref":stop,
                    "exit_reason":"ATR_STOP_GAP_OPEN" if op<=stop else "ATR_STOP","candidate":old["candidate"]})
                pos=None; pending=False
            else: pending=bool(feat[pos["symbol"]]["close"][i]<feat[pos["symbol"]]["exit_low"][exit_w][i])
        for scen in scenarios:
            value=cash[scen]
            if pos is not None:
                px,fee=adverse_price(float(feat[pos["symbol"]]["close"][i]),COSTS[scen],"SELL")
                value+=pos["qty"][scen]*px*(1-fee)
            if not math.isfinite(value) or value<0: raise WalkForwardError("INVALID_EQUITY")
            eq[scen].append((idx[i]+pd.Timedelta(hours=4),value))
    if pos is not None:
        ref=float(feat[pos["symbol"]]["close"][end-1]); timestamp=idx[end-1]+pd.Timedelta(hours=4); old=pos
        for scen in scenarios:
            px,fee=adverse_price(ref,COSTS[scen],"SELL"); cash[scen]+=old["qty"][scen]*px*(1-fee)
        trades.append({"symbol":old["symbol"],"signal_time":old["signal_time"].isoformat(),"entry_time":old["entry_time"].isoformat(),"exit_time":timestamp.isoformat(),
            "entry_ref":old["entry_ref"],"exit_ref":ref,"stop_ref":old["stop_ref"],"exit_reason":"WINDOW_END_LIQUIDATION","candidate":old["candidate"]})
        for scen in scenarios: eq[scen][-1]=(eq[scen][-1][0],cash[scen])
    metrics={}
    for scen in scenarios:
        vals=np.asarray([v for _,v in eq[scen]],float); peak=np.maximum.accumulate(vals); dd=100*(vals/peak-1)
        metrics[scen]={"final_usdt":float(cash[scen]),"return_pct":100*(float(cash[scen])/INITIAL_USDT-1),"max_drawdown_pct":float(np.min(dd)) if len(dd) else 0.0,
            "entries":sum(1 for t in trades if t["exit_reason"]!="WINDOW_END_LIQUIDATION")}
    return ({"metrics":metrics,"trades":trades,"equity":eq,"rejected":rejects} if collect else (metrics,trades))

def fast_screen_candidate(feat,cache,candidate,start:int,end:int):
    """Vectorized fixed-24h proxy used only to shortlist; finalists pass full stop/channel replay."""
    ew,xw,ep,sl,sm,adx_min=candidate; idx=feat["BTCUSDT"]["frame"].index
    last=min(end-25,len(idx)-25)
    if last<=start: return {"rank":None,"trades":0,"screen_status":"NO_SIGNAL_IN_TRAIN_WINDOW"}
    bm=cache[("BTCUSDT",ew,ep,sl,adx_min)][start:last]; em=cache[("ETHUSDT",ew,ep,sl,adx_min)][start:last]
    bf,ef=feat["BTCUSDT"],feat["ETHUSDT"]
    with np.errstate(divide="ignore",invalid="ignore"):
        bs=(bf["close"][start:last]-bf["entry_high"][ew][start:last])/bf["atr"][start:last]
        es=(ef["close"][start:last]-ef["entry_high"][ew][start:last])/ef["atr"][start:last]
    valid=bm|em
    if not np.any(valid): return {"rank":None,"trades":0,"screen_status":"NO_SIGNAL_IN_TRAIN_WINDOW"}
    choose_eth=em & (~bm | (es>bs)); positions=np.flatnonzero(valid)+start; accepted=[]; next_allowed=start
    for signal_i in positions:
        if signal_i<next_allowed or signal_i+25>=end: continue
        sym="ETHUSDT" if choose_eth[signal_i-start] else "BTCUSDT"; f=feat[sym]
        accepted.append((sym,signal_i,float(f["open"][signal_i+1]),float(f["open"][signal_i+25])))
        next_allowed=signal_i+25
    if len(accepted)<3: return {"rank":None,"trades":len(accepted),"screen_status":"TOO_FEW_NONOVERLAPPING_TRADES"}
    returns={}; ddvals={}
    for scen in ("BASE","STRESS_1"):
        cash=INITIAL_USDT; peak=INITIAL_USDT; maxdd=0.0
        for sym,signal_i,entry_ref,exit_ref in accepted:
            buy,fb=adverse_price(entry_ref,COSTS[scen],"BUY"); qty=cash*FRACTION/(buy*(1+fb)); cash*=1-FRACTION
            sell,fs=adverse_price(exit_ref,COSTS[scen],"SELL"); cash+=qty*sell*(1-fs)
            peak=max(peak,cash); maxdd=min(maxdd,100*(cash/peak-1))
        returns[scen]=100*(cash/INITIAL_USDT-1); ddvals[scen]=maxdd
    if returns["BASE"]<=0 or returns["STRESS_1"]<=0 or ddvals["BASE"]<-25 or ddvals["STRESS_1"]<-25:
        return {"rank":None,"trades":len(accepted),"screen_BASE":returns["BASE"],"screen_STRESS_1":returns["STRESS_1"],
            "screen_DD_BASE":ddvals["BASE"],"screen_DD_STRESS_1":ddvals["STRESS_1"],"screen_status":"FAILED_FAST_SCREEN_ECONOMIC_GATE"}
    rank=(min(returns["BASE"],returns["STRESS_1"]),min(ddvals["BASE"],ddvals["STRESS_1"]),-sm,-ew,-xw)
    return {"rank":rank,"trades":len(accepted),"screen_BASE":returns["BASE"],"screen_STRESS_1":returns["STRESS_1"],
        "screen_DD_BASE":ddvals["BASE"],"screen_DD_STRESS_1":ddvals["STRESS_1"],"screen_status":"PASSED_FAST_SCREEN"}

def monthly_selection(month_starts, idx, feat, cache, grid):
    selections=[]; audit=[]; finalists_per_fold=80
    for month_start in month_starts:
        boundary=int(idx.searchsorted(month_start,side="left"))
        if boundary<=0: continue
        start=int(idx.searchsorted(month_start-pd.Timedelta(days=TRAIN_DAYS),side="left")); end=boundary; mon=str(month_start.strftime("%Y-%m"))
        if end-start<500:
            selections.append({"month":mon,"status":"NO_TRADE_INSUFFICIENT_TRAINING","candidate":None,"train_start":idx[start].isoformat(),
                "train_end_exclusive":month_start.isoformat(),"configs_screened":0,"exact_replays":0})
            continue
        screened=[]; per_candidate={}
        for candidate in grid:
            sm=fast_screen_candidate(feat,cache,candidate,start,end)
            row={"month":mon,"entry_window":candidate[0],"exit_window":candidate[1],"ema_period":candidate[2],"ema_slope_lag":candidate[3],
                "atr_stop_multiple":candidate[4],"adx_min":candidate[5],"train_start":idx[start].isoformat(),"train_end_exclusive":month_start.isoformat(),
                "fast_screen_trades":sm.get("trades",0),"fast_screen_status":sm.get("screen_status","UNKNOWN"),
                "fast_screen_BASE_return_pct":sm.get("screen_BASE"),"fast_screen_STRESS_1_return_pct":sm.get("screen_STRESS_1"),
                "fast_screen_BASE_max_drawdown_pct":sm.get("screen_DD_BASE"),"fast_screen_STRESS_1_max_drawdown_pct":sm.get("screen_DD_STRESS_1"),
                "exact_replay_performed":False,"exact_eligible":False,"selected_for_next_month":False}
            audit.append(row); per_candidate[candidate]=row
            if sm.get("rank") is not None: screened.append((sm["rank"],candidate,sm))
        screened.sort(key=lambda x:x[0],reverse=True); finalists=screened[:finalists_per_fold]
        best=None; eligible_count=0
        for srank,candidate,screen_metrics in finalists:
            m,trades=simulate_slice(feat,cache,candidate,start,end,scenarios=("BASE","STRESS_1"),collect=False)
            n=sum(1 for t in trades if t["exit_reason"]!="WINDOW_END_LIQUIDATION"); base,stress=m["BASE"],m["STRESS_1"]
            exact_ok=n>=3 and base["return_pct"]>0 and stress["return_pct"]>0 and base["max_drawdown_pct"]>=-25 and stress["max_drawdown_pct"]>=-25
            row=per_candidate[candidate]
            row.update({"exact_replay_performed":True,"exact_training_trades":n,"exact_BASE_return_pct":base["return_pct"],"exact_STRESS_1_return_pct":stress["return_pct"],
                "exact_BASE_max_drawdown_pct":base["max_drawdown_pct"],"exact_STRESS_1_max_drawdown_pct":stress["max_drawdown_pct"],"exact_eligible":bool(exact_ok),
                "exact_rejection_reason":"" if exact_ok else ("FEWER_THAN_3_TRADES" if n<3 else "NONPOSITIVE_BASE_OR_STRESS1" if base["return_pct"]<=0 or stress["return_pct"]<=0 else "DRAWDOWN_WORSE_THAN_MINUS_25")})
            if not exact_ok: continue
            eligible_count+=1; rank=(min(base["return_pct"],stress["return_pct"]),min(base["max_drawdown_pct"],stress["max_drawdown_pct"]),-candidate[4],-candidate[0],-candidate[1])
            if best is None or rank>best["rank"]: best={"rank":rank,"candidate":candidate,"train_metrics":m,"trade_count":n,"screen_metrics":screen_metrics}
        if best is None:
            selections.append({"month":mon,"status":"NO_TRADE_NO_TRAINING_CANDIDATE_PASSED_GATES","candidate":None,"train_start":idx[start].isoformat(),
                "train_end_exclusive":month_start.isoformat(),"configs_screened":len(grid),"screen_passed_candidates":len(screened),"exact_replays":len(finalists),"eligible_candidates":0})
        else:
            cfg=best["candidate"]; base=best["train_metrics"]["BASE"]; stress=best["train_metrics"]["STRESS_1"]; per_candidate[cfg]["selected_for_next_month"]=True
            selections.append({"month":mon,"status":"SELECTED_FROM_PAST_ONLY","candidate":cfg,"train_start":idx[start].isoformat(),"train_end_exclusive":month_start.isoformat(),
                "configs_screened":len(grid),"screen_passed_candidates":len(screened),"exact_replays":len(finalists),"eligible_candidates":eligible_count,"training_trades":best["trade_count"],
                "training_BASE_return_pct":base["return_pct"],"training_STRESS_1_return_pct":stress["return_pct"],"training_BASE_max_drawdown_pct":base["max_drawdown_pct"],
                "training_STRESS_1_max_drawdown_pct":stress["max_drawdown_pct"],"selection_score_min_BASE_STRESS1_pct":best["rank"][0],
                "fast_screen_BASE_return_pct":best["screen_metrics"]["screen_BASE"],"fast_screen_STRESS_1_return_pct":best["screen_metrics"]["screen_STRESS_1"]})
    return selections,audit

def simulate_oos(feat,cache,idx,grid_by_month,start,end):
    cash={s:INITIAL_USDT for s in COSTS}; pos=None; pending=False; entry_times=deque()
    rejected={"while_open":0,"rolling_cap":0,"overlap_signal":0}; trades=[]; events=[]
    curves={s:[{"timestamp":OOS_START.isoformat(),"bar_start":OOS_START.isoformat(),"equity_usdt":INITIAL_USDT,"cash_usdt":INITIAL_USDT,"position_symbol":None}] for s in COSTS}
    signal_count=0
    for i in range(start,end):
        ts=idx[i]; exited=False
        if pos is not None and pending:
            ref=float(feat[pos["symbol"]]["open"][i]); old=pos
            for scen in COSTS:
                px,fee=adverse_price(ref,COSTS[scen],"SELL"); cash[scen]+=old["qty"][scen]*px*(1-fee)
            trade={"symbol":old["symbol"],"signal_time":old["signal_time"].isoformat(),"entry_time":old["entry_time"].isoformat(),"exit_time":ts.isoformat(),
                "entry_ref":old["entry_ref"],"exit_ref":ref,"stop_ref":old["stop_ref"],"exit_reason":"DONCHIAN_NEXT_OPEN",
                "entry_month":old["entry_time"].strftime("%Y-%m"),"exit_month":ts.strftime("%Y-%m"),"candidate":old["candidate"]}
            trades.append(trade);events.append({"timestamp":ts.isoformat(),"event":"EXIT","symbol":old["symbol"],"reason":trade["exit_reason"]})
            pos=None;pending=False;exited=True
        candidates=[]
        if i>start:
            j=i-1; selected=grid_by_month.get(str(idx[j].to_period("M")))
            if selected is not None:
                ew,xw,ep,sl,sm,adx=selected
                for sym in ("BTCUSDT","ETHUSDT"):
                    f=feat[sym]
                    if cache[(sym,ew,ep,sl,adx)][j] and np.isfinite(f["atr"][j]) and f["atr"][j]>0:
                        candidates.append(((f["close"][j]-f["entry_high"][ew][j])/f["atr"][j],sym,selected))
        signal_count+=len(candidates)
        if pos is not None and candidates: rejected["while_open"]+=len(candidates)
        elif exited and candidates: rejected["overlap_signal"]+=len(candidates)
        elif pos is None and not exited and candidates:
            candidates.sort(key=lambda v:(-float(v[0]),v[1]));strength,sym,candidate=candidates[0]
            while entry_times and ts-entry_times[0]>pd.Timedelta(days=30): entry_times.popleft()
            if len(entry_times)>=MAX_ENTRIES_30D: rejected["rolling_cap"]+=len(candidates)
            else:
                ref=float(feat[sym]["open"][i]); quantities={}
                for scen in COSTS:
                    px,fee=adverse_price(ref,COSTS[scen],"BUY");budget=cash[scen]*FRACTION
                    quantities[scen]=budget/(px*(1+fee));cash[scen]-=budget
                ew,xw,ep,sl,sm,adx=candidate;stop=ref-sm*float(feat[sym]["atr"][j])
                if stop<=0: raise WalkForwardError("NONPOSITIVE_STOP")
                pos={"symbol":sym,"entry_time":ts,"signal_time":idx[j]+pd.Timedelta(hours=4),"entry_ref":ref,"stop_ref":stop,
                    "qty":quantities,"candidate":candidate,"exit_w":xw}
                entry_times.append(ts);events.append({"timestamp":ts.isoformat(),"event":"ENTRY","symbol":sym,"reason":"WALK_FORWARD_DONCHIAN_EMA_ADX","candidate":candidate,"signal_strength_atr":float(strength)})
        if pos is not None:
            f=feat[pos["symbol"]];op=float(f["open"][i]);lo=float(f["low"][i]);stop=float(pos["stop_ref"]);stop_ref=stop_reference_fill(op,lo,stop)
            if stop_ref is not None:
                old=pos
                for scen in COSTS:
                    px,fee=adverse_price(float(stop_ref),COSTS[scen],"SELL");cash[scen]+=old["qty"][scen]*px*(1-fee)
                reason="ATR_STOP_GAP_OPEN" if op<=stop else "ATR_STOP"; timestamp=ts+pd.Timedelta(hours=4)
                trades.append({"symbol":old["symbol"],"signal_time":old["signal_time"].isoformat(),"entry_time":old["entry_time"].isoformat(),"exit_time":timestamp.isoformat(),
                    "entry_ref":old["entry_ref"],"exit_ref":float(stop_ref),"stop_ref":stop,"exit_reason":reason,"entry_month":old["entry_time"].strftime("%Y-%m"),
                    "exit_month":timestamp.strftime("%Y-%m"),"candidate":old["candidate"]})
                events.append({"timestamp":timestamp.isoformat(),"event":"EXIT","symbol":old["symbol"],"reason":reason});pos=None;pending=False
            else: pending=bool(f["close"][i]<f["exit_low"][pos["exit_w"]][i])
        rowtime=ts+pd.Timedelta(hours=4)
        for scen in COSTS:
            value=cash[scen]
            if pos is not None:
                px,fee=adverse_price(float(feat[pos["symbol"]]["close"][i]),COSTS[scen],"SELL");value+=pos["qty"][scen]*px*(1-fee)
            curves[scen].append({"timestamp":rowtime.isoformat(),"bar_start":ts.isoformat(),"equity_usdt":value,"cash_usdt":cash[scen],"position_symbol":pos["symbol"] if pos else None})
    if pos is not None:
        ref=float(feat[pos["symbol"]]["close"][end-1]);timestamp=idx[end-1]+pd.Timedelta(hours=4);old=pos
        for scen in COSTS:
            px,fee=adverse_price(ref,COSTS[scen],"SELL");cash[scen]+=old["qty"][scen]*px*(1-fee)
        trades.append({"symbol":old["symbol"],"signal_time":old["signal_time"].isoformat(),"entry_time":old["entry_time"].isoformat(),"exit_time":timestamp.isoformat(),
            "entry_ref":old["entry_ref"],"exit_ref":ref,"stop_ref":old["stop_ref"],"exit_reason":"OOS_END_LIQUIDATION",
            "entry_month":old["entry_time"].strftime("%Y-%m"),"exit_month":timestamp.strftime("%Y-%m"),"candidate":old["candidate"]})
        for scen in COSTS:
            curves[scen][-1]["equity_usdt"]=cash[scen];curves[scen][-1]["cash_usdt"]=cash[scen];curves[scen][-1]["position_symbol"]=None
    summary={}
    for scen in COSTS:
        q=np.asarray([r["equity_usdt"] for r in curves[scen]],float);peak=np.maximum.accumulate(q);trade_r=[]
        for t in trades:
            buy,fb=adverse_price(float(t["entry_ref"]),COSTS[scen],"BUY");sell,fs=adverse_price(float(t["exit_ref"]),COSTS[scen],"SELL")
            trade_r.append(100*(sell*(1-fs)/(buy*(1+fb))-1))
        summary[scen]={"initial_usdt":INITIAL_USDT,"final_usdt":float(cash[scen]),"return_pct":100*(float(cash[scen])/INITIAL_USDT-1),
            "max_drawdown_pct":float(np.min(100*(q/peak-1))) if len(q) else 0,"closed_trades":len(trades),
            "positive_trades":sum(r>0 for r in trade_r),"win_rate_pct":100*sum(r>0 for r in trade_r)/len(trade_r) if trade_r else 0.0,
            "mean_trade_return_pct":float(np.mean(trade_r)) if trade_r else None,"median_trade_return_pct":float(np.median(trade_r)) if trade_r else None}
    return {"summary":summary,"trades":trades,"events":events,"curves":curves,"rejected_signals":rejected,"signal_count":signal_count}

def monthly_returns(result,feat,index):
    rows=[]
    for scen,curve in result["curves"].items():
        d=pd.DataFrame(curve);d["timestamp"]=pd.to_datetime(d.timestamp,utc=True);d=d.set_index("timestamp")
        for mo,g in d.groupby(d.index.tz_localize(None).to_period("M")):
            key=str(mo);start=pd.Timestamp(key+"-01T00:00:00Z");nxt=start+pd.offsets.MonthBegin(1)
            si=int(d.index.searchsorted(start,side="left"));ei=int(d.index.searchsorted(nxt,side="left"))
            exists=si<len(d) and d.index[si]==start;complete=exists and ei<len(d) and d.index[ei]==nxt and nxt<=OOS_END
            if not exists: continue
            start_eq=float(d.equity_usdt.iloc[si]);end_eq=float(d.equity_usdt.iloc[ei]) if complete else float(g.equity_usdt.iloc[-1])
            refs={"BTCUSDT":None,"ETHUSDT":None};bi=int(index.searchsorted(start,side="left"));xi=int(index.searchsorted(nxt,side="left"))
            if complete and bi<len(index) and xi<len(index):
                for sym in refs:
                    cost=COSTS[scen];buy,fb=adverse_price(float(feat[sym]["open"][bi]),cost,"BUY")
                    qty=start_eq*FRACTION/(buy*(1+fb));sell,fs=adverse_price(float(feat[sym]["open"][xi]),cost,"SELL")
                    refs[sym]=100*((start_eq*(1-FRACTION)+qty*sell*(1-fs))/start_eq-1)
            strat=100*(end_eq/start_eq-1)
            rows.append({"month":key,"cost_scenario":scen,"complete_month":bool(complete),"start_equity_usdt":start_eq,"end_equity_usdt":end_eq,
                "strategy_return_pct":strat,"cash_return_pct":0.0,"btc_monthly_buy_hold_return_pct":refs["BTCUSDT"],"eth_monthly_buy_hold_return_pct":refs["ETHUSDT"],
                "beats_cash":bool(end_eq>start_eq),"beats_btc_monthly_buy_hold":bool(refs["BTCUSDT"] is not None and strat>refs["BTCUSDT"]),
                "beats_eth_monthly_buy_hold":bool(refs["ETHUSDT"] is not None and strat>refs["ETHUSDT"]),
                "closed_trades":sum(1 for t in result["trades"] if t["exit_month"]==key)})
    return rows

def run(dataset:Path,out:Path,source_sha="LOCAL_UNCOMMITTED"):
    if out.exists() and any(out.iterdir()): raise WalkForwardError("OUTPUT_DIRECTORY_NOT_EMPTY")
    out.mkdir(parents=True,exist_ok=True)
    bundle,frames,raw_hash,missing,unknown=load_dataset(dataset)
    four={s:aggregate_4h(frames[s]) for s in frames};common=four["BTCUSDT"].index.intersection(four["ETHUSDT"].index)
    four={s:four[s].loc[common].copy() for s in four};feat={s:indicators(four[s]) for s in four};cache=make_signal_cache(feat);grid=make_grid();idx=common
    starts=[m for m in pd.date_range("2026-01-01T00:00:00Z","2026-10-01T00:00:00Z",freq="MS") if m in idx]
    selections,grid_audit=monthly_selection(starts,idx,feat,cache,grid);selected={r["month"]:tuple(r["candidate"]) if r.get("candidate") else None for r in selections}
    test_start=int(idx.searchsorted(OOS_START,side="left"));test_end=int(idx.searchsorted(OOS_END,side="left"))
    if test_end<=test_start: raise WalkForwardError("OOS_WINDOW_EMPTY")
    oos=simulate_oos(feat,cache,idx,selected,test_start,test_end);mr=monthly_returns(oos,feat,idx)
    monthly_summary=[];mdf=pd.DataFrame(mr)
    for scen in COSTS:
        q=mdf[(mdf.cost_scenario==scen)&mdf.complete_month]
        monthly_summary.append({"cost_scenario":scen,"complete_months":int(len(q)),"positive_strategy_months":int((q.strategy_return_pct>0).sum()),
            "beats_cash_months":int(q.beats_cash.sum()),"beats_btc_monthly_bh_months":int(q.beats_btc_monthly_buy_hold.sum()),"beats_eth_monthly_bh_months":int(q.beats_eth_monthly_buy_hold.sum()),
            "mean_monthly_return_pct":float(q.strategy_return_pct.mean()) if len(q) else None,"median_monthly_return_pct":float(q.strategy_return_pct.median()) if len(q) else None,
            "worst_month_pct":float(q.strategy_return_pct.min()) if len(q) else None})
    benchmarks=[]
    for sym in ("BTCUSDT","ETHUSDT"):
        f=feat[sym];p0=float(f["open"][test_start]);p1=float(f["close"][test_end-1])
        for scen,cost in COSTS.items():
            buy,fb=adverse_price(p0,cost,"BUY");qty=INITIAL_USDT*FRACTION/(buy*(1+fb));sell,fs=adverse_price(p1,cost,"SELL")
            final=INITIAL_USDT*(1-FRACTION)+qty*sell*(1-fs);benchmarks.append({"benchmark":sym+"_BUY_HOLD","cost_scenario":scen,"initial_usdt":INITIAL_USDT,"final_usdt":final,"return_pct":100*(final/INITIAL_USDT-1)})
    for scen in COSTS: benchmarks.append({"benchmark":"CASH","cost_scenario":scen,"initial_usdt":INITIAL_USDT,"final_usdt":INITIAL_USDT,"return_pct":0.0})
    b=next(x for x in benchmarks if x["benchmark"]=="BTCUSDT_BUY_HOLD" and x["cost_scenario"]=="BASE")["return_pct"]
    e=next(x for x in benchmarks if x["benchmark"]=="ETHUSDT_BUY_HOLD" and x["cost_scenario"]=="BASE")["return_pct"]
    eligibility={"positive_BASE":oos["summary"]["BASE"]["return_pct"]>0,"positive_STRESS_1":oos["summary"]["STRESS_1"]["return_pct"]>0,
        "positive_STRESS_2":oos["summary"]["STRESS_2"]["return_pct"]>0,"drawdown_BASE_above_minus_20pct":oos["summary"]["BASE"]["max_drawdown_pct"]>=-20,
        "minimum_30_closed_trades":oos["summary"]["BASE"]["closed_trades"]>=30,"beats_cash_BTC_ETH_BASE":oos["summary"]["BASE"]["return_pct"]>max(0,b,e)}
    winner_eligible=all(eligibility.values())
    info={"schema_version":1,"study":"TREND_MODE_WALK_FORWARD_GRID_V2","status":"WALK_FORWARD_HISTORICAL_RESEARCH_NOT_FUTURE_HOLDOUT","decision":"NO_TRADE",
        "winner_eligible":winner_eligible,"winner_gate_results":eligibility,"source_git_sha":source_sha,"dataset_logical_sha256":bundle["dataset_sha256"],
        "dataset_configuration_sha256":bundle["configuration_sha256"],"dataset_raw_file_sha256":raw_hash,
        "dataset_period_start":str(frames["BTCUSDT"].index[0]),"dataset_period_end":str(frames["BTCUSDT"].index[-1]),
        "oos_start":OOS_START.isoformat(),"oos_end_exclusive":OOS_END.isoformat(),
        "hourly_bars_per_asset":{s:len(frames[s]) for s in frames},"four_hour_bars_per_asset":{s:len(four[s]) for s in four},
        "missing_available_at_count_by_asset":missing,"unknown_finality_count_by_asset":unknown,"grid_dimensions":GRID,"grid_configurations":len(grid),
        "candidate_month_screenings":len(grid)*len(selections),"monthly_walk_forward_folds":len(selections),"training_lookback_days":TRAIN_DAYS,
        "grid_audit_rows":len(grid_audit),"exact_replay_count":sum(bool(a["exact_replay_performed"]) for a in grid_audit),
        "screening_rule":"vectorized fixed-24h proxy screens all 2880 configurations; top 80 eligible configurations per month receive full stop/channel replay",
        "selection_rule":"past 180d only; >=3 trades, positive BASE and STRESS_1, drawdown no worse than -25% in both; maximize min(BASE, STRESS_1); if none, NO_TRADE",
        "oos_summary":oos["summary"],"monthly_summary":monthly_summary,"rejected_signals":oos["rejected_signals"],"raw_signal_count":oos["signal_count"],
        "closed_trade_count":len(oos["trades"]),"benchmarks":benchmarks,"python_version":platform.python_version(),"numpy_version":np.__version__,"pandas_version":pd.__version__,
        "limitations":["This archived history had prior research exposure; masked monthly walk-forward is not a globally untouched holdout.",
            "Historical available_at/finality are unknown for all 30,720 bars.","OHLC proxies are not historical bid/ask fills; costs are modeled.","No PAPER or LIVE orders are produced; NO_TRADE remains."]}
    (out/"results.json").write_text(json.dumps(info,indent=2,sort_keys=True,default=json_default,allow_nan=False)+"\n")
    pd.DataFrame(selections).to_csv(out/"monthly-selections.csv",index=False,float_format="%.12g")
    pd.DataFrame(grid_audit).to_csv(out/"grid-scan-audit.csv",index=False,float_format="%.12g")
    pd.DataFrame(oos["trades"]).to_csv(out/"walk-forward-trades.csv",index=False,float_format="%.12g")
    pd.concat({k:pd.DataFrame(v) for k,v in oos["curves"].items()},names=["cost_scenario"]).reset_index(level=0).to_csv(out/"equity.csv",index=False,float_format="%.12g")
    pd.DataFrame(mr).to_csv(out/"monthly-returns.csv",index=False,float_format="%.12g")
    pd.DataFrame(monthly_summary).to_csv(out/"monthly-summary.csv",index=False,float_format="%.12g")
    pd.DataFrame(benchmarks).to_csv(out/"benchmarks.csv",index=False,float_format="%.12g")
    winner={"status":"CANDIDATE_PASSES_FROZEN_GATE" if winner_eligible else "NO_WINNER_PASSED_FROZEN_GATE","decision":"NO_TRADE",
        "eligibility":eligibility,"oos_summary":oos["summary"],"benchmark_reference":benchmarks,"candidate_month_selections":selections}
    (out/"winner.json").write_text(json.dumps(winner,indent=2,sort_keys=True,default=json_default,allow_nan=False)+"\n")
    rows=["# Tendencia — Grid walk-forward V2, enero–octubre 2026","",
        f"**Dictamen final: {'CANDIDATE PASSES RESEARCH GATE' if winner_eligible else 'NO WINNER PASSED THE FROZEN GATE — NO_TRADE'}.**","",
        f"Main/source commit SHA: '{source_sha}'  ",f"Pinned source raw SHA-256: '{raw_hash}'  ",
        f"Logical dataset SHA-256: '{bundle['dataset_sha256']}'  ",f"Configuration SHA-256: '{bundle['configuration_sha256']}'  ",
        "**Temporal test:** each month uses a candidate selected only from the immediately preceding 180 days. If none passes the fixed training gates, that month is NO_TRADE.","",
        f"## Search volume: {len(grid):,} configurations, {len(grid)*len(selections):,} candidate-month screenings","",
        "Entry channels 20/30/40/55/80/120; exits 10/15/20/30/40; EMA 100/150/200/250; slope lags 3/5/10; stops 2/3/4/5 ATR; ADX gate off/20. A vectorized 24h proxy only shortlists; top 80 per month receive exact stop/channel replay. Complete results and losing candidates are retained in grid-scan-audit.csv.","",
        "## Month selections (trained on past only)","","| Month | Status | Eligible candidates | Selected rule | Training BASE | Training STRESS_1 |","|---|---|---:|---|---:|---:|"]
    for r in selections:
        c=r.get("candidate");rule="NO_TRADE" if not c else f"Donchian {c[0]}/{c[1]}, EMA{c[2]} slope {c[3]}, stop {c[4]} ATR, ADX≥{c[5]}"
        rows.append(f"| {r['month']} | {r['status']} | {r.get('eligible_candidates',0)} | {rule} | {r.get('training_BASE_return_pct',float('nan')):.3f}% | {r.get('training_STRESS_1_return_pct',float('nan')):.3f}% |")
    rows += ["","## OOS result Jan 1–Oct 17, 2026","","| Cost | Final USDT | Return | Max drawdown | Trades | Win rate |","|---|---:|---:|---:|---:|---:|"]
    for scen,m in oos["summary"].items():rows.append(f"| {scen} | {m['final_usdt']:.4f} | {m['return_pct']:+.3f}% | {m['max_drawdown_pct']:+.3f}% | {m['closed_trades']} | {m['win_rate_pct']:.1f}% |")
    rows += ["","## Frozen winner gate","","| Gate | Result |","|---|---|"]
    for k,v in eligibility.items():rows.append(f"| {k} | {'PASS' if v else 'FAIL'} |")
    rows += ["","## Buy-and-hold references","","| Benchmark | Cost | Return |","|---|---|---:|"]
    for q in benchmarks:rows.append(f"| {q['benchmark']} | {q['cost_scenario']} | {q['return_pct']:+.3f}% |")
    rows += ["","## Monthly strategy versus cash/BTC/ETH","","| Month | Cost | Strategy | Cash | BTC monthly BH | ETH monthly BH | Complete |","|---|---|---:|---:|---:|---:|---|"]
    for r in mr:
        fmt=lambda v:"—" if v is None or pd.isna(v) else f"{v:+.3f}%"
        rows.append(f"| {r['month']} | {r['cost_scenario']} | {fmt(r['strategy_return_pct'])} | {fmt(r['cash_return_pct'])} | {fmt(r['btc_monthly_buy_hold_return_pct'])} | {fmt(r['eth_monthly_buy_hold_return_pct'])} | {r['complete_month']} |")
    rows += ["","## Limits","","This is a masked monthly walk-forward on previously investigated history, not a globally untouched holdout. All source bars have unknown available_at/finality and costs are modeled, not observed bid/ask. It is an offline test of signal/timing/accounting, not executable PAPER proof. **NO_TRADE remains.**"]
    (out/"REPORT.md").write_text("\n".join(rows)+"\n",encoding="utf-8")
    files={}
    for p in sorted(out.iterdir()):
        if p.is_file():files[p.name]={"size_bytes":p.stat().st_size,"sha256":sha256(p.read_bytes())}
    manifest={"schema_version":1,"study":info["study"],"decision":info["decision"],"winner_eligible":winner_eligible,"source_git_sha":source_sha,
        "dataset_raw_sha256":raw_hash,"dataset_logical_sha256":bundle["dataset_sha256"],"config_sha256":bundle["configuration_sha256"],
        "grid_configurations":len(grid),"candidate_month_screenings":len(grid)*len(selections),"grid_audit_rows":len(grid_audit),
        "exact_replay_count":info["exact_replay_count"],"monthly_folds":len(selections),"oos_start":info["oos_start"],"oos_end_exclusive":info["oos_end_exclusive"],
        "missing_available_at_count_by_asset":missing,"files":files}
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True,default=json_default)+"\n")
    for name,meta in files.items():
        data=(out/name).read_bytes()
        if len(data)!=meta["size_bytes"] or sha256(data)!=meta["sha256"]:raise WalkForwardError("OUTPUT_HASH_MISMATCH:"+name)
    return info,manifest

def main():
    p=argparse.ArgumentParser(description="Fixed 2,880 config masked walk-forward trend scan; no orders")
    p.add_argument("--dataset",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--source-sha",default="LOCAL_UNCOMMITTED")
    a=p.parse_args();info,_=run(a.dataset,a.output_dir,a.source_sha)
    print("GRID_CONFIGURATIONS:",info["grid_configurations"]);print("OOS_FOLDS:",info["monthly_walk_forward_folds"])
    for scen,m in info["oos_summary"].items():print(f"{scen}: return={m['return_pct']:+.4f}% drawdown={m['max_drawdown_pct']:+.4f}% trades={m['closed_trades']}")
    print("WINNER_ELIGIBLE:",info["winner_eligible"]);print("DECISION:",info["decision"]);return 0
if __name__=="__main__":raise SystemExit(main())
