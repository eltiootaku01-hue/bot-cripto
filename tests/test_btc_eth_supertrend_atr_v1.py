from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
from research.replay_btc_eth_supertrend_atr_v1 import (
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


def test_supertrend_has_expected_warmup_and_boolean_state_contract():
    rows=dataset(100,"2")
    states=study._supertrend_states(rows)
    for asset in ASSETS:
        vector=states[asset]
        assert len(vector)==100
        assert all(x is None for x in vector[:SUPERTREND_ATR_PERIOD-1])
        assert all(x in (None, True, False) for x in vector)
        assert any(x is True for x in vector[SUPERTREND_ATR_PERIOD:])

def test_supertrend_states_are_invariant_to_future_suffix():
    from copy import deepcopy
    rows=dataset(120,"3")
    original=study._supertrend_states(rows)
    changed=deepcopy(rows)
    cut=80
    for asset in ASSETS:
        for i in range(cut,len(changed[asset])):
            changed[asset][i]=bar(i,Decimal("100000")+i,Decimal("100100")+i,Decimal("99900")+i)
    modified=study._supertrend_states(changed)
    for asset in ASSETS:
        assert original[asset][:cut]==modified[asset][:cut]

def test_supertrend_state_does_not_depend_on_future_close_or_high():
    rows=dataset(120,"2")
    original=study._supertrend_states(rows)
    for asset in ASSETS:
        changed=deepcopy(rows)
        i=70
        changed[asset][i]=bar(i,Decimal("2000"),Decimal("2100"),Decimal("1900"))
        modified=study._supertrend_states(changed)
        assert original[asset][:i]==modified[asset][:i]
