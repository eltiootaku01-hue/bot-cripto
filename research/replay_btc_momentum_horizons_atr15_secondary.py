"""Pre-registered BTC 90d momentum strategy with fixed ATR-based exposure sizing.

Research-only risk overlay to address excessive drawdown seen in issue #142.
No private API, keys, orders, leverage, or shorting.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from bot_obrero.binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from bot_obrero.binance_spot import BinanceSpotRestConfig
from bot_obrero.virtual_backtest import _market_data_record, _validate_history

UTC = timezone.utc
SYMBOL = "BTCUSDT"
INTERVAL = "1d"
FULL_START = datetime(2020, 1, 1, tzinfo=UTC)
END = datetime(2026, 10, 22, tzinfo=UTC)
BAR = timedelta(days=1)
WARMUP_BARS = 400
WARMUP_START = FULL_START - BAR * WARMUP_BARS
EXPECTED_BARS = WARMUP_BARS + (END - FULL_START).days
ZERO, ONE = Decimal("0"), Decimal("1")
BPS = Decimal("10000")
MAX_POSITION_FRACTION = Decimal("0.95")
TARGET_ANNUAL_ATR_PROXY = Decimal("0.15")
TRADING_DAYS_PER_YEAR = Decimal("365")
MAX_ENTRIES_30D = 5
CAPITALS = (Decimal("60"), Decimal("100"))
SCENARIOS = (
    ("BASE", Decimal("0.001"), Decimal("10"), Decimal("5")),
    ("STRESS_1", Decimal("0.001"), Decimal("25"), Decimal("10")),
    ("STRESS_2", Decimal("0.002"), Decimal("50"), Decimal("20")),
)
CANDIDATE_CONFIGS = (
    ("BTC_MOMENTUM_60D_CROSS0_SMA200_ATR14_TARGET15", 60),
    ("BTC_MOMENTUM_126D_CROSS0_SMA200_ATR14_TARGET15", 126),
    ("BTC_MOMENTUM_180D_CROSS0_SMA200_ATR14_TARGET15", 180),
)
CANDIDATES = tuple(name for name, _ in CANDIDATE_CONFIGS)
WINDOWS = (
    ("FULL_2020_2026", FULL_START, END),
    ("HOLDOUT_2020_2022", datetime(2020, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
    ("RECENT_2023_2026", datetime(2023, 1, 1, tzinfo=UTC), END),
    ("2020", datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
    ("2021", datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
    ("2022", datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
    ("2023", datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
    ("2024", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
    ("2025", datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    ("2026_YTD", datetime(2026, 1, 1, tzinfo=UTC), END),
)


def _rules():
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    instrument = metadata.resolve_active(SYMBOL)
    payload = metadata._request(SYMBOL)
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list) or len(symbols) != 1 or not isinstance(symbols[0], dict):
        raise RuntimeError("FAIL-CLOSED: ExchangeInfo did not return exactly one BTCUSDT record.")
    record = symbols[0]
    if record.get("symbol") != SYMBOL or record.get("status") != "TRADING" or record.get("isSpotTradingAllowed") is not True:
        raise RuntimeError("FAIL-CLOSED: ExchangeInfo does not confirm active BTCUSDT Spot.")
    filters = record.get("filters")
    if not isinstance(filters, list):
        raise RuntimeError("FAIL-CLOSED: BTCUSDT filters missing.")
    lot = next((f for f in filters if isinstance(f, dict) and f.get("filterType") == "LOT_SIZE"), None)
    notion = next((f for f in filters if isinstance(f, dict) and f.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL")), None)
    if not isinstance(lot, dict) or not isinstance(notion, dict):
        raise RuntimeError("FAIL-CLOSED: BTCUSDT LOT_SIZE/minimum-notional missing.")
    try:
        step, min_qty, min_notional = Decimal(lot["stepSize"]), Decimal(lot["minQty"]), Decimal(notion["minNotional"])
    except (KeyError, TypeError, ArithmeticError, ValueError) as exc:
        raise RuntimeError("FAIL-CLOSED: malformed BTCUSDT filters.") from exc
    if min(step, min_qty, min_notional) <= ZERO:
        raise RuntimeError("FAIL-CLOSED: nonpositive BTCUSDT filter.")
    snapshot = {"LOT_SIZE": lot, "MIN_NOTIONAL_OR_NOTIONAL": notion}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {
        "instrument_id": instrument.instrument_id, "step": step, "min_qty": min_qty,
        "min_notional": min_notional, "snapshot": snapshot, "sha256": digest,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "scope": "current public Binance ExchangeInfo; historical filters not established",
    }


def _fetch(rules):
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    metadata.resolve_active(SYMBOL)
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    acquired = adapter.fetch_historical_market_data(
        symbol=SYMBOL, interval=INTERVAL, start_time=int(WARMUP_START.timestamp() * 1000),
        end_time=int(END.timestamp() * 1000), page_limit=1000, max_requests=5,
    )
    items = sorted((x for x in acquired if WARMUP_START <= x.payload.start < END), key=lambda x: x.payload.start)
    expected = [WARMUP_START + BAR * i for i in range(EXPECTED_BARS)]
    observed = [x.payload.start for x in items]
    if observed != expected:
        raise RuntimeError(
            f"FAIL-CLOSED: expected {EXPECTED_BARS} contiguous daily bars, got {len(items)}; "
            f"missing={len(set(expected)-set(observed))}; unexpected={len(set(observed)-set(expected))}. No interpolation."
        )
    accepted, excluded, unknown_comp, unknown_final = _validate_history(items, INTERVAL)
    if len(accepted) != EXPECTED_BARS or excluded:
        raise RuntimeError(f"FAIL-CLOSED: accepted={len(accepted)}, excluded={excluded}, expected={EXPECTED_BARS}.")
    if any(x.instrument.instrument_id != rules["instrument_id"] for x in items):
        raise RuntimeError("FAIL-CLOSED: unexpected BTCUSDT identity.")
    records = [_market_data_record(x) for x in items]
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return items, records, digest, unknown_comp, unknown_final


def _sma(values, period):
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1:i + 1], ZERO) / Decimal(period)
    return result


def _momentum_signals_and_weights(items):
    closes = [x.payload.close for x in items]
    highs = [x.payload.high for x in items]
    lows = [x.payload.low for x in items]
    sma200 = _sma(closes, 200)
    true_ranges = []
    for i, item in enumerate(items):
        high, low = highs[i], lows[i]
        if i == 0:
            tr = high - low
        else:
            prev_close = closes[i - 1]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    atr14 = [None] * len(items)
    for i in range(13, len(items)):
        atr14[i] = sum(true_ranges[i - 13:i + 1], ZERO) / Decimal("14")

    weights = [None] * len(items)
    for i in range(199, len(items)):
        if atr14[i] is None or atr14[i] <= ZERO or closes[i] <= ZERO:
            continue
        atr_fraction = atr14[i] / closes[i]
        denom = atr_fraction * TRADING_DAYS_PER_YEAR.sqrt()
        if denom > ZERO:
            weight = min(MAX_POSITION_FRACTION, TARGET_ANNUAL_ATR_PROXY / denom)
            weights[i] = weight if weight > ZERO else None

    entries = {name: [False] * len(items) for name in CANDIDATES}
    exits = {name: [False] * len(items) for name in CANDIDATES}
    for name, lookback in CANDIDATE_CONFIGS:
        for i in range(max(200, lookback + 1), len(items)):
            momentum = closes[i] / closes[i - lookback] - ONE
            previous_momentum = closes[i - 1] / closes[i - 1 - lookback] - ONE
            entries[name][i] = (
                momentum > ZERO
                and previous_momentum <= ZERO
                and sma200[i] is not None
                and closes[i] > sma200[i]
            )
            exits[name][i] = momentum <= ZERO or (
                sma200[i] is not None and closes[i] < sma200[i]
            )
    return entries, exits, weights, atr14, sma200

def _adverse(spread, slip):
    return (slip + spread / Decimal("2")) / BPS


def _qty(raw, rules):
    return (raw / rules["step"]).to_integral_value(rounding=ROUND_DOWN) * rules["step"]


def _buy_hold(items, rules, start, end, capital, fee, spread, slip):
    evaluation = [x for x in items if start <= x.payload.start < end]
    expected = (end - start).days
    if len(evaluation) != expected:
        raise RuntimeError(f"FAIL-CLOSED B&H {start.isoformat()}: rows={len(evaluation)}, expected={expected}.")
    adverse = _adverse(spread, slip)
    price_in = evaluation[0].payload.open * (ONE + adverse)
    qty = _qty(capital * MAX_POSITION_FRACTION / (price_in * (ONE + fee)), rules)
    notional = qty * price_in
    if qty < rules["min_qty"] or notional < rules["min_notional"]:
        raise RuntimeError("FAIL-CLOSED: BTC B&H violates current lot/min-notional assumptions.")
    buy_fee = notional * fee
    quote = capital - notional - buy_fee
    price_out = evaluation[-1].payload.close * (ONE - adverse)
    sell_notional = qty * price_out
    sell_fee = sell_notional * fee
    return quote + sell_notional - sell_fee, buy_fee + sell_fee


def _simulate(candidate, items, rules, entries, exits, weights, atr14, window, start, end, capital, cost, fee, spread, slip):
    history, excluded, unknown_comp, unknown_final = _validate_history(items, INTERVAL)
    evaluation = [x for x in history if start <= x.payload.start < end]
    expected = (end - start).days
    if len(evaluation) != expected:
        raise RuntimeError(f"FAIL-CLOSED {window}: rows={len(evaluation)}, expected={expected}.")
    idxmap = {x.payload.start: i for i, x in enumerate(history)}
    quote, qty, entry_cost = capital, ZERO, ZERO
    fees = ZERO
    fills, curve, buy_times = [], [], []
    peak, pending = capital, None
    for n, item in enumerate(evaluation):
        candle = item.payload
        i = idxmap[candle.start]
        if pending is not None:
            adverse = _adverse(spread, slip)
            if pending[0] == "BUY" and qty == ZERO:
                position_weight = pending[1]
                if position_weight is None or not ZERO < position_weight <= MAX_POSITION_FRACTION:
                    raise RuntimeError("FAIL-CLOSED: invalid pending risk weight.")
                price = candle.open * (ONE + adverse)
                bought_qty = _qty(quote * position_weight / (price * (ONE + fee)), rules)
                notional = bought_qty * price
                cutoff = candle.start - timedelta(days=30)
                buy_times = [t for t in buy_times if cutoff < t <= candle.start]
                reason = None
                if bought_qty < rules["min_qty"]:
                    reason = "MIN_QTY"
                elif notional < rules["min_notional"]:
                    reason = "MIN_NOTIONAL"
                elif notional * (ONE + fee) > quote:
                    reason = "COSTS_EXCEED_BALANCE"
                elif len(buy_times) >= MAX_ENTRIES_30D:
                    reason = "MAX_ENTRIES_30D"
                if reason:
                    fills.append({
                        "candidate": candidate, "window": window, "timestamp_utc": candle.start.isoformat(),
                        "side": "REJECTED_BUY", "quantity_btc": str(bought_qty), "price_usdt": str(price),
                        "fee_usdt": "0", "realized_pnl_usdt": "", "risk_weight": str(position_weight), "reason": reason,
                    })
                else:
                    buy_fee = notional * fee
                    entry_cost = notional + buy_fee
                    quote -= entry_cost
                    qty = bought_qty
                    fees += buy_fee
                    buy_times.append(candle.start)
                    fills.append({
                        "candidate": candidate, "window": window, "timestamp_utc": candle.start.isoformat(),
                        "side": "BUY", "quantity_btc": str(qty), "price_usdt": str(price),
                        "fee_usdt": str(buy_fee), "realized_pnl_usdt": "", "risk_weight": str(position_weight),
                        "atr14_at_signal": str(atr14[i - 1] if i > 0 and atr14[i - 1] is not None else ""),
                        "reason": "completed momentum-cross signal; ATR14 sizing fixed on signal close; next open fill",
                    })
            elif pending[0] == "SELL" and qty > ZERO:
                price = candle.open * (ONE - adverse)
                notional = qty * price
                sell_fee = notional * fee
                proceeds = notional - sell_fee
                realized = proceeds - entry_cost
                quote += proceeds
                fees += sell_fee
                fills.append({
                    "candidate": candidate, "window": window, "timestamp_utc": candle.start.isoformat(),
                    "side": "SELL", "quantity_btc": str(qty), "price_usdt": str(price),
                    "fee_usdt": str(sell_fee), "realized_pnl_usdt": str(realized), "risk_weight": "",
                    "reason": "momentum <= 0 or close < SMA200; next open fill",
                })
                qty, entry_cost = ZERO, ZERO
            pending = None

        adverse = _adverse(spread, slip)
        equity = quote + qty * candle.close * (ONE - adverse) * (ONE - fee)
        peak = max(peak, equity)
        curve.append({
            "candidate": candidate, "window": window, "timestamp_utc": candle.end.isoformat(),
            "equity_usdt": equity, "position_btc": qty, "drawdown": equity / peak - ONE if peak else ZERO,
        })
        if n >= len(evaluation) - 1:
            continue
        if qty == ZERO and entries[candidate][i]:
            cutoff = candle.end - timedelta(days=30)
            recent = [t for t in buy_times if cutoff < t <= candle.end]
            if len(recent) < MAX_ENTRIES_30D and weights[i] is not None:
                pending = ("BUY", weights[i])
        elif qty > ZERO and exits[candidate][i]:
            pending = ("SELL", None)

    if qty > ZERO:
        last = evaluation[-1].payload
        price = last.close * (ONE - _adverse(spread, slip))
        notional = qty * price
        exit_fee = notional * fee
        proceeds = notional - exit_fee
        realized = proceeds - entry_cost
        quote += proceeds
        fees += exit_fee
        fills.append({
            "candidate": candidate, "window": window, "timestamp_utc": last.end.isoformat(), "side": "SELL",
            "quantity_btc": str(qty), "price_usdt": str(price), "fee_usdt": str(exit_fee),
            "realized_pnl_usdt": str(realized), "risk_weight": "", "reason": "TERMINAL_LIQUIDATION at window close",
        })
        qty = ZERO
        peak = max(peak, curve[-1]["equity_usdt"])
        curve[-1] = {
            "candidate": candidate, "window": window, "timestamp_utc": curve[-1]["timestamp_utc"],
            "equity_usdt": quote, "position_btc": ZERO, "drawdown": quote / peak - ONE if peak else ZERO,
        }

    final = quote if qty == ZERO else quote + qty * evaluation[-1].payload.close * (
        ONE - _adverse(spread, slip)
    ) * (ONE - fee)
    sells = [f for f in fills if f["candidate"] == candidate and f["window"] == window and f["side"] == "SELL"]
    wins = sum(Decimal(f["realized_pnl_usdt"]) > ZERO for f in sells if f["realized_pnl_usdt"])
    losses = sum(Decimal(f["realized_pnl_usdt"]) < ZERO for f in sells if f["realized_pnl_usdt"])
    result = {
        "candidate": candidate, "window": window, "window_start": start.isoformat(), "window_end_exclusive": end.isoformat(),
        "capital_initial_usdt": str(capital), "cost_scenario": cost,
        "final_equity_usdt": str(final), "net_pnl_usdt": str(final - capital),
        "return_pct": str((final / capital - ONE) * Decimal("100")),
        "max_drawdown_pct": str(min((x["drawdown"] for x in curve), default=ZERO) * Decimal("100")),
        "completed_round_trips": len(sells), "wins": int(wins), "losses": int(losses),
        "total_fees_usdt": str(fees),
        "time_in_market_pct": str(Decimal(sum(x["position_btc"] > ZERO for x in curve)) / Decimal(len(curve)) * Decimal("100")),
        "buy_count": sum(f["candidate"] == candidate and f["window"] == window and f["side"] == "BUY" for f in fills),
        "rejected_buys": sum(f["candidate"] == candidate and f["window"] == window and f["side"] == "REJECTED_BUY" for f in fills),
        "excluded_history_records": excluded, "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
    }
    return result, fills, curve

def main():
    output = Path("momentum-horizons-output")
    output.mkdir(exist_ok=True)
    print("Frozen BTC momentum-cross screen: lookbacks 60/126/180d, close>SMA200, ATR14 target proxy 15%.")
    print("Weight=min(0.95, 0.15 / ((ATR14/close)*sqrt(365))); fixed at completed signal close, no daily rebalance.")
    print("Research HEAD:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    print(f"Evaluation UTC: [{FULL_START.isoformat()}, {END.isoformat()}); warm-up={WARMUP_BARS} daily bars from {WARMUP_START.isoformat()}.")
    print("Virtual Spot only; preregistered protocol: GitHub issue #145.")

    rules = _rules()
    items, records, data_sha, unknown_comp, unknown_final = _fetch(rules)
    entries, exits, weights, atr14, sma200 = _momentum_signals_and_weights(items)
    sample_weights = [w for w in weights if w is not None]
    print(
        f"BTCUSDT bars={len(items)} SHA256={data_sha}; LOT_SIZE step={rules['step']}; "
        f"minQty={rules['min_qty']}; minNotional={rules['min_notional']}; filterSHA={rules['sha256']}; "
        f"market completeness UNKNOWN={unknown_comp}; candle finality UNKNOWN={unknown_final}"
    )
    evidence = {
        "symbol": SYMBOL, "interval": INTERVAL,
        "evaluation_start": FULL_START.isoformat(), "evaluation_end_exclusive": END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(), "warmup_bars": WARMUP_BARS,
        "expected_bars": EXPECTED_BARS, "observed_bars": len(items), "data_gaps_interpolated": 0,
        "dataset_sha256": data_sha,
        "market_data_completeness_unknown_count": unknown_comp,
        "candle_finality_unknown_count": unknown_final,
        "current_exchange_filters": {
            "step_size": str(rules["step"]), "min_qty": str(rules["min_qty"]),
            "min_notional": str(rules["min_notional"]), "snapshot": rules["snapshot"],
            "sha256": rules["sha256"], "retrieved_at_utc": rules["retrieved_at"], "scope": rules["scope"],
        },
        "candidate_rules": {
            name: {
                "lookback_days": lookback,
                "entry": f"{lookback}-day close momentum crosses from <=0 to >0 and close>SMA200",
                "exit": f"{lookback}-day momentum<=0 or close<SMA200",
            } for name, lookback in CANDIDATE_CONFIGS
        },
        "risk_sizing": {
            "atr_period": 14, "target_annualized_atr_proxy": str(TARGET_ANNUAL_ATR_PROXY),
            "formula": "min(0.95, 0.15 / ((ATR14/close) * sqrt(365)))",
            "min_computed_weight": str(min(sample_weights)) if sample_weights else None,
            "max_computed_weight": str(max(sample_weights)) if sample_weights else None,
            "note": "ATR scaled exposure proxy, not a guarantee of realized volatility; held constant until exit",
        },
        "windows": [{"name": n, "start": s.isoformat(), "end_exclusive": e.isoformat()} for n, s, e in WINDOWS],
        "cost_scenarios": [
            {"name": n, "fee_per_fill": str(fee), "assumed_full_spread_bps": str(spread),
             "assumed_slippage_bps_per_fill": str(slippage)}
            for n, fee, spread, slippage in SCENARIOS
        ],
        "data": records,
    }
    (output / "dataset-evidence.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    results, all_fills, all_curves = [], [], []
    for candidate, lookback in CANDIDATE_CONFIGS:
        print(f"\nCANDIDATE {candidate} (lookback={lookback}d)")
        for window, start, end in WINDOWS:
            for capital in CAPITALS:
                for cost_name, fee, spread, slip in SCENARIOS:
                    result, fills, curve = _simulate(
                        candidate, items, rules, entries, exits, weights, atr14,
                        window, start, end, capital, cost_name, fee, spread, slip,
                    )
                    bh_final, bh_fees = _buy_hold(items, rules, start, end, capital, fee, spread, slip)
                    result.update({
                        "lookback_days": lookback,
                        "btc_buy_hold_final_equity_usdt": str(bh_final),
                        "btc_buy_hold_net_pnl_usdt": str(bh_final - capital),
                        "btc_buy_hold_fees_usdt": str(bh_fees),
                        "cash_final_equity_usdt": str(capital),
                        "dataset_sha256": data_sha,
                        "filter_sha256": rules["sha256"],
                    })
                    results.append(result)
                    all_fills.extend(
                        {"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **fill}
                        for fill in fills
                    )
                    all_curves.extend(
                        {"capital_initial_usdt": str(capital), "cost_scenario": cost_name, **point}
                        for point in curve
                    )
                    print(
                        f"RESULT | {candidate} | {window} | capital={capital} | {cost_name} | "
                        f"PnL={Decimal(result['net_pnl_usdt']):+.6f} | return={Decimal(result['return_pct']):+.3f}% | "
                        f"DD={Decimal(result['max_drawdown_pct']):.3f}% | cycles={result['completed_round_trips']} | "
                        f"BTC_BH={Decimal(result['btc_buy_hold_net_pnl_usdt']):+.4f}"
                    )

    for filename, rows in (("summary.csv", results), ("fills.csv", all_fills), ("equity.csv", all_curves)):
        with (output / filename).open("w", newline="", encoding="utf-8") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            else:
                f.write("\n")

    stress = [r for r in results if r["cost_scenario"] == "STRESS_2"]
    annual_windows = ("2020", "2021", "2022", "2023", "2024", "2025", "2026_YTD")
    gates = {}
    for candidate, lookback in CANDIDATE_CONFIGS:
        subset = [r for r in stress if r["candidate"] == candidate]
        by_window = {name: [r for r in subset if r["window"] == name] for name, _, _ in WINDOWS}

        def two_capital_rows(name):
            return len(by_window.get(name, [])) == len(CAPITALS)

        def pnl_positive(name):
            return two_capital_rows(name) and all(
                Decimal(r["net_pnl_usdt"]) > ZERO for r in by_window[name]
            )

        def full_block_ok(name):
            return two_capital_rows(name) and all(
                Decimal(r["net_pnl_usdt"]) > ZERO
                and r["completed_round_trips"] >= 5
                and Decimal(r["max_drawdown_pct"]) >= Decimal("-15")
                for r in by_window[name]
            )

        full_ok = full_block_ok("FULL_2020_2026")
        holdout_ok = full_block_ok("HOLDOUT_2020_2022")
        recent_ok = full_block_ok("RECENT_2023_2026")

        annual_rows = [r for name in annual_windows for r in by_window.get(name, [])]
        annual_dd_ok = len(annual_rows) == len(annual_windows) * len(CAPITALS) and all(
            Decimal(r["max_drawdown_pct"]) >= Decimal("-15") for r in annual_rows
        )
        bear_rows = by_window.get("2022", [])
        bear_ok = len(bear_rows) == len(CAPITALS) and all(
            Decimal(r["net_pnl_usdt"]) >= ZERO for r in bear_rows
        )

        profitable_years = {}
        for capital in CAPITALS:
            profitable_years[str(capital)] = sum(
                Decimal(r["net_pnl_usdt"]) > ZERO
                for r in annual_rows
                if Decimal(r["capital_initial_usdt"]) == capital
            )
        four_years_ok = all(profitable_years[str(capital)] >= 4 for capital in CAPITALS)

        ytd_rows = by_window.get("2026_YTD", [])
        ytd_ok = len(ytd_rows) == len(CAPITALS) and all(
            Decimal(r["net_pnl_usdt"]) > ZERO
            and Decimal(r["net_pnl_usdt"]) > Decimal(r["btc_buy_hold_net_pnl_usdt"])
            for r in ytd_rows
        )
        gates[candidate] = {
            "full_period_pass": full_ok,
            "2020_2022_holdout_pass": holdout_ok,
            "2023_2026_recent_pass": recent_ok,
            "annual_drawdown_pass": annual_dd_ok,
            "2022_cash_or_profit_pass": bear_ok,
            "profitable_year_counts_by_capital": profitable_years,
            "at_least_4_profitable_year_windows_each_capital": four_years_ok,
            "2026_ytd_profit_and_beat_btc_buy_hold": ytd_ok,
            "passes_all_preregistered_gates": (
                full_ok and holdout_ok and recent_ok and annual_dd_ok and bear_ok and four_years_ok and ytd_ok
            ),
        }

    report = [
        "# BTC momentum 60/126/180-day horizons — ATR14 target proxy 15%", "",
        "**Disposition:** retrospective research only; NO_TRADE remains in force.", "",
        f"- Full interval: [{FULL_START.isoformat()}, {END.isoformat()}); warm-up: {WARMUP_BARS} bars; candles: {len(items)}.",
        f"- Dataset SHA-256: {data_sha}; current filter snapshot SHA-256: {rules['sha256']}.",
        f"- Current public LOT_SIZE step {rules['step']}, minQty {rules['min_qty']}, minNotional {rules['min_notional']}; historical filters are UNKNOWN.",
        "- Signal: lookback momentum crosses <=0 to >0 while close>SMA200; exit when momentum<=0 or close<SMA200. Signals are on completed daily candles; fills at next daily open; terminal liquidation each window.",
        "- Risk layer: weight=min(95%, 15%/((ATR14/close)*sqrt(365))); simple ATR14 mean true range, weight fixed on signal close and held to exit. This is an ATR proxy, not guaranteed realized volatility.",
        "- Cost assumptions: BASE fee 10 bps/fill, full assumed spread 10 bps, slippage 5 bps/fill; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread and slippage are not historical bookTicker observations.",
        "- Windows: full 2020–2026; reset-capital HOLDOUT 2020–2022 and RECENT 2023–2026; annual/YTD resets for 2020 through 2026 YTD.", "",
        "| Candidate | Window | Capital | Cost | Net PnL | Return | Max DD | Cycles | BTC B&H PnL |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        report.append(
            f"| {result['candidate']} | {result['window']} | {result['capital_initial_usdt']} | {result['cost_scenario']} | "
            f"{Decimal(result['net_pnl_usdt']):+.4f} | {Decimal(result['return_pct']):+.2f}% | "
            f"{Decimal(result['max_drawdown_pct']):.2f}% | {result['completed_round_trips']} | "
            f"{Decimal(result['btc_buy_hold_net_pnl_usdt']):+.4f} |"
        )
    report.extend(["", "## Preregistered promotion gate", ""])
    for candidate, gate in gates.items():
        report.append(
            f"- **{candidate}: {'PASS' if gate['passes_all_preregistered_gates'] else 'FAIL'}** — "
            f"{json.dumps(gate, sort_keys=True)}"
        )
    report.extend([
        "",
        "The gate requires positive STRESS_2 PnL for both capital levels in full, 2020–2022 and 2023–2026 blocks; >=5 cycles in each block; drawdown no worse than 15% in full and each annual/YTD slice; nonnegative 2022; at least 4 positive year/YTD slices at both capitals; and positive 2026 YTD profit greater than BTC B&H.",
        "",
        "The target-volatility proxy and tested momentum horizons are a new research configuration after prior screens. All candidates are tested in the same registered batch, but the work is still retrospective strategy development, not a blind prospective result. Point-in-time data availability is UNKNOWN and spread/slippage are assumed.",
        "",
    ])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("PROMOTION_GATES:", json.dumps(gates, sort_keys=True))
    print(f"Evidence written to {output}/.")


if __name__ == "__main__":
    main()
