from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
from bot_obrero.market_data import Candle, CandleFinality, CandleState, DataCompleteness, DataQuality, InstrumentIdentity, InstrumentType, MarketData, SourceIdentity
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine
from research.parabolic_sar_btc_ema200_atr_v1 import psar_series, ema_series, entry_condition_at, exit_condition_at, signals_and_weights, AF_STEP, AF_MAX

UTC=timezone.utc

def item(index,close,high,low,open_=None):
    start=datetime(2018,11,27,tzinfo=UTC)+timedelta(days=index)
    c=Decimal(str(close)); h=Decimal(str(high)); l=Decimal(str(low)); op=c if open_ is None else Decimal(str(open_))
    p=Candle(start=start,end=start+timedelta(days=1)-timedelta(milliseconds=1),timeframe="1d",open=op,high=h,low=l,close=c,volume=Decimal("100"),candle_state=CandleState.CLOSED,completeness=DataCompleteness.COMPLETE,finality=CandleFinality.UNKNOWN)
    inst=InstrumentIdentity(instrument_id="binance:SPOT:BTCUSDT",symbol="BTC/USDT",market="SPOT",instrument_type=InstrumentType.CRYPTO_SPOT,base_asset="BTC",quote_asset="USDT")
    src=SourceIdentity(source_id="binance-spot-rest",provider="binance",venue="BINANCE")
    return MarketData(instrument=inst,source=src,data_type="CANDLE",observed_at=start,received_at=p.end,available_at=None,payload=p,quality=DataQuality.VALID,completeness=DataCompleteness.UNKNOWN)

def manual_bars():
    raw=[
      ("100","110","90"),("110","112","91"),("112","114","92"),
      ("114","116","93"),("60","118","50"),("125","130","55")
    ]
    return [item(i,c,h,l) for i,(c,h,l) in enumerate(raw)]

def fixture(n=560):
    rows=[]
    for i in range(n):
        if i<120: c=Decimal("100")+Decimal(i)*Decimal("0.7")
        elif i<190: c=Decimal("184")-Decimal(i-120)*Decimal("1.4")
        else: c=Decimal("86")+Decimal(i-190)*Decimal("1.25")
        # Each candle has a causal range around its close.
        rows.append(item(i,c,c+Decimal("3"),c-Decimal("3")))
    return rows

def test_initialization_af_update_clamps_and_both_reversals():
    ps=psar_series(manual_bars())
    assert ps["long"][1] is True
    assert ps["sar"][1]==Decimal("90")
    assert ps["ep"][1]==Decimal("112")
    assert ps["af"][1]==AF_STEP
    assert ps["sar"][2]==Decimal("90")
    assert ps["af"][2]==Decimal("0.04")
    assert ps["long"][4] is False
    assert ps["sar"][4]==Decimal("116")
    assert ps["af"][4]==AF_STEP
    assert ps["long"][5] is True
    assert ps["sar"][5]==Decimal("50")
    assert ps["af"][5]==AF_STEP
    assert AF_MAX==Decimal("0.20")

def test_entry_needs_short_to_long_flip_and_close_above_ema200():
    closes=[Decimal("100"),Decimal("100"),Decimal("110"),Decimal("105")]
    state=[None,False,True,False]
    ema=[None,Decimal("90"),Decimal("100"),Decimal("100")]
    assert entry_condition_at(closes,state,ema,2)
    assert not entry_condition_at(closes,state,ema,3)
    ema[2]=Decimal("111")
    assert not entry_condition_at(closes,state,ema,2)

def test_exit_is_long_to_short_flip_or_ema200_loss():
    closes=[Decimal("100"),Decimal("110"),Decimal("90"),Decimal("80")]
    state=[None,True,False,False]
    ema=[None,Decimal("95"),Decimal("85"),Decimal("90")]
    assert not exit_condition_at(closes,state,ema,1)
    assert exit_condition_at(closes,state,ema,2)
    assert exit_condition_at(closes,state,ema,3)

def test_ema_warmup_causality_and_bounded_atr_weight():
    rows=fixture()
    base=signals_and_weights(rows)
    cut=350
    altered=deepcopy(rows)
    for i in range(cut+1,len(altered)):
        altered[i]=item(i,"100000","100005","99995")
    other=signals_and_weights(altered)
    for k in range(5):
        assert base[k][cut]==other[k][cut]
    for key in base[5]:
        assert base[5][key][cut]==other[5][key][cut]
    assert base[4][198] is None and base[4][199] is not None
    assert all(w is None or Decimal(0)<w<=Decimal("0.95") for w in base[2])

def test_shared_ledger_next_open_execution_and_terminal_liquidation():
    rows=[item(i,100+i,102+i,98+i,open_=100+i) for i in range(500)]
    rules={"instrument_id":"binance:SPOT:BTCUSDT","step":Decimal("0.0001"),"min_qty":Decimal("0.0001"),"min_notional":Decimal("5"),"snapshot":{},"sha256":"unit-test"}
    entries=[False]*len(rows); exits=[False]*len(rows); weights=[None]*len(rows)
    atr=[Decimal("1")]*len(rows); ema=[None]*len(rows)
    entries[450]=True; exits[455]=True; weights[450]=Decimal("0.2")
    old=engine.CANDIDATE
    try:
        engine.CANDIDATE="UNIT_PSAR"
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
