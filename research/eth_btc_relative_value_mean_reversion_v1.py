"""Frozen ETH/BTC relative-value mean-reversion replay; public data only, no orders."""
from __future__ import annotations
import argparse, csv, json, os, sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import btc_volume_shock_reversal_v1 as core

STRATEGY = "ETH_BTC_RELATIVE_VALUE_MEAN_REVERSION"
PROTOCOL = "ETH_BTC_RELATIVE_VALUE_MEAN_REVERSION_V1"
EMA_PERIOD = 100
RATIO_WINDOW = 60
ENTRY_Z = Decimal("-1.50")
ENTRY_ALLOC = Decimal("0.20")
TAKE_PROFIT = Decimal("1.04")
STOP_LOSS = Decimal("0.97")
MAX_HOLD = 10

BTC_ROWS: Sequence[dict[str, Any]] = ()
BTC_EMA: list[Decimal] = []
ENTRY_SIGNALS: list[bool] = []

def ema_close(rows: Sequence[dict[str, Any]], period: int = EMA_PERIOD) -> list[Decimal]:
    """Recursive EMA seeded at the first close, causal at every index."""
    if not rows:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    complement = Decimal(1) - alpha
    values = [core.D(rows[0]["close"])]
    for i in range(1, len(rows)):
        close = core.D(rows[i]["close"])
        values.append(alpha * close + complement * values[-1])
    return values

def relative_value_zscore(eth_rows: Sequence[dict[str, Any]],
                         btc_rows: Sequence[dict[str, Any]], i: int) -> Decimal | None:
    """Current ETH/BTC ratio z-score vs the previous 60 completed daily ratios, excluding i."""
    if len(eth_rows) != len(btc_rows) or i < RATIO_WINDOW or i >= len(eth_rows):
        return None
    ratios = [
        core.D(eth_rows[j]["close"]) / core.D(btc_rows[j]["close"])
        for j in range(i - RATIO_WINDOW, i)
    ]
    current = core.D(eth_rows[i]["close"]) / core.D(btc_rows[i]["close"])
    mean = sum(ratios, Decimal(0)) / Decimal(RATIO_WINDOW)
    variance = sum(((x - mean) ** 2 for x in ratios), Decimal(0)) / Decimal(RATIO_WINDOW)
    if variance <= 0:
        return None
    std = variance.sqrt()
    if std <= 0:
        return None
    return (current - mean) / std

def relative_value_entry_signal(eth_rows: Sequence[dict[str, Any]],
                                btc_rows: Sequence[dict[str, Any]], i: int,
                                btc_ema: Sequence[Decimal] | None = None) -> bool:
    if len(eth_rows) != len(btc_rows) or i < max(EMA_PERIOD - 1, RATIO_WINDOW):
        return False
    ema = btc_ema if btc_ema is not None else ema_close(btc_rows)
    if len(ema) != len(btc_rows):
        raise ValueError("BTC_EMA_LENGTH_MISMATCH")
    if core.D(btc_rows[i]["close"]) <= ema[i]:
        return False
    z = relative_value_zscore(eth_rows, btc_rows, i)
    return z is not None and z <= ENTRY_Z

def _entry_signal(_: Sequence[dict[str, Any]], i: int) -> bool:
    return 0 <= i < len(ENTRY_SIGNALS) and ENTRY_SIGNALS[i]

def _cash_result(cash: Decimal, rows: Sequence[dict[str, Any]], a: int, b: int) -> dict[str, Any]:
    return {
        "method": "CASH", "final": cash, "pnl": Decimal(0), "return_pct": Decimal(0),
        "mdd_pct": Decimal(0), "trades": 0, "wins": 0, "losses": 0, "fees": Decimal(0),
        "friction": Decimal(0), "rejects": 0, "fills": [],
        "curve": [{"timestamp_utc": rows[a]["timestamp"], "phase": "START", "equity_usdt": core.ds(cash)}]
        + [{"timestamp_utc": rows[i]["timestamp"], "phase": "CLOSE", "equity_usdt": core.ds(cash)} for i in range(a, b)],
    }

def _tag_result(result: dict[str, Any], method: str, symbol: str) -> dict[str, Any]:
    result["method"] = method
    for fill in result["fills"]:
        fill["method"] = method
        fill["asset"] = symbol
        if "qty_btc" in fill:
            fill["qty_asset"] = fill.pop("qty_btc")
    return result

def gate_results(metrics: Sequence[dict[str, str]], data_ok: bool) -> dict[str, Any]:
    if not data_ok:
        return {"overall": "BLOCKED", "passed": 0, "failed": 1,
                "checks": [{"gate": "PAIRED_DATA_INTEGRITY", "status": "BLOCKED"}]}
    checks = []
    def val(window: str, capital: Decimal, cost: str, method: str, field: str) -> Decimal:
        return core.metric_value(metrics, window, capital, cost, method, field)
    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"gate": name, "status": "PASS" if ok else "FAIL", "details": detail})
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    annual = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    for capital in core.CAPITALS:
        cap = str(int(capital))
        for win in principal:
            pnl = val(win, capital, "STRESS_2", STRATEGY, "net_pnl_usdt")
            trades = int(val(win, capital, "STRESS_2", STRATEGY, "closed_roundtrips"))
            add(f"positive_pnl_{win}_{cap}", pnl > 0, f"PnL={pnl} USDT")
            add(f"five_roundtrips_{win}_{cap}", trades >= 5, f"closed={trades}")
        for win, _, _ in core.WINDOWS:
            dd = val(win, capital, "STRESS_2", STRATEGY, "max_drawdown_pct")
            add(f"drawdown_at_least_minus15_{win}_{cap}", dd >= -15, f"max drawdown={dd}%")
        p22 = val("2022", capital, "STRESS_2", STRATEGY, "net_pnl_usdt")
        add(f"2022_nonnegative_{cap}", p22 >= 0, f"PnL={p22} USDT")
        positive = sum(val(w, capital, "STRESS_2", STRATEGY, "net_pnl_usdt") > 0 for w in annual)
        add(f"four_positive_annual_windows_{cap}", positive >= 4, f"positive={positive}/7")
        recent = val("RECENT_2023_2026", capital, "STRESS_2", STRATEGY, "net_pnl_usdt")
        cash = val("RECENT_2023_2026", capital, "STRESS_2", "CASH", "net_pnl_usdt")
        eth_bh = val("RECENT_2023_2026", capital, "STRESS_2", "ETH_BUY_AND_HOLD", "net_pnl_usdt")
        add(f"recent_beats_cash_{cap}", recent > cash, f"strategy={recent}; cash={cash}")
        add(f"recent_beats_eth_buy_hold_{cap}", recent > eth_bh, f"strategy={recent}; ETH_BH={eth_bh}")
    failed = sum(item["status"] != "PASS" for item in checks)
    return {"overall": "PASS" if failed == 0 else "FAIL", "passed": len(checks)-failed, "failed": failed,
            "checks": checks,
            "promotion_note": "Previously inspected archive, not a pristine holdout; any historical pass requires independent prospective shadow validation."}

def _blocked(outdir: Path, error: str, manifest: dict[str, Any]) -> int:
    outdir.mkdir(parents=True, exist_ok=True)
    manifest["status"] = "BLOCKED"
    manifest["blocker"] = error
    (outdir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (outdir / "report.md").write_text(
        "# ETH/BTC relative-value mean reversion V1\n\nStatus: BLOCKED. No economic result is valid.\n\n"
        f"Blocker: {error}\n\nNo candle was interpolated or silently removed. Operational decision: NO_TRADE.\n",
        encoding="utf-8")
    print("BLOCKED:" + error, file=sys.stderr)
    return 2

def run(outdir: Path) -> int:
    global BTC_ROWS, BTC_EMA, ENTRY_SIGNALS
    outdir.mkdir(parents=True, exist_ok=True)
    original_symbol = core.SYMBOL
    manifest: dict[str, Any] = {
        "protocol": PROTOCOL, "preregistration_issue": 160,
        "symbols": ["BTCUSDT", "ETHUSDT"], "interval": "1d",
        "start_utc": core.START.isoformat(), "end_exclusive_utc": core.END.isoformat(),
        "point_in_time_availability": "UNKNOWN", "historical_exchange_filter_equivalence": "UNKNOWN",
        "historical_spread_slippage": "MODELED_ASSUMPTIONS_ONLY", "main_changed": False,
        "operational_decision": "NO_TRADE", "reused_engine_origin_commit": "d2d26aa7604ba357fe3b4f8422afa64b97b080e0",
        "frozen_rule": {
            "ratio": "ETHUSDT close / BTCUSDT close",
            "ratio_lookback_completed_daily_bars": RATIO_WINDOW,
            "ratio_window_excludes_current_bar": True,
            "entry_zscore_lte": str(ENTRY_Z),
            "btc_regime": "BTC close > recursive EMA100; alpha=2/(100+1); first close seed",
            "entry_fraction_of_available_cash": str(ENTRY_ALLOC),
            "take_profit_multiple": str(TAKE_PROFIT), "stop_loss_multiple": str(STOP_LOSS),
            "max_holding_daily_candles": MAX_HOLD,
            "execution": "signal after completed UTC daily close; ETH entry/exit next daily open; terminal close liquidation",
        },
        "costs": {k: {f: core.ds(v) for f, v in d.items()} for k, d in core.COSTS.items()},
    }
    try:
        datasets: dict[str, list[dict[str, Any]]] = {}
        pages_by_symbol: dict[str, Any] = {}
        quality_by_symbol: dict[str, Any] = {}
        hashes_by_symbol: dict[str, str] = {}
        filters_by_symbol: dict[str, dict[str, Decimal]] = {}
        filter_evidence: dict[str, Any] = {}
        for symbol in ("BTCUSDT", "ETHUSDT"):
            core.SYMBOL = symbol
            rows, pages = core.fetch_history()
            quality = core.validate_candles(rows)
            datasets[symbol] = rows
            pages_by_symbol[symbol] = pages
            quality_by_symbol[symbol] = quality
            hashes_by_symbol[symbol] = core.canonical_data_hash(rows)
            if not quality["ok"]:
                manifest.update({"quality_by_symbol": quality_by_symbol, "row_counts": {s: len(v) for s, v in datasets.items()},
                                 "raw_pages_by_symbol": pages_by_symbol, "dataset_sha256_by_symbol": hashes_by_symbol})
                return _blocked(outdir, "DATA_INTEGRITY_GATE_FAILED:" + symbol, manifest)
            flt, evidence = core.fetch_filters()
            filters_by_symbol[symbol] = flt
            filter_evidence[symbol] = evidence
        btc_rows, eth_rows = datasets["BTCUSDT"], datasets["ETHUSDT"]
        btc_times = [int(row["open_ms"]) for row in btc_rows]
        eth_times = [int(row["open_ms"]) for row in eth_rows]
        if btc_times != eth_times:
            manifest.update({"quality_by_symbol": quality_by_symbol, "row_counts": {s: len(v) for s, v in datasets.items()},
                             "raw_pages_by_symbol": pages_by_symbol, "dataset_sha256_by_symbol": hashes_by_symbol,
                             "btc_only_timestamps": len(set(btc_times)-set(eth_times)),
                             "eth_only_timestamps": len(set(eth_times)-set(btc_times))})
            return _blocked(outdir, "BTC_ETH_DAILY_TIMESTAMP_PAIR_MISMATCH", manifest)

        core.SYMBOL = "ETHUSDT"
        BTC_ROWS = btc_rows
        BTC_EMA = ema_close(btc_rows)
        ENTRY_SIGNALS = [relative_value_entry_signal(eth_rows, btc_rows, i, BTC_EMA) for i in range(len(eth_rows))]
        signal_count = sum(ENTRY_SIGNALS)
        manifest.update({
            "quality_by_symbol": quality_by_symbol,
            "row_counts": {s: len(v) for s, v in datasets.items()},
            "first_open_utc_by_symbol": {s: v[0]["timestamp"] for s, v in datasets.items()},
            "last_open_utc_by_symbol": {s: v[-1]["timestamp"] for s, v in datasets.items()},
            "raw_pages_by_symbol": pages_by_symbol,
            "canonical_dataset_sha256_by_symbol": hashes_by_symbol,
            "paired_timestamp_series_sha256": core.sha(core.canon(btc_times)),
            "exchange_info_by_symbol": filter_evidence,
            "filters_current_by_symbol": {s: {k: core.ds(v) for k, v in f.items()} for s, f in filters_by_symbol.items()},
            "signal_count_over_archive": signal_count,
            "btc_ema_method": "recursive EMA seeded at first close; alpha=2/(period+1); no future closes",
            "ratio_zscore_method": "population standard deviation of prior 60 completed daily ratios; current ratio compared to that distribution",
        })
        with (outdir / "candles.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["symbol", "open_ms", "timestamp", "close_ms", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            for symbol, rows in datasets.items():
                for row in rows:
                    writer.writerow({"symbol": symbol, "open_ms": row["open_ms"], "timestamp": row["timestamp"],
                                     "close_ms": row["close_ms"], **{k: core.ds(row[k]) for k in ("open", "high", "low", "close", "volume")}})

        index = {int(row["open_ms"]): i for i, row in enumerate(eth_rows)}
        metrics: list[dict[str, Any]] = []
        fill_rows: list[dict[str, Any]] = []
        equity_rows: list[dict[str, Any]] = []
        for window, start, end in core.WINDOWS:
            if core.to_ms(start) not in index:
                raise core.DataIntegrityError("WINDOW_START_MISSING:" + window)
            a = index[core.to_ms(start)]
            b = index.get(core.to_ms(end), len(eth_rows))
            if b <= a:
                raise core.DataIntegrityError("WINDOW_BOUNDARY_ORDER:" + window)
            for capital in core.CAPITALS:
                for cost_name in core.COSTS:
                    for method in (STRATEGY, "ETH_BUY_AND_HOLD", "BTC_BUY_AND_HOLD", "CASH"):
                        if method == STRATEGY:
                            core.entry_signal = _entry_signal
                            core.ALLOC = ENTRY_ALLOC
                            core.TP = TAKE_PROFIT
                            core.SL = STOP_LOSS
                            core.MAX_HOLD = MAX_HOLD
                            result = core.simulate(eth_rows, a, b, capital, cost_name, filters_by_symbol["ETHUSDT"], window, "VOLUME_SHOCK_REVERSAL")
                            result = _tag_result(result, STRATEGY, "ETHUSDT")
                        elif method in ("ETH_BUY_AND_HOLD", "BTC_BUY_AND_HOLD"):
                            symbol = "ETHUSDT" if method == "ETH_BUY_AND_HOLD" else "BTCUSDT"
                            rows = datasets[symbol]
                            result = core.simulate(rows, a, b, capital, cost_name, filters_by_symbol[symbol], window, "BTC_BUY_AND_HOLD")
                            result = _tag_result(result, method, symbol)
                        else:
                            result = _cash_result(capital, eth_rows, a, b)
                        metrics.append(core.metric_row(window, start, end, capital, cost_name, result))
                        for fill in result["fills"]:
                            fill_rows.append({**fill, "window": fill.get("window", window), "capital": fill.get("capital", core.ds(capital)),
                                              "cost": fill.get("cost", cost_name), "asset": fill.get("asset", "")})
                        equity_rows.extend({"window": window, "capital": core.ds(capital), "cost": cost_name,
                                            "method": result["method"], **point} for point in result["curve"])

        metric_fields = ["window", "start_utc", "end_exclusive_utc", "capital", "cost", "method", "final_equity_usdt",
                         "net_pnl_usdt", "return_pct", "max_drawdown_pct", "closed_roundtrips", "wins", "losses",
                         "fees_usdt", "spread_slippage_usdt", "rejected_orders"]
        fill_fields = ["window", "capital", "cost", "method", "asset", "trade_id", "side", "reason", "signal_utc",
                       "execution_utc", "execution_type", "raw_price", "fill_price", "qty_asset", "notional_usdt",
                       "fee_usdt", "friction_usdt", "cash_after"]
        equity_fields = ["window", "capital", "cost", "method", "timestamp_utc", "phase", "equity_usdt"]
        core.csv_write(outdir / "summary.csv", metrics, metric_fields)
        core.csv_write(outdir / "fills.csv", fill_rows, fill_fields)
        core.csv_write(outdir / "equity.csv", equity_rows, equity_fields)
        gates = gate_results(metrics, True)
        manifest["status"] = "COMPLETED"
        manifest["strategy_gate"] = gates["overall"]
        manifest["output_counts"] = {"summary_rows": len(metrics), "fill_rows": len(fill_rows), "equity_rows": len(equity_rows)}
        (outdir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        (outdir / "gate_results.json").write_text(json.dumps(gates, indent=2, sort_keys=True), encoding="utf-8")
        lines = [
            "# ETH/BTC relative-value dislocation mean reversion V1", "", "Frozen economic gate: " + gates["overall"], "",
            "| Dataset | Candles | SHA-256 |", "|---|---:|---|",
        ]
        for symbol in ("BTCUSDT", "ETHUSDT"):
            lines.append(f"| {symbol} | {len(datasets[symbol])} | {hashes_by_symbol[symbol]} |")
        lines += ["", f"BTC/ETH paired timestamps SHA-256: {manifest['paired_timestamp_series_sha256']}",
                  f"Entry signals across archive after causal warm-up: {signal_count}",
                  "Continuity and paired timestamp validation: PASS; no gaps, duplicates or OHLCV contract failures.",
                  "Historical point-in-time availability: UNKNOWN; historic lot filters: UNKNOWN.",
                  "Spread/slippage are modeled assumptions, not observed historic quotes.", "",
                  "## Strategy results across costs", "",
                  "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | Fees USDT | Friction USDT |",
                  "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
        for window, _, _ in core.WINDOWS:
            for capital in core.CAPITALS:
                for cost_name in core.COSTS:
                    row = next(x for x in metrics if x["window"] == window and x["capital"] == core.ds(capital)
                               and x["cost"] == cost_name and x["method"] == STRATEGY)
                    lines.append(f"| {window} | {int(capital)} | {cost_name} | {row['net_pnl_usdt']} | {row['return_pct']} | "
                                 f"{row['max_drawdown_pct']} | {row['closed_roundtrips']} | {row['fees_usdt']} | {row['spread_slippage_usdt']} |")
        lines += ["", "## Frozen gates", "", f"Passed: {gates['passed']}; failed: {gates['failed']}", "",
                  "| Gate | Status | Details |", "|---|---|---|"]
        lines += [f"| {item['gate']} | {item['status']} | {item.get('details', '')} |" for item in gates["checks"]]
        lines += ["", "A successful workflow means the research program completed, not that the strategy is profitable.",
                  "The archive was inspected previously, so it is not a pristine holdout. Any historical pass requires prospective shadow validation.",
                  "No private endpoints, credentials, real orders, leverage or main changes. Operational decision: NO_TRADE."]
        (outdir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({"status": "COMPLETED", "btc_rows": len(btc_rows), "eth_rows": len(eth_rows),
                          "btc_sha256": hashes_by_symbol["BTCUSDT"], "eth_sha256": hashes_by_symbol["ETHUSDT"],
                          "signals": signal_count, "strategy_gate": gates["overall"], "passed": gates["passed"],
                          "failed": gates["failed"], "main_changed": False}, sort_keys=True))
        return 0
    except Exception as exc:
        return _blocked(outdir, type(exc).__name__ + ":" + str(exc), manifest)
    finally:
        core.SYMBOL = original_symbol

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="eth-btc-relative-value-output")
    return run(Path(parser.parse_args(argv).output_dir))

if __name__ == "__main__":
    raise SystemExit(main())
