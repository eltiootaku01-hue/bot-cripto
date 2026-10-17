"""Paired BTC→ETH annual virtual research; public data only, never LIVE execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from .binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from .binance_spot import BinanceSpotRestConfig
from .product_v2 import (
    InstrumentFilters, ProductV2Error, active_usdt_universe, backtest, buy_hold_metrics,
    historical_btc_entry_admission, histories_temporally_aligned, public_json,
    validation_window_bounds,
)
from .virtual_backtest import _market_data_from_record, _market_data_record, _validate_history

D = Decimal
ZERO, ONE = D("0"), D("1")
BTC, ETH, INTERVAL = "BTCUSDT", "ETHUSDT", "1h"
BPD, WARMUP_DAYS, VALIDATION_DAYS, VALIDATION_WINDOWS = 24, 90, 60, 3
FAST, SLOW, RSI = 12, 26, 14
RSI_BUY, RSI_SELL, FRACTION = D("50"), D("70"), D("0.95")
MIN_CORR_30D, MIN_CORR_90D, MAX_ENTRIES = D("0.50"), D("0.60"), 5
COSTS = (
    ("BASE", D("0.001"), D("10"), D("5")),
    ("STRESS_1", D("0.001"), D("25"), D("10")),
    ("STRESS_2", D("0.002"), D("50"), D("20")),
)
FILTER_DECIMALS = (
    "min_qty", "step_size", "min_notional", "max_qty",
    "market_min_qty", "market_step_size", "market_max_qty",
)


class BTCETHResearchError(RuntimeError):
    """Invalid data, configuration, or paired-study prerequisites."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _config(days: int, capital: Decimal) -> dict[str, Any]:
    return {
        "schema_version": 1, "pair": [BTC, ETH], "interval": INTERVAL,
        "evaluation_days": days, "warmup_days": WARMUP_DAYS,
        "validation_days": VALIDATION_DAYS, "validation_windows": VALIDATION_WINDOWS,
        "initial_usdt": str(capital), "fast_ema": FAST, "slow_ema": SLOW, "rsi_period": RSI,
        "rsi_buy_min": str(RSI_BUY), "rsi_sell_at": str(RSI_SELL), "position_fraction": str(FRACTION),
        "min_corr_30d": str(MIN_CORR_30D), "min_corr_90d": str(MIN_CORR_90D),
        "max_entries_per_rolling_30d": MAX_ENTRIES,
        "terminal_liquidation": "last-evaluation-close-with-modeled-costs",
        "costs": [{"name": n, "fee": str(f), "spread_bps": str(s), "slippage_bps": str(l)} for n, f, s, l in COSTS],
    }


def _filter_to_record(filters: InstrumentFilters) -> dict[str, Any]:
    record = {name: (str(getattr(filters, name)) if getattr(filters, name) is not None else None)
              for name in FILTER_DECIMALS}
    record["market_lot_size_present"] = filters.market_lot_size_present
    return record


def _filter_from_record(raw: Mapping[str, Any]) -> InstrumentFilters:
    values: dict[str, Any] = {}
    for name in FILTER_DECIMALS:
        item = raw.get(name)
        try:
            value = None if item is None else D(str(item))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BTCETHResearchError(f"invalid market filter value: {name}") from exc
        if value is not None and not value.is_finite():
            raise BTCETHResearchError(f"non-finite market filter value: {name}")
        values[name] = value
    present = raw.get("market_lot_size_present")
    if present is not None and type(present) is not bool:
        raise BTCETHResearchError("market_lot_size_present must be boolean or null")
    return InstrumentFilters(
        min_qty=values["min_qty"], step_size=values["step_size"], min_notional=values["min_notional"],
        max_qty=values["max_qty"], market_min_qty=values["market_min_qty"],
        market_step_size=values["market_step_size"], market_max_qty=values["market_max_qty"],
        market_lot_size_present=present,
    )


def _require_market_filters(symbol: str, filters: InstrumentFilters) -> None:
    if filters.market_lot_size_present is not True:
        raise BTCETHResearchError(f"{symbol}: MARKET_LOT_SIZE_UNKNOWN")
    for name in ("market_min_qty", "market_step_size", "market_max_qty", "min_notional"):
        value = getattr(filters, name)
        if value is None or value <= ZERO:
            raise BTCETHResearchError(f"{symbol}: MARKET_FILTER_INVALID_OR_INCOMPLETE:{name}")
    if filters.market_min_qty > filters.market_max_qty:
        raise BTCETHResearchError(f"{symbol}: MARKET_FILTER_MIN_EXCEEDS_MAX")


def _data_payload(btc_items: Sequence[Any], eth_items: Sequence[Any],
                  filters: Mapping[str, InstrumentFilters]) -> dict[str, Any]:
    return {
        "history": {
            BTC: [_market_data_record(item) for item in btc_items],
            ETH: [_market_data_record(item) for item in eth_items],
        },
        "filters": {symbol: _filter_to_record(filters[symbol]) for symbol in (BTC, ETH)},
    }


def _make_bundle(btc_items, eth_items, filters, *, days: int, capital: Decimal, acquired_at: datetime):
    data = _data_payload(btc_items, eth_items, filters)
    config = _config(days, capital)
    return {
        "schema_version": 1, "acquired_at": acquired_at.astimezone(timezone.utc).isoformat(),
        "configuration": config, "configuration_sha256": hashlib.sha256(_canonical(config)).hexdigest(),
        "dataset_sha256": hashlib.sha256(_canonical(data)).hexdigest(), "data": data,
        "evidence_note": "Hashes identify the data and configuration; they do not prove historical point-in-time availability or future profitability.",
    }


def _load_bundle(path: Path, *, days: int, capital: Decimal):
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BTCETHResearchError(f"cannot read dataset bundle: {exc}") from exc
    if not isinstance(bundle, Mapping) or bundle.get("schema_version") != 1:
        raise BTCETHResearchError("unsupported BTC-ETH bundle schema")
    config, data = bundle.get("configuration"), bundle.get("data")
    if config != _config(days, capital):
        raise BTCETHResearchError("saved configuration differs from requested strategy")
    config_hash = hashlib.sha256(_canonical(config)).hexdigest()
    if config_hash != bundle.get("configuration_sha256"):
        raise BTCETHResearchError("configuration SHA-256 mismatch")
    if not isinstance(data, Mapping) or not isinstance(data.get("history"), Mapping) or not isinstance(data.get("filters"), Mapping):
        raise BTCETHResearchError("bundle lacks both candle histories or filter records")
    dataset_hash = hashlib.sha256(_canonical(data)).hexdigest()
    if dataset_hash != bundle.get("dataset_sha256"):
        raise BTCETHResearchError("dataset SHA-256 mismatch")
    histories = {}
    for symbol in (BTC, ETH):
        rows = data["history"].get(symbol)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
            raise BTCETHResearchError(f"bundle history invalid for {symbol}")
        histories[symbol] = [_market_data_from_record(row) for row in rows]
    filters = {symbol: _filter_from_record(data["filters"][symbol]) for symbol in (BTC, ETH)}
    hydrated = _data_payload(histories[BTC], histories[ETH], filters)
    if hashlib.sha256(_canonical(hydrated)).hexdigest() != dataset_hash:
        raise BTCETHResearchError("rehydrated data hash mismatch")
    try:
        acquired_at = datetime.fromisoformat(str(bundle["acquired_at"]))
    except (KeyError, ValueError) as exc:
        raise BTCETHResearchError("invalid dataset acquisition timestamp") from exc
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise BTCETHResearchError("dataset acquisition timestamp must be timezone-aware")
    return histories[BTC], histories[ETH], filters, acquired_at, dataset_hash, config_hash


def _fetch_history(adapter, symbol: str, start: datetime, end: datetime, expected_bars: int) -> list[Any]:
    max_requests = max(2, (expected_bars + 999) // 1000 + 2)
    raw = adapter.fetch_historical_market_data(
        symbol=symbol, interval=INTERVAL, start_time=int(start.timestamp() * 1000),
        end_time=int(end.timestamp() * 1000), page_limit=1000, max_requests=max_requests,
    )
    accepted, _, _, _ = _validate_history(raw, INTERVAL)
    return accepted


def _acquire(days: int, capital: Decimal):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    history_days = days + WARMUP_DAYS + VALIDATION_DAYS * VALIDATION_WINDOWS + 5
    start = now - timedelta(days=history_days)
    expected = history_days * BPD + 2
    universe = active_usdt_universe(public_json("/api/v3/exchangeInfo"))
    if BTC not in universe or ETH not in universe:
        raise BTCETHResearchError("BTCUSDT/ETHUSDT active Spot metadata unavailable")
    filters = {BTC: universe[BTC].filters, ETH: universe[ETH].filters}
    for symbol in (BTC, ETH):
        _require_market_filters(symbol, filters[symbol])
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    if metadata.resolve_active(BTC).symbol != "BTC/USDT" or metadata.resolve_active(ETH).symbol != "ETH/USDT":
        raise BTCETHResearchError("resolved instrument identity mismatch")
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    btc = _fetch_history(adapter, BTC, start, now, expected)
    eth = _fetch_history(adapter, ETH, start, now, expected)
    if not histories_temporally_aligned(btc, eth):
        raise BTCETHResearchError("BTC/ETH hourly candle timestamps are not exactly aligned")
    return btc, eth, filters, now


def _evaluation_plan(total_items: int, days: int):
    if isinstance(days, bool) or not isinstance(days, int) or not 365 <= days <= 400:
        raise BTCETHResearchError("annual research requires 365–400 evaluation days")
    warmup, evaluation, validation = WARMUP_DAYS * BPD, days * BPD, VALIDATION_DAYS * BPD
    try:
        bounds = validation_window_bounds(total_items, warmup, evaluation, validation, VALIDATION_WINDOWS)
    except ProductV2Error as exc:
        raise BTCETHResearchError(str(exc)) from exc
    return warmup, evaluation, validation, bounds


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    covariance = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    value = covariance / math.sqrt(vx * vy)
    return value if math.isfinite(value) else None


def rolling_correlation_summary(btc_items: Sequence[Any], eth_items: Sequence[Any], *,
                                evaluation_start_index: int, evaluation_end_index: int,
                                lookback_days: int) -> dict[str, Any]:
    """Daily-sampled rolling hourly-return correlations restricted to the evaluation period."""
    if not histories_temporally_aligned(btc_items, eth_items):
        raise BTCETHResearchError("BTC/ETH histories are not aligned")
    if len(btc_items) != len(eth_items) or not 1 <= evaluation_start_index < evaluation_end_index <= len(btc_items):
        raise BTCETHResearchError("invalid evaluation bounds for correlation")
    if lookback_days not in (30, 90):
        raise BTCETHResearchError("lookback_days must be 30 or 90")
    rb, re = [ZERO], [ZERO]
    for i in range(1, len(btc_items)):
        before_btc, before_eth = btc_items[i - 1].payload.close, eth_items[i - 1].payload.close
        if before_btc <= ZERO or before_eth <= ZERO:
            raise BTCETHResearchError("non-positive close blocks return correlation")
        rb.append(btc_items[i].payload.close / before_btc - ONE)
        re.append(eth_items[i].payload.close / before_eth - ONE)
    window = lookback_days * BPD
    samples = []
    for i in range(max(evaluation_start_index, window), evaluation_end_index, BPD):
        value = _pearson([float(x) for x in rb[i-window+1:i+1]],
                         [float(x) for x in re[i-window+1:i+1]])
        if value is not None:
            samples.append(value)
    if not samples:
        return {"lookback_days": lookback_days, "samples": 0, "minimum": None, "median": None,
                "mean": None, "maximum": None, "fraction_at_or_above_0_5": None}
    return {
        "lookback_days": lookback_days, "samples": len(samples), "minimum": min(samples),
        "median": statistics.median(samples), "mean": sum(samples) / len(samples), "maximum": max(samples),
        "fraction_at_or_above_0_5": sum(x >= 0.5 for x in samples) / len(samples),
    }


def _return_pct(result: Any) -> Decimal:
    return (result.final_equity / result.config.initial_quote_balance - ONE) * D("100")


def _run_result(eth_items, filters, *, days, capital, fee, spread, slippage, start_at, end_at, gate=None):
    return backtest(
        ETH, eth_items, days * BPD, filters, initial_quote_balance=capital,
        fee_rate=fee, spread_bps=spread, slippage_bps=slippage, fast_ema_period=FAST,
        slow_ema_period=SLOW, rsi_period=RSI, rsi_buy_min=RSI_BUY, rsi_sell_at=RSI_SELL,
        position_fraction=FRACTION, liquidate_at_end=True,
        evaluation_start=start_at, evaluation_end=end_at, entry_admission=gate,
    )


def _print_result(scope: str, strategy: str, cost: str, result: Any) -> None:
    buys = sum(fill.side == "BUY" for fill in result.fills)
    sells = sum(fill.side == "SELL" for fill in result.fills)
    terminal = sum(fill.reason == "TERMINAL_LIQUIDATION" for fill in result.fills)
    print(
        f"RESULT scope={scope} strategy={strategy} cost={cost} initial_usdt={result.config.initial_quote_balance} "
        f"final_equity_usdt={result.final_equity} net_pnl_usdt={result.final_equity-result.config.initial_quote_balance} "
        f"return_pct={_return_pct(result)} max_drawdown_pct={result.max_drawdown*D('100')} "
        f"fills={len(result.fills)} buys={buys} sells={sells} terminal_liquidations={terminal} "
        f"fees_usdt={result.total_fees} final_base_position={result.final_base_position} "
        f"final_quote_balance={result.final_quote_balance} entry_rejections={len(result.entry_rejections)}"
    )
    for fill in result.fills:
        print(f"FILL scope={scope} strategy={strategy} cost={cost} timestamp={fill.timestamp.isoformat()} "
              f"side={fill.side} quantity={fill.quantity} price={fill.price} fee={fill.fee} "
              f"quote_after={fill.quote_balance_after} realized_pnl={fill.realized_pnl} reason={fill.reason}")


def run_research(*, btc_items, eth_items, filters, days, capital, acquired_at, dataset_hash, config_hash) -> int:
    if not histories_temporally_aligned(btc_items, eth_items):
        raise BTCETHResearchError("BTC/ETH histories are not exactly aligned")
    for symbol, items in ((BTC, btc_items), (ETH, eth_items)):
        accepted, _, _, _ = _validate_history(items, INTERVAL)
        if len(accepted) != len(items):
            raise BTCETHResearchError(f"{symbol}: history revalidation changed the candle count")
        _require_market_filters(symbol, filters[symbol])
    warmup, evaluation, validation, bounds = _evaluation_plan(len(btc_items), days)
    start_at, end_at = btc_items[warmup].payload.start, btc_items[warmup + evaluation].payload.start
    btc_eval, eth_eval = btc_items[warmup:warmup+evaluation], eth_items[warmup:warmup+evaluation]
    gate = historical_btc_entry_admission(
        eth_items, btc_items, fast_ema_period=FAST, slow_ema_period=SLOW,
        rsi_period=RSI, rsi_buy_min=RSI_BUY, minimum_corr30d=MIN_CORR_30D,
        minimum_corr90d=MIN_CORR_90D, periods_per_day=BPD,
    )
    corr30 = rolling_correlation_summary(btc_items, eth_items, evaluation_start_index=warmup,
                                        evaluation_end_index=warmup+evaluation, lookback_days=30)
    corr90 = rolling_correlation_summary(btc_items, eth_items, evaluation_start_index=warmup,
                                        evaluation_end_index=warmup+evaluation, lookback_days=90)
    btc_unknown_availability = sum(x.available_at is None for x in btc_items)
    eth_unknown_availability = sum(x.available_at is None for x in eth_items)
    btc_unknown_finality = sum(x.payload.finality.value == "UNKNOWN" for x in btc_items)
    eth_unknown_finality = sum(x.payload.finality.value == "UNKNOWN" for x in eth_items)

    print("BOT-CRIPTO — BTC→ETH FOLLOW RESEARCH V1")
    print("Mode: public Binance Spot history; virtual accounting only; no private endpoints or orders")
    print(f"Observation UTC: {datetime.now(timezone.utc).isoformat()}; dataset acquired UTC: {acquired_at.isoformat()}")
    print(f"Evaluation interval={INTERVAL}; exact evaluation candles={evaluation}; evaluation days={days}")
    print(f"Warm-up bars={warmup}; validation={VALIDATION_WINDOWS} x {validation} hourly bars")
    print(f"Evaluation range UTC=[{start_at.isoformat()}, {end_at.isoformat()})")
    print(f"Initial virtual balance={capital} USDT; no ARS conversion is inferred")
    print(f"Strategy: EMA{FAST}/EMA{SLOW}+RSI{RSI}; BTC gate uses 30d/90d correlation and BTC EMA/RSI regime")
    print(f"Entry thresholds: corr30d>={MIN_CORR_30D}; corr90d>={MIN_CORR_90D}; max entries/rolling 30d={MAX_ENTRIES}")
    print("Fill timing: closed hourly signal; no fill before the next hourly open")
    print("Terminal liquidation: last evaluation close adjusted for modeled spread/slippage and exit fee")
    print(f"Dataset SHA-256: {dataset_hash}")
    print(f"Configuration SHA-256: {config_hash}")
    print(f"available_at UNKNOWN bars: BTC={btc_unknown_availability}; ETH={eth_unknown_availability}")
    print(f"finality UNKNOWN bars: BTC={btc_unknown_finality}; ETH={eth_unknown_finality}")
    if btc_unknown_availability or eth_unknown_availability or btc_unknown_finality or eth_unknown_finality:
        print("TEMPORAL WARNING: dataset hashes prove identity, not point-in-time availability/finality.")
    for corr in (corr30, corr90):
        print(f"ROLLING_CORRELATION lookback_days={corr['lookback_days']} samples={corr['samples']} "
              f"min={corr['minimum']} median={corr['median']} mean={corr['mean']} max={corr['maximum']} "
              f"fraction_ge_0.5={corr['fraction_at_or_above_0_5']}")

    gated_eval, gated_oos = [], []
    for cost_name, fee, spread, slip in COSTS:
        baseline = _run_result(eth_items, filters[ETH], days=days, capital=capital, fee=fee, spread=spread,
                               slippage=slip, start_at=start_at, end_at=end_at)
        integrated = _run_result(eth_items, filters[ETH], days=days, capital=capital, fee=fee, spread=spread,
                                 slippage=slip, start_at=start_at, end_at=end_at, gate=gate)
        _print_result("EVALUATION", "BASELINE", cost_name, baseline)
        _print_result("EVALUATION", "BTC_GATED", cost_name, integrated)
        gated_eval.append(integrated)
        eth_bh, eth_dd = buy_hold_metrics(eth_eval, fee, spread, slip)
        btc_bh, btc_dd = buy_hold_metrics(btc_eval, fee, spread, slip)
        print(f"BENCHMARK scope=EVALUATION cost={cost_name} cash_return_pct=0 "
              f"ETH_buy_hold_return_pct={eth_bh} ETH_buy_hold_drawdown_pct={eth_dd} "
              f"BTC_buy_hold_return_pct={btc_bh} BTC_buy_hold_drawdown_pct={btc_dd}")
        for window_no, (start_i, end_i) in enumerate(bounds, start=1):
            window_start, window_end = btc_items[start_i].payload.start, btc_items[end_i].payload.start
            window_days = (end_i - start_i) // BPD
            b = _run_result(eth_items, filters[ETH], days=window_days, capital=capital, fee=fee, spread=spread,
                            slippage=slip, start_at=window_start, end_at=window_end)
            g = _run_result(eth_items, filters[ETH], days=window_days, capital=capital, fee=fee, spread=spread,
                            slippage=slip, start_at=window_start, end_at=window_end, gate=gate)
            _print_result(f"VALIDATION_{window_no}", "BASELINE", cost_name, b)
            _print_result(f"VALIDATION_{window_no}", "BTC_GATED", cost_name, g)
            gated_oos.append(g)
            eth_vbh, eth_vdd = buy_hold_metrics(eth_items[start_i:end_i], fee, spread, slip)
            btc_vbh, btc_vdd = buy_hold_metrics(btc_items[start_i:end_i], fee, spread, slip)
            print(f"BENCHMARK scope=VALIDATION_{window_no} cost={cost_name} "
                  f"range_utc=[{window_start.isoformat()},{window_end.isoformat()}) cash_return_pct=0 "
                  f"ETH_buy_hold_return_pct={eth_vbh} ETH_buy_hold_drawdown_pct={eth_vdd} "
                  f"BTC_buy_hold_return_pct={btc_vbh} BTC_buy_hold_drawdown_pct={btc_vdd}")

    classification = "NO_ROBUST_EDGE_FOUND"
    if len(gated_eval) == len(COSTS) and len(gated_oos) == len(COSTS) * VALIDATION_WINDOWS:
        if all(_return_pct(result) > ZERO for result in gated_eval + gated_oos):
            classification = "POSITIVE_HISTORICAL_RESULT_REQUIRES_PROSPECTIVE_PAPER_VALIDATION"
    print(f"RESEARCH CLASSIFICATION: {classification}")
    print("FINAL DECISION: NO_TRADE")
    print("A backtest cannot establish guaranteed future profit.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Annual BTC→ETH paired historical virtual research; public Spot data only.")
    parser.add_argument("--days", type=int, default=365, help="evaluation days, 365–400")
    parser.add_argument("--initial-usdt", type=str, default="31", help="hypothetical virtual balance, not an ARS conversion")
    parser.add_argument("--dataset-output", type=Path, default=None, help="save the normalized paired dataset and hashes")
    parser.add_argument("--dataset-input", type=Path, default=None, help="replay a saved paired dataset offline")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if isinstance(args.days, bool) or not isinstance(args.days, int) or not 365 <= args.days <= 400:
            raise BTCETHResearchError("--days must be between 365 and 400")
        try:
            capital = D(args.initial_usdt)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BTCETHResearchError("--initial-usdt must be an exact Decimal") from exc
        if not capital.is_finite() or capital <= ZERO:
            raise BTCETHResearchError("--initial-usdt must be finite and positive")
        if args.dataset_input:
            btc, eth, filters, acquired_at, dataset_hash, config_hash = _load_bundle(
                args.dataset_input, days=args.days, capital=capital
            )
        else:
            btc, eth, filters, acquired_at = _acquire(args.days, capital)
            bundle = _make_bundle(btc, eth, filters, days=args.days, capital=capital, acquired_at=acquired_at)
            dataset_hash, config_hash = bundle["dataset_sha256"], bundle["configuration_sha256"]
            if args.dataset_output:
                args.dataset_output.parent.mkdir(parents=True, exist_ok=True)
                args.dataset_output.write_text(json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        required = WARMUP_DAYS * BPD + args.days * BPD + VALIDATION_DAYS * BPD * VALIDATION_WINDOWS + 1
        if len(btc) < required or len(eth) < required:
            raise BTCETHResearchError(f"insufficient history: require at least {required} closed hourly bars")
        if not histories_temporally_aligned(btc, eth):
            raise BTCETHResearchError("BTC/ETH histories are not aligned")
        return run_research(btc_items=btc, eth_items=eth, filters=filters, days=args.days, capital=capital,
                            acquired_at=acquired_at, dataset_hash=dataset_hash, config_hash=config_hash)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
