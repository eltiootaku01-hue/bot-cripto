from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
from research.replay_btc_eth_macd_cross_atr_v1 import (
    _atr_target_weights, _atr_series, ATR_TARGET_ANNUAL, MAX_POSITION_FRACTION, ASSETS, MACD_FAST, MACD_SLOW, MACD_SIGNAL, _macd_indicators,
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


def test_macd_line_signal_and_crosses_are_causal():
    from research.replay_btc_eth_macd_cross_atr_v1 import _macd_indicators
    items=dataset(150,"2")
    closes={a:[x.payload.close for x in items[a]] for a in ASSETS}
    base=_macd_indicators(closes)
    cut=100
    changed={a:list(v) for a,v in closes.items()}
    for asset in ASSETS:
        for i in range(cut+1,len(changed[asset])):
            changed[asset][i]=Decimal("100000")+Decimal(i)
    altered=_macd_indicators(changed)
    for asset in ASSETS:
        for key in ("macd","signal","histogram","bullish_cross","bearish_cross"):
            assert base[asset][key][:cut+1]==altered[asset][key][:cut+1]

def test_macd_cross_flags_match_completed_bar_definitions():
    from research.replay_btc_eth_macd_cross_atr_v1 import _macd_indicators
    items=dataset(120,"2")
    closes={a:[x.payload.close for x in items[a]] for a in ASSETS}
    macd=_macd_indicators(closes)
    for asset in ASSETS:
        d=macd[asset]
        for i in range(1,len(closes[asset])):
            prior=(d["macd"][i-1],d["signal"][i-1])
            now=(d["macd"][i],d["signal"][i])
            if any(v is None for v in (*prior,*now)):
                assert not d["bullish_cross"][i]
                assert not d["bearish_cross"][i]
            else:
                assert d["bullish_cross"][i] == (now[0]>now[1] and prior[0]<=prior[1])
                assert d["bearish_cross"][i] == (now[0]<now[1] and prior[0]>=prior[1])

def test_entry_requires_ema200_confirmation_and_bullish_cross():
    from research.replay_btc_eth_macd_cross_atr_v1 import _target_macd
    items=dataset(100,"2")
    closes={a:[x.payload.close for x in items[a]] for a in ASSETS}
    atrs=_atr_series(items)
    macds=_macd_indicators(closes)
    no_emas={a:[None]*len(items[a]) for a in ASSETS}
    target,reason=_target_macd(80,closes,no_emas,macds,atrs)
    assert target is None
    assert "bullish MACD" in reason
