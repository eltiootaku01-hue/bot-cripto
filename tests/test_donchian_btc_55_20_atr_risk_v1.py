from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy

from research import donchian_btc_55_20_atr_risk_v1 as s

UTC=timezone.utc

def bar(i, close, high, low):
    start=datetime(2019,1,1,tzinfo=UTC)+timedelta(days=i)
    return SimpleNamespace(payload=SimpleNamespace(
        start=start, close=Decimal(str(close)), high=Decimal(str(high)), low=Decimal(str(low))
    ))

def fixture(n=100):
    rows=[]
    for i in range(n):
        rows.append(bar(i,"100","101","99"))
    # close over the prior 55-day high; breakout reference excludes this current bar
    rows[55]=bar(55,"103","104","102")
    # close under the prior 20-day low; exit reference excludes current bar
    rows[80]=bar(80,"98","99","97")
    return rows

def test_channel_windows_exclude_current_candle_and_detect_frozen_events():
    rows=fixture()
    entries, exits, weights, atr, upper, lower=s.donchian_signals_and_weights(rows)
    assert upper[54] is None
    assert upper[55]==Decimal("101")
    assert entries[55]
    assert lower[79]==Decimal("99")
    assert lower[80]==Decimal("99")
    assert exits[80]
    assert weights[55] is not None and Decimal(0)<weights[55]<=Decimal("0.95")

def test_future_candles_do_not_change_past_breakout_or_exit_signals():
    rows=fixture()
    baseline=s.donchian_signals_and_weights(rows)
    changed=deepcopy(rows)
    for i in range(81,len(changed)):
        changed[i].payload.close=Decimal("100000")
        changed[i].payload.high=Decimal("100001")
        changed[i].payload.low=Decimal("99999")
    altered=s.donchian_signals_and_weights(changed)
    for part in (0,1,2,3,4,5):
        assert baseline[part][55]==altered[part][55]
        assert baseline[part][80]==altered[part][80]

def test_frozen_channel_lookbacks_and_output_shapes():
    rows=fixture()
    result=s.donchian_signals_and_weights(rows)
    assert len(result)==6
    assert all(len(x)==len(rows) for x in result)
    assert s.ENTRY_LOOKBACK==55 and s.EXIT_LOOKBACK==20
