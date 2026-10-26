from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy

from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine
from research.dmi_btc_adx_ema200_atr_v1 import (
    dmi_series, ema_series, entry_condition_at, exit_condition_at,
    dmi_signals_and_weights, DMI_PERIOD, ADX_MIN,
)

UTC = timezone.utc

def candle(index, high, low, close, open_=None):
    start = datetime(2018, 11, 27, tzinfo=UTC) + timedelta(days=index)
    h, l, c = Decimal(str(high)), Decimal(str(low)), Decimal(str(close))
    op = c if open_ is None else Decimal(str(open_))
    payload = Candle(
        start=start, end=start+timedelta(days=1)-timedelta(milliseconds=1), timeframe="1d",
        open=op, high=h, low=l, close=c, volume=Decimal("100"),
        candle_state=CandleState.CLOSED, completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.UNKNOWN,
    )
    instrument = InstrumentIdentity(
        instrument_id="binance:SPOT:BTCUSDT", symbol="BTC/USDT", market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT, base_asset="BTC", quote_asset="USDT",
    )
    source = SourceIdentity(source_id="binance-spot-rest", provider="binance", venue="BINANCE")
    return MarketData(
        instrument=instrument, source=source, data_type="CANDLE", observed_at=start,
        received_at=payload.end, available_at=None, payload=payload,
        quality=DataQuality.VALID, completeness=DataCompleteness.UNKNOWN,
    )

def monotone_up_fixture(n=70):
    rows=[]
    for i in range(n):
        high=Decimal("110")+Decimal(2*i)
        low=Decimal("90")+Decimal(i)
        close=low+(high-low)*Decimal("0.75")
        rows.append(candle(i,high,low,close))
    return rows

def test_true_range_and_directional_movement_match_manual_formula():
    rows=[candle(0,"110","90","100"),candle(1,"113","89","108")]
    out=dmi_series(rows,1)
    assert out["true_range"][1]==Decimal("24")
    assert out["plus_dm"][1]==Decimal("3")
    assert out["minus_dm"][1]==Decimal("0")
    assert out["smoothed_tr"][1]==Decimal("24")
    assert out["smoothed_plus_dm"][1]==Decimal("3")
    assert out["smoothed_minus_dm"][1]==Decimal("0")
    assert out["plus_di"][1]==Decimal("12.5")
    assert out["minus_di"][1]==Decimal("0")
    assert out["dx"][1]==Decimal("100")

def test_wilder_warmup_and_adx_recursion_are_frozen():
    out=dmi_series(monotone_up_fixture(55),14)
    assert out["plus_di"][13] is None and out["plus_di"][14] is not None
    assert out["minus_di"][14]==Decimal("0") and out["dx"][14]==Decimal("100")
    assert out["adx"][26] is None
    assert out["adx"][27]==Decimal("100") and out["adx"][28]==Decimal("100")

def test_indicator_arrays_are_causal_and_ema200_has_exact_warmup():
    rows=monotone_up_fixture(280)
    base=dmi_signals_and_weights(rows)
    cut=250
    modified=deepcopy(rows)
    for i in range(cut+1,len(modified)):
        modified[i]=candle(i,"1000000","999000","999900")
    changed=dmi_signals_and_weights(modified)
    for component in range(len(base)):
        assert base[component][cut]==changed[component][cut]
    ema=ema_series([x.payload.close for x in rows],200)
    assert ema[198] is None and ema[199] is not None

def test_entry_requires_positive_di_cross_adx_threshold_and_ema_regime():
    closes=[Decimal("100"),Decimal("100"),Decimal("110")]
    pdi=[Decimal("15"),Decimal("10"),Decimal("30")]
    mdi=[Decimal("20"),Decimal("20"),Decimal("10")]
    adx=[Decimal("25"),Decimal("26"),Decimal("28")]
    ema=[Decimal("90"),Decimal("90"),Decimal("95")]
    assert entry_condition_at(closes,pdi,mdi,adx,ema,2)
    weak=adx[:]; weak[2]=Decimal("24.99")
    assert not entry_condition_at(closes,pdi,mdi,weak,ema,2)
    no_cross=pdi[:]; no_cross[1]=Decimal("25")
    assert not entry_condition_at(closes,no_cross,mdi,adx,ema,2)
    trend=ema[:]; trend[2]=Decimal("111")
    assert not entry_condition_at(closes,pdi,mdi,adx,trend,2)

def test_exit_requires_negative_di_cross_or_close_below_ema200():
    closes=[Decimal("100"),Decimal("101"),Decimal("90")]
    pdi=[Decimal("25"),Decimal("30"),Decimal("20")]
    mdi=[Decimal("20"),Decimal("15"),Decimal("25")]
    ema=[Decimal("80"),Decimal("80"),Decimal("95")]
    assert not exit_condition_at(closes,pdi,mdi,ema,1)
    assert exit_condition_at(closes,pdi,mdi,ema,2)

def test_shared_ledger_next_open_entry_exit_and_terminal_liquidation():
    rows=[candle(i,110+i*2,90+i,100+i) for i in range(500)]
    rules={"instrument_id":"binance:SPOT:BTCUSDT","step":Decimal("0.0001"),
           "min_qty":Decimal("0.0001"),"min_notional":Decimal("5"),"snapshot":{},"sha256":"unit-test"}
    entries=[False]*len(rows); exits=[False]*len(rows); weights=[None]*len(rows)
    atr=[Decimal("1")]*len(rows); ema=[None]*len(rows)
    entries[450]=True; exits[455]=True; weights[450]=Decimal("0.2")
    old=engine.CANDIDATE
    try:
        engine.CANDIDATE="UNIT_DMI"
        result,fills,curve=engine._simulate(rows,rules,entries,exits,weights,atr,ema,"EXIT",
          rows[449].payload.start,rows[458].payload.start,Decimal("60"),"STRESS_2",
          Decimal("0.002"),Decimal("50"),Decimal("20"))
        buys=[x for x in fills if x["side"]=="BUY"]
        sells=[x for x in fills if x["side"]=="SELL"]
        assert len(buys)==1 and buys[0]["timestamp_utc"]==rows[451].payload.start.isoformat()
        assert len(sells)==1 and sells[0]["timestamp_utc"]==rows[456].payload.start.isoformat()
        assert Decimal(result["total_fees_usdt"])>0

        result2,fills2,curve2=engine._simulate(rows,rules,entries,[False]*len(rows),weights,atr,ema,"TERM",
          rows[449].payload.start,rows[455].payload.start,Decimal("60"),"STRESS_2",
          Decimal("0.002"),Decimal("50"),Decimal("20"))
        terminal=[x for x in fills2 if x["side"]=="SELL"]
        assert len(terminal)==1
        assert terminal[0]["timestamp_utc"]==rows[454].payload.end.isoformat()
        assert "terminal" in terminal[0]["reason"].lower()
        assert Decimal(result2["total_fees_usdt"])>0
    finally:
        engine.CANDIDATE=old
