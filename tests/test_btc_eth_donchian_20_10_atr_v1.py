from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
from research.replay_btc_eth_donchian_20_10_atr_v1 import (
    _atr_target_weights, _donchian_levels, ATR_TARGET_ANNUAL, MAX_POSITION_FRACTION, ASSETS, DONCHIAN_ENTRY_BARS, DONCHIAN_EXIT_BARS,
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


def test_donchian_channels_exclude_current_candle_high_and_low():
    items=dataset(60,"2")
    base_levels,base_atr= _donchian_levels(items)
    i=35
    for asset in ASSETS:
        rows=items[asset]
        expected_entry=max(x.payload.high for x in rows[i-DONCHIAN_ENTRY_BARS:i])
        expected_exit=min(x.payload.low for x in rows[i-DONCHIAN_EXIT_BARS:i])
        assert base_levels[asset]["entry_high"][i]==expected_entry
        assert base_levels[asset]["exit_low"][i]==expected_exit
        changed=deepcopy(items)
        p=changed[asset][i].payload
        p.high=p.high+Decimal("100000")
        p.low=p.low-Decimal("100000")
        newer,_=_donchian_levels(changed)
        assert newer[asset]["entry_high"][i]==base_levels[asset]["entry_high"][i]
        assert newer[asset]["exit_low"][i]==base_levels[asset]["exit_low"][i]

def test_donchian_signal_uses_previous_bars_only_and_requires_ema200():
    from research.replay_btc_eth_donchian_20_10_atr_v1 import _target_breakout
    items=dataset(60,"2")
    levels,atrs=_donchian_levels(items)
    closes={a:[x.payload.close for x in items[a]] for a in ASSETS}
    # A missing EMA means no trade; no synthetic defaults are permitted.
    emas={a:[None]*len(items[a]) for a in ASSETS}
    target,reason=_target_breakout(35,closes,emas,levels,atrs)
    assert target is None
    assert "neither BTC nor ETH" in reason
