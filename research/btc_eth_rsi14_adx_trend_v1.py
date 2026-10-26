from __future__ import annotations
import argparse, csv, hashlib, json, math
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from bot_obrero.binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.virtual_backtest import _market_data_record, _validate_history
from datetime import datetime, timedelta, timezone

STRATEGY = "BTC_ETH_4H_RSI14_ADX14_TREND_V1"
ASSETS = ("BTCUSDT", "ETHUSDT")
BAR = pd.Timedelta(hours=4)
CAPITALS = (Decimal("60"), Decimal("100"))
TARGET = Decimal("0.15")
MAX_WEIGHT = Decimal("0.50")
ZERO, ONE, HUNDRED, BPS = Decimal("0"), Decimal("1"), Decimal("100"), Decimal("10000")
WINDOWS = {
 "COMBINED_2025_2026_YTD": (pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2026-10-22T00:00:00Z")),
 "2025": (pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2026-01-01T00:00:00Z")),
 "2026_YTD": (pd.Timestamp("2026-01-01T00:00:00Z"), pd.Timestamp("2026-10-22T00:00:00Z")),
}
COSTS = {
 "BASE": {"fee_rate": Decimal("0.001"), "spread_bps": Decimal("10"), "slippage_bps": Decimal("5")},
 "STRESS_1": {"fee_rate": Decimal("0.001"), "spread_bps": Decimal("25"), "slippage_bps": Decimal("10")},
 "STRESS_2": {"fee_rate": Decimal("0.002"), "spread_bps": Decimal("50"), "slippage_bps": Decimal("20")},
}
RECORD_HASHES = {
 "BTCUSDT": "b5e3f7e490af598f44885c5b24983c16d6141a3d7c3cfe6f469ff95712eceb66",
 "ETHUSDT": "4fc086bbb600b4a51ee2881c5f54cc4121de583b83797e00207a2e63122dc392",
}
OHLCV_HASHES = {
 "BTCUSDT": "a508cb02259c15ec67e6df8c1c8be406b1d5f64350cae17a33c463e728793d9d",
 "ETHUSDT": "d75f1b78f0c11ba889c81458801abf0d14758329819c55d6eda6efb0151a8510",
}
BASELINE_15 = {
 ("COMBINED_2025_2026_YTD", Decimal("60")): Decimal("9.99129"),
 ("COMBINED_2025_2026_YTD", Decimal("100")): Decimal("16.79469"),
 ("2025", Decimal("60")): Decimal("7.38137"),
 ("2025", Decimal("100")): Decimal("12.40606"),
 ("2026_YTD", Decimal("60")): Decimal("3.76917"),
 ("2026_YTD", Decimal("100")): Decimal("6.23236"),
}

class StudyError(ValueError): pass

def sha256_bytes(b: bytes) -> str:
 return hashlib.sha256(b).hexdigest()

def canonical_hash(obj: Any) -> str:
 return sha256_bytes(json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode())

def _ema(values: np.ndarray, period: int) -> np.ndarray:
 out=np.full(len(values),np.nan,dtype=float)
 valid=np.flatnonzero(np.isfinite(values))
 if not len(valid): return out
 start=int(valid[0]); suffix=values[start:]
 bad=np.flatnonzero(~np.isfinite(suffix)); end=start+int(bad[0]) if len(bad) else len(values)
 if end-start<period: return out
 seed=start+period-1
 out[seed]=float(np.mean(values[start:seed+1]))
 alpha=2.0/(period+1.0)
 for i in range(seed+1,end): out[i]=alpha*values[i]+(1.0-alpha)*out[i-1]
 return out

def _rma(values: np.ndarray, period: int) -> np.ndarray:
 out=np.full(len(values),np.nan,dtype=float)
 valid=np.flatnonzero(np.isfinite(values))
 if not len(valid): return out
 start=int(valid[0])
 if start+period>len(values) or not np.isfinite(values[start:]).all(): return out
 seed=start+period-1
 out[seed]=float(np.mean(values[start:seed+1]))
 for i in range(seed+1,len(values)): out[i]=((period-1)*out[i-1]+values[i])/period
 return out

def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
 d=frame.copy()
 if not isinstance(d.index,pd.DatetimeIndex) or d.index.tz is None: raise StudyError("UTC_INDEX_REQUIRED")
 if not (d.index.to_series().diff().dropna()==BAR).all(): raise StudyError("GAP_OR_DUPLICATE_4H")
 values=d[["open","high","low","close","volume"]].to_numpy(dtype=float)
 if not np.isfinite(values).all(): raise StudyError("NONFINITE_OHLCV")
 o,h,l,c,v=(d[x].to_numpy(dtype=float) for x in ("open","high","low","close","volume"))
 if (np.minimum.reduce([o,h,l,c])<=0).any() or (v<0).any() or (h<np.maximum.reduce([o,l,c])).any() or (l>np.minimum.reduce([o,h,c])).any():
  raise StudyError("INVALID_OHLCV")
 tr=np.empty(len(d)); plus=np.zeros(len(d)); minus=np.zeros(len(d))
 for i in range(len(d)):
  tr[i]=(h[i]-l[i]) if i==0 else max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
  if i:
   up_move=h[i]-h[i-1]; down_move=l[i-1]-l[i]
   plus[i]=up_move if up_move>down_move and up_move>0 else 0.0
   minus[i]=down_move if down_move>up_move and down_move>0 else 0.0
 atr=pd.Series(tr,index=d.index).rolling(14,min_periods=14).mean().to_numpy()
 ema20,ema50,ema200=_ema(c,20),_ema(c,50),_ema(c,200)

 # Wilder RSI(14) from close-to-close gains/losses. First bar has no delta.
 delta=np.diff(c,prepend=np.nan)
 gains=np.maximum(delta,0.0); losses=np.maximum(-delta,0.0)
 gains[0]=np.nan; losses[0]=np.nan
 avg_gain=_rma(gains,14); avg_loss=_rma(losses,14)
 rsi=np.full(len(d),np.nan)
 both=np.isfinite(avg_gain)&np.isfinite(avg_loss)
 both_zero=both&(avg_gain==0)&(avg_loss==0)
 loss_zero=both&(avg_loss==0)&(avg_gain>0)
 regular=both&(avg_loss>0)
 rsi[both_zero]=50.0
 rsi[loss_zero]=100.0
 rs=np.divide(avg_gain,avg_loss,out=np.full(len(d),np.nan),where=regular)
 rsi[regular]=100.0-(100.0/(1.0+rs[regular]))

 # Wilder ADX(14), with a seeded 14-DX average after DI becomes available.
 smooth_tr=_rma(tr,14); smooth_plus=_rma(plus,14); smooth_minus=_rma(minus,14)
 plus_di=np.divide(100.0*smooth_plus,smooth_tr,out=np.full(len(d),np.nan),where=smooth_tr>0)
 minus_di=np.divide(100.0*smooth_minus,smooth_tr,out=np.full(len(d),np.nan),where=smooth_tr>0)
 denominator=plus_di+minus_di
 dx=np.divide(100.0*np.abs(plus_di-minus_di),denominator,out=np.full(len(d),np.nan),where=denominator>0)
 adx=np.full(len(d),np.nan); first_dx=13; seed_end=first_dx+14-1
 if len(d)>seed_end and np.isfinite(dx[first_dx:seed_end+1]).all():
  adx[seed_end]=float(np.mean(dx[first_dx:seed_end+1]))
  for i in range(seed_end+1,len(d)):
   if not math.isfinite(dx[i]): raise StudyError("ADX_INPUT_GAP")
   adx[i]=((14-1)*adx[i-1]+dx[i])/14.0

 cross_up=np.zeros(len(d),dtype=bool); cross_down=np.zeros(len(d),dtype=bool)
 for i in range(1,len(d)):
  prev,cur=rsi[i-1],rsi[i]
  if math.isfinite(prev) and math.isfinite(cur):
   cross_up[i]=prev<=50.0 and cur>50.0
   cross_down[i]=prev>=45.0 and cur<45.0
 buy=cross_up&(c>ema200)&(ema50>ema200)&(c>ema50)&(adx>=20.0)&(atr>0.0)
 exit_=cross_down|(c<=ema50)
 target_weight=np.full(len(d),np.nan)
 annual=(atr/c)*math.sqrt(365*6); ok=np.isfinite(annual)&(annual>0)
 target_weight[ok]=np.minimum(float(MAX_WEIGHT),float(TARGET)/annual[ok])
 d["ema20"],d["ema50"],d["ema200"]=ema20,ema50,ema200
 d["rsi14"],d["rsi_cross_up"],d["rsi_cross_down"]=rsi,cross_up,cross_down
 d["atr14"],d["adx14"]=atr,adx
 d["buy_signal"],d["exit_signal"]=buy,exit_
 # Cross-asset selection is highest ADX14; stable alphabetical tie-break.
 d["signal_strength"]=adx
 d["weight"]=target_weight
 return d

def load_dataset(path: Path):
 raw=path.read_bytes(); bundle=json.loads(raw)
 if bundle.get("interval")!="4h" or bundle.get("expected_bars_per_asset")!=4254: raise StudyError("PINNED_DATASET_CONTRACT_MISMATCH")
 if set(bundle.get("data",{}))!=set(ASSETS): raise StudyError("ASSET_UNIVERSE_MISMATCH")
 frames={}; record_hashes={}; ohlcv_hashes={}; filters={}
 for symbol in ASSETS:
  records=bundle["data"][symbol]
  if len(records)!=4254: raise StudyError("BAR_COUNT_MISMATCH:"+symbol)
  full=canonical_hash(records)
  if full!=RECORD_HASHES[symbol] or full!=bundle["asset_dataset_sha256"][symbol]: raise StudyError("SOURCE_RECORD_HASH_MISMATCH:"+symbol)
  canonical=[]; rows=[]
  for item in records:
   c=item["candle"]
   if c.get("candle_state")!="CLOSED" or c.get("finality")!="FINAL" or c.get("completeness")!="COMPLETE": raise StudyError("CANDLE_CONTRACT_FAILURE:"+symbol)
   if item.get("quality")!="VALID" or item.get("data_type")!="CANDLE": raise StudyError("RECORD_QUALITY_FAILURE:"+symbol)
   canonical.append({k:c[k] for k in ("start","open","high","low","close","volume")})
   rows.append({"timestamp":c["start"],**{k:float(c[k]) for k in ("open","high","low","close","volume")}})
  ch=canonical_hash(canonical)
  if ch!=OHLCV_HASHES[symbol]: raise StudyError("CANONICAL_OHLCV_HASH_MISMATCH:"+symbol)
  frame=pd.DataFrame(rows); frame["timestamp"]=pd.to_datetime(frame.timestamp,utc=True)
  frame=frame.set_index("timestamp",drop=False)
  if frame.index[0]!=pd.Timestamp("2024-11-12T00:00:00Z") or frame.index[-1]!=pd.Timestamp("2026-10-21T20:00:00Z"): raise StudyError("START_END_MISMATCH:"+symbol)
  if not (frame.index.to_series().diff().dropna()==BAR).all(): raise StudyError("TIMESTAMP_GAP:"+symbol)
  frames[symbol]=frame; record_hashes[symbol]=full; ohlcv_hashes[symbol]=ch
  raw_filter=bundle["metadata"][symbol]["current_filters"]
  filters[symbol]={"step":Decimal(raw_filter["step"]),"min_qty":Decimal(raw_filter["snapshot"]["LOT_SIZE"]["minQty"]),
                   "min_notional":Decimal(raw_filter["min_notional"]),"hash":raw_filter["sha256"]}
 if not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index): raise StudyError("BTC_ETH_NOT_PAIRED")
 provenance={"source_file_sha256":sha256_bytes(raw),"record_hashes":record_hashes,"ohlcv_hashes":ohlcv_hashes,
  "metadata_completeness_unknown":{s:bundle["metadata"][s]["completeness_unknown"] for s in ASSETS},
  "metadata_finality_unknown":{s:bundle["metadata"][s]["finality_unknown"] for s in ASSETS},
  "filter_hashes":{s:filters[s]["hash"] for s in ASSETS}}
 return frames,filters,provenance


def fetch_pinned_live():
    utc = timezone.utc
    start = datetime(2024, 11, 12, tzinfo=utc)
    bars = [start + timedelta(hours=4*i) for i in range(4254)]
    expected_filter_hashes = {
        "BTCUSDT": "82b372432bcbfee18363c3ffc6d22c87465f3e7a64e561e4e382c7d331d47725",
        "ETHUSDT": "2338832043b80849325ceb2d359b180e30ea1b90a866bbacce7c865f38751e0f",
    }
    frames, filters, record_hashes, ohlcv_hashes = {}, {}, {}, {}
    unknown_completeness, unknown_finality = {}, {}
    raw_records = {}
    for symbol in ASSETS:
        metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
        metadata.resolve_active(symbol)
        payload = metadata._request(symbol)
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, list) or len(symbols) != 1 or not isinstance(symbols[0], dict):
            raise StudyError("EXCHANGEINFO_SYMBOL_RECORD_INVALID:" + symbol)
        record = symbols[0]
        if record.get("symbol") != symbol or record.get("status") != "TRADING" or record.get("isSpotTradingAllowed") is not True:
            raise StudyError("EXCHANGEINFO_SPOT_NOT_TRADABLE:" + symbol)
        all_filters = record.get("filters")
        if not isinstance(all_filters, list):
            raise StudyError("EXCHANGEINFO_FILTERS_MISSING:" + symbol)
        lot = next((x for x in all_filters if isinstance(x, dict) and x.get("filterType") == "LOT_SIZE"), None)
        notion = next((x for x in all_filters if isinstance(x, dict) and x.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL")), None)
        if not isinstance(lot, dict) or not isinstance(notion, dict):
            raise StudyError("EXCHANGEINFO_LOT_OR_NOTIONAL_MISSING:" + symbol)
        snapshot = {"LOT_SIZE": lot, "MIN_NOTIONAL_OR_NOTIONAL": notion}
        filter_hash = canonical_hash(snapshot)
        if filter_hash != expected_filter_hashes[symbol]:
            raise StudyError("CURRENT_FILTER_SNAPSHOT_CHANGED:" + symbol + ":" + filter_hash)
        try:
            step, min_qty, min_notional = Decimal(lot["stepSize"]), Decimal(lot["minQty"]), Decimal(notion["minNotional"])
        except Exception as exc:
            raise StudyError("EXCHANGEINFO_FILTER_PARSE_ERROR:" + symbol) from exc
        if min(step, min_qty, min_notional) <= ZERO:
            raise StudyError("EXCHANGEINFO_NONPOSITIVE_FILTER:" + symbol)
        adapter = BinanceMetadataBackedAdapter(metadata=metadata)
        items = []
        for offset in range(0, len(bars), 800):
            wanted = bars[offset:offset+800]
            page = adapter.fetch_market_data(
                symbol=symbol, interval="4h", limit=len(wanted),
                start_time=int(wanted[0].timestamp()*1000),
                end_time=int(wanted[-1].timestamp()*1000), allow_empty=False
            )
            page = sorted(page, key=lambda x: x.payload.start)
            observed = [x.payload.start for x in page]
            if observed != wanted:
                raise StudyError("FETCH_TIMESTAMP_PAGE_MISMATCH:" + symbol + ":" + str(offset))
            items.extend(page)
        if [x.payload.start for x in items] != bars:
            raise StudyError("FETCH_TIMESTAMP_SEQUENCE_MISMATCH:" + symbol)
        accepted, excluded, unknown_comp, unknown_final = _validate_history(items, "4h")
        if len(accepted) != len(bars) or excluded:
            raise StudyError("FETCH_HISTORY_VALIDATION_FAILED:" + symbol)
        records = [_market_data_record(x) for x in items]
        canonical = [{k: rec["candle"][k] for k in ("start", "open", "high", "low", "close", "volume")} for rec in records]
        ohlcv_hash = canonical_hash(canonical)
        if ohlcv_hash != OHLCV_HASHES[symbol]:
            raise StudyError("PINNED_OHLCV_HASH_MISMATCH:" + symbol + ":" + ohlcv_hash)
        rows = []
        for rec in records:
            candle = rec["candle"]
            rows.append({"timestamp": candle["start"], **{k: float(candle[k]) for k in ("open", "high", "low", "close", "volume")}})
        frame = pd.DataFrame(rows)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.set_index("timestamp", drop=False)
        if not (frame.index.to_series().diff().dropna() == BAR).all():
            raise StudyError("FETCHED_CANDLE_GAP:" + symbol)
        frames[symbol] = frame
        raw_records[symbol] = records
        record_hashes[symbol] = canonical_hash(records)
        ohlcv_hashes[symbol] = ohlcv_hash
        unknown_completeness[symbol] = int(unknown_comp)
        unknown_finality[symbol] = int(unknown_final)
        filters[symbol] = {"step": step, "min_qty": min_qty, "min_notional": min_notional, "hash": filter_hash}
    if not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index):
        raise StudyError("FETCHED_BTC_ETH_INDEX_MISMATCH")
    prov = {
        "source_file_sha256": canonical_hash(raw_records),
        "record_hashes": record_hashes,
        "ohlcv_hashes": ohlcv_hashes,
        "metadata_completeness_unknown": unknown_completeness,
        "metadata_finality_unknown": unknown_finality,
        "filter_hashes": {s: filters[s]["hash"] for s in ASSETS},
    }
    return frames, filters, prov

def _price(ref: Decimal,cost:dict[str,Decimal],side:str):
 if ref<=0: raise StudyError("INVALID_FILL_PRICE")
 adverse=(cost["spread_bps"]/Decimal(2)+cost["slippage_bps"])/BPS
 if side=="BUY": return ref*(ONE+adverse),cost["fee_rate"]
 if side=="SELL": return ref*(ONE-adverse),cost["fee_rate"]
 raise StudyError("UNKNOWN_SIDE")

def _floor(qty:Decimal,step:Decimal):
 if qty<0 or step<=0: raise StudyError("INVALID_QTY")
 return (qty/step).to_integral_value(rounding=ROUND_DOWN)*step

def _exit(pos,ref,ts,reason,cash,cost,trades):
 fill,fee_rate=_price(ref,cost,"SELL"); gross=pos["qty"]*fill; fee=gross*fee_rate; net=gross-fee
 trades.append({"symbol":pos["symbol"],"signal_time":pos["signal_time"].isoformat(),"entry_time":pos["entry_time"].isoformat(),
  "exit_time":ts.isoformat(),"entry_ref":str(pos["entry_ref"]),"entry_fill":str(pos["entry_fill"]),"exit_ref":str(ref),
  "exit_fill":str(fill),"qty":str(pos["qty"]),"entry_notional":str(pos["notional"]),"entry_fee":str(pos["entry_fee"]),
  "exit_fee":str(fee),"entry_debit":str(pos["debit"]),"exit_net_proceeds":str(net),"net_pnl":str(net-pos["debit"]),
  "net_trade_return_pct":str((net/pos["debit"]-ONE)*HUNDRED),"weight":str(pos["weight"]),
  "signal_strength":pos["strength"],"exit_reason":reason,"terminal":reason=="TERMINAL_CLOSE"})
 return cash+net

def _mark(cash,pos,frames,ts,cost):
 if pos is None: return cash
 ref=Decimal(str(frames[pos["symbol"]].loc[ts,"close"])); fill,fee=_price(ref,cost,"SELL")
 return cash+pos["qty"]*fill*(ONE-fee)

def _signals(indicators,ts):
 candidates=[]
 for s in ASSETS:
  row=indicators[s].loc[ts]
  if bool(row.buy_signal): candidates.append((float(row.signal_strength),s,row))
 return sorted(candidates,key=lambda x:(-x[0],x[1]))

def simulate(indicators,filters,window,capital,cost_name,cost):
 start,end=WINDOWS[window]; idx=indicators["BTCUSDT"].index
 chosen=idx[(idx>=start)&(idx<end)]
 if len(chosen)<20: raise StudyError("INSUFFICIENT_WINDOW:"+window)
 cash=capital; pos=None; trades=[]; eq=[{"timestamp":chosen[0].isoformat(),"equity":str(cash),"cash":str(cash),"symbol":""}]
 rejected=0
 for ts in chosen:
  i=idx.get_loc(ts); exited=False
  if i>0:
   sig_ts=idx[i-1]
   if pos is not None and i-1>=pos["entry_idx"] and bool(indicators[pos["symbol"]].loc[sig_ts].exit_signal):
    cash=_exit(pos,Decimal(str(indicators[pos["symbol"]].loc[ts,"open"])),ts,"MACD_DOWN_OR_EMA50_NEXT_OPEN",cash,cost,trades)
    pos=None; exited=True
   if pos is None and not exited:
    cand=_signals(indicators,sig_ts)
    if cand:
     strength,s,row=cand[0]
     ref=Decimal(str(indicators[s].loc[ts,"open"])); fill,fee_rate=_price(ref,cost,"BUY")
     weight=min(MAX_WEIGHT,max(ZERO,Decimal(str(float(row.weight)))))
     budget=cash*weight; qty=_floor(budget/(fill*(ONE+fee_rate)),filters[s]["step"])
     notional=qty*fill; fee=notional*fee_rate; debit=notional+fee
     if qty<filters[s]["min_qty"] or notional<filters[s]["min_notional"] or debit<=0 or debit>cash:
      rejected+=1
     else:
      cash-=debit; pos={"symbol":s,"signal_time":sig_ts+BAR,"entry_time":ts,"entry_ref":ref,"entry_fill":fill,
       "qty":qty,"notional":notional,"entry_fee":fee,"debit":debit,"weight":weight,"strength":strength,"entry_idx":i}
  eq.append({"timestamp":ts.isoformat(),"equity":str(_mark(cash,pos,indicators,ts,cost)),"cash":str(cash),"symbol":pos["symbol"] if pos else ""})
 if pos is not None:
  ts=chosen[-1]; cash=_exit(pos,Decimal(str(indicators[pos["symbol"]].loc[ts,"close"])),ts,"TERMINAL_CLOSE",cash,cost,trades)
  eq[-1].update(equity=str(cash),cash=str(cash),symbol="")
 values=np.array([float(x["equity"]) for x in eq]); peaks=np.maximum.accumulate(values)
 dd=np.where(peaks>0,(values/peaks-1)*100,0)
 final=Decimal(str(values[-1])); fees=sum((Decimal(t["entry_fee"])+Decimal(t["exit_fee"]) for t in trades),ZERO)
 return {"status":"COMPLETED","strategy":STRATEGY,"window":window,"initial_usdt":str(capital),"cost_scenario":cost_name,
  "bars":len(chosen),"start":chosen[0].isoformat(),"end_exclusive":end.isoformat(),"final_equity":str(final),"net_pnl":str(final-capital),
  "return_pct":str((final/capital-ONE)*HUNDRED),"max_drawdown_pct":float(dd.min()),"round_trips":len(trades),"fees":str(fees),
  "rejected_min_notional_or_lot":rejected,"trades":trades,"equity":eq}

def buy_hold(frame,symbol,window,capital,cost_name,cost,filters):
 start,end=WINDOWS[window]; d=frame.loc[(frame.index>=start)&(frame.index<end)]
 first,last=d.iloc[0],d.iloc[-1]; meta=filters[symbol]
 fill,fee=_price(Decimal(str(first.open)),cost,"BUY")
 qty=_floor(capital*Decimal("0.95")/(fill*(ONE+fee)),meta["step"]); notional=qty*fill; debit=notional*(ONE+fee)
 if qty<meta["min_qty"] or notional<meta["min_notional"] or debit>capital: final=capital
 else:
  sell,sfee=_price(Decimal(str(last.close)),cost,"SELL"); final=capital-debit+qty*sell*(ONE-sfee)
 return {"status":"COMPLETED","window":window,"symbol":symbol,"initial_usdt":str(capital),"cost_scenario":cost_name,
  "net_pnl":str(final-capital),"return_pct":str((final/capital-ONE)*HUNDRED),"start":d.index[0].isoformat(),"end_exclusive":end.isoformat()}

def write_csv(path,rows):
 keys=sorted({k for r in rows for k in r})
 with path.open("w",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=keys,extrasaction="ignore");w.writeheader()
  for r in rows: w.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in r.items()})

def run(dataset,outdir,source_sha,fetch_pinned=False):
 if fetch_pinned:
  frames,filters,prov=fetch_pinned_live()
 else:
  if dataset is None: raise StudyError('DATASET_PATH_REQUIRED_UNLESS_FETCHING_PINNED_CANDLES')
  frames,filters,prov=load_dataset(dataset)
 ind={s:_indicators(frames[s]) for s in ASSETS}
 cfg={"strategy":STRATEGY,"entry":"Wilder RSI14 crosses from <=50 to >50; close>EMA200; EMA50>EMA200; close>EMA50; Wilder ADX14>=20",
  "selection":"max ADX14; alphabetical tie","exit":"Wilder RSI14 crosses from >=45 to <45 or close<=EMA50; next open",
  "sizing":"min(50%,15%/((ATR14/close)*sqrt(365*6))); frozen at signal close","EMA":"SMA seed and recursive alpha=2/(period+1)",
  "ADX":"Wilder RMA14, DI from smoothed TR/DM and ADX seed from 14 valid DX values","ATR14":"simple rolling mean TR",
  "capitals":[str(x) for x in CAPITALS],"costs":{n:{k:str(v) for k,v in c.items()} for n,c in COSTS.items()},
  "windows":{n:[x[0].isoformat(),x[1].isoformat()] for n,x in WINDOWS.items()},"filter_hashes":prov["filter_hashes"]}
 cfg_hash=canonical_hash(cfg)
 indicators_payload={s:[{"timestamp":ts.isoformat(),**{k:(None if not math.isfinite(float(r[k])) else float(r[k])) for k in
  ("ema20","ema50","ema200","rsi14","atr14","adx14","signal_strength","weight")},
  "rsi_cross_up":bool(r.rsi_cross_up),"rsi_cross_down":bool(r.rsi_cross_down),"buy_signal":bool(r.buy_signal),"exit_signal":bool(r.exit_signal)}
  for ts,r in ind[s].iterrows()] for s in ASSETS}
 ind_hash=canonical_hash(indicators_payload)
 outdir.mkdir(parents=True,exist_ok=True); results=[]; summaries=[]; benches=[]; fills=[]; equities=[]
 for cost_name,cost in COSTS.items():
  for window in WINDOWS:
   for capital in CAPITALS:
    r=simulate(ind,filters,window,capital,cost_name,cost); results.append(r)
    summaries.append({k:r.get(k) for k in ("status","window","initial_usdt","cost_scenario","bars","start","end_exclusive","final_equity","net_pnl","return_pct","max_drawdown_pct","round_trips","fees","rejected_min_notional_or_lot")})
    fills += [{**t,"window":window,"initial_usdt":str(capital),"cost_scenario":cost_name} for t in r["trades"]]
    equities += [{**e,"window":window,"initial_usdt":str(capital),"cost_scenario":cost_name,"strategy":STRATEGY} for e in r["equity"]]
    for s in ASSETS: benches.append(buy_hold(frames[s],s,window,capital,cost_name,cost,filters))
 for (window,capital),pnl in BASELINE_15.items():
  benches.append({"status":"REPORTED_IN_ISSUE_181","window":window,"symbol":"ISSUE_181_ROTATION_ATR15","initial_usdt":str(capital),
   "cost_scenario":"STRESS_2","net_pnl":str(pnl),"return_pct":str(pnl/capital*HUNDRED),"source_url":"https://github.com/eltiootaku01-hue/bot-cripto/issues/181"})
 def metric(w,c): return next(x for x in results if x["window"]==w and Decimal(x["initial_usdt"])==c and x["cost_scenario"]=="STRESS_2")
 def bmetric(w,c,s): return next(x for x in benches if x["window"]==w and Decimal(str(x["initial_usdt"]))==c and x["cost_scenario"]=="STRESS_2" and x["symbol"]==s)
 gates=[]
 for capital in CAPITALS:
  for window in WINDOWS:
   r=metric(window,capital)
   gates.append({"gate":"positive_net_pnl","window":window,"capital":str(capital),"pass":Decimal(r["net_pnl"])>0,"observed":r["net_pnl"]})
   gates.append({"gate":"max_drawdown_ge_minus_15pct","window":window,"capital":str(capital),"pass":r["max_drawdown_pct"]>=-15,"observed":r["max_drawdown_pct"]})
  for window in ("COMBINED_2025_2026_YTD","2026_YTD"):
   r=metric(window,capital); ref=BASELINE_15[(window,capital)]
   gates.append({"gate":"minimum_5_round_trips","window":window,"capital":str(capital),"pass":r["round_trips"]>=5,"observed":r["round_trips"]})
   gates.append({"gate":"beat_rotation_atr15","window":window,"capital":str(capital),"pass":Decimal(r["net_pnl"])>ref,"observed":r["net_pnl"],"reference_pnl":str(ref)})
  ytd=metric("2026_YTD",capital)
  gates.append({"gate":"2026_YTD_beat_cash","window":"2026_YTD","capital":str(capital),"pass":Decimal(ytd["net_pnl"])>0,"observed":ytd["net_pnl"],"reference_pnl":"0"})
  for s in ASSETS:
   b=bmetric("2026_YTD",capital,s)
   gates.append({"gate":"2026_YTD_beat_"+s+"_buy_hold","window":"2026_YTD","capital":str(capital),
    "pass":Decimal(ytd["net_pnl"])>Decimal(b["net_pnl"]),"observed":ytd["net_pnl"],"reference_pnl":b["net_pnl"]})
 passed=sum(bool(g["pass"]) for g in gates); failed=len(gates)-passed
 decision="PASS_HISTORICAL_SHADOW_ONLY" if failed==0 and len(gates)==26 else "FAIL_NO_TRADE"
 result={"strategy":STRATEGY,"decision":decision,"status":"HISTORICAL_REPLAY_NOT_PROSPECTIVE_HOLDOUT","source_git_sha":source_sha,
  "source_file_sha256":prov["source_file_sha256"],"source_record_hashes":prov["record_hashes"],"canonical_ohlcv_hashes":prov["ohlcv_hashes"],
  "metadata_completeness_unknown":prov["metadata_completeness_unknown"],"metadata_finality_unknown":prov["metadata_finality_unknown"],
  "filter_hashes":prov["filter_hashes"],"strategy_configuration_sha256":cfg_hash,"indicators_and_signals_sha256":ind_hash,
  "bar_counts_4h":{s:len(frames[s]) for s in ASSETS},"bar_start_4h":{s:frames[s].index[0].isoformat() for s in ASSETS},
  "bar_end_4h":{s:frames[s].index[-1].isoformat() for s in ASSETS},"historical_point_in_time_availability":"UNKNOWN",
  "historical_executable_quotes":"UNKNOWN","historical_filter_equivalence":"UNKNOWN","costs_are_modelled_assumptions":True,
  "strategy_configuration":cfg,"scenario_results":summaries,"benchmarks":benches,"gates":gates,
  "gate_summary":{"passed":passed,"failed":failed,"total":len(gates)}}
 def write_json(name,obj): (outdir/name).write_text(json.dumps(obj,sort_keys=True,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else None)+"\n",encoding="utf-8")
 write_json("result.json",result);write_json("gate_results.json",{"decision":decision,"summary":result["gate_summary"],"gates":gates})
 write_csv(outdir/"summary.csv",summaries);write_csv(outdir/"benchmarks.csv",benches);write_csv(outdir/"fills.csv",fills);write_csv(outdir/"equity.csv",equities)
 report=["# BTC/ETH RSI14 reclaim + Wilder ADX trend filter V1 — deterministic, no AI","",f"Decision: {decision}",
  "Historical replay only, not a prospective holdout.",f"Canonical fetched source-bundle SHA-256: {prov['source_file_sha256']}",
  f"BTC source records SHA-256: {prov['record_hashes']['BTCUSDT']}",f"ETH source records SHA-256: {prov['record_hashes']['ETHUSDT']}",
  f"BTC canonical OHLCV SHA-256: {prov['ohlcv_hashes']['BTCUSDT']}",f"ETH canonical OHLCV SHA-256: {prov['ohlcv_hashes']['ETHUSDT']}",
  f"Configuration SHA-256: {cfg_hash}",f"Indicator/signals SHA-256: {ind_hash}",f"Source code SHA: {source_sha}",
  "Point-in-time historical availability, executable quotes, and historical filter equivalence: UNKNOWN. Spread/slippage are modeled assumptions.","",
  "## STRESS_2 net results","","| Window | Capital | PnL USDT | Return | Max DD | Round trips | Fees |","|---|---:|---:|---:|---:|---:|---:|"]
 for r in summaries:
  if r["cost_scenario"]=="STRESS_2":
   report.append(f"| {r['window']} | {r['initial_usdt']} | {float(r['net_pnl']):+.4f} | {float(r['return_pct']):+.2f}% | {float(r['max_drawdown_pct']):.2f}% | {r['round_trips']} | {float(r['fees']):.4f} |")
 report += ["","## Frozen gates","",f"PASS {passed}/{len(gates)}; FAIL {failed}.","| Gate | Window | Capital | Result | Observed | Reference |","|---|---|---:|---|---:|---:|"]
 for g in gates: report.append(f"| {g['gate']} | {g['window']} | {g['capital']} | {'PASS' if g['pass'] else 'FAIL'} | {g['observed']} | {g.get('reference_pnl','')} |")
 report += ["","## Limitations","","This uses fixed deterministic RSI/ADX indicators and no AI/ML. Historical PASS only allows consideration for prospective shadow, not PAPER/LIVE.",
  "Data point-in-time availability and historic executable quotes remain UNKNOWN. Main, PRIME, and operational NO_TRADE stay unchanged."]
 (outdir/"report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
 file_manifest={}
 for p in sorted(outdir.iterdir()):
  if p.is_file() and p.name!="manifest.json":
   b=p.read_bytes();file_manifest[p.name]={"size_bytes":len(b),"sha256":sha256_bytes(b)}
 write_json("manifest.json",{"strategy":STRATEGY,"decision":decision,"source_git_sha":source_sha,"source_file_sha256":prov["source_file_sha256"],
  "strategy_configuration_sha256":cfg_hash,"indicators_and_signals_sha256":ind_hash,"files":file_manifest})
 return result

def main():
 p=argparse.ArgumentParser(description="Frozen non-AI BTC/ETH RSI14 ADX backtest")
 p.add_argument("--dataset",type=Path);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--source-sha",default="UNKNOWN");p.add_argument("--fetch-pinned-candles",action="store_true")
 a=p.parse_args();r=run(a.dataset,a.output_dir,a.source_sha,a.fetch_pinned_candles)
 print("STRATEGY:",r["strategy"]);print("DECISION:",r["decision"]);print("DATASET_SHA256:",r["source_file_sha256"])
 print("CONFIG_SHA256:",r["strategy_configuration_sha256"]);print("INDICATORS_SHA256:",r["indicators_and_signals_sha256"])
 print("GATES:",json.dumps(r["gate_summary"],sort_keys=True))
 for x in r["scenario_results"]:
  if x["cost_scenario"]=="STRESS_2": print("STRESS_2:",x["window"],x["initial_usdt"],x["net_pnl"],x["return_pct"],x["max_drawdown_pct"],x["round_trips"])
 return 0
if __name__=="__main__": raise SystemExit(main())
