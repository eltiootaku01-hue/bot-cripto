"""Frozen BTC hourly RSI(2) pullback with EMA720, ATR14 stop, and 24-hour maximum hold."""
from __future__ import annotations
import csv,hashlib,json,subprocess
from datetime import datetime,timedelta,timezone
from decimal import Decimal,ROUND_DOWN
from pathlib import Path
from bot_obrero.binance_instruments import BinanceMetadataBackedAdapter,BinanceSpotInstrumentMetadata
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.virtual_backtest import _market_data_record,_validate_history

UTC=timezone.utc
INTERVAL="1h"; BAR=timedelta(hours=1)
START=datetime(2026,1,1,tzinfo=UTC); END=datetime(2026,10,22,tzinfo=UTC)
WARMUP_BARS=720; WARMUP_START=START-BAR*WARMUP_BARS
EVAL_BARS=int((END-START)/BAR); EXPECTED_BARS=WARMUP_BARS+EVAL_BARS
ZERO,ONE,HUNDRED,BPS=Decimal("0"),Decimal("1"),Decimal("100"),Decimal("10000")
POSITION_FRACTION=Decimal("0.50"); BENCHMARK_FRACTION=Decimal("0.95")
EMA_PERIOD=720; RSI_PERIOD=2; RSI_ENTRY=Decimal("10"); RSI_EXIT=Decimal("60")
ATR_PERIOD=14; ATR_STOP_MULTIPLE=Decimal("1.5"); MAX_HOLD_BARS=24; MAX_BUYS_PER_30D=5
SYMBOL="BTCUSDT"
SCENARIOS=(("BASE",Decimal("0.001"),Decimal("10"),Decimal("5")),
           ("STRESS_1",Decimal("0.001"),Decimal("25"),Decimal("10")),
           ("STRESS_2",Decimal("0.002"),Decimal("50"),Decimal("20")))
WINDOWS=(("FULL_YTD",START,END),
         ("FOLD_JAN_MAR",START,datetime(2026,4,1,tzinfo=UTC)),
         ("FOLD_APR_JUN",datetime(2026,4,1,tzinfo=UTC),datetime(2026,7,1,tzinfo=UTC)),
         ("FOLD_JUL_OCT",datetime(2026,7,1,tzinfo=UTC),END))

def _current_rules(symbol):
    metadata=BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    instrument=metadata.resolve_active(symbol); payload=metadata._request(symbol)
    symbols=payload.get("symbols") if isinstance(payload,dict) else None
    if not isinstance(symbols,list) or len(symbols)!=1 or not isinstance(symbols[0],dict):
        raise RuntimeError(f"FAIL-CLOSED: ExchangeInfo did not return one {symbol} record")
    rec=symbols[0]
    if rec.get("symbol")!=symbol or rec.get("status")!="TRADING" or rec.get("isSpotTradingAllowed") is not True:
        raise RuntimeError(f"FAIL-CLOSED: ExchangeInfo does not confirm active Spot {symbol}")
    filters=rec.get("filters")
    if not isinstance(filters,list): raise RuntimeError("FAIL-CLOSED: exchange filters missing")
    lot=next((x for x in filters if isinstance(x,dict) and x.get("filterType")=="LOT_SIZE"),None)
    notional=next((x for x in filters if isinstance(x,dict) and x.get("filterType") in ("NOTIONAL","MIN_NOTIONAL")),None)
    if not isinstance(lot,dict) or not isinstance(notional,dict): raise RuntimeError("FAIL-CLOSED: LOT_SIZE or min-notional filter missing")
    try: step,min_qty,min_notional=Decimal(lot["stepSize"]),Decimal(lot["minQty"]),Decimal(notional["minNotional"])
    except (KeyError,TypeError,ValueError,ArithmeticError) as exc: raise RuntimeError("FAIL-CLOSED: malformed filters") from exc
    if min(step,min_qty,min_notional)<=ZERO: raise RuntimeError("FAIL-CLOSED: filters must be positive")
    snapshot={"LOT_SIZE":lot,"MIN_NOTIONAL_OR_NOTIONAL":notional}
    sha=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    return {"instrument_id":instrument.instrument_id,"step":step,"min_qty":min_qty,"min_notional":min_notional,
            "snapshot":snapshot,"sha256":sha,"retrieved_at_utc":datetime.now(UTC).isoformat(),
            "scope":"current public ExchangeInfo only; historic filters unknown"}

def _fetch(rules):
    meta=BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    meta.resolve_active(SYMBOL); adapter=BinanceMetadataBackedAdapter(metadata=meta)
    items=sorted((x for x in adapter.fetch_historical_market_data(
        symbol=SYMBOL,interval=INTERVAL,start_time=int(WARMUP_START.timestamp()*1000),
        end_time=int(END.timestamp()*1000),page_limit=1000,max_requests=10)
        if WARMUP_START<=x.payload.start<END),key=lambda x:x.payload.start)
    expected=[WARMUP_START+BAR*i for i in range(EXPECTED_BARS)]
    observed=[x.payload.start for x in items]
    if observed!=expected:
        raise RuntimeError(f"FAIL-CLOSED BTCUSDT: expected {EXPECTED_BARS} contiguous hourly candles, got {len(items)}; missing={len(set(expected)-set(observed))}; unexpected={len(set(observed)-set(expected))}. No interpolation.")
    accepted,excluded,unknown_comp,unknown_final=_validate_history(items,INTERVAL)
    if len(accepted)!=EXPECTED_BARS or excluded: raise RuntimeError(f"FAIL-CLOSED BTCUSDT: accepted={len(accepted)}, excluded={excluded}")
    if any(x.instrument.instrument_id!=rules["instrument_id"] for x in items): raise RuntimeError("FAIL-CLOSED: instrument identity mismatch")
    records=[_market_data_record(x) for x in items]
    sha=hashlib.sha256(json.dumps(records,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    return items,records,sha,unknown_comp,unknown_final

def _ema(values,period):
    alpha=Decimal("2")/Decimal(period+1); out=[None]*len(values); value=values[0]
    for i,x in enumerate(values):
        if i: value=alpha*x+(ONE-alpha)*value
        if i>=period-1: out[i]=value
    return out

def _rsi(values,period=2):
    gains=[ZERO]*len(values); losses=[ZERO]*len(values); out=[None]*len(values)
    for i in range(1,len(values)):
        d=values[i]-values[i-1]; gains[i]=max(d,ZERO); losses[i]=max(-d,ZERO)
    for i in range(period,len(values)):
        g=sum(gains[i-period+1:i+1],ZERO)/Decimal(period)
        l=sum(losses[i-period+1:i+1],ZERO)/Decimal(period)
        if g==ZERO and l==ZERO: out[i]=Decimal("50")
        elif l==ZERO: out[i]=HUNDRED
        else: out[i]=HUNDRED-HUNDRED/(ONE+g/l)
    return out

def _atr(highs,lows,closes,period=14):
    tr=[None]*len(closes); out=[None]*len(closes)
    for i in range(1,len(closes)):
        tr[i]=max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
    for i in range(period,len(closes)):
        window=tr[i-period+1:i+1]
        if all(v is not None for v in window): out[i]=sum(window,ZERO)/Decimal(period)
    return out

def _adverse(spread,slippage): return (slippage+spread/Decimal("2"))/BPS
def _floor_step(value,step): return (value/step).to_integral_value(rounding=ROUND_DOWN)*step

def _buy_hold(eval_items,capital,rules,fee,spread,slippage):
    adverse=_adverse(spread,slippage); rule=rules
    price=eval_items[0].payload.open*(ONE+adverse)
    qty=_floor_step(capital*BENCHMARK_FRACTION/(price*(ONE+fee)),rule["step"]); notional=qty*price
    if qty<rule["min_qty"] or notional<rule["min_notional"] or notional*(ONE+fee)>capital:
        raise RuntimeError("FAIL-CLOSED: BTC buy-and-hold violates lot assumptions")
    buy_fee=notional*fee; cash=capital-notional-buy_fee
    exit_notional=qty*eval_items[-1].payload.close*(ONE-adverse); sell_fee=exit_notional*fee
    return cash+exit_notional-sell_fee,buy_fee+sell_fee

def _simulate(items,window_name,window_start,window_end,capital,scenario,fee,spread,slippage,
              closes,opens,highs,lows,emas,rsis,atrs,rules,hashes):
    index={x.payload.start:i for i,x in enumerate(items)}
    ev=[x for x in items if window_start<=x.payload.start<window_end]
    expected=int((window_end-window_start)/BAR)
    if len(ev)!=expected: raise RuntimeError(f"FAIL-CLOSED {window_name}: got {len(ev)} bars, expected {expected}")
    quote,pos,entry_cost=capital,ZERO,ZERO
    entry_price=None; entry_index=None; pending=None; fees=ZERO; peak=capital
    fills,curve,realized,buy_times=[],[],[],[]
    for ei,item in enumerate(ev):
        c=item.payload; ts=c.start; i=index[ts]
        if pending is not None:
            side=pending; pending=None
            if side=="BUY" and pos==ZERO:
                adv=_adverse(spread,slippage); px=opens[i]*(ONE+adv)
                qty=_floor_step(quote*POSITION_FRACTION/(px*(ONE+fee)),rules["step"]); notional=qty*px
                cutoff=ts-timedelta(days=30); buy_times=[t for t in buy_times if cutoff<t<=ts]
                reject=None
                if qty<rules["min_qty"]: reject="MIN_QTY"
                elif notional<rules["min_notional"]: reject="MIN_NOTIONAL"
                elif notional*(ONE+fee)>quote: reject="COSTS_EXCEED_BALANCE"
                elif len(buy_times)>=MAX_BUYS_PER_30D: reject="MAX_BUYS_IN_ROLLING_30D"
                if reject:
                    fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                        "timestamp_utc":ts.isoformat(),"side":"REJECTED_BUY","symbol":SYMBOL,"quantity":str(qty),
                        "price_usdt":str(px),"fee_usdt":"0","realized_pnl_usdt":"","reason":reject})
                else:
                    f=notional*fee; entry_cost=notional+f; quote-=entry_cost; fees+=f; pos=qty
                    entry_price=px; entry_index=i; buy_times.append(ts)
                    fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                        "timestamp_utc":ts.isoformat(),"side":"BUY","symbol":SYMBOL,"quantity":str(qty),
                        "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":"",
                        "reason":"RSI2<10 and close>EMA720; 50% sizing"})
            elif side=="SELL" and pos>ZERO:
                adv=_adverse(spread,slippage); px=opens[i]*(ONE-adv); notional=pos*px; f=notional*fee
                proceeds=notional-f; pnl=proceeds-entry_cost; quote+=proceeds; fees+=f; realized.append(pnl)
                fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                    "timestamp_utc":ts.isoformat(),"side":"SELL","symbol":SYMBOL,"quantity":str(pos),
                    "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":str(pnl),"reason":"next-open exit"})
                pos,entry_cost=ZERO,ZERO; entry_price=None; entry_index=None
        adv=_adverse(spread,slippage)
        eq=quote if pos==ZERO else quote+pos*closes[i]*(ONE-adv)*(ONE-fee)
        peak=max(peak,eq)
        curve.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
            "timestamp_utc":c.end.isoformat(),"equity_usdt":str(eq),"position_btc":str(pos),
            "drawdown_pct":str((eq/peak-ONE)*HUNDRED if peak else ZERO)})
        if ei==len(ev)-1: continue
        # Form signal after current completed close; fill can only occur at the next hourly open.
        if pos==ZERO:
            if rsis[i] is not None and rsis[i]<RSI_ENTRY and emas[i] is not None and closes[i]>emas[i]:
                pending="BUY"
        else:
            bars_held=i-entry_index+1
            atr=atrs[i]
            hit_rsi=rsis[i] is not None and rsis[i]>=RSI_EXIT
            lost_regime=emas[i] is not None and closes[i]<=emas[i]
            hit_stop=atr is not None and entry_price is not None and closes[i]<=entry_price-ATR_STOP_MULTIPLE*atr
            hit_time=bars_held>=MAX_HOLD_BARS
            if hit_rsi or lost_regime or hit_stop or hit_time:
                why=("RSI2>=60" if hit_rsi else "close<=EMA720" if lost_regime else
                     "close<=entry-1.5*ATR14" if hit_stop else "24-hour timeout")
                fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                    "timestamp_utc":c.end.isoformat(),"side":"SIGNAL_SELL","symbol":SYMBOL,"quantity":str(pos),
                    "price_usdt":"","fee_usdt":"0","realized_pnl_usdt":"","reason":why})
                pending="SELL"
    if pos>ZERO:
        last=ev[-1].payload; i=index[last.start]
        px=closes[i]*(ONE-_adverse(spread,slippage)); notional=pos*px; f=notional*fee
        proceeds=notional-f; pnl=proceeds-entry_cost; quote+=proceeds; fees+=f; realized.append(pnl)
        fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
            "timestamp_utc":last.end.isoformat(),"side":"SELL","symbol":SYMBOL,"quantity":str(pos),
            "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":str(pnl),"reason":"terminal liquidation"})
        peak=max(peak,Decimal(curve[-1]["equity_usdt"])); pos=ZERO
        curve[-1]["equity_usdt"]=str(quote); curve[-1]["position_btc"]="0"
        curve[-1]["drawdown_pct"]=str((quote/peak-ONE)*HUNDRED if peak else ZERO)
    bnh,bnh_fees=_buy_hold(ev,capital,rules,fee,spread,slippage)
    sells=[f for f in fills if f["period"]==window_name and f["capital_initial_usdt"]==str(capital)
        and f["cost_scenario"]==scenario and f["side"]=="SELL"]
    result={"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
        "final_equity_usdt":str(quote),"net_pnl_usdt":str(quote-capital),"return_pct":str((quote/capital-ONE)*HUNDRED),
        "max_drawdown_pct":str(min((Decimal(x["drawdown_pct"]) for x in curve),default=ZERO)),
        "completed_round_trips":len(sells),"wins":sum(p>ZERO for p in realized),"losses":sum(p<ZERO for p in realized),
        "total_fees_usdt":str(fees),"cash_final_equity_usdt":str(capital),
        "btc_buy_hold_final_equity_usdt":str(bnh),"btc_buy_hold_net_pnl_usdt":str(bnh-capital),
        "btc_buy_hold_fees_usdt":str(bnh_fees),"dataset_sha256":hashes["dataset"],
        "filters_sha256":hashes["filters"],"evaluation_bars":expected}
    return result,fills,curve

def main():
    out=Path("btc-rsi2-output"); out.mkdir(exist_ok=True)
    print("Frozen BTC 1h RSI2 pullback; EMA720 regime filter; 1.5 ATR14 stop; 24-bar timeout; 50% sizing.")
    print("Research HEAD:",subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip())
    rules=_current_rules(SYMBOL)
    got=_fetch(rules); items,records,dataset_sha,unknown_comp,unknown_final=got
    print(f"BTCUSDT bars={len(items)} SHA256={dataset_sha}; completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}; current filter SHA={rules['sha256']}")
    manifest={"symbol":SYMBOL,"interval":INTERVAL,"evaluation_start":START.isoformat(),"evaluation_end_exclusive":END.isoformat(),
        "warmup_start":WARMUP_START.isoformat(),"warmup_bars":WARMUP_BARS,"expected_bars":EXPECTED_BARS,
        "observed_bars":len(items),"dataset_sha256":dataset_sha,"completeness_unknown":unknown_comp,"finality_unknown":unknown_final,
        "current_filters":{"step":str(rules["step"]),"min_qty":str(rules["min_qty"]),"min_notional":str(rules["min_notional"]),
            "snapshot":rules["snapshot"],"sha256":rules["sha256"],"retrieved_at_utc":rules["retrieved_at_utc"],"scope":rules["scope"]},
        "no_missing_or_interpolated_bars":True,"records":records}
    (out/"dataset-evidence.json").write_text(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    closes=[x.payload.close for x in items]; opens=[x.payload.open for x in items]
    highs=[x.payload.high for x in items]; lows=[x.payload.low for x in items]
    emas=_ema(closes,EMA_PERIOD); rsis=_rsi(closes,RSI_PERIOD); atrs=_atr(highs,lows,closes,ATR_PERIOD)
    hashes={"dataset":dataset_sha,"filters":rules["sha256"]}
    all_results=[]; all_fills=[]; all_curves=[]
    for name,start,end in WINDOWS:
        for capital in (Decimal("60"),Decimal("100")):
            for scenario,fee,spread,slippage in SCENARIOS:
                result,fills,curve=_simulate(items,name,start,end,capital,scenario,fee,spread,slippage,
                    closes,opens,highs,lows,emas,rsis,atrs,rules,hashes)
                all_results.append(result); all_fills.extend(fills); all_curves.extend(curve)
                print(f"RESULT | {name} | {capital} | {scenario} | PnL={Decimal(result['net_pnl_usdt']):+.4f} | "
                      f"return={Decimal(result['return_pct']):+.3f}% | DD={Decimal(result['max_drawdown_pct']):.3f}% | "
                      f"trades={result['completed_round_trips']} W/L={result['wins']}/{result['losses']} | "
                      f"fees={Decimal(result['total_fees_usdt']):.4f} | BTC B&H={Decimal(result['btc_buy_hold_net_pnl_usdt']):+.4f}")
    for name,rows in (("summary.csv",all_results),("fills.csv",all_fills),("equity.csv",all_curves)):
        with (out/name).open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=sorted({k for row in rows for k in row})); w.writeheader(); w.writerows(rows)
    stress=[r for r in all_results if r["cost_scenario"]=="STRESS_2"]
    table={(r["period"],r["capital_initial_usdt"]):r for r in stress}
    caps=("60","100")
    full_positive=all(Decimal(table[("FULL_YTD",c)]["net_pnl_usdt"])>ZERO for c in caps)
    trade_count=all(table[("FULL_YTD",c)]["completed_round_trips"]>=5 for c in caps)
    risk=all(Decimal(table[(period,c)]["max_drawdown_pct"])>=Decimal("-15")
        for period in ("FULL_YTD","FOLD_JAN_MAR","FOLD_APR_JUN","FOLD_JUL_OCT") for c in caps)
    fold_counts={c:sum(Decimal(table[(period,c)]["net_pnl_usdt"])>ZERO for period in ("FOLD_JAN_MAR","FOLD_APR_JUN","FOLD_JUL_OCT")) for c in caps}
    fold_gate=all(v>=2 for v in fold_counts.values())
    benchmark=all(Decimal(table[("FULL_YTD",c)]["net_pnl_usdt"])>Decimal(table[("FULL_YTD",c)]["btc_buy_hold_net_pnl_usdt"]) for c in caps)
    gates={"positive_full_window_pnl_both_capitals":full_positive,"at_least_5_full_window_trades_both_capitals":trade_count,
        "max_drawdown_no_worse_than_minus15_full_and_all_folds":risk,"profitable_folds_count":{**fold_counts,"passed":fold_gate},
        "beats_cash_and_btc_buy_hold_full_window":benchmark}
    passed=full_positive and trade_count and risk and fold_gate and benchmark
    report=["# BTC 1h RSI(2) + EMA720 + ATR stop + 24h timeout","",
        "**Disposition:** research only; NO_TRADE remains in force.","",
        f"- Evaluation window [{START.isoformat()}, {END.isoformat()}); {EVAL_BARS} hourly bars + {WARMUP_BARS} warm-up bars.",
        f"- Dataset SHA-256: {dataset_sha}; current filters SHA-256: {rules['sha256']}.",
        f"- Market data completeness UNKNOWN={unknown_comp}; finality UNKNOWN={unknown_final}.",
        "- Entry: RSI(2)<10 and close>EMA720; signal at completed hour close, fill next hour open.",
        "- Exit: RSI(2)>=60, close<=EMA720, close<=executed entry price - 1.5*ATR14, or 24 completed bars held; next-open exit. Terminal liquidation at final close.",
        "- Capital 60/100 USDT; 50% of available quote at entry; max 5 actual buys per rolling 30 days. Current lot rules are hashed; historic rules unknown.",
        "- Costs: BASE fee 10bps/spread 10bps/slippage 5bps; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are assumptions, not historic book fills.","",
        "| Period | Capital | Cost | Net PnL | Return | Max DD | Trades | W/L | Fees | BTC B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in all_results:
        report.append(f"| {r['period']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
            f"{r['wins']}/{r['losses']} | {Decimal(r['total_fees_usdt']):.4f} | {Decimal(r['btc_buy_hold_net_pnl_usdt']):+.4f} |")
    report+=["","## Preregistered gate (STRESS_2)",""]
    for k,v in gates.items(): report.append(f"- {k}: {json.dumps(v,sort_keys=True)}")
    report+=["",f"**Historical gate: {'PASS' if passed else 'FAIL'}**","",
        "The YTD sample is retrospective research and not a pristine blind holdout. Spread/slippage remain assumptions. Even a pass only qualifies the frozen rule for a future prospective shadow holdout; it does not authorize PAPER/LIVE or change TOP1/PRIME.",""]
    (out/"report.md").write_text("\n".join(report),encoding="utf-8")
    print("PROMOTION_GATES:",json.dumps(gates,sort_keys=True))
if __name__=="__main__": main()
