from datetime import date, timedelta
from decimal import Decimal
from research import btc_volume_shock_reversal_v1 as core
from research import btc_volatility_squeeze_breakout_v1 as s

def candle(day,op="100",hi="105",lo="95",close="100"):
    ms=core.to_ms(day)
    return {"open_ms":ms,"timestamp":core.stamp(ms),"close_ms":ms+core.DAY_MS-1,
      "open":Decimal(op),"high":Decimal(hi),"low":Decimal(lo),"close":Decimal(close),"volume":Decimal("10")}

def filters():
    return {"min_qty":Decimal("0.01"),"step":Decimal("0.01"),"min_notional":Decimal("5")}

def fixture():
    start=date(2020,1,1); rows=[]
    for i in range(181):
        day=start+timedelta(days=i)
        if i<140: row=candle(day,hi="105",lo="95")
        else: row=candle(day,hi="100.5",lo="99.5")
        rows.append(row)
    rows[180]=candle(start+timedelta(days=180),op="100",hi="107",lo="99.5",close="106")
    return rows

def test_true_range_and_atr14_pct_match_manual_values():
    rows=fixture()
    assert s.true_range_at(rows,20)==Decimal("10")
    assert s.atr_pct_at(rows,30)==Decimal("0.1")
    assert s.atr_pct_at(rows,179)==Decimal("0.01")

def test_squeeze_breakout_signal_requires_compression_and_prior_high_break():
    rows=fixture()
    assert s.squeeze_entry_signal(rows,180)
    rows[180]["close"]=Decimal("100.4")
    assert not s.squeeze_entry_signal(rows,180)

def test_signal_uses_only_data_available_by_current_close():
    rows=fixture()
    baseline=s.squeeze_entry_signal(rows,180)
    rows.extend([candle(date(2020,7,1)+timedelta(days=i),hi="999999",lo="1",close="900000") for i in range(5)])
    assert s.squeeze_entry_signal(rows,180)==baseline

def test_squeeze_exit_is_next_open_and_ledger_accounts_for_fees():
    rows=fixture()
    start=date(2020,1,1)
    rows.append(candle(start+timedelta(days=181),op="106.5",hi="113",lo="106",close="112"))
    rows.append(candle(start+timedelta(days=182),op="111",hi="112",lo="110",close="111"))
    rows.append(candle(start+timedelta(days=183),op="111",hi="112",lo="110",close="111"))
    old=(core.entry_signal,core.TP,core.SL,core.MAX_HOLD)
    try:
        core.entry_signal=s.squeeze_entry_signal; core.TP=s.TAKE_PROFIT; core.SL=s.STOP_LOSS; core.MAX_HOLD=s.MAX_HOLD
        result=core.simulate(rows,0,len(rows),Decimal("60"),"BASE",filters(),"UNIT","VOLUME_SHOCK_REVERSAL")
    finally:
        core.entry_signal,core.TP,core.SL,core.MAX_HOLD=old
    buys=[f for f in result["fills"] if f["side"]=="BUY"]
    sells=[f for f in result["fills"] if f["side"]=="SELL"]
    assert len(buys)==1 and len(sells)==1
    assert buys[0]["execution_utc"]==rows[181]["timestamp"]
    assert sells[0]["execution_utc"]==rows[182]["timestamp"]
    assert sells[0]["execution_type"]=="NEXT_OPEN"
    assert result["final"]==Decimal(sells[0]["cash_after"])
    assert result["fees"]>0 and result["friction"]>0

def test_quantity_is_rounded_and_cash_spend_includes_fee():
    qty,px,fee,reject=core.buy_size(Decimal("60"),Decimal("100"),Decimal("0.2"),core.COSTS["STRESS_2"],filters())
    assert reject is None and qty%filters()["step"]==0
    assert qty*px+fee<=Decimal("12")

def test_shared_replay_engine_terminal_liquidation_and_current_exchange_filters():
    rows=fixture()[:181]
    rows[180]=candle(date(2020,6,29),op="100",hi="107",lo="99.5",close="106")
    rows.append(candle(date(2020,6,30),op="106.5",hi="107",lo="106",close="106.5"))
    old=(core.entry_signal,core.TP,core.SL,core.MAX_HOLD)
    try:
        core.entry_signal=s.squeeze_entry_signal; core.TP=s.TAKE_PROFIT; core.SL=s.STOP_LOSS; core.MAX_HOLD=s.MAX_HOLD
        result=core.simulate(rows,0,len(rows),Decimal("60"),"STRESS_2",filters(),"TERM","VOLUME_SHOCK_REVERSAL")
    finally:
        core.entry_signal,core.TP,core.SL,core.MAX_HOLD=old
    sells=[f for f in result["fills"] if f["side"]=="SELL"]
    assert len(sells)==1 and sells[0]["execution_type"]=="TERMINAL_CLOSE"
    exchange={"symbols":[{"symbol":"BTCUSDT","filters":[
      {"filterType":"LOT_SIZE","minQty":"0.00001","stepSize":"0.00001"},
      {"filterType":"MIN_NOTIONAL","minNotional":"5"}]}]}
    assert core.filters_from_exchange_info(exchange)["min_notional"]==Decimal("5")
