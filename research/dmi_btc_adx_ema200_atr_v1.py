"""Frozen BTC Wilder DMI/ADX crossover + EMA200 regime and ATR14 sizing replay."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import replay_btc_momentum_90d_atr_full_2020_2026 as engine

CANDIDATE = "BTC_WILDER_DMI_ADX14_CROSS_EMA200_ATR14"
PROTOCOL = "BTC_DMI_ADX_EMA200_ATR_V1"
DMI_PERIOD = 14
ADX_MIN = Decimal("25")
EMA_PERIOD = 200
ZERO, ONE, HUNDRED = Decimal("0"), Decimal("1"), Decimal("100")


def ema_series(values: Sequence[Decimal], period: int = EMA_PERIOD) -> list[Decimal | None]:
    if not values:
        return []
    alpha = Decimal("2") / Decimal(period + 1)
    complement = ONE - alpha
    current = values[0]
    out: list[Decimal | None] = [None] * len(values)
    for i, value in enumerate(values):
        if i:
            current = alpha * value + complement * current
        if i >= period - 1:
            out[i] = current
    return out


def dmi_series(items: Sequence[Any], period: int = DMI_PERIOD) -> dict[str, list[Decimal | None]]:
    """Causal Wilder TR/DM/DI/DX/ADX series with explicit warm-up."""
    if period <= 0:
        raise ValueError("DMI_PERIOD_MUST_BE_POSITIVE")
    n = len(items)
    closes = [item.payload.close for item in items]
    highs = [item.payload.high for item in items]
    lows = [item.payload.low for item in items]

    tr: list[Decimal | None] = [None] * n
    pdm: list[Decimal | None] = [None] * n
    mdm: list[Decimal | None] = [None] * n
    if n:
        tr[0], pdm[0], mdm[0] = highs[0] - lows[0], ZERO, ZERO
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        pdm[i] = up if up > ZERO and up > down else ZERO
        mdm[i] = down if down > ZERO and down > up else ZERO

    str_: list[Decimal | None] = [None] * n
    spdm: list[Decimal | None] = [None] * n
    smdm: list[Decimal | None] = [None] * n
    if n > period:
        str_[period] = sum(((tr[i] or ZERO) for i in range(1, period + 1)), ZERO)
        spdm[period] = sum(((pdm[i] or ZERO) for i in range(1, period + 1)), ZERO)
        smdm[period] = sum(((mdm[i] or ZERO) for i in range(1, period + 1)), ZERO)
        for i in range(period + 1, n):
            assert str_[i - 1] is not None and spdm[i - 1] is not None and smdm[i - 1] is not None
            str_[i] = str_[i - 1] - str_[i - 1] / Decimal(period) + (tr[i] or ZERO)
            spdm[i] = spdm[i - 1] - spdm[i - 1] / Decimal(period) + (pdm[i] or ZERO)
            smdm[i] = smdm[i - 1] - smdm[i - 1] / Decimal(period) + (mdm[i] or ZERO)

    pdi: list[Decimal | None] = [None] * n
    mdi: list[Decimal | None] = [None] * n
    dx: list[Decimal | None] = [None] * n
    for i in range(period, n):
        if str_[i] is None or spdm[i] is None or smdm[i] is None or str_[i] <= ZERO:
            continue
        pdi[i] = HUNDRED * spdm[i] / str_[i]
        mdi[i] = HUNDRED * smdm[i] / str_[i]
        denom = pdi[i] + mdi[i]
        if denom > ZERO:
            dx[i] = HUNDRED * abs(pdi[i] - mdi[i]) / denom

    adx: list[Decimal | None] = [None] * n
    seed_start = None
    for start in range(period, n):
        window = dx[start:start + period]
        if len(window) == period and all(x is not None for x in window):
            seed_start = start
            break
    if seed_start is not None:
        seed_index = seed_start + period - 1
        adx[seed_index] = sum((x for x in dx[seed_start:seed_index + 1] if x is not None), ZERO) / Decimal(period)
        for i in range(seed_index + 1, n):
            if dx[i] is not None and adx[i - 1] is not None:
                adx[i] = (adx[i - 1] * Decimal(period - 1) + dx[i]) / Decimal(period)

    return {
        "true_range": tr, "plus_dm": pdm, "minus_dm": mdm,
        "smoothed_tr": str_, "smoothed_plus_dm": spdm, "smoothed_minus_dm": smdm,
        "plus_di": pdi, "minus_di": mdi, "dx": dx, "adx": adx,
    }


def entry_condition_at(closes, plus_di, minus_di, adx, ema200, i: int) -> bool:
    if i <= 0 or i >= len(closes):
        return False
    if any(i >= len(v) for v in (plus_di, minus_di, adx, ema200)):
        return False
    p0, m0 = plus_di[i - 1], minus_di[i - 1]
    p1, m1, strength, trend = plus_di[i], minus_di[i], adx[i], ema200[i]
    if any(x is None for x in (p0, m0, p1, m1, strength, trend)):
        return False
    return p0 <= m0 and p1 > m1 and strength >= ADX_MIN and closes[i] > trend


def exit_condition_at(closes, plus_di, minus_di, ema200, i: int) -> bool:
    if i < 0 or i >= len(closes) or i >= len(ema200) or ema200[i] is None:
        return False
    bearish_cross = False
    if i > 0 and i < len(plus_di) and i < len(minus_di):
        p0, m0, p1, m1 = plus_di[i - 1], minus_di[i - 1], plus_di[i], minus_di[i]
        if all(x is not None for x in (p0, m0, p1, m1)):
            bearish_cross = m0 <= p0 and m1 > p1
    return bearish_cross or closes[i] < ema200[i]


def dmi_signals_and_weights(items: Sequence[Any]):
    closes = [item.payload.close for item in items]
    highs = [item.payload.high for item in items]
    lows = [item.payload.low for item in items]
    n = len(items)
    ema200 = ema_series(closes, EMA_PERIOD)
    dmi = dmi_series(items, DMI_PERIOD)

    # Sizing reuses the simple-mean ATR14 convention from Issue #143;
    # the DMI calculation separately uses Wilder-smoothed true range.
    atr14: list[Decimal | None] = [None] * n
    tr = dmi["true_range"]
    for i in range(DMI_PERIOD - 1, n):
        window = tr[i - DMI_PERIOD + 1:i + 1]
        if all(x is not None for x in window):
            atr14[i] = sum((x for x in window if x is not None), ZERO) / Decimal(DMI_PERIOD)

    entries, exits = [False] * n, [False] * n
    weights: list[Decimal | None] = [None] * n
    annual_target, max_weight, annualization = Decimal("0.20"), Decimal("0.95"), Decimal(365).sqrt()
    for i in range(n):
        entries[i] = entry_condition_at(closes, dmi["plus_di"], dmi["minus_di"], dmi["adx"], ema200, i)
        exits[i] = exit_condition_at(closes, dmi["plus_di"], dmi["minus_di"], ema200, i)
        if atr14[i] is not None and atr14[i] > ZERO and closes[i] > ZERO:
            denominator = (atr14[i] / closes[i]) * annualization
            if denominator > ZERO:
                weights[i] = min(max_weight, annual_target / denominator)
    return entries, exits, weights, atr14, ema200, dmi


def frozen_gate(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    stress = [x for x in results if x["cost_scenario"] == "STRESS_2"]
    lookup = {(x["window"], x["capital_initial_usdt"]): x for x in stress}
    checks = []
    principal = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", "2026_YTD")
    annual = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    dd_windows = ("FULL_2020_2026", "EARLIER_2020_2022", "RECENT_2023_2026", *annual)
    def add(name, passed, detail):
        checks.append({"gate": name, "status": "PASS" if passed else "FAIL", "details": detail})
    for capital in engine.CAPITALS:
        cap = str(capital)
        for window in principal:
            row = lookup.get((window, cap))
            add(f"positive_pnl_{window}_{cap}", row is not None and Decimal(row["net_pnl_usdt"]) > ZERO,
                "missing result" if row is None else f"PnL={row['net_pnl_usdt']} USDT")
            add(f"five_roundtrips_{window}_{cap}", row is not None and int(row["completed_round_trips"]) >= 5,
                "missing result" if row is None else f"round trips={row['completed_round_trips']}")
        for window in dd_windows:
            row = lookup.get((window, cap))
            add(f"drawdown_limit_{window}_{cap}", row is not None and Decimal(row["max_drawdown_pct"]) >= Decimal("-15"),
                "missing result" if row is None else f"max DD={row['max_drawdown_pct']}%")
        row22 = lookup.get(("2022", cap))
        add(f"2022_nonnegative_{cap}", row22 is not None and Decimal(row22["net_pnl_usdt"]) >= ZERO,
            "missing result" if row22 is None else f"PnL={row22['net_pnl_usdt']} USDT")
        positive = sum(lookup.get((window, cap)) is not None and Decimal(lookup[(window, cap)]["net_pnl_usdt"]) > ZERO for window in annual)
        add(f"four_positive_annual_windows_{cap}", positive >= 4, f"positive windows={positive}/7")
        recent = lookup.get(("RECENT_2023_2026", cap))
        add(f"recent_beats_cash_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > ZERO,
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; cash=0")
        add(f"recent_beats_btc_buy_hold_{cap}", recent is not None and Decimal(recent["net_pnl_usdt"]) > Decimal(recent["btc_buy_hold_net_pnl_usdt"]),
            "missing result" if recent is None else f"strategy={recent['net_pnl_usdt']}; BTC_BH={recent['btc_buy_hold_net_pnl_usdt']}")
    failed = sum(x["status"] != "PASS" for x in checks)
    return {
        "overall": "PASS" if failed == 0 else "FAIL",
        "passed": len(checks) - failed, "failed": failed, "checks": checks,
        "candidate_origin": "Wilder DMI/ADX preregistered in Issue #168; frozen ATR sizing reused from Issue #143.",
        "promotion_note": "Historical archive and related trend ideas were inspected previously; any historical pass still requires prospective shadow validation. NO_TRADE.",
    }


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    engine.CANDIDATE = CANDIDATE
    rules = engine._rules()
    items, records, data_sha, unknown_comp, unknown_final = engine._fetch(rules)
    entries, exits, weights, atr14, ema200, dmi = dmi_signals_and_weights(items)
    indicator_rows = []
    for i, item in enumerate(items):
        indicator_rows.append({
            "index": i, "timestamp_utc": item.payload.start.isoformat(), "close": str(item.payload.close),
            "true_range": None if dmi["true_range"][i] is None else str(dmi["true_range"][i]),
            "plus_dm": None if dmi["plus_dm"][i] is None else str(dmi["plus_dm"][i]),
            "minus_dm": None if dmi["minus_dm"][i] is None else str(dmi["minus_dm"][i]),
            "smoothed_tr": None if dmi["smoothed_tr"][i] is None else str(dmi["smoothed_tr"][i]),
            "smoothed_plus_dm": None if dmi["smoothed_plus_dm"][i] is None else str(dmi["smoothed_plus_dm"][i]),
            "smoothed_minus_dm": None if dmi["smoothed_minus_dm"][i] is None else str(dmi["smoothed_minus_dm"][i]),
            "plus_di14": None if dmi["plus_di"][i] is None else str(dmi["plus_di"][i]),
            "minus_di14": None if dmi["minus_di"][i] is None else str(dmi["minus_di"][i]),
            "dx14": None if dmi["dx"][i] is None else str(dmi["dx"][i]),
            "adx14": None if dmi["adx"][i] is None else str(dmi["adx"][i]),
            "ema200": None if ema200[i] is None else str(ema200[i]),
            "atr14_simple_mean": None if atr14[i] is None else str(atr14[i]),
            "risk_weight": None if weights[i] is None else str(weights[i]),
            "entry_signal": entries[i], "exit_signal": exits[i],
        })
    indicator_sha = hashlib.sha256(json.dumps(indicator_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    evidence = {
        "protocol": PROTOCOL, "preregistration_issue": 168, "candidate": CANDIDATE,
        "symbol": engine.SYMBOL, "interval": engine.INTERVAL,
        "evaluation_start": engine.FULL_START.isoformat(), "evaluation_end_exclusive": engine.END.isoformat(),
        "warmup_start": engine.WARMUP_START.isoformat(), "warmup_bars": engine.WARMUP_BARS,
        "expected_bars": engine.EXPECTED_BARS, "observed_bars": len(items),
        "dataset_sha256": data_sha, "indicator_series_sha256": indicator_sha,
        "entry_signal_count": sum(entries), "exit_condition_count": sum(exits),
        "plus_di_available_count": sum(x is not None for x in dmi["plus_di"]),
        "minus_di_available_count": sum(x is not None for x in dmi["minus_di"]),
        "dx_available_count": sum(x is not None for x in dmi["dx"]),
        "adx_available_count": sum(x is not None for x in dmi["adx"]),
        "market_data_completeness_unknown_count": unknown_comp, "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot_sha256": rules["sha256"],
            "scope": "current public filter only; historical filters not established",
        },
        "frozen_rule": {
            "dmi_period": DMI_PERIOD, "adx_threshold": str(ADX_MIN),
            "wilder_smoothing": "initial sums bars 1..14, then prior - prior/14 + current",
            "adx_seed": "mean of first 14 valid DX values, then Wilder smoothing",
            "entry": "+DI crosses above -DI; ADX14>=25; close>EMA200",
            "exit": "-DI crosses above +DI or close<EMA200",
            "execution": "signal after completed daily close; next daily open; terminal close liquidation",
            "atr14_sizing": "simple 14-bar mean true range; min(95%,20%/((ATR14/close)*sqrt(365))) frozen at signal close",
            "max_entries_rolling_30d": engine.MAX_ENTRIES_30D,
        },
        "cost_scenarios": [
            {"name": name, "fee_per_fill": str(fee), "assumed_full_spread_bps": str(spread),
             "assumed_slippage_bps_per_fill": str(slippage)}
            for name, fee, spread, slippage in engine.SCENARIOS
        ],
        "retrospective_selection_warning": True, "data": records, "indicator_rows": indicator_rows,
    }
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    results, all_fills, all_curves = [], [], []
    for window, start, end in engine.WINDOWS:
        for capital in engine.CAPITALS:
            for cost, fee, spread, slippage in engine.SCENARIOS:
                result, fills, curve = engine._simulate(
                    items, rules, entries, exits, weights, atr14, ema200,
                    window, start, end, capital, cost, fee, spread, slippage
                )
                for fill in fills:
                    if fill.get("side") in ("BUY", "REJECTED_BUY"):
                        fill["reason"] = "+DI crossed above -DI with ADX14>=25 and close>EMA200; ATR14 sizing frozen at signal close"
                    elif fill.get("side") == "SELL":
                        fill["reason"] = "-DI crossed above +DI or close<EMA200; next-open or terminal close"
                bh, bh_fees = engine._buy_hold(items, rules, start, end, capital, fee, spread, slippage)
                result.update({
                    "btc_buy_hold_final_equity_usdt": str(bh), "btc_buy_hold_net_pnl_usdt": str(bh-capital),
                    "btc_buy_hold_fees_usdt": str(bh_fees), "cash_final_equity_usdt": str(capital),
                    "dataset_sha256": data_sha,
                })
                results.append(result)
                all_fills.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **fill} for fill in fills)
                all_curves.extend({"capital_initial_usdt": str(capital), "cost_scenario": cost, **point} for point in curve)

    for filename, rows in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with (output / filename).open("w", newline="", encoding="utf-8") as handle:
            if rows:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            else:
                handle.write("\n")
    gates = frozen_gate(results)
    evidence["strategy_gate"] = gates["overall"]
    evidence["output_counts"] = {"summary_rows": len(results), "fill_rows": len(all_fills), "equity_rows": len(all_curves)}
    (output / "dataset-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    (output / "gate_results.json").write_text(json.dumps(gates, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    report = [
        "# BTC Wilder DMI/ADX crossover + EMA200 + ATR14 sizing", "",
        "Frozen economic gate: " + gates["overall"], "",
        f"Daily candles={len(items)}; dataset SHA-256={data_sha}; indicator SHA-256={indicator_sha}.",
        f"Entry signals={sum(entries)}; exit-condition bars={sum(exits)}.",
        f"Available +DI={evidence['plus_di_available_count']}; -DI={evidence['minus_di_available_count']}; DX={evidence['dx_available_count']}; ADX={evidence['adx_available_count']}.",
        f"Market completeness UNKNOWN={unknown_comp}; candle finality UNKNOWN={unknown_final}.",
        "Historical filters, point-in-time availability and executable historic quotes remain UNKNOWN.",
        "Spread/slippage are assumptions, not observed historic order-book quotes.", "",
        "| Window | Capital | Cost | Net PnL USDT | Return % | Max DD % | Round trips | BTC B&H PnL | Fees USDT |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        report.append(
            f"| {row['window']} | {row['capital_initial_usdt']} | {row['cost_scenario']} | "
            f"{Decimal(row['net_pnl_usdt']):+.4f} | {Decimal(row['return_pct']):+.2f}% | "
            f"{Decimal(row['max_drawdown_pct']):.2f}% | {row['completed_round_trips']} | "
            f"{Decimal(row['btc_buy_hold_net_pnl_usdt']):+.4f} | {Decimal(row['total_fees_usdt']):.4f} |"
        )
    report.extend(["", f"Passed: {gates['passed']}; failed: {gates['failed']}.", "",
                   "| Gate | Status | Details |", "|---|---|---|"])
    report.extend([f"| {x['gate']} | {x['status']} | {x['details']} |" for x in gates["checks"]])
    report.extend(["", "Previously inspected archive; no pristine holdout claim. Any historical pass still needs prospective shadow validation.",
                   "No live orders, leverage, private endpoints or main changes. NO_TRADE.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({
        "status": "COMPLETED", "rows": len(items), "dataset_sha256": data_sha,
        "entries": sum(entries), "exits": sum(exits), "adx_ready": evidence["adx_available_count"],
        "gate": gates["overall"], "passed": gates["passed"], "failed": gates["failed"], "main_changed": False,
    }, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="dmi-adx-ema200-atr-output")
    return run(Path(parser.parse_args(argv).output_dir))


if __name__ == "__main__":
    raise SystemExit(main())
