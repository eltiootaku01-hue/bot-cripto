from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bot_obrero.analysis_contracts import ArtifactNature, MarketObservation, Provenance
from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from bot_obrero.research_strategies import (
    DonchianBreakoutStrategy, EmaRsiStrategy, MeanReversionStrategy,
    SignalAction, StrategyContext,
)
from bot_obrero.research_portfolio import PortfolioConfig, run_shared_portfolio
from bot_obrero.virtual_backtest import BacktestConfig, _observation, run_virtual_backtest

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def item(index: int, close: Decimal, interval: str = "4h", *, open_price: Decimal | None = None,
         high: Decimal | None = None, low: Decimal | None = None, symbol: str = "BTCUSDT") -> MarketData:
    delta = timedelta(hours=4) if interval == "4h" else timedelta(minutes=1)
    start = START + index * delta
    end = start + delta - timedelta(milliseconds=1)
    opening = open_price if open_price is not None else close
    candle = Candle(
        start=start, end=end, timeframe=interval, open=opening,
        high=high if high is not None else max(opening, close),
        low=low if low is not None else min(opening, close), close=close,
        volume=Decimal("10"), candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE, finality=CandleFinality.FINAL,
    )
    base = symbol[:-4]
    instrument = InstrumentIdentity(
        instrument_id=f"binance:SPOT:{symbol}", symbol=f"{base}/USDT", market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT, base_asset=base, quote_asset="USDT",
    )
    return MarketData(
        instrument=instrument,
        source=SourceIdentity(source_id="binance-spot-rest", provider="binance", venue="BINANCE"),
        data_type="CANDLE", observed_at=start, received_at=end, available_at=end,
        payload=candle, quality=DataQuality.VALID, completeness=DataCompleteness.COMPLETE,
    )


def obs(index: int, close: Decimal, high: Decimal | None = None, low: Decimal | None = None):
    stamp = START + index * timedelta(hours=4)
    return MarketObservation(
        symbol="BTC/USDT", observation_timestamp=stamp, available_timestamp=stamp,
        observation_type="CLOSED_BAR", values={"close": close,
            "high": high if high is not None else close, "low": low if low is not None else close},
        provenance=Provenance(source="test", nature=ArtifactNature.OBSERVED),
        venue="TEST", observation_id=f"bar-{index}",
    )


def context(history, *, in_position=False, current=None, previous=None, interval="4h"):
    return StrategyContext(
        history=tuple(history), in_position=in_position, current_values=current or {},
        previous_values=previous or {}, timestamp=START, entries_in_rolling_window=0,
        interval=interval,
    )


def test_signal_contract_has_reason_and_donchian_excludes_current_bar():
    bars = [obs(i, Decimal("10"), Decimal("10"), Decimal("9")) for i in range(20)]
    bars.append(obs(20, Decimal("11"), Decimal("100"), Decimal("8")))
    signal = DonchianBreakoutStrategy().decide(context(bars))
    assert signal.action is SignalAction.BUY
    assert "current bar excluded" in signal.reason
    assert DonchianBreakoutStrategy().decide(context(bars[:20])).action is SignalAction.HOLD


def test_donchian_exit_uses_prior_ten_lows_not_current_low():
    bars = [obs(i, Decimal("20"), Decimal("21"), Decimal("19")) for i in range(20)]
    bars.append(obs(20, Decimal("18"), Decimal("20"), Decimal("1")))
    signal = DonchianBreakoutStrategy().decide(context(bars, in_position=True))
    assert signal.action is SignalAction.SELL
    assert "prior 10-bar low" in signal.reason


def test_mean_reversion_uses_decimal_population_deviation_and_ema200_filter():
    bars = [obs(i, Decimal("50") + Decimal(i) / Decimal("4")) for i in range(180)]
    for i, price in enumerate((Decimal("99"), Decimal("100"), Decimal("101"), Decimal("100")) * 5):
        bars.append(obs(180 + i, price))
    bars.append(obs(200, Decimal("97")))
    signal = MeanReversionStrategy().decide(context(bars))
    assert signal.action is SignalAction.BUY
    assert "EMA200" in signal.reason


def _market_series(symbol: str, values: list[Decimal]) -> list[MarketData]:
    return [item(i, value, symbol=symbol) for i, value in enumerate(values)]


def test_shared_portfolio_has_one_cash_ledger_and_at_most_one_position():
    rising = [Decimal("100") + Decimal(i) for i in range(240)]
    flat = [Decimal("100") for _ in range(240)]
    result = run_shared_portfolio(
        {"AAAUSDT": _market_series("AAAUSDT", rising),
         "BBBUSDT": _market_series("BBBUSDT", flat)},
        PortfolioConfig(initial_cash=Decimal("1000")),
    )
    assert len(result.equity_curve) > 0
    assert result.final_cash >= 0
    assert result.final_symbol in (None, "AAAUSDT", "BBBUSDT")
    for point in result.equity_curve:
        marked = point.cash + (point.quantity * point.mark_price if point.symbol else Decimal("0"))
        assert point.equity == marked
    assert len([fill for fill in result.fills if fill.side == "BUY"]) <= 1


def test_shared_portfolio_refuses_missing_or_misaligned_history():
    series = _market_series("AAAUSDT", [Decimal("100") + Decimal(i) for i in range(240)])
    other = _market_series("BBBUSDT", [Decimal("100") for _ in range(240)])
    with pytest.raises(ValueError, match="gap or interval mismatch"):
        run_shared_portfolio({"AAAUSDT": series, "BBBUSDT": other[:100] + other[101:]})


def test_ema_rsi_strategy_adapter_preserves_legacy_ledger():
    # Regression fixture uses the same small EMA/RSI configuration as the original engine.
    values = [Decimal(x) for x in ("10", "9", "8", "9", "10", "11", "12", "11", "10", "9", "8", "7", "6")]
    items = [item(i, value, interval="1m") for i, value in enumerate(values)]
    cfg = BacktestConfig(
        symbol="BTCUSDT", interval="1m", candles=13, fast_ema_period=2, slow_ema_period=3,
        rsi_period=2, rsi_buy_min=Decimal("0"), rsi_sell_at=Decimal("100"),
        initial_quote_balance=Decimal("10000"), position_fraction=Decimal("1"),
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("5"),
    )
    legacy = run_virtual_backtest(items, cfg)
    adapted = run_virtual_backtest(items, cfg, strategy=EmaRsiStrategy(
        fast_period=2, slow_period=3, rsi_period=2,
        rsi_buy_min=Decimal("0"), rsi_sell_at=Decimal("100")))
    assert adapted.fills == legacy.fills
    assert adapted.equity_curve == legacy.equity_curve
    assert adapted.final_quote_balance == legacy.final_quote_balance

def test_cost_scenarios_change_costs_but_not_deterministic_ema_rsi_signals():
    from dataclasses import replace

    values = [Decimal(x) for x in ("10", "9", "8", "9", "10", "11", "12", "11", "10", "9", "8", "7", "6")]
    items = [item(i, value, interval="1m") for i, value in enumerate(values)]
    cfg = BacktestConfig(
        symbol="BTCUSDT", interval="1m", candles=13, fast_ema_period=2, slow_ema_period=3,
        rsi_period=2, rsi_buy_min=Decimal("0"), rsi_sell_at=Decimal("100"),
        initial_quote_balance=Decimal("10000"), position_fraction=Decimal("1"),
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("5"), spread_bps=Decimal("10"),
    )
    strategy = EmaRsiStrategy(fast_period=2, slow_period=3, rsi_period=2,
                              rsi_buy_min=Decimal("0"), rsi_sell_at=Decimal("100"))
    results = [
        run_virtual_backtest(items, replace(cfg, fee_rate=fee, spread_bps=spread, slippage_bps=slip),
                             strategy=strategy)
        for fee, spread, slip in (
            (Decimal("0.001"), Decimal("10"), Decimal("5")),
            (Decimal("0.001"), Decimal("25"), Decimal("10")),
            (Decimal("0.002"), Decimal("50"), Decimal("20")),
        )
    ]
    signatures = [[(fill.timestamp, fill.side, fill.reason) for fill in result.fills] for result in results]
    assert signatures[0] == signatures[1] == signatures[2]
    assert results[0].total_fees != results[2].total_fees


def test_daily_equity_metrics_reports_deterministic_complete_day_drawdown():
    from bot_obrero.product_v2 import daily_equity_metrics

    closes = [Decimal(x) for x in ("10", "9", "8", "9", "10", "11", "12", "11", "10", "9", "8", "7")]
    items = [item(i, value, interval="4h") for i, value in enumerate(closes)]
    cfg = BacktestConfig(
        symbol="BTCUSDT", interval="4h", candles=12, fast_ema_period=2, slow_ema_period=3,
        rsi_period=2, rsi_buy_min=Decimal("0"), rsi_sell_at=Decimal("100"),
        initial_quote_balance=Decimal("10000"), position_fraction=Decimal("1"),
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("5"), spread_bps=Decimal("10"),
    )
    result = run_virtual_backtest(items, cfg)
    rows, summary = daily_equity_metrics(result)
    assert summary["complete_days"] == 2
    assert summary["max_daily_close_drawdown_pct"] == min(row["daily_drawdown"] for row in rows) * Decimal("100")
    assert summary["net_pnl"] == result.final_equity - cfg.initial_quote_balance
