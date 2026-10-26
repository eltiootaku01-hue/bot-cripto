from datetime import datetime, timedelta, timezone
from decimal import Decimal
from copy import deepcopy
from bot_obrero.market_data import Candle, CandleFinality, CandleState, DataCompleteness, DataQuality, InstrumentIdentity, InstrumentType, MarketData, SourceIdentity
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine
from research.rsi2_btc_ema200_mean_reversion_v1 import ema_series, sma_series, rsi_wilder, entry_condition_at, exit_condition_at, signals_and_weights

UTC=timezone.utc

def item(index,close,high=None,low=None,open_=None):
    start=datetime(2018,11,27,tzinfo=UTC)+timedelta(days=index)
    c=Decimal(str(close)); op=c if open_ is None else Decimal(str(open_))
    h=max(c,op)+Decimal("2") if high is None else Decimal(str(high))
    l=min(c,op)-Decimal("2") if low is None else Decimal(str(low))
    p=Candle(start=start,end=start+timedelta(days=1)-timedelta(milliseconds=1),timeframe="1d",open=op,high=h,low=l,close=c,volume=Decimal("100"),candle_state=CandleState.CLOSED,completeness=DataCompleteness.COMPLETE,finality=CandleFinality.UNKNOWN)
    inst=InstrumentIdentity(instrument_id="binance:SPOT:BTCUSDT",symbol="BTC/USDT",market="SPOT",instrument_type=InstrumentType.CRYPTO_SPOT,base_asset="BTC",quote_asset="USDT")
    src=SourceIdentity(source_id="binance-spot-rest",provider="binance",venue="BINANCE")
    return MarketData(instrument=inst,source=src,data_type="CANDLE",observed_at=start,received_at=p.end,available_at=None,payload=p,quality=DataQuality.VALID,completeness=DataCompleteness.UNKNOWN)

def fixture(n=520):
    rows=[]
    for i in range(n):
        if i<260: c=Decimal("100")
        elif i<440: c=Decimal("100")+Decimal(i-259)*Decimal("0.8")
        else: c=Decimal("244")-Decimal(i-440)*Decimal("0.4")+(Decimal("3") if i%4 else Decimal("0"))
        rows.append(item(i,c))
    return rows

def test_rsi_wilder_seed_recursion_and_edge_cases():
    values=[Decimal("10"),Decimal("12"),Decimal("11"),Decimal("14")]
    r=rsi_wilder(values,2)
    assert r[0] is None and r[1] is None
    assert r[2]==Decimal("100")-Decimal("100")/(Decimal("1")+Decimal("2"))
    assert r[3]==Decimal("100")-Decimal("100")/(Decimal("1")+Decimal("8"))
    flat=rsi_wilder([Decimal("50")]*8,2)
    assert all(x==Decimal("50") for x in flat[2:])
    up=rsi_wilder([Decimal(10+i) for i in range(8)],2)
    assert up[2]==Decimal("100")

def test_ema200_and_sma5_warmup():
    values=[Decimal(100+i) for i in range(205)]
    ema=ema_series(values,200); sma=sma_series(values,5)
    assert ema[198] is None and ema[199] is not None
    assert sma[3] is None and sma[4]==sum(values[:5],Decimal(0))/Decimal(5)

def test_frozen_entry_and_exit_conditions():
    closes=[Decimal("100"),Decimal("98"),Decimal("97"),Decimal("102")]
    rsi=[None,Decimal("9"),Decimal("50"),Decimal("75")]
    ema=[None,Decimal("90"),Decimal("90"),Decimal("90")]
    sma=[None,Decimal("99"),Decimal("98"),Decimal("101")]
    assert entry_condition_at(closes,rsi,ema,1)
    assert not entry_condition_at(closes,rsi,ema,0)
    assert not entry_condition_at(closes,rsi,ema,2)
    assert exit_condition_at(closes,rsi,sma,ema,3)
    ema[2]=Decimal("100")
    assert exit_condition_at(closes,rsi,sma,ema,2)

def test_features_are_causal_and_weights_are_bounded():
    rows=fixture(); base=signals_and_weights(rows); cut=350
    changed=deepcopy(rows)
    for i in range(cut+1,len(changed)): changed[i]=item(i,"999999")
    other=signals_and_weights(changed)
    for component in range(7): assert base[component][cut]==other[component][cut]
    assert all(w is None or Decimal(0)<w<=Decimal("0.95") for w in base[2])

def test_shared_ledger_next_open_and_terminal_liquidation():
    rows=[item(i,100+i,open_=100+i) for i in range(500)]
    rules={"instrument_id":"binance:SPOT:BTCUSDT","step":Decimal("0.0001"),"min_qty":Decimal("0.0001"),"min_notional":Decimal("5"),"snapshot":{},"sha256":"unit-test"}
    entries=[False]*len(rows); exits=[False]*len(rows); weights=[None]*len(rows)
    atr=[Decimal("1")]*len(rows); ema=[None]*len(rows); entries[450]=True; exits[455]=True; weights[450]=Decimal("0.2")
    old=engine.CANDIDATE
    try:
        engine.CANDIDATE="UNIT_RSI2"
        r1,f1,_=engine._simulate(rows,rules,entries,exits,weights,atr,ema,"EXIT",rows[449].payload.start,rows[458].payload.start,Decimal("60"),"STRESS_2",Decimal("0.002"),Decimal("50"),Decimal("20"))
        buys=[x for x in f1 if x["side"]=="BUY"]; sells=[x for x in f1 if x["side"]=="SELL"]
        assert len(buys)==1 and buys[0]["timestamp_utc"]==rows[451].payload.start.isoformat()
        assert len(sells)==1 and sells[0]["timestamp_utc"]==rows[456].payload.start.isoformat()
        assert Decimal(r1["total_fees_usdt"])>0
        r2,f2,_=engine._simulate(rows,rules,entries,[False]*len(rows),weights,atr,ema,"TERM",rows[449].payload.start,rows[455].payload.start,Decimal("60"),"STRESS_2",Decimal("0.002"),Decimal("50"),Decimal("20"))
        terminal=[x for x in f2 if x["side"]=="SELL"]
        assert len(terminal)==1 and terminal[0]["timestamp_utc"]==rows[454].payload.end.isoformat()
        assert "terminal" in terminal[0]["reason"].lower() and Decimal(r2["total_fees_usdt"])>0
    finally:
        engine.CANDIDATE=old
