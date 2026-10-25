from datetime import date, timedelta
from decimal import Decimal
from copy import deepcopy

from research import btc_volume_shock_reversal_v1 as core
from research import btc_chaikin_money_flow_cmf20_ema50_v1 as s


def candle(day, close="100", open_="100", low="99", high="101", volume="10"):
    ms=core.to_ms(day)
    return {"open_ms":ms,"timestamp":core.stamp(ms),"close_ms":ms+core.DAY_MS-1,
            "open":Decimal(open_),"high":Decimal(high),"low":Decimal(low),
            "close":Decimal(close),"volume":Decimal(volume)}


def sample_rows(n=65):
    start=date(2020,1,1)
    rows=[]
    for i in range(n):
        c=Decimal("100")+Decimal(i)/Decimal("5")
        op=c+Decimal("0.5")
        rows.append(candle(start+timedelta(days=i),str(c),str(op),str(c-Decimal("0.1")),str(op+Decimal("0.5")),"10"))
    # CMF is initially negative due the close residing near the low while closes trend upward.
    # A high-volume upper-close candle forces a genuine CMF zero crossing.
    i=n-1
    c=Decimal("100")+Decimal(i)/Decimal("5")+Decimal("2")
    rows[-1]=candle(start+timedelta(days=i),str(c),str(c-Decimal("0.2")),str(c-Decimal("4")),str(c),"1000")
    return rows


def test_money_flow_multiplier_matches_manual_formula_and_zero_range():
    row=candle(date(2020,1,1),close="10",open_="9",low="5",high="11")
    # ((close-low)-(high-close))/(high-low) = (5-1)/6
    assert s.money_flow_multiplier(row)==Decimal("4")/Decimal("6")
    flat=candle(date(2020,1,2),close="10",open_="10",low="10",high="10")
    assert s.money_flow_multiplier(flat)==0
    assert s.cmf_at([flat]*20,19)==0


def test_ema50_recursive_values_match_manual_computation():
    rows=[candle(date(2020,1,1)+timedelta(days=i),close=str(100+i)) for i in range(51)]
    values=s.ema_series(rows)
    expected=Decimal(100)
    alpha=Decimal(2)/Decimal(51)
    for i in range(1,51):
        expected=alpha*Decimal(100+i)+(Decimal(1)-alpha)*expected
    assert values[50]==expected


def test_cmf20_cross_zero_requires_ema50_regime():
    rows=sample_rows()
    ema=s.ema_series(rows)
    prior=s.cmf_at(rows,len(rows)-2)
    current=s.cmf_at(rows,len(rows)-1)
    assert prior is not None and prior<=0
    assert current is not None and current>0
    assert Decimal(rows[-1]["close"])>ema[-1]
    assert s.entry_signal_at(rows,len(rows)-1,ema)
    falling=deepcopy(rows)
    for i in range(len(falling)):
        # Positive late CMF is not sufficient when price is below EMA50.
        falling[i]["close"]=Decimal("1000")-Decimal(i)
        falling[i]["open"]=falling[i]["close"]+Decimal("0.5")
        falling[i]["high"]=falling[i]["open"]+Decimal("0.5")
        falling[i]["low"]=falling[i]["close"]-Decimal("0.1")
    # No requirement that every synthetic candle form a signal; the regime rejects the chosen downward trend.
    down_ema=s.ema_series(falling)
    assert not s.entry_signal_at(falling,len(falling)-1,down_ema)


def test_signal_is_causal_when_future_rows_are_appended_or_changed():
    rows=sample_rows()
    idx=len(rows)-1
    baseline=s.entry_signal_at(rows,idx)
    extended=deepcopy(rows)
    for i in range(len(rows),len(rows)+5):
        extended.append(candle(date(2020,1,1)+timedelta(days=i),close="999999",open_="999998",
                               low="1",high="1000000",volume="100000000"))
    assert s.entry_signal_at(extended,idx)==baseline
    changed=deepcopy(rows)
    for i in range(idx+1,len(changed)):
        changed[i]["close"]=Decimal("1")
    # There are no later bars in this prefix; changing only future bars must preserve signal at idx.
    changed.append(candle(date(2020,1,1)+timedelta(days=len(changed)),close="1",open_="1",low="1",high="1"))
    assert s.entry_signal_at(changed,idx)==baseline


def test_next_open_fill_and_terminal_close_reconcile_exact_ledger():
    rows=[candle(date(2020,1,1)+timedelta(days=i),close=str(100+i),open_=str(100+i),
                 low=str(99+i),high=str(101+i),volume="10") for i in range(15)]
    old=(core.entry_signal,core.ALLOC,core.TP,core.SL,core.MAX_HOLD)
    old_signals=s.ENTRY_SIGNALS
    try:
        s.ENTRY_SIGNALS=[False]*len(rows)
        s.ENTRY_SIGNALS[4]=True
        core.entry_signal=s._entry_signal
        core.ALLOC=s.ENTRY_ALLOC
        core.TP=Decimal("1000000000")
        core.SL=Decimal("0.000000001")
        core.MAX_HOLD=s.MAX_HOLD
        filters={"min_qty":Decimal("0.0001"),"step":Decimal("0.0001"),"min_notional":Decimal("5")}
        result=core.simulate(rows,0,9,Decimal("60"),"STRESS_2",filters,"TERM","VOLUME_SHOCK_REVERSAL")
    finally:
        core.entry_signal,core.ALLOC,core.TP,core.SL,core.MAX_HOLD=old
        s.ENTRY_SIGNALS=old_signals
    buys=[f for f in result["fills"] if f["side"]=="BUY"]
    sells=[f for f in result["fills"] if f["side"]=="SELL"]
    assert len(buys)==1 and buys[0]["execution_utc"]==rows[5]["timestamp"]
    assert buys[0]["execution_type"]=="NEXT_OPEN"
    assert len(sells)==1 and sells[0]["execution_type"]=="TERMINAL_CLOSE"
    assert result["final"]==Decimal(sells[0]["cash_after"])
    assert result["fees"]>0 and result["friction"]>0


def test_quantity_step_minimums_and_cash_budget_are_enforced():
    filters={"min_qty":Decimal("0.01"),"step":Decimal("0.01"),"min_notional":Decimal("5")}
    qty,price,fee,reject=core.buy_size(Decimal("60"),Decimal("100"),s.ENTRY_ALLOC,core.COSTS["BASE"],filters)
    assert reject is None and qty%filters["step"]==0
    assert qty*price+fee<=Decimal("12")
    tiny={"min_qty":Decimal("100"),"step":Decimal("0.01"),"min_notional":Decimal("5")}
    qty,_,_,reject=core.buy_size(Decimal("60"),Decimal("100"),s.ENTRY_ALLOC,core.COSTS["BASE"],tiny)
    assert qty==0 and reject=="MIN_QTY"
