from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
import research.replay_btc_eth_sma50_sma200_atr_v1 as study
from research.replay_btc_eth_sma50_sma200_atr_v1 import (
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


def test_sma_regime_warmup_and_causal_prefix():
    rows=dataset(300,"2")
    states=study._sma_trend_states(rows)
    for asset in ASSETS:
        assert all(x is None for x in states[asset]["regime"][:199])
        assert all(x in (None,True,False) for x in states[asset]["regime"])
        assert any(x is True for x in states[asset]["regime"][200:])

def test_sma_state_does_not_rewrite_when_future_bars_change():
    rows=dataset(320,"3")
    original=study._sma_trend_states(rows)
    changed=deepcopy(rows)
    cut=250
    for asset in ASSETS:
        for i in range(cut,len(changed[asset])):
            price=Decimal("50000")+Decimal(i-cut)*Decimal("700")
            changed[asset][i]=bar(i,price,price+Decimal("300"),price-Decimal("250"))
    updated=study._sma_trend_states(changed)
    for asset in ASSETS:
        assert original[asset]["regime"][:cut]==updated[asset]["regime"][:cut]
        assert original[asset]["sma50"][:cut]==updated[asset]["sma50"][:cut]
        assert original[asset]["sma200"][:cut]==updated[asset]["sma200"][:cut]
