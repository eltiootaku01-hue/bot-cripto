"""Run frozen Product V2-R5 research experiments on public Binance Spot history.

The five-symbol panel is exploratory, not a reconstructed historical universe.
This command performs no private API calls and creates no real orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from .binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from .binance_spot import BinanceSpotRestConfig
from .research_portfolio import PortfolioConfig, run_shared_portfolio
from .research_strategies import DonchianBreakoutStrategy, MeanReversionStrategy
from .virtual_backtest import BacktestConfig, BacktestError, _expected_next_start, _validate_history, run_virtual_backtest

D = Decimal
PANEL = ("XRPUSDT", "STRKUSDT", "WUSDT", "SUIUSDT", "ENAUSDT")
FETCH_SYMBOLS = (*PANEL, "BTCUSDT")
SCENARIOS = (
    ("BASE", D("0.001"), D("10"), D("5")),
    ("STRESS_1", D("0.001"), D("25"), D("10")),
    ("STRESS_2", D("0.002"), D("50"), D("20")),
)
INITIAL_CASH = D("1000")
ZERO = D("0")
INTERVAL = "4h"


def _download_histories(days: int):
    now = datetime.now(timezone.utc)
    end = now.replace(hour=(now.hour // 4) * 4, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days + 45)
    start = start.replace(hour=(start.hour // 4) * 4, minute=0, second=0, microsecond=0)
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    histories: dict[str, list[Any]] = {}
    failures: dict[str, str] = {}
    for symbol in FETCH_SYMBOLS:
        try:
            instrument = metadata.resolve_active(symbol)
            adapter = BinanceMetadataBackedAdapter(metadata=metadata)
            items = adapter.fetch_historical_market_data(
                symbol=symbol, interval=INTERVAL,
                start_time=int(start.timestamp() * 1000),
                end_time=int(end.timestamp() * 1000),
                page_limit=1000, max_requests=3,
            )
            accepted, _, _, _ = _validate_history(items, INTERVAL)
            if len(accepted) < days * 6 + 2:
                raise BacktestError(f"insufficient closed history: {len(accepted)} bars")
            if accepted[-1].instrument.instrument_id != instrument.instrument_id:
                raise BacktestError("canonical instrument identity mismatch")
            histories[symbol] = accepted
        except Exception as exc:
            failures[symbol] = f"{type(exc).__name__}: {exc}"
    meta = {
        "retrieved_at_utc": now.isoformat(), "requested_interval": INTERVAL,
        "evaluation_days": days, "evaluation_bars_expected": days * 6,
        "history_start_utc": start.isoformat(), "history_end_boundary_utc": end.isoformat(),
        "panel": list(PANEL), "panel_classification": "EXPLORATORY_ONLY",
        "benchmark_symbol": "BTCUSDT",
        "history_bar_counts": {symbol: len(items) for symbol, items in histories.items()},
        "acquisition_failures": failures,
        "capital_usdt": str(INITIAL_CASH),
        "capital_provenance": "Product V2 normalized hypothetical research ledger; not ARS conversion",
        "historical_available_at": "may be UNKNOWN; bar-close simulation assumption is explicit",
        "current_instrument_filters": "not treated as historically known; no historical eligibility reconstruction",
    }
    return histories, meta


def _metrics(result, initial: Decimal) -> dict[str, Any]:
    points = result.equity_curve
    daily: dict[Any, list[Any]] = {}
    for point in points:
        day = (point.timestamp.astimezone(timezone.utc) - timedelta(microseconds=1)).date()
        daily.setdefault(day, []).append(point)
    complete_changes: list[Decimal] = []
    previous_equity = initial
    for day in sorted(daily):
        day_points = daily[day]
        midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        expected = tuple(midnight + timedelta(hours=4*i) - timedelta(milliseconds=1) for i in range(1, 7))
        stamps = tuple(point.timestamp.astimezone(timezone.utc) for point in day_points)
        if stamps == expected:
            complete_changes.append(day_points[-1].equity - previous_equity)
        previous_equity = day_points[-1].equity
    net = result.final_equity - initial
    notionals = sum((fill.quantity * fill.price for fill in result.fills), D("0"))
    exposure = D(sum(point.base_position > 0 for point in points)) / D(len(points)) * D("100") if points else None
    return {
        "net_pnl_usdt": str(net),
        "return_pct": str((result.final_equity / initial - 1) * 100),
        "max_drawdown_pct": str(result.max_drawdown * 100),
        "fills": len(result.fills),
        "closed_trades": sum(fill.side == "SELL" for fill in result.fills),
        "fees_usdt": str(result.total_fees),
        "exposure_pct": str(exposure) if exposure is not None else None,
        "turnover_initial_capital_multiple": str(notionals / initial),
        "final_equity_usdt": str(result.final_equity),
        "complete_utc_days": len(complete_changes),
        "mean_complete_day_pnl_usdt": str(sum(complete_changes, D("0")) / D(len(complete_changes))) if complete_changes else None,
        "median_complete_day_pnl_usdt": str(median(complete_changes)) if complete_changes else None,
    }


def _benchmark_btc(items, start_index: int, count: int, fee: Decimal, spread: Decimal, slippage: Decimal):
    if not items or start_index < 0 or count < 2 or start_index + count > len(items):
        return {"status": "EVIDENCE_INSUFFICIENT"}
    window = items[start_index:start_index + count]
    adverse = (spread / D("2") + slippage) / D("10000")
    buy_price = window[0].payload.open * (D("1") + adverse)
    quantity = INITIAL_CASH / (buy_price * (D("1") + fee))
    # Mark at the same final close convention as the backtest; an open position is not force-liquidated.
    ending_equity = quantity * window[-1].payload.close
    return {
        "net_pnl_usdt": str(ending_equity - INITIAL_CASH),
        "return_pct": str((ending_equity / INITIAL_CASH - 1) * 100),
        "assumption": "buy first evaluation open with adverse entry cost and fee; mark to last close without forced liquidation",
    }


def _run_single(symbol, items, btc_items, strategy, strategy_name, scenario, eval_items):
    scenario_name, fee, spread, slippage = scenario
    output: dict[str, Any] = {"candidate": strategy_name, "symbol": symbol, "scenario": scenario_name}
    windows = [("FULL_180D", 0, len(eval_items))]
    window_size = 60 * 6
    windows.extend((f"WALK_FORWARD_{i+1}", i*window_size, window_size) for i in range(3))
    btc_start = {item.payload.start: i for i, item in enumerate(btc_items)}
    for label, offset, count in windows:
        if offset + count > len(eval_items):
            output[label] = {"status": "EVIDENCE_INSUFFICIENT"}
            continue
        start = eval_items[offset].payload.start
        end = eval_items[offset + count].payload.start if offset + count < len(eval_items) else None
        cfg = BacktestConfig(
            symbol=symbol, interval=INTERVAL, candles=count,
            fast_ema_period=12, slow_ema_period=26, rsi_period=14,
            initial_quote_balance=INITIAL_CASH, position_fraction=D("1"),
            fee_rate=fee, spread_bps=spread, slippage_bps=slippage,
            max_entries_per_30d=5,
        )
        try:
            result = run_virtual_backtest(items, cfg, evaluation_start=start, evaluation_end=end, strategy=strategy)
            metrics = _metrics(result, INITIAL_CASH)
            metrics["cash_benchmark_return_pct"] = "0"
            metrics["btc_buy_hold"] = _benchmark_btc(btc_items, btc_start.get(start, -1), count, fee, spread, slippage)
            metrics["first_candle"] = result.first_candle.isoformat()
            metrics["last_candle"] = result.last_candle.isoformat()
            metrics["fills_detail"] = [
                {"timestamp": fill.timestamp.isoformat(), "side": fill.side, "quantity": str(fill.quantity),
                 "price": str(fill.price), "fee": str(fill.fee), "reason": fill.reason,
                 "realized_pnl": str(fill.realized_pnl) if fill.realized_pnl is not None else None}
                for fill in result.fills
            ]
            output[label] = metrics
        except Exception as exc:
            output[label] = {"status": "BLOCKED", "reason": f"{type(exc).__name__}: {exc}"}
    return output


def _run_experiments(histories: dict[str, list[Any]], meta: dict[str, Any]) -> dict[str, Any]:
    days = int(meta["evaluation_days"])
    evaluation_count = days * 6
    research_histories = {s: histories[s] for s in PANEL if s in histories}
    btc_items = histories.get("BTCUSDT", [])
    if len(research_histories) < 2:
        raise BacktestError(f"fewer than two panel symbols have usable data: {meta['acquisition_failures']}")
    common_starts = sorted(set.intersection(*(set(item.payload.start for item in items) for items in research_histories.values())))
    if btc_items:
        common_starts = [stamp for stamp in common_starts if stamp in {item.payload.start for item in btc_items}]
    if len(common_starts) < evaluation_count:
        raise BacktestError(f"only {len(common_starts)} synchronized panel/BTC bars; need {evaluation_count}")
    for before, after in zip(common_starts, common_starts[1:]):
        if _expected_next_start(before, INTERVAL) != after:
            raise BacktestError("panel histories are not continuously synchronized; no interpolation permitted")
    eval_starts = common_starts[-evaluation_count:]
    eval_start = eval_starts[0]
    eval_items_by_symbol = {s: [item for item in items if item.payload.start in set(eval_starts)] for s, items in research_histories.items()}
    if any(len(items) != evaluation_count for items in eval_items_by_symbol.values()):
        raise BacktestError("one or more panel symbols lack the exact common evaluation window")
    btc_eval_items = [item for item in btc_items if item.payload.start in set(eval_starts)] if btc_items else []

    result_rows: list[dict[str, Any]] = []
    candidates = (("A_DONCHIAN_20_10", DonchianBreakoutStrategy),
                  ("B_MEAN_REVERSION_20_EMA200", MeanReversionStrategy))
    for candidate_name, strategy_factory in candidates:
        for scenario in SCENARIOS:
            for symbol, items in research_histories.items():
                result_rows.append(_run_single(symbol, items, btc_eval_items, strategy_factory(),
                                               candidate_name, scenario, eval_items_by_symbol[symbol]))

    portfolio_rows = []
    for scenario_name, fee, spread, slippage in SCENARIOS:
        cfg = PortfolioConfig(initial_cash=INITIAL_CASH, fee_rate=fee, spread_bps=spread, slippage_bps=slippage)
        try:
            portfolio = run_shared_portfolio(research_histories, cfg, evaluation_start=eval_start)
            notionals = sum((fill.quantity * fill.price for fill in portfolio.fills), D("0"))
            exposure = D(sum(point.symbol is not None for point in portfolio.equity_curve)) / D(len(portfolio.equity_curve)) * D("100")
            btc_start = next((i for i, item in enumerate(btc_items) if item.payload.start == eval_start), -1)
            portfolio_rows.append({
                "candidate": "C_SHARED_RELATIVE_STRENGTH", "scenario": scenario_name,
                "first_timestamp": portfolio.first_timestamp.isoformat(),
                "last_timestamp": portfolio.last_timestamp.isoformat(),
                "net_pnl_usdt": str(portfolio.net_pnl),
                "return_pct": str((portfolio.final_equity / INITIAL_CASH - 1) * 100),
                "max_drawdown_pct": str(portfolio.max_drawdown * 100),
                "fills": len(portfolio.fills), "closed_trades": sum(fill.side == "SELL" for fill in portfolio.fills),
                "fees_usdt": str(portfolio.total_fees), "realized_pnl_usdt": str(sum((fill.realized_pnl for fill in portfolio.fills if fill.realized_pnl is not None), D("0"))), "exposure_pct": str(exposure),
                "turnover_initial_capital_multiple": str(notionals / INITIAL_CASH),
                "final_cash": str(portfolio.final_cash), "final_symbol": portfolio.final_symbol,
                "final_quantity": str(portfolio.final_quantity),
                "rejected_transitions": list(portfolio.rejected_transitions),
                "decisions": len(portfolio.decisions), "benchmark_cash_return_pct": "0",
                "benchmark_btc_buy_hold": _benchmark_btc(btc_items, btc_start, evaluation_count, fee, spread, slippage),
            })
        except Exception as exc:
            portfolio_rows.append({"candidate": "C_SHARED_RELATIVE_STRENGTH", "scenario": scenario_name,
                                   "status": "BLOCKED", "reason": f"{type(exc).__name__}: {exc}"})

    classifications = {}
    for candidate in ("A_DONCHIAN_20_10", "B_MEAN_REVERSION_20_EMA200"):
        rows = [row for row in result_rows if row["candidate"] == candidate]
        successful = [row for row in rows if row.get("FULL_180D", {}).get("return_pct") is not None]
        robust = False
        for symbol in research_histories:
            base = next((r for r in rows if r["symbol"] == symbol and r["scenario"] == "BASE"), None)
            if not base or not all(base.get(f"WALK_FORWARD_{i}", {}).get("return_pct") is not None for i in range(1, 4)):
                continue
            full = base["FULL_180D"]
            btc = full.get("btc_buy_hold", {})
            stress = next((r for r in rows if r["symbol"] == symbol and r["scenario"] == "STRESS_2"), None)
            if (btc.get("return_pct") is not None and D(full["return_pct"]) > D(btc["return_pct"])
                    and all(D(base[f"WALK_FORWARD_{i}"]["return_pct"]) > ZERO for i in range(1, 4))
                    and stress and stress.get("FULL_180D", {}).get("return_pct") is not None
                    and D(stress["FULL_180D"]["return_pct"]) > ZERO):
                robust = True
        classifications[candidate] = "EXPLORATORY_ONLY" if robust else ("NO_ROBUST_EDGE_FOUND" if successful else "EVIDENCE_INSUFFICIENT")
    c_success = [row for row in portfolio_rows if row.get("return_pct") is not None]
    c_base = next((row for row in portfolio_rows if row["scenario"] == "BASE"), None)
    c_stress = next((row for row in portfolio_rows if row["scenario"] == "STRESS_2"), None)
    c_class = "EVIDENCE_INSUFFICIENT"
    if c_success:
        c_class = "NO_ROBUST_EDGE_FOUND"
        benchmark = c_base.get("benchmark_btc_buy_hold", {}) if c_base else {}
        if (c_base and c_stress and benchmark.get("return_pct") is not None
                and D(c_base["return_pct"]) > D(benchmark["return_pct"])
                and D(c_base["return_pct"]) > ZERO and D(c_stress["return_pct"]) > ZERO):
            c_class = "EXPLORATORY_ONLY"
    classifications["C_SHARED_RELATIVE_STRENGTH"] = c_class
    return {
        "protocol": {
            **meta, "evaluation_start": eval_start.isoformat(), "evaluation_end": eval_starts[-1].isoformat(),
            "warmup_days_requested": 45,
            "walk_forward_windows": [
                {"name": f"WALK_FORWARD_{i+1}", "start": eval_starts[i*360].isoformat(),
                 "end_exclusive": eval_starts[(i+1)*360].isoformat() if (i+1)*360 < len(eval_starts) else None,
                 "bars": 360} for i in range(3)
            ],
            "cost_scenarios": [{"name": n, "fee_rate": str(f), "spread_bps_full_assumption": str(sp),
                                "slippage_bps_per_fill": str(sl)} for n, f, sp, sl in SCENARIOS],
            "parameters_frozen_before_execution": {
                "A": {"interval": "4h", "entry_prior_high_bars": 20, "exit_prior_low_bars": 10},
                "B": {"interval": "4h", "mean_std_prior_closes": 20, "population_std": True,
                      "entry_z": -2, "exit_z": 0, "stop_z": -3, "trend_ema": 200},
                "C": {"rebalance": "daily UTC 23:59:59.999 close; next common 4h open",
                      "momentum_bars": 42, "trend_ema": 50, "max_positions": 1},
            },
            "strategy_parameter_variants": 1,
            "independent_final_test": "NOT_ESTABLISHED: fixed exploratory panel and no untouched historical universe/test reserved",
            "universe_limitations": "EXPLORATORY_ONLY: fixed five-symbol panel; current metadata is not historical eligibility evidence",
        },
        "single_asset_results": result_rows, "shared_portfolio_results": portfolio_rows,
        "candidate_classifications": classifications, "overall_decision": "NO_TRADE",
        "limitations": [
            "Public historical kline availability does not prove point-in-time listing eligibility.",
            "Spread and slippage are assumptions, not historical measurements.",
            "No ARS-to-USDT conversion is inferred; capital is the existing normalized hypothetical 1000 USDT.",
            "The results are exploratory and do not establish future profitability.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen Product V2-R5 historical research (public Binance Spot only)")
    parser.add_argument("--days", type=int, default=180, choices=(180,))
    parser.add_argument("--output", default="product-v2-r5-report.json")
    args = parser.parse_args(argv)
    try:
        histories, meta = _download_histories(args.days)
        report = _run_experiments(histories, meta)
        report["protocol"]["usable_symbols"] = sorted(histories)
        output = Path(args.output)
        output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print("PRODUCT V2-R5 — HISTORICAL RESEARCH")
        print(f"Panel requested: {', '.join(PANEL)}")
        print(f"Panel usable: {', '.join(s for s in PANEL if s in histories)}")
        print(f"BTC benchmark available: {'yes' if 'BTCUSDT' in histories else 'no'}")
        print(f"Evaluation: {report['protocol']['evaluation_start']} .. {report['protocol']['evaluation_end']}")
        print(f"Capital: {INITIAL_CASH} hypothetical USDT; provenance=existing normalized Product V2 research ledger")
        print("Costs: BASE 0.1%/10bp/5bp; STRESS_1 0.1%/25bp/10bp; STRESS_2 0.2%/50bp/20bp")
        for name, classification in report["candidate_classifications"].items():
            print(f"CLASSIFICATION {name}: {classification}")
        for row in report["shared_portfolio_results"]:
            print("PORTFOLIO " + json.dumps(row, default=str, sort_keys=True))
        print(f"Single-asset result rows: {len(report['single_asset_results'])}")
        print(f"Report: {output}")
        print("FINAL DECISION: NO_TRADE")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
