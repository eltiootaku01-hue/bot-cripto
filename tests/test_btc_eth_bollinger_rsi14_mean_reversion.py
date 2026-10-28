from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
import math
import math
from research.replay_btc_eth_bollinger_rsi14_mean_reversion import (
    _atr_target_weights, _atr_series, _bollinger_rsi_indicators,
    _target_mean_reversion, _exit_reason, _canonical_ohlcv_sha256,
    ATR_TARGET_ANNUAL, MAX_POSITION_FRACTION, ASSETS,
    RSI_ENTRY_MAX, RSI_EXIT_MIN, MAX_HOLD_BARS,
)
UTC=timezone.utc

def bar(i, close, high=None, low=None):
    start=datetime(2024,1,1,tzinfo=UTC)+timedelta(hours=4*i)
    c=Decimal(str(close))
    return SimpleNamespace(payload=SimpleNamespace(
        start=start, end=start+timedelta(hours=4)-timedelta(milliseconds=1),
        open=c, close=c,
        high=Decimal(str(high if high is not None else c*Decimal("1.01"))),
        low=Decimal(str(low if low is not None else c*Decimal("0.99"))),
        volume=Decimal("10.00000000"),
    ))

def synthetic_bars(n=400, volatility="1.5", phase=0.0):
    rows={}
    for asset,base,drift,amp in (
        ("BTCUSDT",Decimal("1000"),0.0005,0.022),
        ("ETHUSDT",Decimal("500"),0.0004,0.029),
    ):
        values=[]
        width=float(volatility)
        for i in range(n):
            x=float(base)*math.exp(drift*i+amp*math.sin(i/23.0+phase)+0.012*math.sin(i/4.7+phase))
            c=Decimal(str(x))
            w=c*Decimal(str(width))/Decimal("100")
            values.append(bar(i,c,c+w,c-w))
        rows[asset]=values
    return rows

def closes_from_items(items):
    return {a:[x.payload.close for x in items[a]] for a in ASSETS}

def test_bollinger_and_rsi_outputs_are_causal_to_future_suffix():
    items=synthetic_bars(n=500,phase=0.1)
    closes=closes_from_items(items)
    full=_bollinger_rsi_indicators(closes)
    cut=360
    prefix={a:list(closes[a][:cut+1]) for a in ASSETS}
    partial=_bollinger_rsi_indicators(prefix)
    for asset in ASSETS:
        for key in ("mid","lower","upper","z","rsi14"):
            assert full[asset][key][:cut+1]==partial[asset][key]

def test_bollinger_population_std_and_rsi_range():
    items=synthetic_bars(n=300)
    result=_bollinger_rsi_indicators(closes_from_items(items))
    for asset in ASSETS:
        values=closes_from_items(items)[asset]
        i=100
        window=values[i-19:i+1]
        mean=sum(window,Decimal("0"))/Decimal(20)
        variance=sum(((x-mean)*(x-mean) for x in window),Decimal("0"))/Decimal(20)
        sd=variance.sqrt()
        assert result[asset]["mid"][i]==mean
        assert result[asset]["lower"][i]==mean-Decimal("2")*sd
        assert result[asset]["upper"][i]==mean+Decimal("2")*sd
        assert result[asset]["rsi14"][14] is not None
        assert all(Decimal("0")<=x<=Decimal("100") for x in result[asset]["rsi14"][14:] if x is not None)

def test_mean_reversion_target_requires_lower_band_rsi_and_ema200_bull_regime():
    closes={"BTCUSDT":[Decimal("100")],"ETHUSDT":[Decimal("100")]}
    emas={"BTCUSDT":[Decimal("90")],"ETHUSDT":[Decimal("90")]}
    atrs={"BTCUSDT":[Decimal("2")],"ETHUSDT":[Decimal("2")]}
    bands={
        "BTCUSDT":{"lower":[Decimal("101")],"z":[Decimal("-2.1")],"rsi14":[Decimal("34")],"mid":[Decimal("105")],"upper":[Decimal("110")]},
        "ETHUSDT":{"lower":[Decimal("102")],"z":[Decimal("-2.7")],"rsi14":[Decimal("30")],"mid":[Decimal("105")],"upper":[Decimal("110")]},
    }
    target,_=_target_mean_reversion(0,closes,emas,bands,atrs)
    assert target=="ETHUSDT"  # deeper standardized downside excursion wins
    emas["ETHUSDT"][0]=Decimal("101")
    target,_=_target_mean_reversion(0,closes,emas,bands,atrs)
    assert target=="BTCUSDT"  # ETH is below own EMA200; BTC remains eligible
    bands["BTCUSDT"]["rsi14"][0]=Decimal("36")
    target,_=_target_mean_reversion(0,closes,emas,bands,atrs)
    assert target is None

def test_mean_reversion_target_does_not_buy_above_lower_band():
    closes={"BTCUSDT":[Decimal("103")],"ETHUSDT":[Decimal("103")]}
    emas={"BTCUSDT":[Decimal("90")],"ETHUSDT":[Decimal("90")]}
    atrs={"BTCUSDT":[Decimal("2")],"ETHUSDT":[Decimal("2")]}
    bands={a:{"lower":[Decimal("100")],"z":[Decimal("0.1")],"rsi14":[Decimal("30")],"mid":[Decimal("105")],"upper":[Decimal("110")]} for a in ASSETS}
    target,_=_target_mean_reversion(0,closes,emas,bands,atrs)
    assert target is None

def test_exit_reasons_prioritize_stop_then_timeout_then_mean_and_rsi():
    assert _exit_reason(Decimal("90"),Decimal("100"),Decimal("35"),Decimal("95"),2)=="STOP_2ATR_CLOSE_NEXT_OPEN"
    assert _exit_reason(Decimal("99"),Decimal("100"),Decimal("35"),Decimal("95"),MAX_HOLD_BARS)=="TIME_EXIT_18_BARS_NEXT_OPEN"
    assert _exit_reason(Decimal("101"),Decimal("100"),Decimal("35"),Decimal("95"),4)=="BB_MID_REVERSION_NEXT_OPEN"
    assert _exit_reason(Decimal("99"),Decimal("100"),Decimal("50"),Decimal("95"),4)=="RSI14_NORMALIZED_NEXT_OPEN"
    assert _exit_reason(Decimal("99"),Decimal("98"),Decimal("40"),Decimal("95"),4)=="BB_MID_REVERSION_NEXT_OPEN"
    assert _exit_reason(Decimal("99"),Decimal("90"),Decimal("40"),Decimal("95"),4) is None

def test_atr_weight_formula_and_cap_match_frozen_15_percent_rule():
    items=synthetic_bars(n=100)
    weights=_atr_target_weights(items)
    annualization=Decimal(365*6).sqrt()
    for asset in ASSETS:
        vector=weights[asset]
        assert all(x is None for x in vector[:13])
        assert all(x is not None and Decimal("0")<x<=MAX_POSITION_FRACTION for x in vector[13:])
        i=40
        rows=items[asset]
        tr=[]
        for j,row in enumerate(rows):
            p=row.payload
            if j==0:
                tr.append(p.high-p.low)
            else:
                prev=rows[j-1].payload.close
                tr.append(max(p.high-p.low,abs(p.high-prev),abs(p.low-prev)))
        atr=sum(tr[i-13:i+1],Decimal("0"))/Decimal(14)
        expected=min(MAX_POSITION_FRACTION,ATR_TARGET_ANNUAL/((atr/rows[i].payload.close)*annualization))
        assert vector[i]==expected

def test_canonical_ohlcv_hash_is_stable_and_sensitive_to_change():
    items=synthetic_bars(n=40)
    initial=_canonical_ohlcv_sha256(items["BTCUSDT"])
    assert initial==_canonical_ohlcv_sha256(deepcopy(items["BTCUSDT"]))
    changed=deepcopy(items["BTCUSDT"])
    old=changed[10].payload
    altered=SimpleNamespace(
        start=old.start,end=old.end,open=old.open,close=old.close+Decimal("0.01"),
        high=old.high+Decimal("0.01"),low=old.low,volume=old.volume)
    changed[10]=SimpleNamespace(payload=altered)
    assert initial!=_canonical_ohlcv_sha256(changed)
