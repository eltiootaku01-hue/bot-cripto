from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from calendar import monthrange

from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine
from research.tsmom_btc_12_1m_ema200_atr_v1 import (
    ema_series, momentum_12_1, monthly_tsmom_signals_and_weights, is_utc_month_end,
)

UTC=timezone.utc

def row(index, close, *, opening=None, high=None, low=None):
    start=datetime(2018,11,27,tzinfo=UTC)+timedelta(days=index)
    c=Decimal(str(close))
    op=c if opening is None else Decimal(str(opening))
    h=max(op,c)+Decimal("2") if high is None else Decimal(str(high))
    l=min(op,c)-Decimal("2") if low is None else Decimal(str(low))
    candle=Candle(
        start=start, end=start+timedelta(days=1)-timedelta(milliseconds=1), timeframe="1d",
        open=op, high=h, low=l, close=c, volume=Decimal("100"),
        candle_state=CandleState.CLOSED, completeness=DataCompleteness.COMPLETE,
        finality=CandleFinality.UNKNOWN,
    )
    instrument=InstrumentIdentity(
        instrument_id="binance:SPOT:BTCUSDT",symbol="BTC/USDT",market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT,base_asset="BTC",quote_asset="USDT",
    )
    source=SourceIdentity(source_id="binance-spot-rest",provider="binance",venue="BINANCE")
    return MarketData(
        instrument=instrument,source=source,data_type="CANDLE",observed_at=start,
        received_at=candle.end,available_at=None,payload=candle,quality=DataQuality.VALID,
        completeness=DataCompleteness.UNKNOWN,
    )

def rising_fixture(n=620):
    rows=[]
    for i in range(n):
        if i<260: close=Decimal("100")
        elif i<470: close=Decimal("100")+Decimal(i-259)*Decimal("0.9")
        else: close=Decimal("289")-Decimal(i-470)*Decimal("0.1")
        rows.append(row(i,close))
    return rows

def test_ema200_warmup_and_recursive_value():
    vals=[Decimal("10"),Decimal("12"),Decimal("9"),Decimal("15")]
    result=ema_series(vals,3)
    alpha=Decimal(2)/Decimal(4)
    assert result[0] is None and result[1] is None
    expected=alpha*Decimal("9")+(Decimal(1)-alpha)*Decimal("11")
    assert result[2]==expected
    assert result[3]==alpha*Decimal("15")+(Decimal(1)-alpha)*expected

def test_12_minus_1_momentum_uses_t_minus_30_not_current_close():
    vals=[Decimal(100+i) for i in range(500)]
    i=400
    expected=vals[i-30]/vals[i-365]-1
    assert momentum_12_1(vals,i)==expected
    changed=vals[:]
    for k in range(i-29,i+1):
        changed[k]=Decimal("999999")
    assert momentum_12_1(changed,i)==expected
    assert momentum_12_1(vals,364) is None

def test_signals_are_month_end_only_and_use_ema200_regime():
    rows=rising_fixture()
    entries,exits,weights,atr,ema,mom,desired,monthly=monthly_tsmom_signals_and_weights(rows)
    assert sum(monthly)>0
    assert any(entries)
    assert all(not signal or monthly[i] for i,signal in enumerate(entries))
    assert all(not signal or monthly[i] for i,signal in enumerate(exits))
    for i,signal in enumerate(entries):
        if signal:
            assert mom[i] is not None and mom[i]>0
            assert ema[i] is not None and rows[i].payload.close>ema[i]
            assert weights[i] is not None and Decimal(0)<weights[i]<=Decimal("0.95")
    assert not any(entries[:365])

def test_features_are_causal_at_each_index():
    rows=rising_fixture()
    base=monthly_tsmom_signals_and_weights(rows)
    cut=430
    altered=rows[:]
    for i in range(cut+1,len(altered)):
        altered[i]=row(i,"1")
    other=monthly_tsmom_signals_and_weights(altered)
    for component in range(len(base)):
        assert base[component][cut]==other[component][cut]

def test_shared_ledger_next_open_execution_and_terminal_liquidation():
    # Use real MarketData/Candle DTOs accepted by the shared strict replay engine.
    rows=[row(i,100+i,opening=100+i) for i in range(500)]
    rules={"instrument_id":"binance:SPOT:BTCUSDT","step":Decimal("0.0001"),
           "min_qty":Decimal("0.0001"),"min_notional":Decimal("5"),
           "snapshot":{},"sha256":"unit-test"}
    entries=[False]*len(rows); exits=[False]*len(rows)
    weights=[None]*len(rows); atr=[Decimal("1")]*len(rows); ema=[None]*len(rows)
    entries[450]=True; exits[455]=True; weights[450]=Decimal("0.2")
    original=engine.CANDIDATE
    try:
        engine.CANDIDATE="UNIT_TSMOM"
        result,fills,curve=engine._simulate(
            rows,rules,entries,exits,weights,atr,ema,"UNIT",
            rows[449].payload.start,rows[458].payload.start,
            Decimal("60"),"STRESS_2",Decimal("0.002"),Decimal("50"),Decimal("20")
        )
        buys=[f for f in fills if f["side"]=="BUY"]
        sells=[f for f in fills if f["side"]=="SELL"]
        assert len(buys)==1 and buys[0]["timestamp_utc"]==rows[451].payload.start.isoformat()
        assert len(sells)==1 and sells[0]["timestamp_utc"]==rows[456].payload.start.isoformat()
        assert result["total_fees_usdt"]>0
        assert result["final_quote_balance"] if "final_quote_balance" in result else result["final_equity_usdt"]
    finally:
        engine.CANDIDATE=original

def test_month_end_helper_uses_calendar_date_without_future_price():
    for index in range(31):
        start=datetime(2024,1,1,tzinfo=UTC)+timedelta(days=index)
        expected=start.day==monthrange(start.year,start.month)[1]
        assert is_utc_month_end(start)==expected
