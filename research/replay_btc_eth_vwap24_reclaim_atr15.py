"""Frozen deterministic BTC/ETH 4h MACD crossover with EMA200 and ATR-targeted exposure; research only."""
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
MACD_FAST=12; MACD_SLOW=26; MACD_SIGNAL=9
EXPECTED_BARS=int((END-WARMUP_START)/BAR)
ZERO,ONE,HUNDRED,BPS=Decimal("0"),Decimal("1"),Decimal("100"),Decimal("10000")
MAX_POSITION_FRACTION=Decimal("0.50"); ATR_TARGET_ANNUAL=Decimal("0.15"); BENCHMARK_FRACTION=Decimal("0.95")
EMA_PERIOD=200
ASSETS=("BTCUSDT","ETHUSDT")
VWAP_PERIOD=24
STOP_ATR_MULTIPLE=Decimal("2")
MAX_HOLD_BARS=18
EXPECTED_CANONICAL_OHLCV_SHA256={
    "BTCUSDT":"a508cb02259c15ec67e6df8c1c8be406b1d5f64350cae17a33c463e728793d9d",
    "ETHUSDT":"d75f1b78f0c11ba889c81458801abf0d14758329819c55d6eda6efb0151a8510",
}

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

def _atr_series(items):
    """Causal ATR14 values for deterministic risk sizing and score normalization."""
    atrs={}
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
        atrs[asset]=atr
    return atrs


def _canonical_ohlcv_sha256(items):
    records=[{"start":x.payload.start.isoformat(),"open":str(x.payload.open),
        "high":str(x.payload.high),"low":str(x.payload.low),"close":str(x.payload.close),
        "volume":str(x.payload.volume)} for x in items]
    return hashlib.sha256(json.dumps(records,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()

def _vwap_indicators(items):
    """Rolling VWAP24 from typical price and volume; signal includes only the current completed candle."""
    output={}
    for asset in ASSETS:
        rows=items[asset]
        typical=[(x.payload.high+x.payload.low+x.payload.close)/Decimal("3") for x in rows]
        volumes=[x.payload.volume for x in rows]
        values=[None]*len(rows)
        for i in range(VWAP_PERIOD-1,len(rows)):
            weights=volumes[i-VWAP_PERIOD+1:i+1]
            total=sum(weights,ZERO)
            if total<=ZERO:
                continue
            numerator=sum((typical[j]*volumes[j] for j in range(i-VWAP_PERIOD+1,i+1)),ZERO)
            values[i]=numerator/total
        output[asset]=values
    return output

def _target_vwap_reclaim(i,closes,emas,ema50s,vwaps,atrs):
    """Buy a completed VWAP24 reclaim inside EMA50>EMA200 and positive 24-bar momentum."""
    if i<=0 or i>=len(closes["BTCUSDT"]):
        return None,"insufficient preceding completed bars for VWAP cross"
    candidates=[]
    for asset in ASSETS:
        close=closes[asset][i]
        previous_close=closes[asset][i-1]
        vwap=vwaps[asset][i]
        prior_vwap=vwaps[asset][i-1]
        ema50=ema50s[asset][i]
        ema200=emas[asset][i]
        atr=atrs[asset][i]
        if any(x is None for x in (vwap,prior_vwap,ema50,ema200,atr)) or atr<=ZERO:
            continue
        if i<VWAP_PERIOD or i<24:
            continue
        momentum=close/closes[asset][i-24]-ONE
        if previous_close<=prior_vwap and close>vwap and close>ema200 and ema50>ema200 and momentum>ZERO:
            candidates.append((momentum,asset))
    if not candidates:
        return None,"neither BTC nor ETH has a VWAP24 reclaim with positive 24-bar momentum and EMA50>EMA200"
    candidates.sort(key=lambda candidate:(-candidate[0],candidate[1]))
    momentum,asset=candidates[0]
    return asset,f"{asset} reclaimed rolling VWAP24 in bullish EMA50/EMA200 regime; 24-bar return={momentum*HUNDRED:.3f}%"

def _exit_reason_vwap(close,vwap,ema50,stop_price,bars_held):
    if stop_price is not None and close<=stop_price:
        return "STOP_2ATR_CLOSE_NEXT_OPEN"
    if bars_held>=MAX_HOLD_BARS:
        return "TIME_EXIT_18_BARS_NEXT_OPEN"
    if vwap is not None and close<vwap:
        return "VWAP24_LOSS_NEXT_OPEN"
    if ema50 is not None and close<ema50:
        return "EMA50_LOSS_NEXT_OPEN"
    return None

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

def _simulate(items,window_name,start,end,capital,scenario,fee,spread,slip,closes,opens,emas,ema50s,vwaps,rules,hashes,risk_weights):
    index={x.payload.start:i for i,x in enumerate(items["BTCUSDT"])}
    atrs=_atr_series(items)
    evals={a:[x for x in items[a] if start<=x.payload.start<end] for a in ASSETS}
    expected=int((end-start)/BAR)
    if any(len(evals[a])!=expected for a in ASSETS): raise RuntimeError(f"FAIL-CLOSED {window_name}: row count differs from {expected}")
    if [x.payload.start for x in evals["BTCUSDT"]]!=[x.payload.start for x in evals["ETHUSDT"]]:
        raise RuntimeError(f"FAIL-CLOSED {window_name}: BTC/ETH timestamps mismatch")
    quote,holding,qty,entry_cost=capital,None,ZERO,ZERO
    fees,peak,pending_target,pending_set,pending_target_weight=ZERO,capital,None,False,ZERO
    position_stop,position_entry_index,pending_entry_atr=None,None,None
    fills,curve,realized,decisions=[],[],[],0
    for n,ref in enumerate(evals["BTCUSDT"]):
        candle=ref.payload; ts=candle.start; i=index[ts]
        if pending_set:
            target=pending_target
            target_weight=pending_target_weight
            entry_atr=pending_entry_atr
            pending_set=False
            pending_target_weight=ZERO
            pending_entry_atr=None
            if target!=holding:
                if holding is not None and qty>ZERO:
                    px=opens[holding][i]*(ONE-_adverse(spread,slip)); notional=qty*px; f=notional*fee
                    proceeds=notional-f; pnl=proceeds-entry_cost; quote+=proceeds; fees+=f; realized.append(pnl)
                    fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                        "timestamp_utc":ts.isoformat(),"side":"SELL","asset":holding,"quantity":str(qty),
                        "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":str(pnl),"reason":"scheduled next-open MACD/EMA50 exit"})
                    holding,qty,entry_cost=None,ZERO,ZERO
                    position_stop,position_entry_index=None,None
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
                        if entry_atr is None or entry_atr<=ZERO:
                            raise RuntimeError("FAIL-CLOSED: entry ATR missing for fixed stop")
                        position_stop=px-STOP_ATR_MULTIPLE*entry_atr
                        position_entry_index=i
                        fills.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
                            "timestamp_utc":ts.isoformat(),"side":"BUY","asset":holding,"quantity":str(qty),
                            "price_usdt":str(px),"fee_usdt":str(f),"realized_pnl_usdt":"",
                            "requested_weight_fraction":str(target_weight),
                            "reason":"MACD bullish crossover; 15% ATR weight frozen at signal close"})
        adverse=_adverse(spread,slip)
        equity=quote if holding is None else quote+qty*closes[holding][i]*(ONE-adverse)*(ONE-fee)
        peak=max(peak,equity)
        curve.append({"period":window_name,"capital_initial_usdt":str(capital),"cost_scenario":scenario,
            "timestamp_utc":candle.end.isoformat(),"equity_usdt":str(equity),"holding":holding or "CASH",
            "drawdown_pct":str((equity/peak-ONE)*HUNDRED if peak else ZERO)})
        # Current completed candle may signal; all orders fill at the next 4h open.
        if n<len(evals["BTCUSDT"])-1:
            tgt=None
            why=""
            if holding is not None:
                current_vwap=vwaps[holding][i]
                current_ema50=ema50s[holding][i]
                held_bars=(i-position_entry_index) if position_entry_index is not None else 0
                why=_exit_reason_vwap(closes[holding][i],current_vwap,current_ema50,position_stop,held_bars) or ""
                if why:
                    decisions+=1
            else:
                tgt,why=_target_vwap_reclaim(i,closes,emas,ema50s,vwaps,atrs)
                if tgt is not None:
                    decisions+=1
            if why:
                requested_weight=ZERO
                requested_entry_atr=None
                if tgt is not None:
                    requested_weight=risk_weights[tgt][i]
                    requested_entry_atr=atrs[tgt][i]
                    if requested_weight is None or requested_weight<=ZERO or requested_entry_atr is None or requested_entry_atr<=ZERO:
                        tgt=None
                        requested_weight=ZERO
                        requested_entry_atr=None
                        why += "; ATR14 sizing/stop unavailable, remain in cash"
                if tgt!=holding:
                    pending_target,pending_set,pending_target_weight=tgt,True,requested_weight
                    pending_entry_atr=requested_entry_atr
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
        "signal_decisions":decisions,"cash_final_equity_usdt":str(capital),**benchmarks,**hashes,
        "evaluation_bars":expected}
    return result,fills,curve

def main():
    out=Path("vwap24-reclaim-atr15-2025-2026-output")
    out.mkdir(exist_ok=True)
    print("Frozen BTC/ETH 4h rolling VWAP24 reclaim + EMA50/EMA200 trend; 15% ATR sizing.")
    print("Entry: previous close<=prior VWAP24 and current close>VWAP24, close>EMA200, EMA50>EMA200, positive 24-bar return; next-open fill.")
    print("Exit: close<VWAP24, close<EMA50, close<=entry fill-2x signal ATR14, or 18 bars; next-open fill.")
    print("Research HEAD:",subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip())
    print(f"Interval [{FULL_START.isoformat()}, {END.isoformat()}); warmup {WARMUP_BARS}; expected bars per asset {EXPECTED_BARS}")
    items,raw,hashes,metadata,rules={},{},{},{},{}
    for asset in ASSETS:
        rules[asset]=_current_rules(asset)
        loaded=_fetch(asset,rules[asset])
        items[asset],raw[asset],hashes[asset]=loaded[0],loaded[1],loaded[2]
        metadata[asset]={"completeness_unknown":loaded[3],"finality_unknown":loaded[4],"observed_bars":len(loaded[0]),
            "dataset_sha256":loaded[2],"current_filters":{"step":str(rules[asset]["step"]),"min_qty":str(rules[asset]["min_qty"]),
            "min_notional":str(rules[asset]["min_notional"]),"snapshot":rules[asset]["snapshot"],"sha256":rules[asset]["sha256"],
            "retrieved_at_utc":rules[asset]["retrieved_at_utc"],"scope":rules[asset]["scope"]}}
        print(f"{asset}: bars={len(loaded[0])}, SHA256={loaded[2]}, completeness UNKNOWN={loaded[3]}, finality UNKNOWN={loaded[4]}, filters={rules[asset]['sha256']}")
    if [x.payload.start for x in items["BTCUSDT"]]!=[x.payload.start for x in items["ETHUSDT"]]:
        raise RuntimeError("FAIL-CLOSED: BTC/ETH timestamps are not aligned")
    canonical_hashes={asset:_canonical_ohlcv_sha256(items[asset]) for asset in ASSETS}
    for asset,expected_hash in EXPECTED_CANONICAL_OHLCV_SHA256.items():
        if canonical_hashes[asset]!=expected_hash:
            raise RuntimeError(f"BLOCKED: {asset} canonical OHLCV SHA-256 differs from preregistered Issue #193: {canonical_hashes[asset]}")
        print(f"{asset}: canonical OHLCV SHA-256 matched Issue #181: {canonical_hashes[asset]}")
    closes={a:[x.payload.close for x in items[a]] for a in ASSETS}
    opens={a:[x.payload.open for x in items[a]] for a in ASSETS}
    emas={a:_ema(closes[a],EMA_PERIOD) for a in ASSETS}
    ema50s={a:_ema(closes[a],50) for a in ASSETS}
    atrs=_atr_series(items)
    vwaps=_vwap_indicators(items)
    risk_weights=_atr_target_weights(items)
    risk_series=[{"index":i,"timestamp_utc":items["BTCUSDT"][i].payload.start.isoformat(),
        "BTCUSDT_weight":None if risk_weights["BTCUSDT"][i] is None else str(risk_weights["BTCUSDT"][i]),
        "ETHUSDT_weight":None if risk_weights["ETHUSDT"][i] is None else str(risk_weights["ETHUSDT"][i])}
        for i in range(len(items["BTCUSDT"]))]
    risk_hash=hashlib.sha256(json.dumps(risk_series,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    vwap_records={a:[{"timestamp":items[a][i].payload.start.isoformat(),"vwap24":None if vwaps[a][i] is None else str(vwaps[a][i])}
        for i in range(len(items[a]))] for a in ASSETS}
    vwap_hash=hashlib.sha256(json.dumps(vwap_records,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    signal_records={a:[{"timestamp":items[a][i].payload.start.isoformat(),
        "target":_target_vwap_reclaim(i,closes,emas,ema50s,vwaps,atrs)[0]}
        for i in range(len(items[a]))] for a in ASSETS}
    signal_hash=hashlib.sha256(json.dumps(signal_records,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    combined=hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    manifest={"full_start":FULL_START.isoformat(),"end_exclusive":END.isoformat(),"warmup_start":WARMUP_START.isoformat(),
        "warmup_bars":WARMUP_BARS,"interval":INTERVAL,"expected_bars_per_asset":EXPECTED_BARS,"missing_or_interpolated_bars":0,
        "combined_dataset_sha256":combined,"asset_dataset_sha256":hashes,"canonical_ohlcv_sha256":canonical_hashes,
        "metadata":metadata,"risk_weight_series_sha256":risk_hash,"risk_weight_series":risk_series,
        "vwap24_series_sha256":vwap_hash,"signal_series_sha256":signal_hash,
        "rule":{"strategy":"Rolling VWAP24 reclaim + EMA50>EMA200 trend",
            "entry":"previous close<=prior VWAP24, close>current VWAP24, close>EMA200, EMA50>EMA200, 24-bar return>0; fill next open",
            "exit":"close<VWAP24 OR close<EMA50 OR close<=entry fill-2*signal ATR14 OR held bars>=18; fill next open",
            "risk_overlay":"weight=min(50%,15%/((ATR14/close)*sqrt(365*6))); frozen at signal close",
            "atr_target_annualized_proxy":"15%","position_weight_cap":str(MAX_POSITION_FRACTION),
            "canonical_ohlcv_sha256_required":EXPECTED_CANONICAL_OHLCV_SHA256,
            "benchmark_fraction":str(BENCHMARK_FRACTION),
            "costs":[{"name":n,"fee":str(f),"assumed_full_spread_bps":str(s),"assumed_slippage_bps":str(l)} for n,f,s,l in SCENARIOS],
            "windows":[{"name":n,"start":s.isoformat(),"end_exclusive":e.isoformat()} for n,s,e in WINDOWS]},
        "data":raw}
    (out/"dataset-evidence.json").write_text(json.dumps(manifest,sort_keys=True,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    evidence_hashes={"btc_dataset_sha256":hashes["BTCUSDT"],"eth_dataset_sha256":hashes["ETHUSDT"],
        "combined_dataset_sha256":combined,"btc_filter_sha256":rules["BTCUSDT"]["sha256"],"eth_filter_sha256":rules["ETHUSDT"]["sha256"],
        "btc_canonical_ohlcv_sha256":canonical_hashes["BTCUSDT"],"eth_canonical_ohlcv_sha256":canonical_hashes["ETHUSDT"],
        "vwap24_series_sha256":vwap_hash,"signal_series_sha256":signal_hash}
    results,all_fills,all_curves=[],[],[]
    for win,start,end in WINDOWS:
        for capital in (Decimal("60"),Decimal("100")):
            for scenario,fee,spread,slip in SCENARIOS:
                row,fills,curve=_simulate(items,win,start,end,capital,scenario,fee,spread,slip,
                    closes,opens,emas,ema50s,vwaps,rules,evidence_hashes,risk_weights)
                results.append(row); all_fills.extend(fills); all_curves.extend(curve)
                print(f"RESULT | {win} | capital={capital} | {scenario} | PnL={Decimal(row['net_pnl_usdt']):+.5f} | "
                    f"return={Decimal(row['return_pct']):+.3f}% | DD={Decimal(row['max_drawdown_pct']):.3f}% | "
                    f"closures={row['completed_round_trips']} W/L={row['realized_wins']}/{row['realized_losses']} | "
                    f"fees={Decimal(row['total_fees_usdt']):.4f} | BTC B&H={Decimal(row['btc_usdt_buy_hold_net_pnl_usdt']):+.3f} | "
                    f"ETH B&H={Decimal(row['eth_usdt_buy_hold_net_pnl_usdt']):+.3f}")
    for name,rows in (("summary.csv",results),("fills.csv",all_fills),("equity.csv",all_curves)):
        with (out/name).open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=sorted({k for row in rows for k in row}))
            writer.writeheader();writer.writerows(rows)
    stress=[r for r in results if r["cost_scenario"]=="STRESS_2"]
    by={(r["period"],r["capital_initial_usdt"]):r for r in stress}
    caps=("60","100")
    periods=("COMBINED_2025_2026","2025","2026_YTD")
    positive=all(Decimal(by[(p,c)]["net_pnl_usdt"])>ZERO for p in periods for c in caps)
    closures=all(by[(p,c)]["completed_round_trips"]>=5 for p in ("COMBINED_2025_2026","2026_YTD") for c in caps)
    risk=all(Decimal(by[(p,c)]["max_drawdown_pct"])>=Decimal("-15") for p in periods for c in caps)
    ytd_benchmark=all(Decimal(by[("2026_YTD",c)]["net_pnl_usdt"])>ZERO
        and Decimal(by[("2026_YTD",c)]["net_pnl_usdt"])>Decimal(by[("2026_YTD",c)]["btc_usdt_buy_hold_net_pnl_usdt"])
        and Decimal(by[("2026_YTD",c)]["net_pnl_usdt"])>Decimal(by[("2026_YTD",c)]["eth_usdt_buy_hold_net_pnl_usdt"]) for c in caps)
    baseline_15pct={
        ("COMBINED_2025_2026","60"):Decimal("9.9912854577983"),
        ("COMBINED_2025_2026","100"):Decimal("16.7946922422263"),
        ("2026_YTD","60"):Decimal("3.7691729934701"),
        ("2026_YTD","100"):Decimal("6.2323575776842")}
    beats_current=all(Decimal(by[(p,c)]["net_pnl_usdt"])>baseline_15pct[(p,c)]
        for p in ("COMBINED_2025_2026","2026_YTD") for c in caps)
    gates={"positive_stress2_pnl_all_windows_both_capitals":positive,
        "at_least_5_closures_combined_and_ytd_both_capitals":closures,
        "max_drawdown_no_worse_than_minus15_all_windows_both_capitals":risk,
        "2026_ytd_beats_cash_btc_and_eth_buy_hold":ytd_benchmark,
        "beats_15pct_atr_rotation_in_combined_and_ytd":beats_current}
    passed=positive and closures and risk and ytd_benchmark and beats_current
    report=["# BTC/ETH 4h rolling VWAP24 reclaim + EMA50/EMA200 trend — ATR15 sizing, 2025–2026 validation","",
        "Disposition: retrospective research only; NO_TRADE remains in force.","",
        f"- Risk weight series SHA-256: {risk_hash}; VWAP24 series SHA-256: {vwap_hash}; signals SHA-256: {signal_hash}.",
        f"- Full interval: [{FULL_START.isoformat()}, {END.isoformat()}); {EXPECTED_BARS-WARMUP_BARS} evaluation bars per asset plus {WARMUP_BARS} warm-up bars.",
        f"- Combined SHA-256: {combined}; BTC record SHA-256: {hashes['BTCUSDT']}; ETH record SHA-256: {hashes['ETHUSDT']}.",
        f"- Canonical OHLCV hashes (must match #181 exactly): {json.dumps(canonical_hashes,sort_keys=True)}.",
        f"- Market-data metadata: {json.dumps({a:{'completeness_unknown':metadata[a]['completeness_unknown'],'finality_unknown':metadata[a]['finality_unknown']} for a in ASSETS},sort_keys=True)}.",
        "- Entry requires prior close at/below prior rolling VWAP24 and current close above current VWAP24, plus close>EMA200, EMA50>EMA200 and positive 24-bar return.",
        "- Exit on completed-bar close below VWAP24/EMA50, below fixed entry-fill-2x signal ATR14, or after 18 bars; all fills next open.",
        "- Position sizing uses the frozen #181 ATR15 rule capped at 50%; one position; no same-open re-entry.",
        "- Costs: BASE fee 10 bps/fill, full spread 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20.",
        "- Historical availability, exact historical lot filters and executable quotes remain UNKNOWN; spread/slippage are modeled.","",
        "| Period | Capital | Cost | Net PnL | Return | Max DD | Closures | W/L | Fees | BTC B&H | ETH B&H |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        report.append(f"| {r['period']} | {r['capital_initial_usdt']} | {r['cost_scenario']} | {Decimal(r['net_pnl_usdt']):+.4f} | "
            f"{Decimal(r['return_pct']):+.2f}% | {Decimal(r['max_drawdown_pct']):.2f}% | {r['completed_round_trips']} | "
            f"{r['realized_wins']}/{r['realized_losses']} | {Decimal(r['total_fees_usdt']):.4f} | "
            f"{Decimal(r['btc_usdt_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(r['eth_usdt_buy_hold_net_pnl_usdt']):+.4f} |")
    report += ["","## Preregistered promotion gates (STRESS_2)",""]
    for k,v in gates.items(): report.append(f"- {k}: {json.dumps(v,sort_keys=True)}")
    report += ["",f"Historical gate: {'PASS' if passed else 'FAIL'}","",
        "This is a retrospective result, not a clean holdout or future-profit guarantee. Even a pass only qualifies for shadow consideration, not PAPER/LIVE. Preserve main, PRIME and NO_TRADE.",""]
    (out/"report.md").write_text("\n".join(report),encoding="utf-8")
    gate_payload={"strategy":"BTC_ETH_VWAP24_RECLAIM_ATR15_V1","decision":"PASS_HISTORICAL_SHADOW_ONLY" if passed else "FAIL_NO_TRADE",
        "gate_summary":{"passed":sum(bool(v) for v in gates.values()),"failed":sum(not bool(v) for v in gates.values()),"total":len(gates)},"gates":gates}
    (out/"gate_results.json").write_text(json.dumps(gate_payload,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    result_payload={"strategy":"BTC_ETH_VWAP24_RECLAIM_ATR15_V1","decision":gate_payload["decision"],
        "source_git_sha":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        "source_raw_dataset_sha256":combined,"canonical_ohlcv_sha256":canonical_hashes,"risk_weight_series_sha256":risk_hash,
        "vwap24_series_sha256":vwap_hash,"signal_series_sha256":signal_hash,
        "scenario_results":[{k:r.get(k) for k in ("period","capital_initial_usdt","cost_scenario","net_pnl_usdt","return_pct","max_drawdown_pct","completed_round_trips","realized_wins","realized_losses","total_fees_usdt")} for r in results],
        "gates":gates}
    (out/"result.json").write_text(json.dumps(result_payload,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    file_hashes={}
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name!="manifest.json":
            payload=path.read_bytes();file_hashes[path.name]={"size_bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}
    artifact_manifest={"strategy":"BTC_ETH_VWAP24_RECLAIM_ATR15_V1","decision":gate_payload["decision"],
        "source_git_sha":result_payload["source_git_sha"],"dataset_sha256":combined,"canonical_ohlcv_sha256":canonical_hashes,
        "risk_weight_series_sha256":risk_hash,"vwap24_series_sha256":vwap_hash,"signal_series_sha256":signal_hash,"files":file_hashes}
    (out/"manifest.json").write_text(json.dumps(artifact_manifest,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("PROMOTION_GATES:",json.dumps(gates,sort_keys=True))
    print("GATE_SUMMARY:",json.dumps(gate_payload["gate_summary"],sort_keys=True))
    print("Evidence written to",out)

if __name__=="__main__": main()
