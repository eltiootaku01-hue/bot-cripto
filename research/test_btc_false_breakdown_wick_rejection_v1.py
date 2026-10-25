from datetime import date, timedelta
from decimal import Decimal
from copy import deepcopy

from research import btc_volume_shock_reversal_v1 as core
from research import btc_false_breakdown_wick_rejection_v1 as s


def candle(day, op="100", hi="101", lo="99", close="100"):
    ms = core.to_ms(day)
    return {
        "open_ms": ms, "timestamp": core.stamp(ms), "close_ms": ms + core.DAY_MS - 1,
        "open": Decimal(op), "high": Decimal(hi), "low": Decimal(lo),
        "close": Decimal(close), "volume": Decimal("10"),
    }


def filters():
    return {"min_qty": Decimal("0.01"), "step": Decimal("0.01"), "min_notional": Decimal("5")}


def fixture():
    start = date(2020, 1, 1)
    rows = [candle(start + timedelta(days=i)) for i in range(103)]
    rows[100] = candle(start + timedelta(days=100), op="100", hi="101", lo="97", close="100.5")
    rows[101] = candle(start + timedelta(days=101), op="100", hi="101", lo="99", close="100.5")
    rows[102] = candle(start + timedelta(days=102), op="100.5", hi="101", lo="99.5", close="100.5")
    return rows


def test_ema100_recursive_value_matches_manual_calculation():
    rows = [candle(date(2020, 1, 1) + timedelta(days=i)) for i in range(100)]
    rows.append(candle(date(2020, 4, 10), op="100", hi="101", lo="99", close="100.5"))
    expected = Decimal("100") + (Decimal(2) / Decimal(101)) * Decimal("0.5")
    assert s.ema_series(rows)[100] == expected


def test_false_breakdown_wick_rejection_signal_passes_all_frozen_conditions():
    rows = fixture()
    assert s.lower_wick_rejection_signal(rows, 100)


def test_signal_rejects_candles_without_prior_low_sweep_or_wick_shape():
    rows = fixture()
    rows[100]["low"] = Decimal("99")
    assert not s.lower_wick_rejection_signal(rows, 100)

    rows = fixture()
    rows[100]["high"] = Decimal("102")
    rows[100]["low"] = Decimal("97")
    rows[100]["close"] = Decimal("100.5")
    assert not s.lower_wick_rejection_signal(rows, 100)


def test_trend_filter_and_body_condition_are_required():
    rows = fixture()
    for row in rows[:100]:
        row["open"] = Decimal("101")
        row["high"] = Decimal("102")
        row["low"] = Decimal("100")
        row["close"] = Decimal("101")
    rows[100] = candle(date(2020, 4, 10), op="100", hi="101", lo="97", close="100.5")
    assert not s.lower_wick_rejection_signal(rows, 100)  # close is below EMA100

    rows = fixture()
    rows[100] = candle(date(2020, 4, 10), op="100.5", hi="101", lo="97", close="100")
    assert not s.lower_wick_rejection_signal(rows, 100)  # bearish body


def test_signal_is_causal_when_future_candles_are_appended():
    rows = fixture()
    before = s.lower_wick_rejection_signal(rows, 100)
    extended = deepcopy(rows)
    for i in range(103, 110):
        extended.append(candle(date(2020, 1, 1) + timedelta(days=i), op="100", hi="100000", lo="1", close="99999"))
    assert s.lower_wick_rejection_signal(extended, 100) == before


def test_next_open_entry_and_exit_preserve_fee_and_ledger_accounting():
    rows = fixture()
    rows[101] = candle(date(2020, 4, 11), op="100", hi="106", lo="99.5", close="105")
    rows[102] = candle(date(2020, 4, 12), op="105.5", hi="107", lo="105", close="106")
    old = (core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD)
    try:
        s.activate_frozen_rules()
        result = core.simulate(rows, 0, len(rows), Decimal("60"), "BASE", filters(), "UNIT", "VOLUME_SHOCK_REVERSAL")
    finally:
        core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD = old
    buys = [f for f in result["fills"] if f["side"] == "BUY"]
    sells = [f for f in result["fills"] if f["side"] == "SELL"]
    assert len(buys) == 1 and len(sells) == 1
    assert buys[0]["signal_utc"] == rows[100]["timestamp"]
    assert buys[0]["execution_utc"] == rows[101]["timestamp"]
    assert buys[0]["execution_type"] == "NEXT_OPEN"
    assert sells[0]["execution_utc"] == rows[102]["timestamp"]
    assert sells[0]["execution_type"] == "NEXT_OPEN"
    assert result["final"] == Decimal(sells[0]["cash_after"])
    assert result["fees"] > 0 and result["friction"] > 0


def test_terminal_liquidation_and_current_quantity_filters_are_enforced():
    rows = fixture()[:102]
    old = (core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD)
    try:
        s.activate_frozen_rules()
        result = core.simulate(rows, 0, len(rows), Decimal("60"), "STRESS_2", filters(), "TERM", "VOLUME_SHOCK_REVERSAL")
    finally:
        core.entry_signal, core.ALLOC, core.TP, core.SL, core.MAX_HOLD = old
    sells = [f for f in result["fills"] if f["side"] == "SELL"]
    assert len(sells) == 1 and sells[0]["execution_type"] == "TERMINAL_CLOSE"
    assert result["final"] == Decimal(sells[0]["cash_after"])

    tiny = {"min_qty": Decimal("10"), "step": Decimal("0.01"), "min_notional": Decimal("5")}
    qty, _, _, reject = core.buy_size(Decimal("60"), Decimal("100"), Decimal("0.2"), core.COSTS["BASE"], tiny)
    assert qty == 0 and reject == "MIN_QTY"


def test_quantity_rounding_and_fee_budget_are_exact():
    qty, px, fee, reject = core.buy_size(Decimal("60"), Decimal("100"), Decimal("0.2"), core.COSTS["STRESS_2"], filters())
    assert reject is None and qty % filters()["step"] == 0
    assert qty * px + fee <= Decimal("12")
