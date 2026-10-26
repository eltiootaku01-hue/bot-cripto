from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy

from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine
from research.kama_btc_efficiency_state_ema200_atr_v1 import (
    efficiency_ratio, kama_series, ema_series, bullish_state_at, exit_condition_at,
    kama_signals_and_weights, ER_PERIOD, FAST_PERIOD, SLOW_PERIOD, ER_MIN,
)

UTC=timezone.utc

def item(index, close, high=None, low=None, open_=None):
    start=datetime(2018,11,27,tzinfo=UTC)+timedelta(days=index)
    c=Decimal(str(close)); op=c if open_ is None else Decimal(str(open_))
    h=max(c,op)+Decimal("2") if high is None else Decimal(str(high))
    l=min(c,op)-Decimal("2") if low is None else Decimal(str(low))
    payload=Candle(
      start=start,end=start+timedelta(days=1)-timedelta(milliseconds=1),timeframe="1d",
      open=op,high=h,low=l,close=c,volume=Decimal("100"),
      candle_state=CandleState.CLOSED,completeness=DataCompleteness.COMPLETE,finality=CandleFinality.UNKNOWN)
    instrument=InstrumentIdentity(
      instrument_id="binance:SPOT:BTCUSDT",symbol="BTC/USDT",market="SPOT",
      instrument_type=InstrumentType.CRYPTO_SPOT,base_asset="BTC",quote_asset="USDT")
    source=SourceIdentity(source_id="binance-spot-rest",provider="binance",venue="BINANCE")
    return MarketData(instrument=instrument,source=source,data_type="CANDLE",observed_at=start,
      received_at=payload.end,available_at=None,payload=payload,quality=DataQuality.VALID,
      completeness=DataCompleteness.UNKNOWN)

def trend_fixture(n=700):
    rows=[]
    for i in range(n):
        if i<260: c=Decimal("100")
        elif i<510: c=Decimal("100")+Decimal(i-259)*Decimal("0.9")
        else: c=Decimal("325")-Decimal(i-510)*Decimal("1.0")
        rows.append(item(i,c))
    return rows

def test_efficiency_ratio_matches_manual_arithmetic_and_zero_path_is_unavailable():
    closes=[Decimal("10"),Decimal("11"),Decimal("10"),Decimal("13")]
    assert efficiency_ratio(closes,3,3)==Decimal("3")/Decimal("5")
    flat=[Decimal("10")]*15
    assert efficiency_ratio(flat,10,10) is None
    assert efficiency_ratio(closes,2,3) is None

def test_kama_smoothing_uses_frozen_fast_and_slow_constants():
    closes=[Decimal("10"),Decimal("11"),Decimal("12"),Decimal("13"),Decimal("14")]
    er,kama=kama_series(closes,period=1,fast_period=2,slow_period=30)
    fast_sc=Decimal(2)/Decimal(3)
    slow_sc=Decimal(2)/Decimal(31)
    sc=(Decimal(1)*(fast_sc-slow_sc)+slow_sc)**2
    assert kama[0]==Decimal("10")
    assert er[1]==Decimal("1")
    assert kama[1]==Decimal("10")+sc*(Decimal("11")-Decimal("10"))

def test_state_requires_efficiency_kama_slope_and_ema200_regime():
    closes=[Decimal("200")]*220
    er=[None]*220; kama=[Decimal("190")]*220; ema=[None]*199+[Decimal("180")]*21
    er[215]=Decimal("0.35"); kama[215]=Decimal("195")
    assert bullish_state_at(closes,er,kama,ema,215) # close>KAMA, rising slope, above EMA
    er[215]=Decimal("0.3499")
    assert not bullish_state_at(closes,er,kama,ema,215)
    er[215]=Decimal("0.8"); kama[210]=Decimal("196")
    assert not bullish_state_at(closes,er,kama,ema,215) # KAMA slope is not positive
    assert not bullish_state_at(closes,er,kama,ema,198) # EMA200 warm-up

def test_exit_triggers_when_kama_slope_fails_or_price_breaks_ema200():
    closes=[Decimal("100")]*8
    kama=[Decimal("101"),Decimal("102"),Decimal("103"),Decimal("104"),Decimal("105"),Decimal("106"),Decimal("101"),Decimal("100")]
    ema=[Decimal("90")]*8
    assert not exit_condition_at(closes,kama,ema,5)
    assert exit_condition_at(closes,kama,ema,6)
    broken=ema[:]; broken[5]=Decimal("101")
    assert exit_condition_at(closes,kama,broken,5)

def test_indicator_series_are_causal_and_sizing_is_bounded():
    rows=trend_fixture()
    baseline=kama_signals_and_weights(rows)
    cut=470
    changed=deepcopy(rows)
    for i in range(cut+1,len(changed)):
        changed[i]=item(i,"1")
    other=kama_signals_and_weights(changed)
    for component in range(8):
        assert baseline[component][cut]==other[component][cut]
    assert all(w is None or Decimal(0)<w<=Decimal("0.95") for w in baseline[2])

def test_shared_ledger_uses_next_open_fills_and_terminal_liquidation():
    rows=[item(i,100+i,open_=100+i) for i in range(500)]
    rules={"instrument_id":"binance:SPOT:BTCUSDT","step":Decimal("0.0001"),
      "min_qty":Decimal("0.0001"),"min_notional":Decimal("5"),"snapshot":{},"sha256":"unit-test"}
    entries=[False]*len(rows); exits=[False]*len(rows); weights=[None]*len(rows)
    atr=[Decimal("1")]*len(rows); ema=[None]*len(rows)
    entries[450]=True; exits[455]=True; weights[450]=Decimal("0.2")
    old=engine.CANDIDATE
    try:
      engine.CANDIDATE="UNIT_KAMA"
      result,fills,curve=engine._simulate(rows,rules,entries,exits,weights,atr,ema,"EXIT",
        rows[449].payload.start,rows[458].payload.start,Decimal("60"),"STRESS_2",
        Decimal("0.002"),Decimal("50"),Decimal("20"))
      buys=[x for x in fills if x["side"]=="BUY"]
      sells=[x for x in fills if x["side"]=="SELL"]
      assert len(buys)==1 and buys[0]["timestamp_utc"]==rows[451].payload.start.isoformat()
      assert len(sells)==1 and sells[0]["timestamp_utc"]==rows[456].payload.start.isoformat()
      assert Decimal(result["total_fees_usdt"])>0
      result2,fills2,_=engine._simulate(rows,rules,entries,[False]*len(rows),weights,atr,ema,"TERM",
        rows[449].payload.start,rows[455].payload.start,Decimal("60"),"STRESS_2",
        Decimal("0.002"),Decimal("50"),Decimal("20"))
      terminal=[x for x in fills2 if x["side"]=="SELL"]
      assert len(terminal)==1 and terminal[0]["timestamp_utc"]==rows[454].payload.end.isoformat()
      assert "terminal" in terminal[0]["reason"].lower()
      assert Decimal(result2["total_fees_usdt"])>0
    finally:
      engine.CANDIDATE=old
