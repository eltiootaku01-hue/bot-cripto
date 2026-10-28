from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
import math
from research.replay_btc_eth_vwap24_reclaim_atr15 import (
    _atr_target_weights, _atr_series, _canonical_ohlcv_sha256,
    _vwap_indicators, _target_vwap_reclaim, _exit_reason_vwap,
    ATR_TARGET_ANNUAL, MAX_POSITION_FRACTION, ASSETS, VWAP_PERIOD, MAX_HOLD_BARS,
)

UTC=timezone.utc

def bar(i, close, high=None, low=None, volume="10"):
    start=datetime(2024,1,1,tzinfo=UTC)+timedelta(hours=4*i)
    c=Decimal(str(close))
    hi=Decimal(str(high if high is not None else c*Decimal("1.01")))
    lo=Decimal(str(low if low is not None else c*Decimal("0.99")))
    return SimpleNamespace(payload=SimpleNamespace(
        start=start,end=start+timedelta(hours=4)-timedelta(milliseconds=1),
        open=c,close=c,high=hi,low=lo,volume=Decimal(str(volume))))

def synthetic_items(n=240, volatility="1.5", phase=0.0):
    data={}
    for asset,base,drift,amp in (
        ("BTCUSDT",Decimal("1000"),0.0005,0.022),
        ("ETHUSDT",Decimal("500"),0.0004,0.029),
    ):
        out=[]
        width=float(volatility)
        for i in range(n):
            close=float(base)*math.exp(drift*i+amp*math.sin(i/23+phase)+0.012*math.sin(i/4.7+phase))
            c=Decimal(str(close))
            w=c*Decimal(str(width))/Decimal("100")
            volume=Decimal("10")+Decimal(i%11)
            out.append(bar(i,c,c+w,c-w,volume))
        data[asset]=out
    return data

def test_vwap24_matches_volume_weighted_typical_price_formula():
    data=synthetic_items(n=80)
    result=_vwap_indicators(data)
    for asset in ASSETS:
        assert all(v is None for v in result[asset][:VWAP_PERIOD-1])
        i=30
        window=data[asset][i-VWAP_PERIOD+1:i+1]
        numerator=sum((((x.payload.high+x.payload.low+x.payload.close)/Decimal("3"))*x.payload.volume for x in window),Decimal("0"))
        denominator=sum((x.payload.volume for x in window),Decimal("0"))
        assert result[asset][i]==numerator/denominator

def test_vwap24_is_causal_to_future_suffix():
    data=synthetic_items(n=180,phase=0.2)
    full=_vwap_indicators(data)
    cut=120
    prefix={asset:list(rows[:cut+1]) for asset,rows in data.items()}
    partial=_vwap_indicators(prefix)
    for asset in ASSETS:
        assert full[asset][:cut+1]==partial[asset]

def test_vwap_reclaim_requires_cross_bull_regime_and_positive_momentum():
    closes={
        "BTCUSDT":[Decimal("95")]*23+[Decimal("101"),Decimal("103")],
        "ETHUSDT":[Decimal("95")]*23+[Decimal("101"),Decimal("104")],
    }
    emas={"BTCUSDT":[Decimal("90")]*25,"ETHUSDT":[Decimal("90")]*25}
    ema50={"BTCUSDT":[Decimal("99")]*25,"ETHUSDT":[Decimal("99")]*25}
    vwaps={"BTCUSDT":[Decimal("100")]*23+[Decimal("101"),Decimal("102")],
           "ETHUSDT":[Decimal("100")]*23+[Decimal("101"),Decimal("102")]}
    atrs={"BTCUSDT":[Decimal("2")]*25,"ETHUSDT":[Decimal("2")]*25}
    target,_=_target_vwap_reclaim(24,closes,emas,ema50,vwaps,atrs)
    assert target=="ETHUSDT"  # strongest 24-bar relative return among valid crosses
    ema50["ETHUSDT"][24]=Decimal("85")  # EMA50 must remain above EMA200
    target,_=_target_vwap_reclaim(24,closes,emas,ema50,vwaps,atrs)
    assert target=="BTCUSDT"
    closes["BTCUSDT"][23]=Decimal("102")  # previous close no longer at/below prior VWAP
    target,_=_target_vwap_reclaim(24,closes,emas,ema50,vwaps,atrs)
    assert target is None

def test_vwap_reclaim_does_not_signal_without_positive_24_bar_return():
    closes={"BTCUSDT":[Decimal("105")]*23+[Decimal("101"),Decimal("103")],
            "ETHUSDT":[Decimal("105")]*23+[Decimal("101"),Decimal("103")]}
    emas={a:[Decimal("90")]*25 for a in ASSETS}
    ema50={a:[Decimal("95")]*25 for a in ASSETS}
    vwaps={a:[Decimal("100")]*23+[Decimal("101"),Decimal("102")] for a in ASSETS}
    atrs={a:[Decimal("2")]*25 for a in ASSETS}
    target,_=_target_vwap_reclaim(24,closes,emas,ema50,vwaps,atrs)
    assert target is None

def test_exit_rules_prioritize_stop_then_timeout_vwap_and_ema50():
    assert _exit_reason_vwap(Decimal("90"),Decimal("100"),Decimal("98"),Decimal("95"),2)=="STOP_2ATR_CLOSE_NEXT_OPEN"
    assert _exit_reason_vwap(Decimal("99"),Decimal("100"),Decimal("98"),Decimal("95"),MAX_HOLD_BARS)=="TIME_EXIT_18_BARS_NEXT_OPEN"
    assert _exit_reason_vwap(Decimal("99"),Decimal("100"),Decimal("98"),Decimal("95"),4)=="VWAP24_LOSS_NEXT_OPEN"
    assert _exit_reason_vwap(Decimal("99"),Decimal("90"),Decimal("100"),Decimal("95"),4)=="EMA50_LOSS_NEXT_OPEN"
    assert _exit_reason_vwap(Decimal("101"),Decimal("100"),Decimal("98"),Decimal("95"),4) is None

def test_atr_target_sizing_matches_frozen_formula_and_cap():
    data=synthetic_items(n=100)
    weights=_atr_target_weights(data)
    annualization=Decimal(365*6).sqrt()
    for asset in ASSETS:
        vector=weights[asset]
        assert all(x is None for x in vector[:13])
        assert all(x is not None and Decimal("0")<x<=MAX_POSITION_FRACTION for x in vector[13:])
        i=40; rows=data[asset]; true_range=[]
        for j,row in enumerate(rows):
            p=row.payload
            if j==0:
                true_range.append(p.high-p.low)
            else:
                prev=rows[j-1].payload.close
                true_range.append(max(p.high-p.low,abs(p.high-prev),abs(p.low-prev)))
        atr=sum(true_range[i-13:i+1],Decimal("0"))/Decimal(14)
        expected=min(MAX_POSITION_FRACTION,ATR_TARGET_ANNUAL/((atr/rows[i].payload.close)*annualization))
        assert vector[i]==expected

def test_canonical_ohlcv_hash_is_repeatable_and_changes_on_modified_candle():
    rows=synthetic_items(n=60)["BTCUSDT"]
    base=_canonical_ohlcv_sha256(rows)
    assert base==_canonical_ohlcv_sha256(deepcopy(rows))
    changed=deepcopy(rows)
    p=changed[12].payload
    edited=SimpleNamespace(start=p.start,end=p.end,open=p.open,close=p.close+Decimal("0.01"),
        high=p.high+Decimal("0.01"),low=p.low,volume=p.volume)
    changed[12]=SimpleNamespace(payload=edited)
    assert base!=_canonical_ohlcv_sha256(changed)
