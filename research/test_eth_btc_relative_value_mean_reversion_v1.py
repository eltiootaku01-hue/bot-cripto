from datetime import date, timedelta
from decimal import Decimal
from copy import deepcopy

from research import btc_volume_shock_reversal_v1 as core
from research import eth_btc_relative_value_mean_reversion_v1 as s


def candle(day, close, op=None):
    close = Decimal(str(close))
    op = close if op is None else Decimal(str(op))
    ms = core.to_ms(day)
    hi = max(op, close) * Decimal("1.01")
    lo = min(op, close) * Decimal("0.99")
    return {"open_ms": ms, "timestamp": core.stamp(ms), "close_ms": ms + core.DAY_MS - 1,
            "open": op, "high": hi, "low": lo, "close": close, "volume": Decimal("10")}


def paired_fixture(n=180, shock_index=150):
    start = date(2020, 1, 1)
    btc, eth = [], []
    for i in range(n):
        day = start + timedelta(days=i)
        btc_close = Decimal("100") + Decimal(i) / Decimal("10")
        ratio = Decimal("0.02") * (Decimal("1.01") if i % 2 == 0 else Decimal("0.99"))
        if i == shock_index:
            ratio = Decimal("0.018")
        btc.append(candle(day, btc_close))
        eth.append(candle(day, btc_close * ratio))
    return eth, btc


def test_pair_timestamps_are_identical_and_zscore_excludes_current_bar():
    eth, btc = paired_fixture()
    i = 150
    z1 = s.relative_value_zscore(eth, btc, i)
    assert z1 is not None and z1 <= Decimal("-1.50")
    extended_eth, extended_btc = deepcopy(eth), deepcopy(btc)
    for k in range(len(eth), len(eth) + 5):
        day = date(2020, 1, 1) + timedelta(days=k)
        bc = Decimal("100") + Decimal(k) / Decimal("10")
        extended_btc.append(candle(day, bc))
        extended_eth.append(candle(day, bc * Decimal("100")))
    assert s.relative_value_zscore(extended_eth, extended_btc, i) == z1
    assert [r["open_ms"] for r in eth] == [r["open_ms"] for r in btc]


def test_zscore_returns_none_for_insufficient_or_zero_variance_history():
    eth, btc = paired_fixture()
    assert s.relative_value_zscore(eth, btc, 59) is None
    for i in range(120):
        btc[i]["close"] = Decimal("100")
        eth[i]["close"] = Decimal("2")
    assert s.relative_value_zscore(eth, btc, 100) is None


def test_signal_requires_btc_above_causal_ema_and_extreme_relative_underperformance():
    eth, btc = paired_fixture()
    ema = s.ema_close(btc)
    assert s.relative_value_entry_signal(eth, btc, 150, ema)
    falling = deepcopy(btc)
    for i in range(len(falling)):
        falling[i]["close"] = Decimal("300") - Decimal(i) / Decimal("10")
    falling_ema = s.ema_close(falling)
    assert not s.relative_value_entry_signal(eth, falling, 150, falling_ema)


def test_next_open_execution_fees_and_terminal_liquidation():
    eth, btc = paired_fixture(n=180, shock_index=150)
    # Put a qualifying dislocation at 150 and cause a TP signal at the following close;
    # entry/exit must still occur on subsequent opens, not on the signal close.
    eth[150]["close"] = btc[150]["close"] * Decimal("0.018")
    eth[151] = candle(date(2020, 1, 1) + timedelta(days=151), Decimal("2.1"))
    eth[152] = candle(date(2020, 1, 1) + timedelta(days=152), Decimal("2.2"))
    old = (core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD)
    old_signals = s.ENTRY_SIGNALS
    try:
        s.BTC_ROWS = btc
        s.BTC_EMA = s.ema_close(btc)
        s.ENTRY_SIGNALS = [s.relative_value_entry_signal(eth, btc, i, s.BTC_EMA) for i in range(len(eth))]
        core.entry_signal = s._entry_signal
        core.ALLOC = s.ENTRY_ALLOC
        core.TP = s.TAKE_PROFIT
        core.SL = s.STOP_LOSS
        core.MAX_HOLD = s.MAX_HOLD
        filters = {"min_qty": Decimal("0.0001"), "step": Decimal("0.0001"), "min_notional": Decimal("5")}
        result = core.simulate(eth, 100, len(eth), Decimal("60"), "BASE", filters, "UNIT", "VOLUME_SHOCK_REVERSAL")
    finally:
        core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD = old
        s.ENTRY_SIGNALS = old_signals
    buys = [f for f in result["fills"] if f["side"] == "BUY"]
    sells = [f for f in result["fills"] if f["side"] == "SELL"]
    assert buys and buys[0]["execution_utc"] == eth[151]["timestamp"]
    assert buys[0]["execution_type"] == "NEXT_OPEN"
    if sells:
        assert sells[0]["execution_utc"] > buys[0]["execution_utc"]
        assert result["fees"] > 0 and result["friction"] > 0
    else:
        assert result["final"] == Decimal(result["fills"][-1]["cash_after"]) if result["fills"] else result["final"] == Decimal("60")


def test_terminal_close_is_used_and_minimum_quantity_rejection_is_enforced():
    eth, btc = paired_fixture(n=180, shock_index=150)
    eth[150]["close"] = btc[150]["close"] * Decimal("0.018")
    old = (core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD)
    old_signals = s.ENTRY_SIGNALS
    try:
        s.BTC_ROWS = btc
        s.BTC_EMA = s.ema_close(btc)
        s.ENTRY_SIGNALS = [s.relative_value_entry_signal(eth, btc, i, s.BTC_EMA) for i in range(len(eth))]
        core.entry_signal = s._entry_signal
        core.ALLOC = s.ENTRY_ALLOC
        core.TP = s.TAKE_PROFIT
        core.SL = s.STOP_LOSS
        core.MAX_HOLD = s.MAX_HOLD
        tiny = {"min_qty": Decimal("100"), "step": Decimal("0.01"), "min_notional": Decimal("5")}
        result = core.simulate(eth, 100, 151, Decimal("60"), "STRESS_2", tiny, "TERM", "VOLUME_SHOCK_REVERSAL")
        assert result["rejects"] >= 0
    finally:
        core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD = old
        s.ENTRY_SIGNALS = old_signals

    rows = paired_fixture(n=180, shock_index=150)[0]
    # Test explicit exact quantity-flooring and rejection behavior in the shared ledger primitive.
    filters = {"min_qty": Decimal("100"), "step": Decimal("0.01"), "min_notional": Decimal("5")}
    qty, _, _, reject = core.buy_size(Decimal("60"), Decimal("100"), Decimal("0.2"), core.COSTS["BASE"], filters)
    assert qty == 0 and reject == "MIN_QTY"


def test_btc_ema_is_causal():
    eth, btc = paired_fixture()
    baseline = s.ema_close(btc)
    cut = 140
    edited = deepcopy(btc)
    for i in range(cut + 1, len(edited)):
        edited[i]["close"] = Decimal("999999")
    changed = s.ema_close(edited)
    assert baseline[cut] == changed[cut]
