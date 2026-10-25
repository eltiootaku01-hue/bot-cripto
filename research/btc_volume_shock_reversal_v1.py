"""Frozen daily BTC volume-shock reversal. Research only; never submits orders."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys, time, urllib.parse, urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_FLOOR, getcontext
from pathlib import Path
from typing import Any, Sequence
getcontext().prec = 28
BASE_URL="https://data-api.binance.vision"
SYMBOL="BTCUSDT"
DAY_MS=86_400_000
START=date(2019,1,1)
END=date(2026,10,22)
VOLUME_LOOKBACK=20
DROP=Decimal("-0.025")
VOLUME_MULTIPLE=Decimal("1.5")
ALLOC=Decimal("0.20")
BH_ALLOC=Decimal("0.95")
TP=Decimal("1.025")
SL=Decimal("0.975")
MAX_HOLD=5
CAPITALS=(Decimal("60"),Decimal("100"))
COSTS={
 "BASE":{"fee":Decimal("0.001"),"spread_bps":Decimal("10"),"slippage_bps":Decimal("5")},
 "STRESS_1":{"fee":Decimal("0.001"),"spread_bps":Decimal("25"),"slippage_bps":Decimal("10")},
 "STRESS_2":{"fee":Decimal("0.002"),"spread_bps":Decimal("50"),"slippage_bps":Decimal("20")},
}
WINDOWS=(
 ("FULL_2020_2026",date(2020,1,1),date(2026,10,22)),
 ("EARLIER_2020_2022",date(2020,1,1),date(2023,1,1)),
 ("RECENT_2023_2026",date(2023,1,1),date(2026,10,22)),
 ("2020",date(2020,1,1),date(2021,1,1)),
 ("2021",date(2021,1,1),date(2022,1,1)),
 ("2022",date(2022,1,1),date(2023,1,1)),
 ("2023",date(2023,1,1),date(2024,1,1)),
 ("2024",date(2024,1,1),date(2025,1,1)),
 ("2025",date(2025,1,1),date(2026,1,1)),
 ("2026_YTD",date(2026,1,1),date(2026,10,22)),
)
class DataIntegrityError(RuntimeError): pass
class PublicDataError(RuntimeError): pass

def D(x:Any)->Decimal:
 y=x if isinstance(x,Decimal) else Decimal(str(x))
 if not y.is_finite(): raise ValueError("NON_FINITE_DECIMAL")
 return y
def ds(x:Decimal)->str: return format(x,"f")
def fmt(x:Decimal,places:int=8)->str: return f"{x:.{places}f}"
def sha(data:bytes)->str: return hashlib.sha256(data).hexdigest()
def canon(x:Any)->bytes: return json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def to_ms(day:date)->int: return int(datetime(day.year,day.month,day.day,tzinfo=timezone.utc).timestamp()*1000)
def stamp(ms:int)->str: return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat().replace("+00:00","Z")
def expected_times(start:date=START,end:date=END)->list[int]:
 if end<=start: raise ValueError("END_MUST_FOLLOW_START")
 return list(range(to_ms(start),to_ms(end),DAY_MS))

def get_json(url:str,retries:int=4)->tuple[Any,bytes]:
 error=None
 for attempt in range(retries):
  try:
   req=urllib.request.Request(url,headers={"User-Agent":"bot-cripto-research/1.0","Accept":"application/json"})
   with urllib.request.urlopen(req,timeout=40) as response:
    if response.status!=200: raise PublicDataError(f"HTTP_{response.status}")
    raw=response.read()
   return json.loads(raw.decode("utf-8")),raw
  except Exception as exc:
   error=exc
   if attempt+1<retries: time.sleep(attempt+1)
 raise PublicDataError(f"PUBLIC_REQUEST_FAILED:{url}:{error}")

def parse_klines(payload:Any)->list[dict[str,Any]]:
 if not isinstance(payload,list): raise PublicDataError("KLINE_RESPONSE_NOT_LIST")
 out=[]
 for row in payload:
  if not isinstance(row,list) or len(row)<7: raise PublicDataError("KLINE_ROW_SCHEMA_INVALID")
  ms=int(row[0])
  out.append({"open_ms":ms,"timestamp":stamp(ms),"close_ms":int(row[6]),
              "open":D(row[1]),"high":D(row[2]),"low":D(row[3]),"close":D(row[4]),"volume":D(row[5])})
 return out

def fetch_history(start:date=START,end:date=END)->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
 end_ms,cursor=to_ms(end),to_ms(start)
 rows,pages=[],[]
 while cursor<end_ms:
  url=BASE_URL+"/api/v3/klines?"+urllib.parse.urlencode({"symbol":SYMBOL,"interval":"1d","startTime":cursor,"endTime":end_ms-1,"limit":1000})
  payload,raw=get_json(url)
  page=[r for r in parse_klines(payload) if cursor<=r["open_ms"]<end_ms]
  pages.append({"url":url,"raw_sha256":sha(raw),"rows":len(page),"cursor_utc":stamp(cursor)})
  if not page: raise PublicDataError("EMPTY_PAGE_AT_"+stamp(cursor))
  rows.extend(page)
  latest=max(r["open_ms"] for r in page)
  if latest<cursor: raise PublicDataError("PAGINATION_DID_NOT_ADVANCE")
  cursor=latest+DAY_MS
 return rows,pages

def validate_candles(rows:Sequence[dict[str,Any]],start:date=START,end:date=END)->dict[str,Any]:
 expected,actual=expected_times(start,end),[int(r["open_ms"]) for r in rows]
 missing=sorted(set(expected)-set(actual)); unexpected=sorted(set(actual)-set(expected))
 duplicates=sorted(x for x,n in Counter(actual).items() if n>1)
 issues=[]
 if actual!=sorted(actual): issues.append("UNSORTED_TIMESTAMPS")
 if missing: issues.append("MISSING_DAILY_CANDLES")
 if unexpected: issues.append("UNEXPECTED_DAILY_CANDLES")
 if duplicates: issues.append("DUPLICATE_TIMESTAMPS")
 for r in rows:
  try:
   op,hi,lo,cl,vol=(D(r[k]) for k in ("open","high","low","close","volume"))
   if min(op,hi,lo,cl)<=0 or vol<0: issues.append("INVALID_OHLCV:"+stamp(int(r["open_ms"])))
   if hi<max(op,lo,cl) or lo>min(op,hi,cl): issues.append("OHLC_RANGE:"+stamp(int(r["open_ms"])))
   if int(r["close_ms"])!=int(r["open_ms"])+DAY_MS-1: issues.append("CLOSE_TIME:"+stamp(int(r["open_ms"])))
  except Exception: issues.append("INVALID_CANDLE:"+str(r.get("open_ms")))
 return {"ok":not issues,"problem_codes":issues[:100],"problem_count":len(issues),
  "expected_rows":len(expected),"observed_rows":len(rows),
  "missing_utc":[stamp(x) for x in missing[:300]],"unexpected_utc":[stamp(x) for x in unexpected[:100]],
  "duplicate_utc":[stamp(x) for x in duplicates[:100]]}

def filters_from_exchange_info(payload:Any)->dict[str,Decimal]:
 if not isinstance(payload,dict) or not isinstance(payload.get("symbols"),list): raise PublicDataError("EXCHANGEINFO_SCHEMA")
 symbols=[s for s in payload["symbols"] if s.get("symbol")==SYMBOL]
 if len(symbols)!=1: raise PublicDataError("EXCHANGEINFO_SYMBOL_NOT_UNIQUE")
 fs={f.get("filterType"):f for f in symbols[0].get("filters",[])}
 lot=fs.get("LOT_SIZE"); notion=fs.get("NOTIONAL") or fs.get("MIN_NOTIONAL")
 if not lot or not notion or not notion.get("minNotional"): raise PublicDataError("EXCHANGE_FILTER_MISSING")
 answer={"min_qty":D(lot["minQty"]),"step":D(lot["stepSize"]),"min_notional":D(notion["minNotional"])}
 if min(answer.values())<=0: raise PublicDataError("EXCHANGE_FILTER_NONPOSITIVE")
 return answer

def fetch_filters()->tuple[dict[str,Decimal],dict[str,Any]]:
 url=BASE_URL+"/api/v3/exchangeInfo?symbol="+SYMBOL
 payload,raw=get_json(url); filters=filters_from_exchange_info(payload)
 return filters,{"url":url,"raw_sha256":sha(raw),"canonical_sha256":sha(canon(payload)),
  "retrieved_at_utc":datetime.now(timezone.utc).isoformat(),"filters":{k:ds(v) for k,v in filters.items()},
  "historical_filter_equivalence":"UNKNOWN"}

def canonical_data_hash(rows:Sequence[dict[str,Any]])->str:
 data=[{"open_ms":int(r["open_ms"]),"close_ms":int(r["close_ms"]),
        **{k:ds(D(r[k])) for k in ("open","high","low","close","volume")}} for r in rows]
 return sha(canon(data))

def median_decimal(values:Sequence[Decimal])->Decimal:
 ordered=sorted(values)
 if not ordered: raise ValueError("EMPTY_MEDIAN")
 n=len(ordered)
 return ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])/2

def entry_signal(rows:Sequence[dict[str,Any]],i:int)->bool:
 """Signal at close i uses rows no later than i; its volume median excludes i."""
 if i<VOLUME_LOOKBACK: return False
 previous,close=D(rows[i-1]["close"]),D(rows[i]["close"])
 if previous<=0 or close/previous-1>DROP: return False
 baseline=median_decimal([D(rows[j]["volume"]) for j in range(i-VOLUME_LOOKBACK,i)])
 return D(rows[i]["volume"])>=baseline*VOLUME_MULTIPLE

def adjusted_price(raw:Decimal,side:str,cost:dict[str,Decimal])->Decimal:
 rate=cost["spread_bps"]/Decimal(20000)+cost["slippage_bps"]/Decimal(10000)
 if side=="BUY": return raw*(1+rate)
 if side=="SELL": return raw*(1-rate)
 raise ValueError("UNKNOWN_SIDE")
def floor_step(qty:Decimal,step:Decimal)->Decimal:
 if step<=0 or qty<0: raise ValueError("BAD_QTY_OR_STEP")
 return (qty/step).to_integral_value(rounding=ROUND_FLOOR)*step

def buy_size(cash:Decimal,raw:Decimal,fraction:Decimal,cost:dict[str,Decimal],
             filters:dict[str,Decimal])->tuple[Decimal,Decimal,Decimal,str|None]:
 px=adjusted_price(raw,"BUY",cost); budget=cash*fraction
 qty=floor_step(budget/(px*(1+cost["fee"])),filters["step"]) if budget>0 else Decimal(0)
 if qty<filters["min_qty"]: return Decimal(0),px,Decimal(0),"MIN_QTY"
 if qty*raw<filters["min_notional"]: return Decimal(0),px,Decimal(0),"MIN_NOTIONAL"
 fee=qty*px*cost["fee"]
 if qty*px+fee>budget or qty*px+fee>cash: return Decimal(0),px,Decimal(0),"BUDGET"
 return qty,px,fee,None

def mark_equity(cash:Decimal,pos:dict[str,Any]|None,close:Decimal,cost:dict[str,Decimal])->Decimal:
 return cash if pos is None else cash+pos["qty"]*adjusted_price(close,"SELL",cost)*(1-cost["fee"])
def max_drawdown(curve:Sequence[Decimal])->Decimal:
 peak=curve[0] if curve else Decimal(1); worst=Decimal(0)
 for value in curve:
  peak=max(peak,value)
  if peak>0: worst=min(worst,value/peak-1)
 return worst

def fill_row(window:str,capital:Decimal,cname:str,method:str,trade_id:str,side:str,reason:str,
             signal_i:int|None,exec_i:int,execution_type:str,raw:Decimal,px:Decimal,qty:Decimal,
             fee:Decimal,cash:Decimal,rows:Sequence[dict[str,Any]])->dict[str,str]:
 return {"window":window,"capital":ds(capital),"cost":cname,"method":method,"trade_id":trade_id,
  "side":side,"reason":reason,"signal_utc":rows[signal_i]["timestamp"] if signal_i is not None else "",
  "execution_utc":rows[exec_i]["timestamp"],"execution_type":execution_type,"raw_price":ds(raw),
  "fill_price":ds(px),"qty_btc":ds(qty),"notional_usdt":ds(qty*px),"fee_usdt":ds(fee),
  "friction_usdt":ds(abs(px-raw)*qty),"cash_after":ds(cash)}

def simulate(rows:Sequence[dict[str,Any]],a:int,b:int,capital:Decimal,cname:str,
             filters:dict[str,Decimal],window:str,method:str)->dict[str,Any]:
 cost=COSTS[cname]; cash=capital; pos=None; pending=None
 fills=[]; curve_rows=[{"timestamp_utc":rows[a]["timestamp"],"phase":"START","equity_usdt":ds(capital)}]
 curve=[capital]; fees=Decimal(0); friction=Decimal(0); rejects=0; closed=0; wins=0; losses=0; trade_no=0
 if method=="BTC_BUY_AND_HOLD":
  qty,px,fee,reject=buy_size(cash,D(rows[a]["open"]),BH_ALLOC,cost,filters)
  if reject: rejects+=1
  else:
   cash-=qty*px+fee; fees+=fee; friction+=abs(px-D(rows[a]["open"]))*qty
   tid=f"{window}-{int(capital)}-{cname}-BH"
   pos={"qty":qty,"entry_spend":qty*px+fee,"entry_price":px,"entry_index":a,"trade_id":tid}
   fills.append(fill_row(window,capital,cname,method,tid,"BUY","BENCHMARK_ENTRY",None,a,"WINDOW_START_OPEN",
                         D(rows[a]["open"]),px,qty,fee,cash,rows))
 for i in range(a,b):
  bar=rows[i]
  if method=="VOLUME_SHOCK_REVERSAL" and i>a and pending:
   action=pending
   if action["side"]=="BUY" and pos is None:
    qty,px,fee,reject=buy_size(cash,D(bar["open"]),ALLOC,cost,filters)
    if reject: rejects+=1
    else:
     trade_no+=1; spend=qty*px+fee; cash-=spend; fees+=fee; friction+=abs(px-D(bar["open"]))*qty
     tid=f"{window}-{int(capital)}-{cname}-{trade_no:04d}"
     pos={"qty":qty,"entry_spend":spend,"entry_price":px,"entry_index":i,"trade_id":tid}
     fills.append(fill_row(window,capital,cname,method,tid,"BUY",action["reason"],action["signal_i"],i,
                           "NEXT_OPEN",D(bar["open"]),px,qty,fee,cash,rows))
   elif action["side"]=="SELL" and pos is not None:
    qty=pos["qty"]; raw=D(bar["open"]); px=adjusted_price(raw,"SELL",cost); gross=qty*px; fee=gross*cost["fee"]
    cash+=gross-fee; fees+=fee; friction+=abs(px-raw)*qty; pnl=gross-fee-pos["entry_spend"]
    closed+=1; wins+=int(pnl>0); losses+=int(pnl<=0)
    fills.append(fill_row(window,capital,cname,method,pos["trade_id"],"SELL",action["reason"],action["signal_i"],i,
                          "NEXT_OPEN",raw,px,qty,fee,cash,rows))
    pos=None
   pending=None
  eq=mark_equity(cash,pos,D(bar["close"]),cost); curve.append(eq)
  curve_rows.append({"timestamp_utc":bar["timestamp"],"phase":"CLOSE","equity_usdt":ds(eq)})
  if method!="VOLUME_SHOCK_REVERSAL" or i>=b-1: continue
  if pos is None:
   if entry_signal(rows,i): pending={"side":"BUY","signal_i":i,"reason":"NEGATIVE_SHOCK_HIGH_VOLUME"}
  else:
   held=i-pos["entry_index"]+1
   reason=("TAKE_PROFIT_2_5_PERCENT" if D(bar["close"])>=pos["entry_price"]*TP else
           "STOP_LOSS_2_5_PERCENT" if D(bar["close"])<=pos["entry_price"]*SL else
           "TIME_EXIT_5_CANDLES" if held>=MAX_HOLD else None)
   if reason: pending={"side":"SELL","signal_i":i,"reason":reason}
 if pos is not None:
  i=b-1; raw=D(rows[i]["close"]); px=adjusted_price(raw,"SELL",cost); qty=pos["qty"]
  gross=qty*px; fee=gross*cost["fee"]; cash+=gross-fee; fees+=fee; friction+=abs(px-raw)*qty
  pnl=gross-fee-pos["entry_spend"]; closed+=1; wins+=int(pnl>0); losses+=int(pnl<=0)
  fills.append(fill_row(window,capital,cname,method,pos["trade_id"],"SELL","TERMINAL_LIQUIDATION",b-1,i,
                        "TERMINAL_CLOSE",raw,px,qty,fee,cash,rows))
  curve[-1]=cash; curve_rows[-1]["equity_usdt"]=ds(cash)
 return {"method":method,"final":cash,"pnl":cash-capital,"return_pct":(cash/capital-1)*100,
  "mdd_pct":max_drawdown(curve)*100,"trades":closed,"wins":wins,"losses":losses,
  "fees":fees,"friction":friction,"rejects":rejects,"fills":fills,"curve":curve_rows}

def csv_write(path:Path,data:Sequence[dict[str,Any]],fields:Sequence[str])->None:
 with path.open("w",newline="",encoding="utf-8") as handle:
  w=csv.DictWriter(handle,fieldnames=list(fields),extrasaction="ignore"); w.writeheader(); w.writerows(data)
def metric_row(window:str,start:date,end:date,capital:Decimal,cname:str,result:dict[str,Any])->dict[str,str]:
 return {"window":window,"start_utc":start.isoformat(),"end_exclusive_utc":end.isoformat(),"capital":ds(capital),
  "cost":cname,"method":result["method"],"final_equity_usdt":fmt(result["final"]),
  "net_pnl_usdt":fmt(result["pnl"]),"return_pct":fmt(result["return_pct"],6),
  "max_drawdown_pct":fmt(result["mdd_pct"],6),"closed_roundtrips":str(result["trades"]),
  "wins":str(result["wins"]),"losses":str(result["losses"]),"fees_usdt":fmt(result["fees"]),
  "spread_slippage_usdt":fmt(result["friction"]),"rejected_orders":str(result["rejects"])}
def metric_value(metrics:Sequence[dict[str,str]],window:str,capital:Decimal,cost:str,method:str,key:str)->Decimal:
 for r in metrics:
  if r["window"]==window and r["capital"]==ds(capital) and r["cost"]==cost and r["method"]==method: return D(r[key])
 raise KeyError("MISSING_METRIC:"+window+":"+ds(capital)+":"+cost+":"+method+":"+key)

def gate_results(metrics:Sequence[dict[str,str]],data_ok:bool)->dict[str,Any]:
 if not data_ok: return {"overall":"BLOCKED","checks":[{"gate":"DATA_INTEGRITY","status":"BLOCKED"}]}
 checks=[]
 def add(name:str,ok:bool,detail:str): checks.append({"gate":name,"status":"PASS" if ok else "FAIL","details":detail})
 principal=("FULL_2020_2026","EARLIER_2020_2022","RECENT_2023_2026","2026_YTD")
 for capital in CAPITALS:
  cap=str(int(capital))
  for win in principal:
   pnl=metric_value(metrics,win,capital,"STRESS_2","VOLUME_SHOCK_REVERSAL","net_pnl_usdt")
   n=int(metric_value(metrics,win,capital,"STRESS_2","VOLUME_SHOCK_REVERSAL","closed_roundtrips"))
   add(f"positive_pnl_{win}_{cap}",pnl>0,f"PnL={pnl} USDT")
   add(f"five_roundtrips_{win}_{cap}",n>=5,f"closed={n}")
  for win,_,_ in WINDOWS:
   dd=metric_value(metrics,win,capital,"STRESS_2","VOLUME_SHOCK_REVERSAL","max_drawdown_pct")
   add(f"drawdown_at_least_minus15_{win}_{cap}",dd>=-15,f"max drawdown={dd}%")
  p22=metric_value(metrics,"2022",capital,"STRESS_2","VOLUME_SHOCK_REVERSAL","net_pnl_usdt")
  add(f"2022_nonnegative_{cap}",p22>=0,f"PnL={p22} USDT")
  annual=("2020","2021","2022","2023","2024","2025","2026_YTD")
  positive=sum(metric_value(metrics,w,capital,"STRESS_2","VOLUME_SHOCK_REVERSAL","net_pnl_usdt")>0 for w in annual)
  add(f"four_positive_annual_windows_{cap}",positive>=4,f"positive={positive}/7")
  recent=metric_value(metrics,"RECENT_2023_2026",capital,"STRESS_2","VOLUME_SHOCK_REVERSAL","net_pnl_usdt")
  cash=metric_value(metrics,"RECENT_2023_2026",capital,"STRESS_2","CASH","net_pnl_usdt")
  bh=metric_value(metrics,"RECENT_2023_2026",capital,"STRESS_2","BTC_BUY_AND_HOLD","net_pnl_usdt")
  add(f"recent_beats_cash_{cap}",recent>cash,f"strategy={recent}; cash={cash}")
  add(f"recent_beats_btc_buy_hold_{cap}",recent>bh,f"strategy={recent}; BTC_BH={bh}")
 fails=sum(1 for c in checks if c["status"]!="PASS")
 return {"overall":"PASS" if fails==0 else "FAIL","passed":len(checks)-fails,"failed":fails,"checks":checks,
         "promotion_note":"Previously inspected historical archive; any pass still requires independent prospective shadow validation."}

def write_blocked(outdir:Path,error:str,manifest:dict[str,Any])->int:
 outdir.mkdir(parents=True,exist_ok=True); manifest["status"]="BLOCKED"; manifest["blocker"]=error
 (outdir/"dataset_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding="utf-8")
 (outdir/"report.md").write_text("# BTC volume-shock reversal V1\n\nOverall status: BLOCKED; no economic result is valid.\n\n"
  f"Blocker: {error}\n\nNo candles were interpolated or silently dropped. Historical point-in-time availability and historical filters remain UNKNOWN. Operational decision: NO_TRADE.\n",encoding="utf-8")
 print("BLOCKED:"+error,file=sys.stderr); return 2

def run(outdir:Path)->int:
 outdir.mkdir(parents=True,exist_ok=True)
 manifest={"protocol":"BTC_VOLUME_SHOCK_REVERSAL_V1","symbol":SYMBOL,"interval":"1d","start_utc":START.isoformat(),
  "end_exclusive_utc":END.isoformat(),"point_in_time_availability":"UNKNOWN",
  "historical_exchange_filter_equivalence":"UNKNOWN","historic_spread_slippage":"MODELED_ASSUMPTIONS_ONLY",
  "main_changed":False,"operational_decision":"NO_TRADE",
  "costs":{k:{a:ds(v) for a,v in val.items()} for k,val in COSTS.items()},
  "frozen_rule":{"return_lte":ds(DROP),"volume_gte_previous_20_median_times":ds(VOLUME_MULTIPLE),
   "entry_fraction":ds(ALLOC),"take_profit_multiple":ds(TP),"stop_loss_multiple":ds(SL),
   "max_holding_daily_candles":MAX_HOLD,"execution":"closed daily candle signal; next open fill; terminal close liquidation"}}
 try:
  rows,pages=fetch_history()
  quality=validate_candles(rows); manifest["quality"]=quality; manifest["row_count"]=len(rows)
  manifest["raw_pages"]=pages; manifest["canonical_dataset_sha256"]=canonical_data_hash(rows)
  if not quality["ok"]: return write_blocked(outdir,"DATA_INTEGRITY_GATE_FAILED",manifest)
  filters,filter_evidence=fetch_filters(); manifest["exchange_info"]=filter_evidence
  manifest["filters_current"]={k:ds(v) for k,v in filters.items()}
  manifest["first_open_utc"]=rows[0]["timestamp"]; manifest["last_open_utc"]=rows[-1]["timestamp"]
  with (outdir/"candles.csv").open("w",newline="",encoding="utf-8") as h:
   writer=csv.DictWriter(h,fieldnames=["open_ms","timestamp","close_ms","open","high","low","close","volume"]); writer.writeheader()
   for r in rows: writer.writerow({"open_ms":r["open_ms"],"timestamp":r["timestamp"],"close_ms":r["close_ms"],
    **{k:ds(r[k]) for k in ("open","high","low","close","volume")}})
  idx={r["open_ms"]:i for i,r in enumerate(rows)}
  metrics=[]; fill_rows=[]; equity_rows=[]
  for wname,start,end in WINDOWS:
   if to_ms(start) not in idx: raise DataIntegrityError("WINDOW_START_MISSING:"+wname)
   a=idx[to_ms(start)]
   # The global data end is exclusive, so no candle exists at END.
   b=idx.get(to_ms(end),len(rows))
   if b<=a: raise DataIntegrityError("WINDOW_BOUNDARY_ORDER:"+wname)
   for capital in CAPITALS:
    for cname in COSTS:
     for method in ("VOLUME_SHOCK_REVERSAL","BTC_BUY_AND_HOLD","CASH"):
      if method=="CASH":
       result={"method":"CASH","final":capital,"pnl":Decimal(0),"return_pct":Decimal(0),"mdd_pct":Decimal(0),
        "trades":0,"wins":0,"losses":0,"fees":Decimal(0),"friction":Decimal(0),"rejects":0,"fills":[],
        "curve":[{"timestamp_utc":rows[a]["timestamp"],"phase":"START","equity_usdt":ds(capital)}]}
       result["curve"] += [{"timestamp_utc":rows[i]["timestamp"],"phase":"CLOSE","equity_usdt":ds(capital)} for i in range(a,b)]
      else: result=simulate(rows,a,b,capital,cname,filters,wname,method)
      metrics.append(metric_row(wname,start,end,capital,cname,result))
      fill_rows.extend(result["fills"])
      equity_rows.extend({"window":wname,"capital":ds(capital),"cost":cname,"method":method,**x} for x in result["curve"])
  mf=["window","start_utc","end_exclusive_utc","capital","cost","method","final_equity_usdt","net_pnl_usdt","return_pct",
      "max_drawdown_pct","closed_roundtrips","wins","losses","fees_usdt","spread_slippage_usdt","rejected_orders"]
  ff=["window","capital","cost","method","trade_id","side","reason","signal_utc","execution_utc","execution_type",
      "raw_price","fill_price","qty_btc","notional_usdt","fee_usdt","friction_usdt","cash_after"]
  ef=["window","capital","cost","method","timestamp_utc","phase","equity_usdt"]
  csv_write(outdir/"summary.csv",metrics,mf); csv_write(outdir/"fills.csv",fill_rows,ff); csv_write(outdir/"equity.csv",equity_rows,ef)
  gr=gate_results(metrics,True); manifest["status"]="COMPLETED"; manifest["strategy_gate"]=gr["overall"]
  manifest["output_counts"]={"summary_rows":len(metrics),"fill_rows":len(fill_rows),"equity_rows":len(equity_rows)}
  (outdir/"dataset_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding="utf-8")
  (outdir/"gate_results.json").write_text(json.dumps(gr,indent=2,sort_keys=True),encoding="utf-8")
  lines=["# BTC daily abnormal-volume shock reversal V1","", "Frozen economic gate: "+gr["overall"],"",
   "Dataset SHA-256: "+manifest["canonical_dataset_sha256"],"Candle count: "+str(manifest["row_count"]),
   "Continuity validation: PASS; timestamp, close-time, and OHLCV contracts passed.",
   "Historical point-in-time availability: UNKNOWN.","Historical exchange-filter equivalence: UNKNOWN.",
   "Spread and slippage are modeled assumptions, not reconstructed historical quotes.","",
   "## Strategy results across frozen costs","",
   "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | Fees | Spread/slippage | Rejected |",
   "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
  for wname,_,_ in WINDOWS:
   for capital in CAPITALS:
    for cname in COSTS:
     r=next(x for x in metrics if x["window"]==wname and x["capital"]==ds(capital) and x["cost"]==cname and x["method"]=="VOLUME_SHOCK_REVERSAL")
     lines.append(f"| {wname} | {int(capital)} | {cname} | {r['net_pnl_usdt']} | {r['return_pct']} | {r['max_drawdown_pct']} | {r['closed_roundtrips']} | {r['fees_usdt']} | {r['spread_slippage_usdt']} | {r['rejected_orders']} |")
  lines += ["","## Frozen gate outcomes","","Passed: "+str(gr["passed"])+"; failed: "+str(gr["failed"]),"",
   "| Gate | Status | Details |","|---|---|---|"]
  lines += [f"| {c['gate']} | {c['status']} | {c.get('details','')} |" for c in gr["checks"]]
  lines += ["","A successful workflow means the research code completed; it does not prove profitability.",
   "This historical archive is not a pristine holdout. A pass would still require prospective shadow validation.",
   "No private endpoints, credentials, real orders, leverage, or main-branch changes are used.","Operational decision: NO_TRADE."]
  (outdir/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
  print(json.dumps({"status":"COMPLETED","rows":len(rows),"dataset_sha256":manifest["canonical_dataset_sha256"],
   "strategy_gate":gr["overall"],"passed":gr["passed"],"failed":gr["failed"],"main_changed":False},sort_keys=True))
  return 0
 except Exception as exc: return write_blocked(outdir,type(exc).__name__+":"+str(exc),manifest)

def main(argv:Sequence[str]|None=None)->int:
 parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output-dir",default="volume-shock-output")
 return run(Path(parser.parse_args(argv).output_dir))
if __name__=="__main__": raise SystemExit(main())
