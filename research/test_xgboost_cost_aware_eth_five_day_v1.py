from datetime import date, timedelta
from decimal import Decimal
from copy import deepcopy
import math

from research import btc_volume_shock_reversal_v1 as core
from research import xgboost_cost_aware_eth_five_day_v1 as s


def candle(day, close, open_=None, volume=10):
    close = Decimal(str(close))
    op = close if open_ is None else Decimal(str(open_))
    hi = max(op, close) * Decimal("1.01")
    lo = min(op, close) * Decimal("0.99")
    ms = core.to_ms(day)
    return {"open_ms": ms, "timestamp": core.stamp(ms), "close_ms": ms+core.DAY_MS-1,
            "open": op, "high": hi, "low": lo, "close": close, "volume": Decimal(str(volume))}


def paired_fixture(n=360):
    start = date(2019, 1, 1)
    eth, btc = [], []
    for i in range(n):
        day = start + timedelta(days=i)
        b = Decimal("100") + Decimal(i) / Decimal("3") + Decimal(str(math.sin(i/9)))
        e = Decimal("0.025") * b * (Decimal("1") + Decimal(str(0.025*math.sin(i/7))))
        eth.append(candle(day, e, volume=10 + (i % 17)))
        btc.append(candle(day, b, volume=20 + (i % 13)))
    return eth, btc


def test_features_are_causal_and_use_only_current_or_past_bars():
    eth, btc = paired_fixture()
    features = s.make_features(eth, btc)
    idx = 300
    assert features[idx] is not None and len(features[idx]) == len(s.FEATURE_NAMES)
    altered_eth, altered_btc = deepcopy(eth), deepcopy(btc)
    for i in range(idx+1, len(eth)):
        altered_eth[i]["close"] = Decimal("999999")
        altered_eth[i]["volume"] = Decimal("999999")
        altered_btc[i]["close"] = Decimal("1")
    altered = s.make_features(altered_eth, altered_btc)
    assert features[idx] == altered[idx]


def test_ratio_zscore_excludes_current_bar_and_handles_flat_reference():
    eth, btc = paired_fixture()
    idx = 300
    z = s.ratio_zscore([r["close"] for r in eth], [r["close"] for r in btc], idx)
    assert z is not None and math.isfinite(z)
    extended_eth, extended_btc = deepcopy(eth), deepcopy(btc)
    for i in range(len(eth), len(eth)+5):
        day = date(2019, 1, 1) + timedelta(days=i)
        extended_eth.append(candle(day, "1000000"))
        extended_btc.append(candle(day, "1"))
    assert s.ratio_zscore([r["close"] for r in extended_eth], [r["close"] for r in extended_btc], idx) == z
    flat_eth = [candle(date(2019, 1, 1)+timedelta(days=i), "2") for i in range(40)]
    flat_btc = [candle(date(2019, 1, 1)+timedelta(days=i), "100") for i in range(40)]
    assert s.ratio_zscore([r["close"] for r in flat_eth], [r["close"] for r in flat_btc], 35) is None


def test_mature_training_indices_never_include_unmatured_labels():
    features = [[float(i)] for i in range(700)]
    decision = 501
    idx = s.mature_training_indices(features, decision)
    assert idx and len(idx) <= s.MAX_TRAINING_ROWS
    assert all(j % s.CADENCE == s.CADENCE-1 for j in idx)
    assert all(j+s.LABEL_EXIT_OFFSET <= decision for j in idx)
    assert all(features[j] is not None for j in idx)
    assert max(j+s.LABEL_EXIT_OFFSET for j in idx) <= decision
    assert len(s.mature_training_indices(features, 100)) < s.MIN_TRAINING_ROWS


def test_fixed_xgboost_estimator_is_deterministic_on_frozen_inputs():
    x = [[float(i)/50.0, math.sin(i/7.0), math.cos(i/11.0)] for i in range(100)]
    y = [0.003*x0[0] - 0.001*x0[1] + 0.002*x0[2] for x0 in x]
    row = [0.8, -0.25, 0.35]
    p1 = s.fit_predict_xgb(x, y, row)
    p2 = s.fit_predict_xgb(x, y, row)
    assert math.isfinite(p1) and abs(p1-p2) <= 1e-8


def test_cost_threshold_is_monotonic_and_includes_fixed_buffer():
    base = s.break_even_threshold("BASE")
    stress1 = s.break_even_threshold("STRESS_1")
    stress2 = s.break_even_threshold("STRESS_2")
    assert Decimal("0.0025") < base < stress1 < stress2
    assert (stress2-base) > Decimal("0")


def test_shared_ledger_uses_next_open_five_day_exit_and_exact_fees():
    rows = [candle(date(2020, 1, 1)+timedelta(days=i), 100+i, open_=100+i) for i in range(15)]
    old = (core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD)
    old_signals = s.ENTRY_SIGNALS
    try:
        s.ENTRY_SIGNALS = [False]*len(rows)
        s.ENTRY_SIGNALS[4] = True
        core.entry_signal = s._entry_signal
        core.ALLOC = s.ENTRY_ALLOC
        core.TP = s.TAKE_PROFIT_UNUSED
        core.SL = s.STOP_LOSS_UNUSED
        core.MAX_HOLD = s.HORIZON
        filters = {"min_qty": Decimal("0.0001"), "step": Decimal("0.0001"), "min_notional": Decimal("5")}
        result = core.simulate(rows, 0, len(rows), Decimal("60"), "BASE", filters, "UNIT", "VOLUME_SHOCK_REVERSAL")
    finally:
        core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD = old
        s.ENTRY_SIGNALS = old_signals
    buys = [f for f in result["fills"] if f["side"] == "BUY"]
    sells = [f for f in result["fills"] if f["side"] == "SELL"]
    assert len(buys) == 1 and len(sells) == 1
    assert buys[0]["execution_utc"] == rows[5]["timestamp"]
    assert buys[0]["execution_type"] == "NEXT_OPEN"
    assert sells[0]["execution_utc"] == rows[10]["timestamp"]
    assert sells[0]["execution_type"] == "NEXT_OPEN"
    assert result["final"] == Decimal(sells[0]["cash_after"])
    assert result["fees"] > 0 and result["friction"] > 0


def test_terminal_liquidation_and_quantity_filter_rejection():
    rows = [candle(date(2020, 1, 1)+timedelta(days=i), 100+i, open_=100+i) for i in range(10)]
    old = (core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD)
    old_signals = s.ENTRY_SIGNALS
    try:
        s.ENTRY_SIGNALS = [False]*len(rows)
        s.ENTRY_SIGNALS[4] = True
        core.entry_signal = s._entry_signal
        core.ALLOC = s.ENTRY_ALLOC
        core.TP = s.TAKE_PROFIT_UNUSED
        core.SL = s.STOP_LOSS_UNUSED
        core.MAX_HOLD = s.HORIZON
        filters = {"min_qty": Decimal("0.0001"), "step": Decimal("0.0001"), "min_notional": Decimal("5")}
        result = core.simulate(rows, 0, len(rows), Decimal("60"), "STRESS_2", filters, "TERM", "VOLUME_SHOCK_REVERSAL")
        sells = [f for f in result["fills"] if f["side"] == "SELL"]
        assert len(sells) == 1 and sells[0]["execution_type"] == "TERMINAL_CLOSE"
        assert result["final"] == Decimal(sells[0]["cash_after"])
    finally:
        core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD = old
        s.ENTRY_SIGNALS = old_signals
    tiny = {"min_qty": Decimal("100"), "step": Decimal("0.01"), "min_notional": Decimal("5")}
    qty, _, _, reject = core.buy_size(Decimal("60"), Decimal("100"), Decimal("0.2"), core.COSTS["BASE"], tiny)
    assert qty == 0 and reject == "MIN_QTY"
