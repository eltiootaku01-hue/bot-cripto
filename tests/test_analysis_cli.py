from __future__ import annotations

import importlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bot_obrero.analysis_cli import (
    AnalysisCLIError,
    _build_config,
    _closed_complete_items,
    _expected_next_start,
    _parser,
    _validate_args,
)
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
          available_at=None, close="10") -> MarketData:
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
        available_at=available_at or start + timedelta(minutes=1),
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
    args = _parser().parse_args(argv)
    with pytest.raises((AnalysisCLIError, SystemExit)):
        _validate_args(args)


def test_interval_step_handles_minutes_and_calendar_month():
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert _expected_next_start(start, "1m") == start + timedelta(minutes=1)
    assert _expected_next_start(start, "1M") == datetime(2026, 2, 28, tzinfo=timezone.utc)


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
    lambda s: [_item(s), _item(s + timedelta(minutes=1), candle_completeness=DataCompleteness.PARTIAL)],
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
