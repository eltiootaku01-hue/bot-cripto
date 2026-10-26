from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from copy import deepcopy

from research.ema_btc_20_50_200_state_atr_v1 import (
    ema_series, ema_state_signals_and_weights,
    FAST_PERIOD, MEDIUM_PERIOD, SLOW_PERIOD,
)

UTC = timezone.utc

def item(index, close, high=None, low=None):
    start = datetime(2018, 1, 1, tzinfo=UTC) + timedelta(days=index)
    c = Decimal(str(close))
    h = c + Decimal("2") if high is None else Decimal(str(high))
    l = c - Decimal("2") if low is None else Decimal(str(low))
    return SimpleNamespace(payload=SimpleNamespace(
        start=start, close=c, high=h, low=l
    ))

def trend_fixture():
    rows = []
    # Flat prehistory, a sustained bull trend, then a bearish turn.
    for i in range(250):
        rows.append(item(i, "100"))
    for i in range(250, 451):
        rows.append(item(i, 100 + (i-249) * 1.0))
    for i in range(451, 650):
        rows.append(item(i, 301 - (i-450) * 1.7))
    return rows

def test_recursive_ema_matches_manual_formula_and_respects_warmup():
    values = [Decimal("10"), Decimal("12"), Decimal("9"), Decimal("15")]
    result = ema_series(values, 3)
    assert result[0] is None and result[1] is None
    alpha = Decimal(2) / Decimal(4)
    expected = alpha * Decimal("9") + (Decimal(1)-alpha) * Decimal("11")
    assert result[2] == expected
    assert result[3] == alpha * Decimal("15") + (Decimal(1)-alpha) * expected

def test_state_transition_enters_once_and_exit_condition_fires_in_reversal():
    rows = trend_fixture()
    entries, exits, weights, atr, ema20, ema50, ema200, state = ema_state_signals_and_weights(rows)
    entry_indices = [i for i, signal in enumerate(entries) if signal]
    exit_indices = [i for i, signal in enumerate(exits) if signal]
    assert entry_indices
    assert exit_indices
    assert all(i >= SLOW_PERIOD - 1 for i in entry_indices + exit_indices)
    assert all(state[i] and not state[i-1] for i in entry_indices if i > SLOW_PERIOD-1)
    assert all(weights[i] is not None and Decimal(0) < weights[i] <= Decimal("0.95") for i in entry_indices)

def test_features_are_causal_to_each_decision_index():
    rows = trend_fixture()
    baseline = ema_state_signals_and_weights(rows)
    cut = 430
    altered = deepcopy(rows)
    for i in range(cut+1, len(altered)):
        altered[i].payload.close = Decimal("1")
        altered[i].payload.high = Decimal("2")
        altered[i].payload.low = Decimal("0.5")
    changed = ema_state_signals_and_weights(altered)
    for part in range(8):
        assert baseline[part][cut] == changed[part][cut]

def test_periods_and_output_shapes_are_frozen():
    rows = trend_fixture()
    result = ema_state_signals_and_weights(rows)
    assert (FAST_PERIOD, MEDIUM_PERIOD, SLOW_PERIOD) == (20, 50, 200)
    assert len(result) == 8
    assert all(len(vector) == len(rows) for vector in result)
    assert result[4][18] is None and result[4][19] is not None
    assert result[5][48] is None and result[5][49] is not None
    assert result[6][198] is None and result[6][199] is not None
