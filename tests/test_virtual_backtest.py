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
    BacktestConfig, BacktestError, _configuration_record, _evidence_hashes, _indicator_series,
    _indicators, _market_data_record, _observation, _read_dataset_bundle, main, run_virtual_backtest,
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


def _eth_hourly_item(index: int, close: str, *, open_price: str | None = None) -> MarketData:
    start = _START + timedelta(hours=index)
    end = start + timedelta(hours=1) - timedelta(milliseconds=1)
    price = Decimal(close)
    opening = Decimal(open_price) if open_price is not None else price
    candle = Candle(
        start=start, end=end, timeframe="1h",
        open=opening, high=max(opening, price), low=min(opening, price), close=price,
        volume=Decimal("1"), candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE, finality=CandleFinality.UNKNOWN,
    )
    instrument = InstrumentIdentity(
        instrument_id="binance:SPOT:ETHUSDT", symbol="ETH/USDT", market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT, base_asset="ETH", quote_asset="USDT",
    )
    source = SourceIdentity(source_id="binance-spot-rest", provider="binance", venue="BINANCE")
    return MarketData(
        instrument=instrument, source=source, data_type="CANDLE",
        observed_at=start, received_at=end, available_at=None,
        payload=candle, quality=DataQuality.VALID, completeness=DataCompleteness.UNKNOWN,
    )


def _eth_four_hour_item(index: int, close: str, *, open_price: str | None = None) -> MarketData:
    start = _START + timedelta(hours=4 * index)
    end = start + timedelta(hours=4) - timedelta(milliseconds=1)
    price = Decimal(close)
    opening = Decimal(open_price) if open_price is not None else price
    candle = Candle(
        start=start, end=end, timeframe="4h",
        open=opening, high=max(opening, price), low=min(opening, price), close=price,
        volume=Decimal("1"), candle_state=CandleState.CLOSED,
        completeness=DataCompleteness.COMPLETE, finality=CandleFinality.UNKNOWN,
    )
    instrument = InstrumentIdentity(
        instrument_id="binance:SPOT:ETHUSDT", symbol="ETH/USDT", market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT, base_asset="ETH", quote_asset="USDT",
    )
    source = SourceIdentity(source_id="binance-spot-rest", provider="binance", venue="BINANCE")
    return MarketData(
        instrument=instrument, source=source, data_type="CANDLE",
        observed_at=start, received_at=end, available_at=None,
        payload=candle, quality=DataQuality.VALID, completeness=DataCompleteness.UNKNOWN,
    )


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


def test_open_position_equity_curve_uses_net_liquidation_value_for_drawdown():
    prices = ("20", "19", "18", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31")
    items = [_item(i, value, open_price=value) for i, value in enumerate(prices)]
    config = _config(
        rsi_sell_at=Decimal("100"),
        spread_bps=Decimal("50"),
        slippage_bps=Decimal("10"),
    )
    result = run_virtual_backtest(items, config)
    point = result.equity_curve[-1]

    assert result.final_base_position > 0
    adverse = (config.slippage_bps + config.spread_bps / Decimal("2")) / Decimal("10000")
    hypothetical_sell_price = point.close * (Decimal("1") - adverse)
    hypothetical_sell_notional = point.base_position * hypothetical_sell_price
    net_liquidation_value = hypothetical_sell_notional - hypothetical_sell_notional * config.fee_rate
    expected_equity = point.quote_balance + net_liquidation_value
    entry = next(fill for fill in result.fills if fill.side == "BUY")
    entry_cost = entry.quantity * entry.price + entry.fee

    assert point.equity == expected_equity
    assert point.equity < point.quote_balance + point.base_position * point.close
    assert result.final_equity == expected_equity
    assert result.unrealized_pnl == net_liquidation_value - entry_cost


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
    assert liquidated.final_equity == regular.final_equity
    assert regular.final_equity < regular.final_quote_balance + regular.final_base_position * items[-1].payload.close
    assert regular.unrealized_pnl < regular.final_base_position * items[-1].payload.close



def test_normalized_dataset_bundle_reloads_identical_candles_and_validates_hashes(tmp_path):
    import hashlib
    import json

    items = _series()
    config = _config(candles=6)
    evaluation_start = items[6].payload.start
    evaluation_end = items[12].payload.start
    dataset_hash, config_hash, records = _evidence_hashes(items, config)
    bundle = {
        "schema_version": 1,
        "acquired_at": _START.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "dataset_sha256": dataset_hash,
        "configuration_sha256": config_hash,
        "configuration": _configuration_record(config),
        "data": records,
    }
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(bundle, sort_keys=True, indent=2), encoding="utf-8")

    restored, restored_start, restored_end = _read_dataset_bundle(path, config)
    restored_hash, _, restored_records = _evidence_hashes(restored, config)
    assert restored_hash == dataset_hash
    assert restored_records == records
    assert restored_start == evaluation_start
    assert restored_end == evaluation_end
    assert len(restored) == len(items)

    bundle["data"][0]["candle"]["close"] = "999"
    path.write_text(json.dumps(bundle, sort_keys=True, indent=2), encoding="utf-8")
    with pytest.raises(BacktestError, match="data hash mismatch"):
        _read_dataset_bundle(path, config)



def test_virtual_backtest_rejects_quantity_over_market_max_quantity():
    result = run_virtual_backtest(_series(), _config(max_qty=Decimal("0.01")))
    assert not result.fills
    assert any("ORDER_REJECTED:MAX_QTY" in reason for reason in result.entry_rejections)


def test_backtest_metrics_are_invariant_to_suffix_strictly_after_evaluation_window():
    """Changing unseen future bars must not rewrite decisions or fills before the cutoff."""
    items = _series((
        "10", "9", "8", "9", "10", "11", "12", "11", "10", "9",
        "8", "7", "6", "80", "0.5",
    ))
    start_index, end_index = 3, 10
    start, end = items[start_index].payload.start, items[end_index].payload.start
    config = _config(candles=end_index - start_index, liquidate_at_end=True)

    changed_future = list(items)
    for index, future_close in ((10, "900"), (11, "0.05"), (12, "700"), (13, "0.01"), (14, "5000")):
        changed_future[index] = _item(index, future_close)

    baseline = run_virtual_backtest(
        items, config, evaluation_start=start, evaluation_end=end,
    )
    perturbed = run_virtual_backtest(
        changed_future, config, evaluation_start=start, evaluation_end=end,
    )
    assert baseline == perturbed



def test_bbmr_top1_is_locked_to_eth_hourly_and_ema720():
    config = BacktestConfig(
        symbol="BTCUSDT", interval="1h", strategy="eth-bbmr-top1",
        candles=3, slow_ema_period=720,
    )
    with pytest.raises(BacktestError, match="frozen for ETHUSDT on 1h"):
        config.validate()


def test_bbmr_top1_generates_causal_signal_and_fills_only_on_next_bar_open():
    # The first 553 closes trend from 100 to 150; the next 167 closes are 200.
    # At index 720, 170 is a >2.5-sigma dip but remains above EMA720.
    baseline = [
        str(Decimal("100") + Decimal(index) * Decimal("50") / Decimal("552"))
        for index in range(553)
    ] + ["200"] * 167
    prices = baseline + ["170", "210", "210"]
    items = [
        _eth_hourly_item(
            index, close,
            open_price=("170" if index == 721 else "210" if index == 722 else close),
        )
        for index, close in enumerate(prices)
    ]
    config = BacktestConfig(
        symbol="ETHUSDT", interval="1h", strategy="eth-bbmr-top1",
        candles=3, fast_ema_period=12, slow_ema_period=720, rsi_period=14,
        initial_quote_balance=Decimal("60"), position_fraction=Decimal("0.95"),
        fee_rate=Decimal("0"), spread_bps=Decimal("0"), slippage_bps=Decimal("0"),
    )
    result = run_virtual_backtest(
        items, config,
        evaluation_start=items[720].payload.start,
        evaluation_end=_START + timedelta(hours=723),
    )
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert result.fills[0].timestamp == items[721].payload.start
    assert result.fills[0].price == Decimal("170")
    assert result.fills[0].reason.startswith("BBMR z-score below -2.5")
    assert result.fills[1].timestamp == items[722].payload.start
    assert result.fills[1].price == Decimal("210")
    assert result.fills[1].reason == "BBMR z-score above +1.0"
    assert result.final_quote_balance > Decimal("60")
    assert result.final_base_position == Decimal("0")
    assert result.final_bollinger_zscore is not None
    assert result.unknown_finality_count == len(items)


def test_bbmr_top1_constant_closes_do_not_divide_by_zero_or_trade():
    prices = ["200"] * 723
    items = [_eth_hourly_item(index, close) for index, close in enumerate(prices)]
    config = BacktestConfig(
        symbol="ETHUSDT", interval="1h", strategy="eth-bbmr-top1",
        candles=3, slow_ema_period=720, initial_quote_balance=Decimal("60"),
        position_fraction=Decimal("0.95"),
    )
    result = run_virtual_backtest(
        items, config,
        evaluation_start=items[720].payload.start,
        evaluation_end=_START + timedelta(hours=723),
    )
    assert result.fills == ()
    assert result.final_quote_balance == Decimal("60")
    assert result.final_bollinger_zscore is None



def test_bbmr_4h_candidate_is_locked_to_eth_four_hour_and_ema180():
    config = BacktestConfig(
        symbol="ETHUSDT", interval="1h", strategy="eth-bbmr-4h-research",
        candles=3, slow_ema_period=180,
    )
    with pytest.raises(BacktestError, match="frozen for ETHUSDT on 4h"):
        config.validate()


def test_bbmr_4h_candidate_generates_causal_signal_and_fills_next_bar_open():
    # EMA180 is already above the start-price regime after the sustained rise.
    # At index 180, 190 is a deep 48-bar z-score dip but remains above EMA180.
    baseline = [
        str(Decimal("100") + Decimal(index) * Decimal("50") / Decimal("120"))
        for index in range(121)
    ] + ["200"] * 59
    prices = baseline + ["190", "220", "220"]
    items = [
        _eth_four_hour_item(
            index, close,
            open_price=("190" if index == 181 else "220" if index == 182 else close),
        )
        for index, close in enumerate(prices)
    ]
    config = BacktestConfig(
        symbol="ETHUSDT", interval="4h", strategy="eth-bbmr-4h-research",
        candles=3, slow_ema_period=180, initial_quote_balance=Decimal("60"),
        position_fraction=Decimal("0.95"), fee_rate=Decimal("0"),
        spread_bps=Decimal("0"), slippage_bps=Decimal("0"),
    )
    result = run_virtual_backtest(
        items, config,
        evaluation_start=items[180].payload.start,
        evaluation_end=_START + timedelta(hours=4 * 183),
    )
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert result.fills[0].timestamp == items[181].payload.start
    assert result.fills[0].price == Decimal("190")
    assert result.fills[0].reason.startswith("BBMR z-score below -0.75")
    assert result.fills[1].timestamp == items[182].payload.start
    assert result.fills[1].price == Decimal("220")
    assert result.fills[1].reason == "BBMR z-score above 2.0"
    assert result.final_quote_balance > Decimal("60")
    assert result.final_base_position == Decimal("0")
    assert result.final_bollinger_zscore is not None
    assert result.unknown_finality_count == len(items)


def test_bbmr_4h_candidate_constant_closes_do_not_divide_by_zero_or_trade():
    items = [_eth_four_hour_item(index, "200") for index in range(183)]
    config = BacktestConfig(
        symbol="ETHUSDT", interval="4h", strategy="eth-bbmr-4h-research",
        candles=3, slow_ema_period=180, initial_quote_balance=Decimal("60"),
        position_fraction=Decimal("0.95"),
    )
    result = run_virtual_backtest(
        items, config,
        evaluation_start=items[180].payload.start,
        evaluation_end=_START + timedelta(hours=4 * 183),
    )
    assert result.fills == ()
    assert result.final_quote_balance == Decimal("60")
    assert result.final_bollinger_zscore is None
