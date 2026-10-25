from datetime import date, timedelta
from decimal import Decimal
from research import btc_volume_shock_reversal_v1 as r

def candle(day, op="100", hi="101", lo="99", close="100", volume="10"):
    ms=r.to_ms(day)
    return {"open_ms":ms,"timestamp":r.stamp(ms),"close_ms":ms+r.DAY_MS-1,
            "open":Decimal(op),"high":Decimal(hi),"low":Decimal(lo),
            "close":Decimal(close),"volume":Decimal(volume)}

def filters():
    return {"min_qty":Decimal("0.01"),"step":Decimal("0.01"),"min_notional":Decimal("5")}

def test_exact_timestamp_continuity_passes_and_missing_candle_fails_closed():
    start=date(2020,1,1)
    rows=[candle(start+timedelta(days=i)) for i in range(3)]
    assert r.validate_candles(rows,start,start+timedelta(days=3))["ok"]
    result=r.validate_candles(rows[:2],start,start+timedelta(days=3))
    assert not result["ok"]
    assert result["missing_utc"]==[r.stamp(r.to_ms(start+timedelta(days=2)))]

def test_invalid_close_time_or_ohlc_is_rejected():
    day=date(2020,1,1)
    row=candle(day)
    row["close_ms"]-=1
    assert not r.validate_candles([row],day,day+timedelta(days=1))["ok"]

def test_entry_signal_uses_only_prior_20_volumes_and_is_suffix_invariant():
    start=date(2020,1,1)
    rows=[candle(start+timedelta(days=i),volume="10") for i in range(20)]
    rows.append(candle(start+timedelta(days=20),op="100",hi="101",lo="96",close="97",volume="15"))
    assert r.entry_signal(rows,20)
    extended=rows+[candle(start+timedelta(days=21),op="1",hi="999999",lo="1",close="900000",volume="999999999")]
    assert r.entry_signal(rows,20)==r.entry_signal(extended,20)

def test_price_shock_and_volume_gate_are_both_required():
    start=date(2020,1,1)
    rows=[candle(start+timedelta(days=i),volume="10") for i in range(20)]
    rows.append(candle(start+timedelta(days=20),op="100",hi="101",lo="98",close="99",volume="15"))
    assert not r.entry_signal(rows,20)
    rows[20]["close"]=Decimal("97")
    assert r.entry_signal(rows,20)
    rows[20]["volume"]=Decimal("14.9")
    assert not r.entry_signal(rows,20)

def test_fill_cost_and_decimal_quantity_rounding_do_not_overspend():
    cost=r.COSTS["BASE"]
    qty,px,fee,reason=r.buy_size(Decimal("60"),Decimal("100"),Decimal("0.20"),cost,filters())
    assert reason is None
    assert qty==Decimal("0.11")
    assert qty%filters()["step"]==0
    assert qty*px+fee<=Decimal("12")
    assert px>Decimal("100") and fee>0
    assert r.adjusted_price(Decimal("100"),"SELL",cost)<Decimal("100")

def test_entry_and_exit_are_filled_at_next_open_with_costs():
    start=date(2020,1,1)
    rows=[candle(start+timedelta(days=i)) for i in range(27)]
    rows[20]=candle(start+timedelta(days=20),op="100",hi="101",lo="96",close="97",volume="20")
    rows[21]=candle(start+timedelta(days=21),op="98",hi="100",lo="97",close="100",volume="10")
    rows[22]=candle(start+timedelta(days=22),op="100",hi="102",lo="99",close="101",volume="10")
    rows[23]=candle(start+timedelta(days=23),op="101",hi="102",lo="100",close="101",volume="10")
    result=r.simulate(rows,0,len(rows),Decimal("60"),"BASE",filters(),"UNIT","VOLUME_SHOCK_REVERSAL")
    buys=[f for f in result["fills"] if f["side"]=="BUY"]
    sells=[f for f in result["fills"] if f["side"]=="SELL"]
    assert len(buys)==1 and len(sells)==1
    assert sells[0]["execution_type"]=="NEXT_OPEN"
    assert sells[0]["execution_utc"]==rows[23]["timestamp"]
    assert result["final"]==Decimal(sells[-1]["cash_after"])
    assert result["trades"]==1 and result["fees"]>0 and result["friction"]>0

def test_terminal_liquidation_closes_position_without_exit_signal():
    start=date(2020,1,1)
    rows=[candle(start+timedelta(days=i)) for i in range(23)]
    rows[20]=candle(start+timedelta(days=20),op="100",hi="101",lo="96",close="97",volume="20")
    rows[21]=candle(start+timedelta(days=21),op="98",hi="99",lo="97",close="98",volume="10")
    rows[22]=candle(start+timedelta(days=22),op="98",hi="99",lo="97",close="98",volume="10")
    result=r.simulate(rows,0,len(rows),Decimal("60"),"STRESS_2",filters(),"TERM","VOLUME_SHOCK_REVERSAL")
    sells=[f for f in result["fills"] if f["side"]=="SELL"]
    assert len(sells)==1 and sells[0]["execution_type"]=="TERMINAL_CLOSE"
    assert sells[0]["reason"]=="TERMINAL_LIQUIDATION"

def test_exchange_filter_parser():
    payload={"symbols":[{"symbol":"BTCUSDT","filters":[
      {"filterType":"LOT_SIZE","minQty":"0.00001","stepSize":"0.00001"},
      {"filterType":"MIN_NOTIONAL","minNotional":"5"}]}]}
    assert r.filters_from_exchange_info(payload)=={"min_qty":Decimal("0.00001"),"step":Decimal("0.00001"),"min_notional":Decimal("5")}
