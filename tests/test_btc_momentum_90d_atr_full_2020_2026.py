from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy

from research.replay_btc_momentum_90d_atr_full_2020_2026 import (
    _momentum_signals_and_weights, _sma, WINDOWS, FULL_START, END, WARMUP_BARS,
)

UTC=timezone.utc

def item(day, open_, high, low, close):
    return SimpleNamespace(payload=SimpleNamespace(
        start=day, open=Decimal(str(open_)), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close))
    ))

def test_frozen_windows_cover_full_earlier_recent_and_annual_slices():
    names=[x[0] for x in WINDOWS]
    assert names==[
      "FULL_2020_2026","EARLIER_2020_2022","RECENT_2023_2026",
      "2020","2021","2022","2023","2024","2025","2026_YTD"
    ]
    assert FULL_START==datetime(2020,1,1,tzinfo=UTC)
    assert END==datetime(2026,10,22,tzinfo=UTC)
    assert WARMUP_BARS==400

def test_atr_weight_and_signal_are_causal_to_index():
    rows=[]
    start=datetime(2018,1,1,tzinfo=UTC)
    for i in range(600):
        close=Decimal("100") if i<250 else Decimal("100") + Decimal(i-249)*Decimal("0.2")
        rows.append(item(start+timedelta(days=i),close,close+Decimal("2"),close-Decimal("2"),close))
    base=_momentum_signals_and_weights(rows)
    idx=300
    altered=deepcopy(rows)
    for i in range(idx+1,len(altered)):
        altered[i].payload.close=Decimal("1000000")
        altered[i].payload.high=Decimal("1000001")
        altered[i].payload.low=Decimal("999999")
    other=_momentum_signals_and_weights(altered)
    assert base[0][idx]==other[0][idx]
    assert base[1][idx]==other[1][idx]
    assert base[2][idx]==other[2][idx]
    for w in base[2]:
        if w is not None:
            assert Decimal(0)<w<=Decimal("0.95")

def test_sma200_is_causal_and_uses_200_closed_closes():
    closes=[Decimal(100+i) for i in range(250)]
    sma=_sma(closes,200)
    assert sma[198] is None
    assert sma[199]==sum(closes[:200],Decimal(0))/Decimal(200)
    altered=closes[:]
    for i in range(201,len(altered)):
        altered[i]=Decimal("999999")
    alt=_sma(altered,200)
    assert sma[200]==alt[200]

def test_no_entry_or_exit_before_warmup():
    rows=[]
    start=datetime(2018,1,1,tzinfo=UTC)
    for i in range(199):
        c=Decimal(100+i)
        rows.append(item(start+timedelta(days=i),c,c+2,c-2,c))
    entries, exits, weights, atr, sma=_momentum_signals_and_weights(rows)
    assert not any(entries)
    assert not any(exits)
    assert all(w is None for w in weights)
