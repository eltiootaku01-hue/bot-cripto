from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy

from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine
from research.pullback_btc_ema20_reclaim_atr_v1 import (
    ema_series, pullback_fraction_at, entry_condition_at, exit_condition_at,
    pullback_signals_and_weights,
)

UTC=timezone.utc

def basic_item(i,close,high=None,low=None,opening=None):
    start=datetime(2018,11,27,tzinfo=UTC)+timedelta(days=i)
    c=Decimal(str(close)); op=c if opening is None else Decimal(str(opening))
    h=max(c,op)+Decimal("2") if high is None else Decimal(str(high))
    l=min(c,op)-Decimal("2") if low is None else Decimal(str(low))
    candle=Candle(
      start=start,end=start+timedelta(days=1)-timedelta(milliseconds=1),timeframe="1d",
      open=op,high=h,low=l,close=c,volume=Decimal("100"),
      candle_state=CandleState.CLOSED,completeness=DataCompleteness.COMPLETE,
      finality=CandleFinality.UNKNOWN)
    instrument=InstrumentIdentity(
      instrument_id="binance:SPOT:BTCUSDT",symbol="BTC/USDT",market="SPOT",
      instrument_type=InstrumentType.CRYPTO_SPOT,base_asset="BTC",quote_asset="USDT")
    source=SourceIdentity(source_id="binance-spot-rest",provider="binance",venue="BINANCE")
    return MarketData(instrument=instrument,source=source,data_type="CANDLE",
      observed_at=start,received_at=candle.end,available_at=None,payload=candle,
      quality=DataQuality.VALID,completeness=DataCompleteness.UNKNOWN)

def fixture(n=620):
    out=[]
    for i in range(n):
      if i<260: c=Decimal("100")
      elif i<430: c=Decimal("100")+Decimal(i-259)*Decimal("0.7")
      elif i<450: c=Decimal("219")-Decimal(i-429)*Decimal("1.4")
      else: c=Decimal("191")+Decimal(i-450)*Decimal("1.1")
      out.append(basic_item(i,c))
    return out

def test_ema20_recursive_seed_and_warmup():
    values=[Decimal("10"),Decimal("12"),Decimal("9"),Decimal("15")]
    e=ema_series(values,3)
    alpha=Decimal(2)/Decimal(4)
    expected=alpha*Decimal("9")+(Decimal(1)-alpha)*Decimal("11")
    assert e[0] is None and e[1] is None
    assert e[2]==expected
    assert e[3]==alpha*Decimal("15")+(Decimal(1)-alpha)*expected

def test_pullback_reference_excludes_current_candle_and_bounds_are_inclusive():
    closes=[Decimal("200")]*40
    highs=[Decimal("220")]*40
    i=35
    assert pullback_fraction_at(closes,highs,i)==Decimal("200")/Decimal("220")-1
    highs[i]=Decimal("1000") # current candle must not change the reference high
    assert pullback_fraction_at(closes,highs,i)==Decimal("200")/Decimal("220")-1

def test_entry_requires_ema20_reclaim_bullish_regime_and_4_to_12_percent_pullback():
    i=220
    closes=[Decimal("200")]*240
    highs=[Decimal("220")]*240
    e20=[Decimal("201")]*240
    e50=[Decimal("200")]*240
    e200=[Decimal("180")]*240
    closes[i-1]=Decimal("198")
    e20[i-1]=Decimal("200")
    closes[i]=Decimal("205")
    e20[i]=Decimal("202")
    assert entry_condition_at(closes,highs,e20,e50,e200,i)
    # Too shallow and too deep pullbacks are both rejected.
    highs[:]=[Decimal("210")]*240
    assert not entry_condition_at(closes,highs,e20,e50,e200,i)
    highs[:]=[Decimal("240")]*240
    assert not entry_condition_at(closes,highs,e20,e50,e200,i)
    # A valid pullback still rejects if price has not reclaimed EMA20 or is below EMA50/200.
    highs[:]=[Decimal("220")]*240
    closes[i]=Decimal("200")
    assert not entry_condition_at(closes,highs,e20,e50,e200,i)

def test_exit_fires_below_ema50_or_ema200():
    closes=[Decimal("100"),Decimal("94"),Decimal("80")]
    e50=[Decimal("90"),Decimal("95"),Decimal("85")]
    e200=[Decimal("70"),Decimal("70"),Decimal("90")]
    assert not exit_condition_at(closes,e50,e200,0)
    assert exit_condition_at(closes,e50,e200,1)
    assert exit_condition_at(closes,e50,e200,2)

def test_full_indicator_series_is_causal_and_risk_weight_is_bounded():
    rows=fixture()
    base=pullback_signals_and_weights(rows)
    cut=420
    changed=deepcopy(rows)
    for i in range(cut+1,len(changed)):
        # Rebuild later candles with extreme prices; past signal arrays must not change.
        changed[i]=basic_item(i,"999999")
    alt=pullback_signals_and_weights(changed)
    for component in range(len(base)):
        assert base[component][cut]==alt[component][cut]
    weights=base[2]
    assert all(w is None or Decimal(0)<w<=Decimal("0.95") for w in weights)

def test_shared_ledger_uses_next_open_and_terminal_close():
    rows=[basic_item(i,100+i,opening=100+i) for i in range(500)]
    rules={"instrument_id":"binance:SPOT:BTCUSDT","step":Decimal("0.0001"),
           "min_qty":Decimal("0.0001"),"min_notional":Decimal("5"),"snapshot":{},"sha256":"unit-test"}
    entries=[False]*len(rows); exits=[False]*len(rows); weights=[None]*len(rows)
    atr=[Decimal("1")]*len(rows); ema=[None]*len(rows)
    entries[450]=True; exits[455]=True; weights[450]=Decimal("0.2")
    old=engine.CANDIDATE
    try:
        engine.CANDIDATE="UNIT_PULLBACK"
        r1,f1,c1=engine._simulate(rows,rules,entries,exits,weights,atr,ema,"EXIT",
          rows[449].payload.start,rows[458].payload.start,Decimal("60"),"STRESS_2",
          Decimal("0.002"),Decimal("50"),Decimal("20"))
        buys=[x for x in f1 if x["side"]=="BUY"]
        sells=[x for x in f1 if x["side"]=="SELL"]
        assert len(buys)==1 and buys[0]["timestamp_utc"]==rows[451].payload.start.isoformat()
        assert len(sells)==1 and sells[0]["timestamp_utc"]==rows[456].payload.start.isoformat()
        assert Decimal(r1["total_fees_usdt"])>0
        r2,f2,c2=engine._simulate(rows,rules,entries,[False]*len(rows),weights,atr,ema,"TERM",
          rows[449].payload.start,rows[455].payload.start,Decimal("60"),"STRESS_2",
          Decimal("0.002"),Decimal("50"),Decimal("20"))
        terminal=[x for x in f2 if x["side"]=="SELL"]
        assert len(terminal)==1
        assert terminal[0]["timestamp_utc"]==rows[454].payload.end.isoformat()
        assert "terminal" in terminal[0]["reason"].lower()
        assert Decimal(r2["total_fees_usdt"])>0
    finally:
        engine.CANDIDATE=old
