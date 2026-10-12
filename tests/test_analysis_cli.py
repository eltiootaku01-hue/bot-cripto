from __future__ import annotations

import importlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_MISSING = object()

from bot_obrero.analysis_cli import (
    AnalysisCLIError,
    _build_config,
    _closed_complete_items,
    _expected_next_start,
    _parser,
    _validate_args,
)
from bot_obrero.binance_spot import BinanceRateLimitError
from bot_obrero.binance_instruments import InstrumentInactive, InstrumentNotFound
from bot_obrero.market_data import (
    Candle,
    CandleFinality,
    CandleState,
    DataCompleteness,
    DataQuality,
    InstrumentIdentity,
    InstrumentType,
    MarketData,
    SourceIdentity,
)


def _item(start: datetime, *, state=CandleState.CLOSED,
          candle_completeness=DataCompleteness.COMPLETE,
          data_completeness=DataCompleteness.COMPLETE,
          quality=DataQuality.VALID, finality=CandleFinality.FINAL,
          available_at=_MISSING, close="10") -> MarketData:
    instrument = InstrumentIdentity(
        instrument_id="binance:SPOT:BTCUSDT",
        symbol="BTC/USDT",
        market="SPOT",
        instrument_type=InstrumentType.CRYPTO_SPOT,
        base_asset="BTC",
        quote_asset="USDT",
    )
    source = SourceIdentity(source_id="binance-spot-rest", provider="binance", venue="BINANCE")
    candle = Candle(
        start=start,
        end=start + timedelta(minutes=1),
        timeframe="1m",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        candle_state=state,
        completeness=candle_completeness,
        finality=finality,
    )
    return MarketData(
        instrument=instrument,
        source=source,
        data_type="CANDLE",
        observed_at=start + timedelta(minutes=1),
        received_at=start + timedelta(minutes=1),
        available_at=(start + timedelta(minutes=1) if available_at is _MISSING else available_at),
        payload=candle,
        quality=quality,
        completeness=data_completeness,
    )


def test_help_does_not_need_network(capsys):
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["--help"])
    assert exc.value.code == 0
    assert "Binance Spot" in capsys.readouterr().out


@pytest.mark.parametrize("symbol", ["btcUSDT", "BTC/USDT", "BTC USDT", ""])
def test_invalid_symbol_rejected_before_network(symbol, monkeypatch):
    from bot_obrero import analysis_cli
    network = Mock(side_effect=AssertionError("network must not be called"))
    monkeypatch.setattr(analysis_cli, "BinanceSpotInstrumentMetadata", network)
    args = _parser().parse_args(["--symbol", symbol, "--interval", "1m"])
    with pytest.raises(AnalysisCLIError, match="symbol"):
        _validate_args(args)
    network.assert_not_called()


@pytest.mark.parametrize("argv", [
    ["--symbol", "BTCUSDT", "--interval", "bogus"],
    ["--symbol", "BTCUSDT", "--interval", "1m", "--candles", "0"],
    ["--symbol", "BTCUSDT", "--interval", "1m", "--candles", "1000"],
    ["--symbol", "BTCUSDT", "--interval", "1m", "--candles", "14", "--rsi-period", "14"],
])
def test_invalid_interval_or_periods_rejected(argv):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        assert exc.code == 2
        return
    with pytest.raises(AnalysisCLIError):
        _validate_args(args)


def test_interval_step_handles_minutes_and_calendar_month():
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert _expected_next_start(start, "1m") == start + timedelta(minutes=1)
    assert _expected_next_start(start, "1s") == start + timedelta(seconds=1)
    assert _expected_next_start(start, "1M") == datetime(2026, 2, 28, tzinfo=timezone.utc)


def test_one_second_interval_is_accepted_and_continuity_is_checked():
    from bot_obrero.binance_intervals import validate_binance_spot_interval
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    validate_binance_spot_interval("1s")
    items = [_item(start), _item(start + timedelta(seconds=1))]
    admitted, excluded = _closed_complete_items(items, "1s")
    assert admitted == items
    assert excluded == 0
    with pytest.raises(AnalysisCLIError, match="gap or interval mismatch"):
        _closed_complete_items([items[0], _item(start + timedelta(seconds=2))], "1s")


def test_closed_complete_candles_are_admissible():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = [_item(start), _item(start + timedelta(minutes=1))]
    admitted, excluded = _closed_complete_items(items, "1m")
    assert admitted == items
    assert excluded == 0


def test_final_open_candle_is_excluded_with_count():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = [
        _item(start),
        _item(start + timedelta(minutes=1), state=CandleState.OPEN,
              candle_completeness=DataCompleteness.PARTIAL,
              data_completeness=DataCompleteness.PARTIAL,
              finality=CandleFinality.NOT_FINAL),
    ]
    admitted, excluded = _closed_complete_items(items, "1m")
    assert len(admitted) == 1
    assert excluded == 1


@pytest.mark.parametrize("items", [
    lambda s: [_item(s), _item(s)],
    lambda s: [_item(s), _item(s + timedelta(minutes=2))],
    lambda s: [_item(s + timedelta(minutes=1)), _item(s)],
    lambda s: [_item(s), _item(s + timedelta(minutes=1), quality=DataQuality.UNKNOWN)],
    lambda s: [
        _item(s),
        _item(s + timedelta(minutes=1), candle_completeness=DataCompleteness.PARTIAL),
        _item(s + timedelta(minutes=2)),
    ],
])
def test_duplicate_gap_unordered_or_internal_inadmissible_data_stops(items):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(AnalysisCLIError):
        _closed_complete_items(items(start), "1m")


def test_config_uses_required_windows_and_identity():
    sma = _build_config("sma", "BTC/USDT", "1m", 20)
    ema = _build_config("ema", "BTC/USDT", "1m", 20)
    rsi = _build_config("rsi", "BTC/USDT", "1m", 14)
    assert sma.window.count == 20
    assert sma.algorithm_identity == "indicator.sma"
    assert ema.window.kind.value == "ALL"
    assert rsi.window.kind.value == "ALL"


def test_module_has_no_financial_runtime_imports():
    import bot_obrero.analysis_cli as cli
    source = importlib.util.find_spec(cli.__name__).origin
    assert source
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "StrategyRuntime" not in text
    assert "FinancialExecutionRuntime" not in text
    assert "TradeProposal" not in text
    assert "api/v3/order" not in text
    assert "api/v3/account" not in text



@pytest.mark.parametrize(
    "item",
    [
        lambda s: _item(s, candle_completeness=DataCompleteness.UNKNOWN),
        lambda s: _item(s, data_completeness=DataCompleteness.UNKNOWN),
        lambda s: _item(s, state=CandleState.UNKNOWN),
        lambda s: _item(s, finality=CandleFinality.UNKNOWN),
    ],
)
def test_unknown_candle_or_marketdata_state_at_end_fails_closed(item):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(AnalysisCLIError, match="unknown"):
        _closed_complete_items([_item(start), item(start + timedelta(minutes=1))], "1m")


def test_unknown_payload_completeness_interior_fails_closed():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    items = [
        _item(start),
        _item(start + timedelta(minutes=1), candle_completeness=DataCompleteness.UNKNOWN),
        _item(start + timedelta(minutes=2)),
    ]
    with pytest.raises(AnalysisCLIError, match="unknown"):
        _closed_complete_items(items, "1m")


def test_fixture_preserves_explicit_none_availability():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    item = _item(start, available_at=None)
    assert item.available_at is None


def _composition_items(closes=("1", "2", "3", "4", "5")):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        _item(start + timedelta(minutes=index), close=close)
        for index, close in enumerate(closes)
    ]


def _install_cli_dependencies(monkeypatch, items, *, instrument_error=None, acquisition_error=None):
    from bot_obrero import analysis_cli
    instrument = SimpleNamespace(
        symbol="BTC/USDT",
        instrument_id="binance:SPOT:BTCUSDT",
        market="SPOT",
        instrument_type=SimpleNamespace(value="CRYPTO_SPOT"),
    )
    metadata = Mock()
    if instrument_error is not None:
        metadata.resolve_active.side_effect = instrument_error
    else:
        metadata.resolve_active.return_value = instrument
    monkeypatch.setattr(analysis_cli, "BinanceSpotInstrumentMetadata", Mock(return_value=metadata))
    adapter = Mock()
    if acquisition_error is not None:
        adapter.fetch_market_data.side_effect = acquisition_error
    else:
        adapter.fetch_market_data.return_value = items
    monkeypatch.setattr(analysis_cli, "BinanceMetadataBackedAdapter", Mock(return_value=adapter))
    decision_time = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(analysis_cli, "_utc_now", Mock(return_value=decision_time))
    return instrument, metadata, adapter, decision_time


def test_full_composition_uses_real_algorithms_and_reports_deterministic_results(monkeypatch, capsys):
    from bot_obrero import analysis_cli
    items = _composition_items()
    _, metadata, adapter, decision_time = _install_cli_dependencies(monkeypatch, items)
    code = analysis_cli.main([
        "--symbol", "BTCUSDT", "--interval", "1m", "--candles", "5",
        "--sma-period", "3", "--ema-period", "3", "--rsi-period", "2",
    ])
    output = capsys.readouterr().out
    assert code == 0
    assert "BINANCE SPOT — READ-ONLY MARKET ANALYSIS" in output
    assert "Canonical symbol: BTC/USDT" in output
    assert "Admissible closed candles: 5" in output
    assert "SMA:" in output and "EMA:" in output and "RSI:" in output
    assert "period: 3" in output and "period: 2" in output
    assert "binance-spot-rest" in output or "binance" in output.lower()
    assert "sma: 4" in output.lower()
    assert "ema: 4" in output.lower()
    assert "rsi: 100" in output.lower()
    assert output.count(decision_time.isoformat()) >= 4
    metadata.resolve_active.assert_called_once_with("BTCUSDT")
    adapter.fetch_market_data.assert_called_once_with(symbol="BTCUSDT", interval="1m", limit=6)


@pytest.mark.parametrize("available_at", [None, datetime(2026, 1, 1, 0, 11, tzinfo=timezone.utc)])
def test_temporally_unknown_or_future_available_data_is_rejected(available_at):
    from bot_obrero.analysis_cli import _analyze
    items = [_item(datetime(2026, 1, 1, tzinfo=timezone.utc), available_at=available_at)]
    with pytest.raises(AnalysisCLIError, match="temporal policy"):
        _analyze(
            items,
            decision_timestamp=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
            symbol="BTC/USDT", interval="1m", sma_period=1, ema_period=1, rsi_period=1,
        )


@pytest.mark.parametrize("exc", [
    TimeoutError("simulated timeout"),
    RuntimeError("simulated transport failure"),
    BinanceRateLimitError(429, "rate limit"),
    BinanceRateLimitError(418, "IP banned"),
])
def test_acquisition_errors_return_error_code_and_no_success_report(monkeypatch, capsys, exc):
    from bot_obrero import analysis_cli
    _install_cli_dependencies(monkeypatch, _composition_items(), acquisition_error=exc)
    code = analysis_cli.main([
        "--symbol", "BTCUSDT", "--interval", "1m", "--candles", "5",
        "--sma-period", "3", "--ema-period", "3", "--rsi-period", "2",
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    assert "READ-ONLY MARKET ANALYSIS" not in captured.out


@pytest.mark.parametrize("exc", [
    InstrumentNotFound("instrument not found"),
    InstrumentInactive("instrument inactive"),
])
def test_unknown_or_inactive_instrument_fails_closed(monkeypatch, capsys, exc):
    from bot_obrero import analysis_cli
    _, metadata, adapter, _ = _install_cli_dependencies(
        monkeypatch, _composition_items(), instrument_error=exc
    )
    code = analysis_cli.main([
        "--symbol", "BTCUSDT", "--interval", "1m", "--candles", "5",
        "--sma-period", "3", "--ema-period", "3", "--rsi-period", "2",
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "ERROR:" in captured.err
    adapter.fetch_market_data.assert_not_called()


def test_insufficient_history_returns_error_code(monkeypatch, capsys):
    from bot_obrero import analysis_cli
    _install_cli_dependencies(monkeypatch, _composition_items(closes=("1", "2", "3")))
    code = analysis_cli.main([
        "--symbol", "BTCUSDT", "--interval", "1m", "--candles", "5",
        "--sma-period", "3", "--ema-period", "3", "--rsi-period", "2",
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "insufficient admissible closed candles" in captured.err


def test_final_open_candle_warning_is_truthful_and_error_path_is_clear(monkeypatch, capsys):
    from bot_obrero import analysis_cli
    items = _composition_items()
    items[-1] = _item(
        datetime(2026, 1, 1, 0, 4, tzinfo=timezone.utc),
        state=CandleState.OPEN,
        candle_completeness=DataCompleteness.PARTIAL,
        data_completeness=DataCompleteness.PARTIAL,
        finality=CandleFinality.NOT_FINAL,
    )
    _install_cli_dependencies(monkeypatch, items)
    code = analysis_cli.main([
        "--symbol", "BTCUSDT", "--interval", "1m", "--candles", "4",
        "--sma-period", "3", "--ema-period", "3", "--rsi-period", "2",
    ])
    captured = capsys.readouterr()
    assert code == 0
    assert "WARNING: final open/partial/non-final candle excluded." in captured.out
    assert "Excluded final candle(s): 1" in captured.out


def test_module_never_invokes_financial_runtime():
    from bot_obrero import analysis_cli
    assert "StrategyRuntime" not in analysis_cli.__dict__
    assert "FinancialExecutionRuntime" not in analysis_cli.__dict__
    assert "TradeProposal" not in analysis_cli.__dict__
