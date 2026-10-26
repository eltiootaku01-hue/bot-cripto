"""Frozen BTC/ETH 4h 42-bar relative-strength rotation with ATR-targeted exposure; research only."""
from __future__ import annotations
import csv, hashlib, json, subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from bot_obrero.binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.virtual_backtest import _market_data_record, _validate_history

UTC=timezone.utc
INTERVAL="4h"; BAR=timedelta(hours=4)
FULL_START=datetime(2025,1,1,tzinfo=UTC); END=datetime(2026,10,22,tzinfo=UTC)
WARMUP_BARS=300; WARMUP_START=FULL_START-BAR*WARMUP_BARS
REBALANCE_BARS=6
EXPECTED_BARS=int((END-WARMUP_START)/BAR)
ZERO,ONE,HUNDRED,BPS=Decimal("0"),Decimal("1"),Decimal("100"),Decimal("10000")
MAX_POSITION_FRACTION=Decimal("0.50"); ATR_TARGET_ANNUAL=Decimal("0.15"); BENCHMARK_FRACTION=Decimal("0.95")
MOMENTUM_LOOKBACK=42; EMA_PERIOD=200
ASSETS=("BTCUSDT","ETHUSDT")
SCENARIOS=(("BASE",Decimal("0.001"),Decimal("10"),Decimal("5")),
           ("STRESS_1",Decimal("0.001"),Decimal("25"),Decimal("10")),
           ("STRESS_2",Decimal("0.002"),Decimal("50"),Decimal("20")))
WINDOWS=(("COMBINED_2025_2026",FULL_START,END),("2025",FULL_START,datetime(2026,1,1,tzinfo=UTC)),("2026_YTD",datetime(2026,1,1,tzinfo=UTC),END))

def _current_rules(symbol):
    metadata=BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    instrument=metadata.resolve_active(symbol); payload=metadata._request(symbol)
    symbols=payload.get("symbols") if isinstance(payload,dict) else None
    if not isinstance(symbols,list) or len(symbols)!=1 or not isinstance(symbols[0],dict):
        raise RuntimeError(f"FAIL-CLOSED: ExchangeInfo did not return exactly one {symbol} record")
    record=symbols[0]
    if record.get("symbol")!=symbol or record.get("status")!="TRADING" or record.get("isSpotTradingAllowed") is not True:
        raise RuntimeError(f"FAIL-CLOSED: ExchangeInfo does not confirm active Spot {symbol}")
    filters=record.get("filters")
    if not isinstance(filters,list): raise RuntimeError(f"FAIL-CLOSED: filters missing for {symbol}")
    lot=next((f for f in filters if isinstance(f,dict) and f.get("filterType")=="LOT_SIZE"),None)
    notion=next((f for f in filters if isinstance(f,dict) and f.get("filterType") in ("NOTIONAL","MIN_NOTIONAL")),None)
    if not isinstance(lot,dict) or not isinstance(notion,dict):
        raise RuntimeError(f"FAIL-CLOSED: lot/min-notional filters missing for {symbol}")
    try: step,min_qty,min_notional=Decimal(lot["stepSize"]),Decimal(lot["minQty"]),Decimal(notion["minNotional"])
    except (KeyError,TypeError,ValueError,ArithmeticError) as exc: raise RuntimeError(f"FAIL-CLOSED: malformed filters for {symbol}") from exc
    if min(step,min_qty,min_notional)<=ZERO: raise RuntimeError(f"FAIL-CLOSED: nonpositive filters for {symbol}")
    snapshot={"LOT_SIZE":lot,"MIN_NOTIONAL_OR_NOTIONAL":notion}
    digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    return {"instrument_id":instrument.instrument_id,"step":step,"min_qty":min_qty,"min_notional":min_notional,
            "snapshot":snapshot,"sha256":digest,"retrieved_at_utc":datetime.now(UTC).isoformat(),
            "scope":"current public Binance ExchangeInfo; historical filters not established"}

def _fetch(symbol,rules):
    """Fetch one-page chunks, then independently validate every timestamp start.
    The normal multi-page helper compares closeTime + 1ms at page boundaries and
    falsely flags some historical Binance 4h boundaries; this path retains strict
    open-time continuity and never fills or interpolates absent candles.
    """
    metadata=BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    metadata.resolve_active(symbol)
    adapter=BinanceMetadataBackedAdapter(metadata=metadata)
    expected=[WARMUP_START+BAR*i for i in range(EXPECTED_BARS)]
    items=[]
    page_size=800
    for offset in range(0,len(expected),page_size):
        wanted=expected[offset:offset+page_size]
        page=adapter.fetch_market_data(
            symbol=symbol,interval=INTERVAL,limit=len(wanted),
            start_time=int(wanted[0].timestamp()*1000),
            end_time=int(wanted[-1].timestamp()*1000),allow_empty=False
        )
        page=sorted(page,key=lambda x:x.payload.start)
        observed=[x.payload.start for x in page]
        if observed!=wanted:
            missing=sorted(set(wanted)-set(observed))
            extra=sorted(set(observed)-set(wanted))
            raise RuntimeError(
                f"FAIL-CLOSED {symbol}: timestamp chunk offset={offset}, expected {len(wanted)} candles, "
                f"received {len(page)}; missing={missing[:6]}; unexpected={extra[:6]}. No interpolation."
            )
        items.extend(page)
    starts=[x.payload.start for x in items]
    if starts!=expected:
        raise RuntimeError(f"FAIL-CLOSED {symbol}: concatenated chunk starts are not exactly contiguous")
    accepted,excluded,unknown_comp,unknown_final=_validate_history(items,INTERVAL)
    if len(accepted)!=EXPECTED_BARS or excluded:
        raise RuntimeError(f"FAIL-CLOSED {symbol}: accepted={len(accepted)}, excluded={excluded}")
    if any(x.instrument.instrument_id!=rules["instrument_id"] for x in items):
        raise RuntimeError(f"FAIL-CLOSED {symbol}: instrument identity mismatch")
    records=[_market_data_record(x) for x in items]
    digest=hashlib.sha256(json.dumps(records,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    return items,records,digest,unknown_comp,unknown_final

def _ema(values,period):
    alpha=Decimal("2")/Decimal(period+1); out=[None]*len(values); current=values[0]
    for i,val in enumerate(values):
        if i: current=alpha*val+(ONE-alpha)*current
        if i>=period-1: out[i]=current
    return out

def _atr_target_weights(items):
    """Causal entry weight=min(50%, 15%/annualized ATR14), frozen at signal close."""
    weights={}
    annualization=Decimal(365*6).sqrt()
    for asset in ASSETS:
        rows=items[asset]
        closes=[x.payload.close for x in rows]
        highs=[x.payload.high for x in rows]
        lows=[x.payload.low for x in rows]
        tr=[]
        for i in range(len(rows)):
            if i==0: tr.append(highs[i]-lows[i])
            else: tr.append(max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])))
        atr=[None]*len(rows)
        for i in range(13,len(rows)): atr[i]=sum(tr[i-13:i+1],ZERO)/Decimal(14)
        vector=[None]*len(rows)
        for i in range(len(rows)):
            if atr[i] is None or atr[i]<=ZERO or closes[i]<=ZERO: continue
            annualized=(atr[i]/closes[i])*annualization
            if annualized>ZERO: vector[i]=min(MAX_POSITION_FRACTION,ATR_TARGET_ANNUAL/annualized)
        weights[asset]=vector
    return weights

def _adverse(spread,slip): return (slip+spread/Decimal("2"))/BPS
def _floor_step(value,step): return (value/step).to_integral_value(rounding=ROUND_DOWN)*step

def _rsi_wilder(closes, period=14):
    """Return standard Wilder RSI values from completed closes; no future data is used."""
    if period < 1:
        raise ValueError("RSI_PERIOD_MUST_BE_POSITIVE")
    values=[None]*len(closes)
    if len(closes) <= period:
        return values
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        delta=closes[i]-closes[i-1]
        gains.append(max(delta,ZERO))
        losses.append(max(-delta,ZERO))
    avg_gain=sum(gains[:period],ZERO)/Decimal(period)
    avg_loss=sum(losses[:period],ZERO)/Decimal(period)
    def value(gain,loss):
        if gain==ZERO and loss==ZERO: return Decimal("50")
        if loss==ZERO: return Decimal("100")
        if gain==ZERO: return ZERO
        rs=gain/loss
        return Decimal("100")-(Decimal("100")/(ONE+rs))
    values[period]=value(avg_gain,avg_loss)
    for i in range(period+1,len(closes)):
        gain=gains[i-1]; loss=losses[i-1]
        avg_gain=(avg_gain*Decimal(period-1)+gain)/Decimal(period)
        avg_loss=(avg_loss*Decimal(period-1)+loss)/Decimal(period)
        values[i]=value(avg_gain,avg_loss)
    return values

def _rsi_states(items):
    return {asset:_rsi_wilder([row.payload.close for row in items[asset]],14) for asset in ASSETS}

def _target(i,closes,emas,rsi_states,holding):
    if holding is not None:
        rsi=rsi_states[holding][i]; ema=emas[holding][i]
        if rsi is not None and rsi>Decimal("40") and ema is not None and closes[holding][i]>ema:
            return holding,f"{holding} retained: RSI14 > 40 and close > own EMA200"
    previous_sample=i-REBALANCE_BARS
    if previous_sample<1 or i<MOMENTUM_LOOKBACK:
        return None,"insufficient causal daily sample or 42-bar momentum history"
    eligible=[]
    for asset in ASSETS:
        current=rsi_states[asset][i]; previous=rsi_states[asset][previous_sample]
        price=closes[asset][i]; ema=emas[asset][i]
        if current is None or previous is None or ema is None:
            continue
        if not (previous<=Decimal("50") and current>Decimal("50") and price>ema):
            continue
        momentum=price/closes[asset][i-MOMENTUM_LOOKBACK]-ONE
        if momentum>ZERO:
            eligible.append((momentum,asset))
    if not eligible:
        return None,"neither BTC nor ETH has an RSI14 cross above 50, positive 42-bar momentum and close > own EMA200"
    eligible.sort(key=lambda p:(p[0],p[1])); momentum,asset=eligible[-1]
    return asset,f"{asset} entered on RSI14 cross above 50; 42-bar momentum={momentum*HUNDRED:.4f}% > 0 and close > own EMA200"

def _buy_hold(eval_items,capital,asset,rules,fee,spread,slip):
    rule=rules[asset]; adverse=_adverse(spread,slip)
    price=eval_items[0].payload.open*(ONE+adverse)
    qty=_floor_step(capital*BENCHMARK_FRACTION/(price*(ONE+fee)),rule["step"])
    notional=qty*price
    if qty<rule["min_qty"] or notional<rule["min_notional"] or notional*(ONE+fee)>capital:
        raise RuntimeError(f"FAIL-CLOSED: {asset} benchmark violates current lot assumptions")
    buy_fee=notional*fee; cash=capital-notional-buy_fee
    sale=qty*eval_items[-1].payload.close*(ONE-adverse); sell_fee=sale*fee
    return cash+sale-sell_fee,buy_fee+sell_fee

def _simulate(items,window_name,start,end,capital,scenario,fee,spread,slip,closes,opens,emas,rsi_states,rules,hashes,risk_weights):
    index={x.payload.start:i for i,x in enumerate(items["BTCUSDT"])}
    evals={a:[x for x in items[a] if start<=x.payload.start<end] for a in ASSETS}
    expected=int((end-start)/BAR)
    if any(len(evals[a])!=expected for a in ASSETS): raise RuntimeError(f"FAIL-CLOSED {window_name}: row count differs from {expected}")
    if [x.payload.start for x in evals["BTCUSDT"]]!=[x.payload.start for x in evals["ETHUSDT"]]:
        raise RuntimeError(f"FAIL-CLOSED {window_name}: BTC/ETH timestamps mismatch")
    quote,holding,qty,entry_cost=capital,None,ZERO,ZERO
    fees,peak,pending_target,pending_set,pending_target_weight=ZERO,capital,None,False,ZERO
    fills,curve,realized,decisions=[],[],[],0
    for n,ref in enumerate(evals["BTCUSDT"]):
        candle=ref.payload; ts=candle.start; i=index[ts]
        if pending_set:
            target=pending_target
            target_weight=pending_target_weight
            pending_set=False
            pending_target_weight=ZERO
            if target!=holding:
                if holding is not None and qty>ZERO:
                    px=opens[holding][i]*(ONE-_adverse(spread,slip)); notional=qty*px; f=notional*fee
                    proceeds=notional-f; pnl=proceeds-entry_cost; quote+=proceeds; fees+=f; realized.append(pnl)
                    fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                        "timestamp_utc":ts.isoformat(),"side":"SELL","asset":holding,"quantity":str(qty),
                        "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":str(pnl),"reason":"scheduled next-open rebalance"})
                    holding,qty,entry_cost=None,ZERO,ZERO
                if target is not None:
                    px=opens[target][i]*(ONE+_adverse(spread,slip)); rule=rules[target]
                    buy_qty=_floor_step(quote*target_weight/(px*(ONE+fee)),rule["step"]); notional=buy_qty*px
                    reject=None
                    if buy_qty<rule["min_qty"]: reject="MIN_QTY"
                    elif notional<rule["min_notional"]: reject="MIN_NOTIONAL"
                    elif notional*(ONE+fee)>quote: reject="COSTS_EXCEED_BALANCE"
                    if reject:
                        fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                            "timestamp_utc":ts.isoformat(),"side":"REJECTED_BUY","asset":target,"quantity":str(buy_qty),
                            "price_usdt":str(px),"fee_usdt":"0","realized_pnl_usdt":"","reason":reject})
                    else:
                        f=notional*fee; entry_cost=notional+f; quote-=entry_cost; fees+=f; holding,qty=target,buy_qty
                        fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                            "timestamp_utc":ts.isoformat(),"side":"BUY","asset":holding,"quantity":str(qty),
                            "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":"",
                            "requested_weight_fraction":str(target_weight),
                            "reason":"daily RSI14 trend-state target; ATR risk weight frozen at signal close"})
        adverse=_adverse(spread,slip)
        equity=quote if holding is None else quote+qty*closes[holding][i]*(ONE-adverse)*(ONE-fee)
        peak=max(peak,equity)
        curve.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
            "timestamp_utc":candle.end.isoformat(),"equity_usdt":str(equity),"holding":holding or "CASH",
            "drawdown_pct":str((equity/peak-ONE)*HUNDRED if peak else ZERO)})
        # Daily RSI14 signal at completed candle close; next 4h open executes the target.
        if n<len(evals["BTCUSDT"])-1 and n>=REBALANCE_BARS-1 and (n-(REBALANCE_BARS-1))%REBALANCE_BARS==0:
            tgt,why=_target(i,closes,emas,rsi_states,holding); decisions+=1
            requested_weight=ZERO
            if tgt is not None:
                requested_weight=risk_weights[tgt][i]
                if requested_weight is None or requested_weight<=ZERO:
                    tgt=None; requested_weight=ZERO
                    why += "; ATR14 risk weight unavailable, so target cash"
            if tgt!=holding:
                pending_target,pending_set,pending_target_weight=tgt,True,requested_weight
                fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                    "timestamp_utc":candle.end.isoformat(),"side":"SIGNAL","asset":tgt or "CASH","quantity":"",
                    "price_usdt":"","fee_usdt":"0","realized_pnl_usdt":"",
                    "requested_weight_fraction":str(requested_weight),"reason":why})
    if holding is not None and qty>ZERO:
        last=evals["BTCUSDT"][-1].payload; i=index[last.start]
        px=closes[holding][i]*(ONE-_adverse(spread,slip)); notional=qty*px; f=notional*fee
        proceeds=notional-f; pnl=proceeds-entry_cost; quote+=proceeds; fees+=f; realized.append(pnl)
        fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
            "timestamp_utc":last.end.isoformat(),"side":"SELL","asset":holding,"quantity":str(qty),
            "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":str(pnl),"reason":"terminal liquidation"})
        peak=max(peak,Decimal(curve[-1]["equity_usdt"])); holding,qty=None,ZERO
        curve[-1]["equity_usdt"]=str(quote); curve[-1]["holding"]="CASH"
        curve[-1]["drawdown_pct"]=str((quote/peak-ONE)*HUNDRED if peak else ZERO)
    benchmarks={}
    for asset in ASSETS:
        bh,bhf=_buy_hold(evals[asset],capital,asset,rules,fee,spread,slip)
        key=asset.lower().replace("usdt","_usdt")
        benchmarks[f"{key}_buy_hold_final_equity_usdt"]=str(bh)
        benchmarks[f"{key}_buy_hold_net_pnl_usdt"]=str(bh-capital)
        benchmarks[f"{key}_buy_hold_fees_usdt"]=str(bhf)
    sell_fills=[f for f in fills if f["period"]==window_name and f["capital_initial_usdt"]==str(capital)
                and f["cost_scenario"]==scenario and f["side"]=="SELL"]
    result={"period":window_name,"window_start":start.isoformat(),"window_end_exclusive":end.isoformat(),
        "capital_initial_usdt":str(capital),"cost_scenario":scenario,"final_equity_usdt":str(quote),
        "net_pnl_usdt":str(quote-capital),"return_pct":str((quote/capital-ONE)*HUNDRED),
        "max_drawdown_pct":str(min((Decimal(x["drawdown_pct"]) for x in curve),default=ZERO)),
        "completed_round_trips":len(sell_fills),"realized_wins":sum(p>ZERO for p in realized),
        "realized_losses":sum(p<ZERO for p in realized),"total_fees_usdt":str(fees),
        "weekly_decisions":decisions,"cash_final_equity_usdt":str(capital),**benchmarks,**hashes,
        "evaluation_bars":expected}
    return result,fills,curve

def main():
    out=Path("btc-eth-rsi14-trend-atr-v1-output"); out.mkdir(exist_ok=True)
    print("Frozen BTC/ETH RSI14 50/40 trend-state, 6-bar cadence, 15% ATR-targeted exposure capped at 50%.")
    print("Rule: every 6 completed 4h bars, enter on RSI14 cross >50 with positive 42-bar momentum and close > EMA200; retain while RSI14 >40 and close > EMA200.")
    print("Research HEAD:",subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip())
    print(f"Interval [{FULL_START.isoformat()}, {END.isoformat()}); warmup {WARMUP_BARS}; expected bars per asset {EXPECTED_BARS}")
    items,raw,hashes,metadata,rules={},{},{},{},{}
    for asset in ASSETS:
        rules[asset]=_current_rules(asset); result=_fetch(asset,rules[asset])
        items[asset],raw[asset],hashes[asset]=result[0],result[1],result[2]
        metadata[asset]={"completeness_unknown":result[3],"finality_unknown":result[4],"observed_bars":len(result[0]),
            "dataset_sha256":result[2],"current_filters":{"step":str(rules[asset]["step"]),"min_qty":str(rules[asset]["min_qty"]),
            "min_notional":str(rules[asset]["min_notional"]),"snapshot":rules[asset]["snapshot"],"sha256":rules[asset]["sha256"],
            "retrieved_at_utc":rules[asset]["retrieved_at_utc"],"scope":rules[asset]["scope"]}}
        print(f"{asset}: bars={len(result[0])}, SHA256={result[2]}, completeness UNKNOWN={result[3]}, finality UNKNOWN={result[4]}, filters={rules[asset]['sha256']}")
    if [x.payload.start for x in items["BTCUSDT"]]!=[x.payload.start for x in items["ETHUSDT"]]:
        raise RuntimeError("FAIL-CLOSED: BTC/ETH timestamps are not aligned")
    risk_weights=_atr_target_weights(items)
    risk_series=[{"index":i,"timestamp_utc":items["BTCUSDT"][i].payload.start.isoformat(),
        "BTCUSDT_weight":None if risk_weights["BTCUSDT"][i] is None else str(risk_weights["BTCUSDT"][i]),
        "ETHUSDT_weight":None if risk_weights["ETHUSDT"][i] is None else str(risk_weights["ETHUSDT"][i])}
        for i in range(len(items["BTCUSDT"]))]
    risk_hash=hashlib.sha256(json.dumps(risk_series,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    combined=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    rsi_states=_rsi_states(items)
    rsi_series=[{"index":i,"timestamp_utc":items["BTCUSDT"][i].payload.start.isoformat(),"BTCUSDT_rsi14":None if rsi_states["BTCUSDT"][i] is None else str(rsi_states["BTCUSDT"][i]),"ETHUSDT_rsi14":None if rsi_states["ETHUSDT"][i] is None else str(rsi_states["ETHUSDT"][i])} for i in range(len(items["BTCUSDT"]))]
    rsi_hash=hashlib.sha256(json.dumps(rsi_series,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    manifest={"full_start":FULL_START.isoformat(),"end_exclusive":END.isoformat(),"warmup_start":WARMUP_START.isoformat(),
        "warmup_bars":WARMUP_BARS,"interval":INTERVAL,"expected_bars_per_asset":EXPECTED_BARS,"missing_or_interpolated_bars":0,
        "combined_dataset_sha256":combined,"asset_dataset_sha256":hashes,"metadata":metadata,
        "risk_weight_series_sha256":risk_hash,"risk_weight_series":risk_series,"rsi14_state_sha256":rsi_hash,"rsi14_state_series":rsi_series,
        "rule":{"entry_signal":"RSI14 crosses above 50 across daily samples, close > own EMA200 and positive 42-bar return; exit when RSI14 <= 40 or close <= EMA200","rsi_period":14,"entry_rsi_threshold":"50","exit_rsi_threshold":"40","risk_overlay":"weight=min(50%,15%/((ATR14/close)*sqrt(365*6))); signal-close weight frozen until exit",
            "atr_target_annualized_proxy":"15%","position_weight_cap":str(MAX_POSITION_FRACTION),
            "benchmark_fraction":str(BENCHMARK_FRACTION),
            "rebalance":"every 6 completed 4h evaluation bars; fill next 4h open",
            "signal":"RSI14 crosses above 50, positive 42-bar momentum and close > own EMA200; retain while RSI14 > 40 and close > EMA200; otherwise cash",
            "costs":[{"name":n,"fee":str(f),"assumed_full_spread_bps":str(s),"assumed_slippage_bps":str(l)} for n,f,s,l in SCENARIOS],
            "windows":[{"name":n,"start":s.isoformat(),"end_exclusive":e.isoformat()} for n,s,e in WINDOWS]},
        "data":raw}
    (out/"dataset-evidence.json").write_text(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    closes={a:[x.payload.close for x in items[a]] for a in ASSETS}
    opens={a:[x.payload.open for x in items[a]] for a in ASSETS}
    emas={a:_ema(closes[a],EMA_PERIOD) for a in ASSETS}
    evidence_hashes={"btc_dataset_sha256":hashes["BTCUSDT"],"eth_dataset_sha256":hashes["ETHUSDT"],
        "combined_dataset_sha256":combined,"btc_filter_sha256":rules["BTCUSDT"]["sha256"],"eth_filter_sha256":rules["ETHUSDT"]["sha256"],"rsi14_state_sha256":rsi_hash}
    results,all_fills,all_curves=[],[],[]
    for win,start,end in WINDOWS:
        for capital in (Decimal("60"),Decimal("100")):
            for scenario,fee,spread,slip in SCENARIOS:
                row,fills,curve=_simulate(items,win,start,end,capital,scenario,fee,spread,slip,closes,opens,emas,rsi_states,rules,evidence_hashes,risk_weights)
                results.append(row); all_fills.extend(fills); all_curves.extend(curve)
                print(f"RESULT | {win} | capital={capital} | {scenario} | PnL={Decimal(row['net_pnl_usdt']):+.5f} | "
                      f"return={Decimal(row['return_pct']):+.3f}% | DD={Decimal(row['max_drawdown_pct']):.3f}% | "
                      f"closures={row['completed_round_trips']} W/L={row['realized_wins']}/{row['realized_losses']} | "
                      f"fees={Decimal(row['total_fees_usdt']):.4f} | BTC B&H={Decimal(row['btc_usdt_buy_hold_net_pnl_usdt']):+.3f} | "
                      f"ETH B&H={Decimal(row['eth_usdt_buy_hold_net_pnl_usdt']):+.3f}")
    for name,rows in (("summary.csv",results),("fills.csv",all_fills),("equity.csv",all_curves)):
        with (out/name).open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=sorted({k for row in rows for k in row})); writer.writeheader(); writer.writerows(rows)
    stress=[r for r in results if r["cost_scenario"]=="STRESS_2"]
    by={(r["period"],r["capital_initial_usdt"]):r for r in stress}
    caps=("60","100")
    periods=("COMBINED_2025_2026","2025","2026_YTD")
    positive=all(Decimal(by[(p,c)]["net_pnl_usdt"])>ZERO for p in periods for c in caps)
    closures=all(by[(p,c)]["completed_round_trips"]>=5 for p in periods for c in caps)
    risk=all(Decimal(by[(p,c)]["max_drawdown_pct"])>=Decimal("-15") for p in periods for c in caps)
    ytd_benchmark=all(
        Decimal(by[("2026_YTD",c)]["net_pnl_usdt"])>Decimal(by[("2026_YTD",c)]["btc_usdt_buy_hold_net_pnl_usdt"])
        and Decimal(by[("2026_YTD",c)]["net_pnl_usdt"])>Decimal(by[("2026_YTD",c)]["eth_usdt_buy_hold_net_pnl_usdt"])
        for c in caps
    )
    gates={"positive_stress2_pnl_all_windows_both_capitals":positive,
        "at_least_5_closures_all_windows_both_capitals":closures,
        "max_drawdown_no_worse_than_minus15_all_windows_both_capitals":risk,
        "2026_ytd_beats_btc_and_eth_buy_hold":ytd_benchmark}
    baseline_15pct = {
        ("COMBINED_2025_2026", "60"): Decimal("9.9912854577983"),
        ("COMBINED_2025_2026", "100"): Decimal("16.7946922422263"),
        ("2025", "60"): Decimal("7.381367674725"),
        ("2025", "100"): Decimal("12.4060619536752"),
        ("2026_YTD", "60"): Decimal("3.7691729934701"),
        ("2026_YTD", "100"): Decimal("6.2323575776842"),
    }
    beats_15pct = all(Decimal(by[(period, cap)]["net_pnl_usdt"]) > baseline_15pct[(period, cap)]
        for period in ("COMBINED_2025_2026","2026_YTD") for cap in caps)
    gates["beats_15pct_candidate_combined_and_ytd_both_capitals"] = beats_15pct
    passed=positive and closures and risk and ytd_benchmark and beats_15pct
    report=["# BTC/ETH 4h RSI14 trend-state — 15% ATR-targeted exposure, 2025–2026 validation","",
        "**Disposition:** retrospective risk-overlay research only; NO_TRADE remains in force.","",
        f"- Risk-weight series SHA-256: {risk_hash}; RSI14 state SHA-256: {rsi_hash}; weight=min(50%,15%/((ATR14/close)*sqrt(365*6))) frozen from signal close.",
        f"- Full interval: [{FULL_START.isoformat()}, {END.isoformat()}); {EXPECTED_BARS-WARMUP_BARS} evaluation bars per asset plus {WARMUP_BARS} warm-up bars.",
        f"- Combined SHA-256: {combined}; BTC SHA-256: {hashes['BTCUSDT']}; ETH SHA-256: {hashes['ETHUSDT']}.",
        f"- Current filter hashes: BTC {rules['BTCUSDT']['sha256']}; ETH {rules['ETHUSDT']['sha256']}; historical filters UNKNOWN.",
        f"- Market-data flags: {json.dumps({a:{'completeness_unknown':metadata[a]['completeness_unknown'],'finality_unknown':metadata[a]['finality_unknown']} for a in ASSETS},sort_keys=True)}.",
        "- Rule frozen: daily sample every 6 completed 4h bars; enter on RSI14 crossing above 50 with close>EMA200 and positive 42-bar momentum; exit if RSI14<=40 or close<=EMA200. Fill at next open; terminal liquidation at each window.",
        "- Entry sizing uses a 15% annualized ATR14 proxy, capped at 50% of available quote and frozen from signal close until exit. Buy-and-hold benchmarks use 95% and matched modeled costs.",
        "- Costs are assumptions: BASE fee 10 bps, full spread 10 bps, slippage 5 bps per fill; STRESS_1 10/25/10; STRESS_2 20/50/20. Not reconstructed historical bookTicker fills.",
        "- Independent windows: combined 2025-2026, calendar 2025, and 2026 YTD; capital reset to cash for each window and cost scenario.","",
        "| Period | Capital | Cost | Net PnL | Return | Max DD | Closures | W/L | Fees | BTC B&H PnL | ETH B&H PnL |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        report.append(f"| {r['period']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
            f"{r['realized_wins']}/{r['realized_losses']} | {Decimal(r['total_fees_usdt']):.4f} | "
            f"{Decimal(r['btc_usdt_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(r['eth_usdt_buy_hold_net_pnl_usdt']):+.4f} |")
    report+=["","## Preregistered promotion gate (STRESS_2)",""]
    for k,v in gates.items(): report.append(f"- {k}: {json.dumps(v,sort_keys=True)}")
    report+=["",f"**Historical gate: {'PASS' if passed else 'FAIL'}**","",
        "The archive is retrospective and 2026 YTD was already inspected during candidate discovery. Positive historical returns do not guarantee future performance. Market-data completeness and point-in-time availability are not established by candle presence; lot filters are current snapshots and spread/slippage remain modeled. Even a pass would only qualify this rule for a prospective shadow holdout; it does not authorize PAPER/LIVE or change TOP1/PRIME.",""]
    (out/"report.md").write_text("\n".join(report),encoding="utf-8")
    gate_output={"decision":"PASS_HISTORICAL_SHADOW_ONLY" if passed else "FAIL_NO_TRADE","gates":gates}
    (out/"gate_results.json").write_text(json.dumps(gate_output,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    output_files={}
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name!="output_manifest.json":
            payload=path.read_bytes()
            output_files[path.name]={"size_bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}
    (out/"output_manifest.json").write_text(json.dumps({"decision":gate_output["decision"],"files":output_files},sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("PROMOTION_GATES:",json.dumps(gates,sort_keys=True))
    print("DECISION:",gate_output["decision"])
    print("Evidence written to",out)

if __name__=="__main__": main()
