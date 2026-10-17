from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from bot_obrero.binance_instruments import InstrumentNotFound
from bot_obrero.market_data import (
    Candle, CandleFinality, CandleState, DataCompleteness, DataQuality,
    InstrumentIdentity, InstrumentType, MarketData, SourceIdentity,
)
from bot_obrero.virtual_backtest import (
    BacktestConfig, BacktestError, _indicator_series, _indicators, _observation, main,
    run_virtual_backtest,
)

_MISSING = object()
_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _item(index: int, close: str, *, state=CandleState.CLOSED,
          candle_completeness=DataCompleteness.COMPLETE,
          market_completeness=DataCompleteness.UNKNOWN,
          quality=DataQuality.VALID, finality=CandleFinality.UNKNOWN,
          available_at=_MISSING, open_price: str | None = None) -> MarketData:
    start = _START + timedelta(minutes=index)
    end = start + timedelta(minutes=1) - timedelta(milliseconds=1)
    price = Decimal(close)
    opening = Decimal(open_price) if open_price is not None else price
    candle = Candle(
        start=start, end=end, timeframe="1m",
        open=opening, high=max(opening, price), low=min(opening, price), close=price,
        volume=Decimal("1"), candle_state=state, completeness=candle_completeness, finality=finality,
    )
    instrument = InstrumentIdentity(
        instrument_id="binance:SPOT:BTCUSDT", symbol="BTC/USDT", market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT, base_asset="BTC", quote_asset="USDT",
    )
    source = SourceIdentity(source_id="binance-spot-rest", provider="binance", venue="BINANCE")
    return MarketData(
        instrument=instrument, source=source, data_type="CANDLE", observed_at=start, received_at=end,
        available_at=None if available_at is _MISSING else available_at,
        payload=candle, quality=quality, completeness=market_completeness,
    )


def _series(prices=("10", "9", "8", "9", "10", "11", "12", "11", "10", "9", "8", "7", "6")):
    return [_item(i, value) for i, value in enumerate(prices)]


def _config(**overrides):
    values = dict(
        symbol="BTCUSDT", interval="1m", candles=13, fast_ema_period=2, slow_ema_period=3,
        rsi_period=2, rsi_buy_min=Decimal("0"), rsi_sell_at=Decimal("100"),
        initial_quote_balance=Decimal("10000"), position_fraction=Decimal("1"),
        fee_rate=Decimal("0.001"), slippage_bps=Decimal("5"),
    )
    values.update(overrides)
    return BacktestConfig(**values)


def test_existing_indicator_algorithms_return_known_sma_ema_rsi_values():
    observations = [_observation(_item(i, value)) for i, value in enumerate(("1", "2", "3"))]
    values = _indicators(observations, _config(candles=5))
    assert values["sma"] == Decimal("2")
    assert values["fast_ema"] == Decimal("2.5")
    assert values["slow_ema"] == Decimal("2")
    assert values["rsi"] == Decimal("100")


def test_full_virtual_simulation_accounts_for_buy_sell_fees_pnl_equity_and_drawdown():
    items = _series()
    result = run_virtual_backtest(items, _config())
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert result.fills[0].timestamp == items[5].payload.start
    assert result.fills[1].timestamp > result.fills[0].timestamp
    assert all(fill.fee > 0 for fill in result.fills)
    assert result.final_quote_balance > 0
    assert all(fill.quote_balance_after >= 0 for fill in result.fills)
    assert result.final_quote_balance >= 0
    assert result.final_base_position == 0
    assert result.total_fees == sum((fill.fee for fill in result.fills), Decimal("0"))
    assert result.realized_pnl == result.fills[-1].realized_pnl
    assert result.final_equity == result.final_quote_balance
    assert len(result.equity_curve) == len(items)
    assert result.max_drawdown <= 0
    assert result.final_sma == Decimal("7")
    assert result.final_rsi == Decimal("1.46484375000000000000000001")


def test_backtest_is_reproducible_for_same_history_and_parameters():
    items = _series()
    assert run_virtual_backtest(items, _config()) == run_virtual_backtest(items, _config())


def test_flat_market_produces_no_trades_and_preserves_balance():
    result = run_virtual_backtest([_item(i, "10") for i in range(13)], _config())
    assert result.fills == ()
    assert result.final_quote_balance == Decimal("10000")
    assert result.final_equity == Decimal("10000")
    assert result.total_fees == 0


@pytest.mark.parametrize("items, message", [
    (lambda: [_item(0, "1"), _item(0, "2"), *_series()[1:]], "duplicate"),
    (lambda: [_item(0, "1"), _item(1, "2"), _item(3, "4"), *_series()[4:]], "gap"),
    (lambda: [_item(0, "1"), _item(1, "2", state=CandleState.UNKNOWN), *_series()[2:]], "unknown candle state"),
    (lambda: [_item(0, "1"), _item(1, "2", candle_completeness=DataCompleteness.UNKNOWN), *_series()[2:]], "unknown candle completeness"),
    (lambda: [_item(0, "1"), _item(1, "2", quality=DataQuality.UNKNOWN), *_series()[2:]], "quality"),
])
def test_inadmissible_history_fails_closed(items, message):
    with pytest.raises(BacktestError, match=message):
        run_virtual_backtest(items(), _config())


def test_final_open_candle_is_excluded_but_internal_partial_is_rejected():
    items = _series()
    items[-1] = _item(12, "6", state=CandleState.OPEN,
                      candle_completeness=DataCompleteness.PARTIAL,
                      market_completeness=DataCompleteness.PARTIAL)
    result = run_virtual_backtest(items, _config(candles=12))
    assert result.excluded_final_candles == 1
    assert result.candle_count == 12
    internal = _series()
    internal[4] = _item(4, "10", state=CandleState.OPEN,
                        candle_completeness=DataCompleteness.PARTIAL,
                        market_completeness=DataCompleteness.PARTIAL)
    with pytest.raises(BacktestError, match="internal"):
        run_virtual_backtest(internal, _config())


def test_explicit_none_availability_remains_unknown_and_late_known_availability_is_rejected():
    item = _item(0, "10", available_at=None)
    assert item.available_at is None
    items = _series()
    items[1] = _item(1, "9", available_at=_START + timedelta(minutes=3))
    with pytest.raises(BacktestError, match="not available by simulated bar close"):
        run_virtual_backtest(items, _config())


@pytest.mark.parametrize("overrides", [
    {"slow_ema_period": 2, "fast_ema_period": 2},
    {"fee_rate": Decimal("0.1")},
    {"position_fraction": Decimal("1.1")},
    {"slippage_bps": Decimal("1001")},
    {"candles": 1001},
    {"candles": 4},
])
def test_invalid_parameters_are_rejected(overrides):
    with pytest.raises(BacktestError):
        run_virtual_backtest(_series(), _config(**overrides))


def test_unknown_envelope_completeness_and_finality_are_reported_not_rewritten():
    result = run_virtual_backtest(_series(), _config())
    assert result.unknown_marketdata_completeness_count == 13
    assert result.unknown_finality_count == 13
    observation = _observation(_series()[0])
    assert observation.available_timestamp == _series()[0].payload.end
    assert observation.provenance.metadata["canonical_available_at"] == "UNKNOWN"


def test_command_fetches_historical_data_and_prints_full_report(monkeypatch, capsys):
    from bot_obrero import virtual_backtest
    items = _series()
    metadata = Mock()
    metadata.resolve_active.return_value = items[0].instrument
    adapter = Mock()
    adapter.fetch_historical_market_data.return_value = items
    monkeypatch.setattr(virtual_backtest, "BinanceSpotInstrumentMetadata", Mock(return_value=metadata))
    monkeypatch.setattr(virtual_backtest, "BinanceMetadataBackedAdapter", Mock(return_value=adapter))
    monkeypatch.setattr(virtual_backtest, "_utc_now", Mock(return_value=datetime(2026, 1, 2, tzinfo=timezone.utc)))
    code = main(["--symbol", "BTCUSDT", "--interval", "1m", "--candles", "13",
                 "--fast-ema-period", "2", "--slow-ema-period", "3", "--rsi-period", "2",
                 "--rsi-buy-min", "0", "--rsi-sell-at", "100"])
    output = capsys.readouterr().out
    assert code == 0
    assert "BOT-CRIPTO — VIRTUAL HISTORICAL BACKTEST" in output
    assert "Virtual fills:" in output
    assert "Final SMA" in output and "Final RSI" in output
    assert "Final equity" in output and "Maximum drawdown" in output
    assert "available_at is UNKNOWN" in output
    adapter.fetch_historical_market_data.assert_called_once()
    assert adapter.fetch_historical_market_data.call_args.kwargs["max_requests"] <= 3


def test_command_reports_unknown_symbol_without_success_report(monkeypatch, capsys):
    from bot_obrero import virtual_backtest
    metadata = Mock()
    metadata.resolve_active.side_effect = InstrumentNotFound("symbol missing")
    monkeypatch.setattr(virtual_backtest, "BinanceSpotInstrumentMetadata", Mock(return_value=metadata))
    adapter = Mock()
    monkeypatch.setattr(virtual_backtest, "BinanceMetadataBackedAdapter", Mock(return_value=adapter))
    code = main(["--symbol", "BTCUSDT", "--interval", "1m", "--candles", "13",
                 "--fast-ema-period", "2", "--slow-ema-period", "3", "--rsi-period", "2"])
    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "VIRTUAL HISTORICAL BACKTEST" not in captured.out
    adapter.fetch_historical_market_data.assert_not_called()



def test_command_reports_historical_transport_failure_without_success_report(monkeypatch, capsys):
    from bot_obrero import virtual_backtest
    item = _series()[0]
    metadata = Mock()
    metadata.resolve_active.return_value = item.instrument
    adapter = Mock()
    adapter.fetch_historical_market_data.side_effect = TimeoutError("simulated public REST timeout")
    monkeypatch.setattr(virtual_backtest, "BinanceSpotInstrumentMetadata", Mock(return_value=metadata))
    monkeypatch.setattr(virtual_backtest, "BinanceMetadataBackedAdapter", Mock(return_value=adapter))
    monkeypatch.setattr(virtual_backtest, "_utc_now", Mock(return_value=datetime(2026, 1, 2, tzinfo=timezone.utc)))
    code = main(["--symbol", "BTCUSDT", "--interval", "1m", "--candles", "13",
                 "--fast-ema-period", "2", "--slow-ema-period", "3", "--rsi-period", "2"])
    captured = capsys.readouterr()
    assert code == 2
    assert "simulated public REST timeout" in captured.err
    assert "VIRTUAL HISTORICAL BACKTEST" not in captured.out

def test_module_does_not_import_or_invoke_financial_execution():
    import inspect
    import bot_obrero.virtual_backtest as module
    assert "StrategyRuntime" not in module.__dict__
    assert "FinancialExecutionRuntime" not in module.__dict__
    assert "TradeProposal" not in module.__dict__
    source = inspect.getsource(module)
    assert "/api/v3/order" not in source
    assert "/api/v3/account" not in source


def test_spread_is_charged_on_both_sides_and_worsens_virtual_execution():
    items = _series()
    no_spread = run_virtual_backtest(items, _config(spread_bps=Decimal("0")))
    wide_spread = run_virtual_backtest(items, _config(spread_bps=Decimal("100")))
    assert [fill.side for fill in no_spread.fills] == ["BUY", "SELL"]
    assert [fill.side for fill in wide_spread.fills] == ["BUY", "SELL"]
    assert wide_spread.fills[0].price > no_spread.fills[0].price
    assert wide_spread.fills[1].price < no_spread.fills[1].price
    assert wide_spread.final_equity < no_spread.final_equity


def test_virtual_buy_respects_quantity_step_and_minimum_notional():
    items = _series()
    result = run_virtual_backtest(
        items,
        _config(quantity_step=Decimal("0.1"), min_qty=Decimal("0.1"), min_notional=Decimal("1")),
    )
    assert result.fills[0].quantity % Decimal("0.1") == 0
    blocked = run_virtual_backtest(
        items,
        _config(min_notional=Decimal("1000000")),
    )
    assert blocked.fills == ()
    assert any("ORDER_REJECTED:MIN_NOTIONAL" in reason for reason in blocked.entry_rejections)
    assert blocked.final_quote_balance == blocked.config.initial_quote_balance
    assert blocked.final_equity >= Decimal("0")
    min_qty_blocked = run_virtual_backtest(items, _config(min_qty=Decimal("1000000")))
    assert min_qty_blocked.fills == ()
    assert any("ORDER_REJECTED:MIN_QTY" in reason for reason in min_qty_blocked.entry_rejections)


def test_virtual_backtest_rejects_invalid_instrument_filters():
    with pytest.raises(BacktestError, match="quantity_step"):
        run_virtual_backtest(_series(), _config(quantity_step=Decimal("0")))


def test_backtest_enforces_maximum_five_buy_entries_in_any_rolling_30_days():
    pattern = ("10", "9", "8", "9", "10", "11", "12", "11", "10", "9", "8", "7", "6")
    prices = pattern * 10
    items = [_item(i, value) for i, value in enumerate(prices)]
    result = run_virtual_backtest(
        items,
        _config(candles=len(items), max_entries_per_30d=5),
    )
    buys = [fill for fill in result.fills if fill.side == "BUY"]
    assert len(buys) == 5
    assert buys[-1].timestamp - buys[0].timestamp < timedelta(days=30)



def test_entry_gate_only_controls_buys_and_does_not_block_exits():
    items = _series()
    result = run_virtual_backtest(items, _config(), entry_admission=lambda index, observations: (True, "historical BTC gates passed"))
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert "historical BTC gates passed" in result.fills[0].reason
    assert result.fills[-1].side == "SELL"


def test_evaluation_window_excludes_warmup_from_metrics():
    items = _series()
    result = run_virtual_backtest(items, _config(candles=6),
        evaluation_start=items[6].payload.start, evaluation_end=items[12].payload.start)
    assert result.candle_count == 6
    assert result.first_candle == items[6].payload.start
    assert result.last_candle == items[11].payload.start
    assert len(result.equity_curve) == 6



def test_rejected_entry_is_recorded_and_no_buy_is_filled():
    items = _series()
    result = run_virtual_backtest(
        items, _config(),
        entry_admission=lambda index, observations: (False, "BTC_REGIME_NOT_BULLISH"),
    )
    assert not any(fill.side == "BUY" for fill in result.fills)
    assert result.entry_rejections
    assert all("BTC_REGIME_NOT_BULLISH" in reason for reason in result.entry_rejections)



def test_baseline_and_btc_gated_backtests_remain_distinct_when_entry_is_rejected():
    items = _series()
    baseline = run_virtual_backtest(items, _config())
    integrated = run_virtual_backtest(
        items, _config(),
        entry_admission=lambda index, observations: (False, "BTC_REGIME_NOT_BULLISH"),
    )

    assert [fill.side for fill in baseline.fills] == ["BUY", "SELL"]
    assert integrated.fills == ()
    assert integrated.entry_rejections
    assert all("BTC_REGIME_NOT_BULLISH" in reason for reason in integrated.entry_rejections)
    assert baseline.final_equity != integrated.final_equity



def test_incremental_indicator_series_matches_reference_algorithms_for_every_prefix():
    prices = tuple(str(100 + ((index * 17) % 23) - index // 7) for index in range(80))
    observations = [_observation(_item(i, value)) for i, value in enumerate(prices)]
    config = _config(candles=len(observations), fast_ema_period=3, slow_ema_period=7, rsi_period=5)
    series = _indicator_series(observations, config)
    for index, cached in enumerate(series):
        if index + 1 < max(config.slow_ema_period, config.rsi_period + 1):
            assert cached is None
            continue
        assert cached == _indicators(observations[:index + 1], config)


def test_annual_hourly_evaluation_length_is_accepted_without_splitting_the_run():
    config = BacktestConfig(symbol="ETHUSDT", interval="1h", candles=8760)
    config.validate()
    assert config.candles == 8760


def test_terminal_liquidation_closes_remaining_position_and_charges_exit_costs():
    prices = ("20", "19", "18", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31")
    items = [_item(i, value, open_price=value) for i, value in enumerate(prices)]
    regular = run_virtual_backtest(items, _config(rsi_sell_at=Decimal("100")))
    liquidated = run_virtual_backtest(
        items, _config(rsi_sell_at=Decimal("100"), liquidate_at_end=True),
    )
    assert regular.final_base_position > 0
    assert liquidated.final_base_position == 0
    assert liquidated.fills[-1].side == "SELL"
    assert liquidated.fills[-1].reason == "TERMINAL_LIQUIDATION"
    assert liquidated.fills[-1].fee > 0
    assert liquidated.final_equity == liquidated.final_quote_balance
    assert liquidated.unrealized_pnl == 0
    assert liquidated.total_fees > regular.total_fees
    assert liquidated.final_equity < regular.final_equity
