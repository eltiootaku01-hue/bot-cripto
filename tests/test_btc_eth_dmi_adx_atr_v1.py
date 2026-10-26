from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
import research.replay_btc_eth_dmi_adx_atr_v1 as study
from research.replay_btc_eth_dmi_adx_atr_v1 import (
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


def test_dmi_wilder_warmup_and_uptrend_direction():
    rows=dataset(100,"2")["BTCUSDT"]
    closes=[r.payload.close for r in rows]
    highs=[r.payload.high for r in rows]
    lows=[r.payload.low for r in rows]
    state=study._dmi_adx(highs,lows,closes,14)
    assert all(x is None for x in state["plus_di"][:14])
    assert all(x is None for x in state["minus_di"][:14])
    assert all(x is None for x in state["adx"][:27])
    assert state["plus_di"][30] is not None
    assert state["minus_di"][30] is not None
    assert state["adx"][30] is not None
    assert state["plus_di"][30] > state["minus_di"][30]
    assert Decimal(0) <= state["adx"][30] <= Decimal(100)

def test_dmi_indicators_are_invariant_to_future_suffix_perturbation():
    rows=dataset(140,"3")["BTCUSDT"]
    highs=[r.payload.high for r in rows]; lows=[r.payload.low for r in rows]
    closes=[r.payload.close for r in rows]
    original=study._dmi_adx(highs,lows,closes,14)
    cut=95
    changed=closes[:cut]+[Decimal("50000")+Decimal(i)*Decimal("700") for i in range(len(closes)-cut)]
    changed_high=[x+Decimal("300") for x in changed]
    changed_low=[x-Decimal("250") for x in changed]
    updated=study._dmi_adx(changed_high,changed_low,changed,14)
    for key in ("plus_di","minus_di","adx"):
        assert original[key][:cut]==updated[key][:cut]

def test_dmi_target_requires_cross_adx_and_ema_then_holds_with_hysteresis():
    n=60; closes={a:[Decimal("100") for _ in range(n)] for a in ASSETS}
    closes["BTCUSDT"][50]=Decimal("150")
    emas={a:[Decimal("120") for _ in range(n)] for a in ASSETS}
    states={}
    for asset in ASSETS:
        plus=[Decimal("10") for _ in range(n)]
        minus=[Decimal("12") for _ in range(n)]
        adx=[Decimal("25") for _ in range(n)]
        plus[44]=Decimal("13"); minus[44]=Decimal("14")
        if asset=="BTCUSDT":
            plus[50]=Decimal("18"); minus[50]=Decimal("11")
        else:
            plus[50]=Decimal("13"); minus[50]=Decimal("14")
        states[asset]={"plus_di":plus,"minus_di":minus,"adx":adx}
    target,reason=study._target(50,closes,emas,states,None)
    assert target=="BTCUSDT"
    states["BTCUSDT"]["adx"][50]=Decimal("16")
    target,reason=study._target(50,closes,emas,states,"BTCUSDT")
    assert target=="BTCUSDT"
    states["BTCUSDT"]["adx"][50]=Decimal("14")
    target,reason=study._target(50,closes,emas,states,"BTCUSDT")
    assert target is None
