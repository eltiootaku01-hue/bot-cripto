from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
import research.replay_btc_eth_rsi14_trend_atr_v1 as study
from research.replay_btc_eth_rsi14_trend_atr_v1 import (
    _atr_target_weights, ATR_TARGET_ANNUAL, MAX_POSITION_FRACTION, ASSETS,
)
UTC=timezone.utc

def bar(i,close,high,low):
    start=datetime(2024,1,1,tzinfo=UTC)+timedelta(hours=4*i)
    return SimpleNamespace(payload=SimpleNamespace(start=start,close=Decimal(str(close)),high=Decimal(str(high)),low=Decimal(str(low))))

def dataset(n=120,volatility="2"):
    rows={}
    for asset,mult in (("BTCUSDT",Decimal("1")),("ETHUSDT",Decimal("1.3"))):
        values=[]
        for i in range(n):
            close=Decimal("1000")+Decimal(i)*Decimal("0.2")*mult
            width=Decimal(str(volatility))*mult
            values.append(bar(i,close,close+width,close-width))
        rows[asset]=values
    return rows

def test_atr_weight_formula_cap_and_warmup():
    items=dataset(80,"2"); weights=_atr_target_weights(items); annualization=Decimal(365*6).sqrt()
    for asset in ASSETS:
        vector=weights[asset]
        assert len(vector)==80
        assert all(x is None for x in vector[:13])
        assert all(x is not None and Decimal(0)<x<=MAX_POSITION_FRACTION for x in vector[13:])
        i=30; rows=items[asset]; tr=[]
        for j,row in enumerate(rows):
            p=row.payload
            if j==0: tr.append(p.high-p.low)
            else:
                prev=rows[j-1].payload.close
                tr.append(max(p.high-p.low,abs(p.high-prev),abs(p.low-prev)))
        atr=sum(tr[i-13:i+1],Decimal(0))/Decimal(14)
        expected=min(MAX_POSITION_FRACTION,ATR_TARGET_ANNUAL/((atr/rows[i].payload.close)*annualization))
        assert vector[i]==expected

def test_weights_are_causal_and_future_bars_do_not_rewrite_past():
    items=dataset(100,"3"); base=_atr_target_weights(items); cut=60; changed=deepcopy(items)
    for asset in ASSETS:
        for i in range(cut+1,len(changed[asset])):
            changed[asset][i]=bar(i,Decimal("100000")+i,Decimal("100100")+i,Decimal("99900")+i)
    other=_atr_target_weights(changed)
    for asset in ASSETS:
        assert base[asset][:cut+1]==other[asset][:cut+1]

def test_higher_true_range_does_not_increase_weight():
    quiet=dataset(80,"1"); volatile=dataset(80,"5")
    a=_atr_target_weights(quiet); b=_atr_target_weights(volatile)
    for asset in ASSETS:
        for i in range(13,80):
            assert b[asset][i]<=a[asset][i]


def test_wilder_rsi_warmup_and_flat_market_contract():
    flat=[Decimal("100") for _ in range(24)]
    result=study._rsi_wilder(flat,14)
    assert all(x is None for x in result[:14])
    assert all(x==Decimal("50") for x in result[14:])
    rising=[Decimal("100")+Decimal(i) for i in range(24)]
    up=study._rsi_wilder(rising,14)
    assert all(x is None for x in up[:14])
    assert all(x==Decimal("100") for x in up[14:])

def test_wilder_rsi_does_not_change_when_future_suffix_changes():
    closes=[Decimal("100")+Decimal(i%9)-Decimal(i%4)*Decimal("0.7") for i in range(100)]
    original=study._rsi_wilder(closes,14)
    cut=67
    changed=closes[:cut]+[Decimal("2000")+Decimal(i)*Decimal("50") for i in range(len(closes)-cut)]
    updated=study._rsi_wilder(changed,14)
    assert original[:cut]==updated[:cut]

def test_rsi_target_entry_cross_and_exit_hysteresis():
    n=60
    closes={"BTCUSDT":[Decimal("100") for _ in range(n)],"ETHUSDT":[Decimal("90") for _ in range(n)]}
    closes["BTCUSDT"][50]=Decimal("150")
    emas={asset:[Decimal("120") for _ in range(n)] for asset in ASSETS}
    rsi={asset:[Decimal("45") for _ in range(n)] for asset in ASSETS}
    for asset in ASSETS:
        rsi[asset][44]=Decimal("49")
        rsi[asset][50]=Decimal("51") if asset=="BTCUSDT" else Decimal("49")
    target,why=study._target(50,closes,emas,rsi,None)
    assert target=="BTCUSDT"
    assert "cross above 50" in why
    # A held position remains when RSI is in the hysteresis band (40,50].
    rsi["BTCUSDT"][50]=Decimal("45")
    target,why=study._target(50,closes,emas,rsi,"BTCUSDT")
    assert target=="BTCUSDT"
    # RSI <=40 closes; a stale RSI value does not by itself re-enter.
    rsi["BTCUSDT"][50]=Decimal("40")
    target,why=study._target(50,closes,emas,rsi,"BTCUSDT")
    assert target is None
