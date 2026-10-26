from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy

from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine
from research.ichimoku_btc_visible_cloud_atr_v1 import (
    midpoint, displaced_visible_series, ichimoku_series, bullish_state_at,
    exit_condition_at, ichimoku_signals_and_weights,
    TENKAN_PERIOD, KIJUN_PERIOD, SENKOU_B_PERIOD, DISPLACEMENT,
)

UTC=timezone.utc

def item(index, close, high=None, low=None, open_=None):
    start=datetime(2018,11,27,tzinfo=UTC)+timedelta(days=index)
    c=Decimal(str(close)); op=c if open_ is None else Decimal(str(open_))
    h=max(c,op)+Decimal("2") if high is None else Decimal(str(high))
    l=min(c,op)-Decimal("2") if low is None else Decimal(str(low))
    payload=Candle(start=start,end=start+timedelta(days=1)-timedelta(milliseconds=1),timeframe="1d",
      open=op,high=h,low=l,close=c,volume=Decimal("100"),
      candle_state=CandleState.CLOSED,completeness=DataCompleteness.COMPLETE,finality=CandleFinality.UNKNOWN)
    instrument=InstrumentIdentity(instrument_id="binance:SPOT:BTCUSDT",symbol="BTC/USDT",market="SPOT",
      instrument_type=InstrumentType.CRYPTO_SPOT,base_asset="BTC",quote_asset="USDT")
    source=SourceIdentity(source_id="binance-spot-rest",provider="binance",venue="BINANCE")
    return MarketData(instrument=instrument,source=source,data_type="CANDLE",observed_at=start,
      received_at=payload.end,available_at=None,payload=payload,quality=DataQuality.VALID,
      completeness=DataCompleteness.UNKNOWN)

def fixture(n=200):
    rows=[]
    for i in range(n):
        close=Decimal("100")+Decimal(i)/Decimal("2")
        rows.append(item(i,close,close+Decimal("3"),close-Decimal("2")))
    return rows

def test_9_26_52_midpoints_match_hand_calculation():
    highs=[Decimal(i) for i in range(1,61)]
    lows=[Decimal(i)-Decimal("10") for i in range(1,61)]
    assert midpoint(highs,lows,8,9)==(Decimal("9")+Decimal("-1"))/Decimal(2)
    assert midpoint(highs,lows,24,26)==(Decimal("25")+Decimal("15"))/Decimal(2)
    assert midpoint(highs,lows,50,52)==(Decimal("51")+Decimal("41"))/Decimal(2)
    assert midpoint(highs,lows,7,9) is None

def test_visible_cloud_uses_span_value_from_26_candles_earlier():
    raw=[Decimal(i) for i in range(80)]
    visible=displaced_visible_series(raw,26)
    assert visible[25] is None
    assert visible[26]==raw[0]
    assert visible[40]==raw[14]
    altered=raw[:]
    altered[41]=Decimal("999999")
    other=displaced_visible_series(altered,26)
    assert visible[40]==other[40]

def test_ichimoku_cloud_warmup_and_causal_offset():
    rows=fixture()
    base=ichimoku_series(rows)
    assert base["tenkan"][7] is None and base["tenkan"][8] is not None
    assert base["kijun"][24] is None and base["kijun"][25] is not None
    assert base["span_b_raw"][50] is None and base["span_b_raw"][51] is not None
    assert base["span_b_visible"][76] is None and base["span_b_visible"][77] is not None
    cut=130
    modified=deepcopy(rows)
    for i in range(cut+1,len(modified)):
        modified[i]=item(i,"999999")
    changed=ichimoku_series(modified)
    for key in base:
        assert base[key][cut]==changed[key][cut]

def test_bullish_state_and_exit_conditions_follow_frozen_cloud_rules():
    close=[Decimal("100")]*10
    tenkan=[Decimal("120")]*10; kijun=[Decimal("110")]*10
    span_a=[Decimal("105")]*10; span_b=[Decimal("104")]*10
    close[5]=Decimal("121")
    assert bullish_state_at(close,tenkan,kijun,span_a,span_b,5)
    span_a[5]=Decimal("130")
    assert not bullish_state_at(close,tenkan,kijun,span_a,span_b,5)
    span_a[5]=Decimal("105"); close[6]=Decimal("100")
    assert exit_condition_at(close,kijun,span_a,span_b,6)

def test_indicator_state_and_atr_weights_are_causal():
    rows=fixture(240)
    base=ichimoku_signals_and_weights(rows)
    cut=160
    altered=deepcopy(rows)
    for i in range(cut+1,len(altered)):
        altered[i]=item(i,"1")
    other=ichimoku_signals_and_weights(altered)
    for component in range(6):
        assert base[component][cut]==other[component][cut]
    assert all(w is None or Decimal(0)<w<=Decimal("0.95") for w in base[2])

def test_shared_ledger_next_open_and_terminal_liquidation():
    rows=[item(i,100+i,open_=100+i) for i in range(500)]
    rules={"instrument_id":"binance:SPOT:BTCUSDT","step":Decimal("0.0001"),
      "min_qty":Decimal("0.0001"),"min_notional":Decimal("5"),"snapshot":{},"sha256":"unit-test"}
    entries=[False]*len(rows); exits=[False]*len(rows); weights=[None]*len(rows)
    atr=[Decimal("1")]*len(rows); kijun=[None]*len(rows)
    entries[450]=True; exits[455]=True; weights[450]=Decimal("0.2")
    old=engine.CANDIDATE
    try:
      engine.CANDIDATE="UNIT_ICHIMOKU"
      result,fills,_=engine._simulate(rows,rules,entries,exits,weights,atr,kijun,"EXIT",
        rows[449].payload.start,rows[458].payload.start,Decimal("60"),"STRESS_2",
        Decimal("0.002"),Decimal("50"),Decimal("20"))
      buys=[x for x in fills if x["side"]=="BUY"]; sells=[x for x in fills if x["side"]=="SELL"]
      assert len(buys)==1 and buys[0]["timestamp_utc"]==rows[451].payload.start.isoformat()
      assert len(sells)==1 and sells[0]["timestamp_utc"]==rows[456].payload.start.isoformat()
      assert Decimal(result["total_fees_usdt"])>0
      r2,f2,_=engine._simulate(rows,rules,entries,[False]*len(rows),weights,atr,kijun,"TERM",
        rows[449].payload.start,rows[455].payload.start,Decimal("60"),"STRESS_2",
        Decimal("0.002"),Decimal("50"),Decimal("20"))
      terminal=[x for x in f2 if x["side"]=="SELL"]
      assert len(terminal)==1 and terminal[0]["timestamp_utc"]==rows[454].payload.end.isoformat()
      assert "terminal" in terminal[0]["reason"].lower()
      assert Decimal(r2["total_fees_usdt"])>0
    finally:
      engine.CANDIDATE=old
