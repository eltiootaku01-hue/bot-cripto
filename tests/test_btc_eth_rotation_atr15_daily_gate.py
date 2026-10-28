from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy
import pytest

from research.replay_btc_eth_rotation_atr15_daily_gate import (
    _atr_target_weights,
    _canonical_ohlcv_sha256,
    _completed_daily_regime,
    ATR_TARGET_ANNUAL,
    MAX_POSITION_FRACTION,
    ASSETS,
)

UTC = timezone.utc

def bar(i, close, high, low):
    start = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=4*i)
    end = start + timedelta(hours=4) - timedelta(milliseconds=1)
    close = Decimal(str(close))
    return SimpleNamespace(payload=SimpleNamespace(
        start=start, end=end, open=close, close=close,
        high=Decimal(str(high)), low=Decimal(str(low)),
        volume=Decimal("10.00000000"),
    ))

def dataset(n=120, volatility="2"):
    rows = {}
    for asset, mult in (("BTCUSDT", Decimal("1")), ("ETHUSDT", Decimal("1.3"))):
        values = []
        for i in range(n):
            close = Decimal("1000") + Decimal(i) * Decimal("0.2") * mult
            width = Decimal(str(volatility)) * mult
            values.append(bar(i, close, close+width, close-width))
        rows[asset] = values
    return rows

def daily_dataset(days=210):
    n = days*6
    return dataset(n=n, volatility="2")["BTCUSDT"]

def test_atr_weight_formula_cap_and_warmup():
    items = dataset(80, "2")
    weights = _atr_target_weights(items)
    annualization = Decimal(365*6).sqrt()
    for asset in ASSETS:
        vector = weights[asset]
        assert len(vector) == 80
        assert all(x is None for x in vector[:13])
        assert all(x is not None and Decimal(0) < x <= MAX_POSITION_FRACTION for x in vector[13:])
        i = 30
        rows = items[asset]
        tr = []
        for j, row in enumerate(rows):
            p = row.payload
            if j == 0:
                tr.append(p.high-p.low)
            else:
                prev = rows[j-1].payload.close
                tr.append(max(p.high-p.low, abs(p.high-prev), abs(p.low-prev)))
        atr = sum(tr[i-13:i+1], Decimal(0))/Decimal(14)
        expected = min(MAX_POSITION_FRACTION, ATR_TARGET_ANNUAL/((atr/rows[i].payload.close)*annualization))
        assert vector[i] == expected

def test_daily_gate_only_uses_last_fully_completed_utc_daily_candle():
    items = daily_dataset(days=210)
    gate = _completed_daily_regime(items)
    assert len(gate) == len(items)
    # The first 199 complete daily candles cannot have a recursive daily EMA200.
    assert not any(gate[:199*6+5])
    # At the first five 4h closes of day 200, that day's daily candle is still incomplete.
    day_idx = 199*6
    assert gate[day_idx+4] is False
    # Only the last 4h candle close makes the 200th complete daily close available.
    assert gate[day_idx+5] is True

def test_daily_gate_is_causal_to_future_suffix():
    full = daily_dataset(days=215)
    base = _completed_daily_regime(full)
    cut = 204*6
    changed = deepcopy(full)
    for i in range(cut+1, len(changed)):
        changed[i] = bar(i, Decimal("100000")+i, Decimal("100100")+i, Decimal("99900")+i)
    after = _completed_daily_regime(changed)
    assert base[:cut+1] == after[:cut+1]

def test_daily_gate_does_not_peek_at_daily_close_before_last_4h_bar():
    original = daily_dataset(days=205)
    changed = deepcopy(original)
    day_idx = 200*6
    # Change the final 4h candle of the chosen day enough to change its daily close.
    changed[day_idx+5] = bar(day_idx+5, Decimal("1"), Decimal("1.1"), Decimal("0.9"))
    gate_original = _completed_daily_regime(original)
    gate_changed = _completed_daily_regime(changed)
    # Earlier 4h closes that day still only see the previous completed daily candle.
    assert gate_original[day_idx+4] == gate_changed[day_idx+4]

def test_missing_4h_candle_fails_closed_for_daily_gate():
    items = daily_dataset(days=3)
    with pytest.raises(RuntimeError, match="FAIL-CLOSED"):
        _completed_daily_regime(items[:8] + items[9:])

def test_canonical_ohlcv_hash_is_stable_and_sensitive_to_price_changes():
    items = dataset(30)["BTCUSDT"]
    baseline = _canonical_ohlcv_sha256(items)
    assert baseline == _canonical_ohlcv_sha256(deepcopy(items))
    changed = deepcopy(items)
    row = changed[10]
    p = row.payload
    changed[10] = SimpleNamespace(payload=SimpleNamespace(
        start=p.start, end=p.end, open=p.open, high=p.high,
        low=p.low, close=p.close+Decimal("0.01"), volume=p.volume,
    ))
    assert baseline != _canonical_ohlcv_sha256(changed)
