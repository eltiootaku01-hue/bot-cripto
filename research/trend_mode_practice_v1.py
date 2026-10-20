from __future__ import annotations
import argparse, hashlib, json, math, platform
from collections import deque
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

EXPECTED_DATASET_SHA256 = "13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8"
EXPECTED_CONFIG_SHA256 = "2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a"
EXPECTED_RAW_SHA256 = "3851a560aafbaa44f7d57081bd12f7dfdae070c2abff3d65e122ad8fcbd6df36"
INITIAL_USDT = 31.0
POSITION_FRACTION = 0.95
MAX_ENTRIES_ROLLING_30D = 5
DONCHIAN_ENTRY = 40
DONCHIAN_EXIT = 20
EMA_PERIOD = 200
EMA_SLOPE_LOOKBACK = 5
ATR_PERIOD = 14
ATR_STOP_MULTIPLE = 3.0
COSTS = {
    "BASE": {"fee_rate": 0.001, "spread_bps": 10.0, "slippage_bps": 5.0},
    "STRESS_1": {"fee_rate": 0.001, "spread_bps": 25.0, "slippage_bps": 10.0},
    "STRESS_2": {"fee_rate": 0.002, "spread_bps": 50.0, "slippage_bps": 20.0},
}

class TrendReplayError(ValueError):
    pass

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def canonical_sha256(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())

def json_default(value: Any):
    if isinstance(value, np.generic): return value.item()
    if isinstance(value, pd.Timestamp): return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")

def load_dataset(path: Path):
    raw = path.read_bytes()
    raw_sha = sha256_bytes(raw)
    if raw_sha != EXPECTED_RAW_SHA256: raise TrendReplayError("PINNED_RAW_DATASET_SHA256_MISMATCH")
    bundle = json.loads(raw)
    if bundle.get("dataset_sha256") != EXPECTED_DATASET_SHA256: raise TrendReplayError("PINNED_DATASET_SHA256_MISMATCH")
    if bundle.get("configuration_sha256") != EXPECTED_CONFIG_SHA256: raise TrendReplayError("PINNED_CONFIGURATION_SHA256_MISMATCH")
    if canonical_sha256(bundle.get("data")) != EXPECTED_DATASET_SHA256: raise TrendReplayError("DATASET_HASH_RECOMPUTE_FAILED")
    if canonical_sha256(bundle.get("configuration")) != EXPECTED_CONFIG_SHA256: raise TrendReplayError("CONFIGURATION_HASH_RECOMPUTE_FAILED")
    frames = {}
    missing_availability, unknown_finality = {}, {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        records = bundle["data"]["history"][symbol]
        rows = []
        for item in records:
            candle = item["candle"]
            rows.append({"timestamp": candle["start"], **{k: candle[k] for k in ("open", "high", "low", "close", "volume")},
                "available_at": item.get("available_at"), "finality": candle.get("finality"),
                "candle_state": candle.get("candle_state"), "completeness": candle.get("completeness")})
        d = pd.DataFrame(rows)
        d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True, errors="raise")
        for col in ("open", "high", "low", "close", "volume"): d[col] = pd.to_numeric(d[col], errors="raise")
        if len(d) != 15360 or not d.timestamp.is_unique or not d.timestamp.is_monotonic_increasing:
            raise TrendReplayError(f"INVALID_BAR_COUNT_OR_CHRONOLOGY:{symbol}")
        if not (d.timestamp.diff().dropna() == pd.Timedelta(hours=1)).all(): raise TrendReplayError(f"HOURLY_GAP:{symbol}")
        if not (d[["open", "high", "low", "close"]] > 0).all().all(): raise TrendReplayError(f"INVALID_OHLC:{symbol}")
        if not (d.high >= d[["open", "close", "low"]].max(axis=1)).all(): raise TrendReplayError(f"INVALID_HIGH:{symbol}")
        if not (d.low <= d[["open", "close", "high"]].min(axis=1)).all(): raise TrendReplayError(f"INVALID_LOW:{symbol}")
        if not (d.candle_state == "CLOSED").all() or not (d.completeness == "COMPLETE").all():
            raise TrendReplayError(f"CANDLE_STATE_CONTRACT_FAILED:{symbol}")
        missing_availability[symbol] = int(d.available_at.isna().sum())
        unknown_finality[symbol] = int((d.finality.astype(str).str.upper() != "FINAL").sum())
        frames[symbol] = d.set_index("timestamp", drop=False)
    if not frames["BTCUSDT"].index.equals(frames["ETHUSDT"].index): raise TrendReplayError("BTC_ETH_HISTORY_NOT_ALIGNED")
    return bundle, frames, raw_sha, missing_availability, unknown_finality

def aggregate_4h(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only four contiguous UTC hourly candles; drop incomplete edge buckets."""
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None: raise TrendReplayError("UTC_DATETIME_INDEX_REQUIRED")
    rows = []
    for bucket, group in frame.groupby(frame.index.floor("4h"), sort=True):
        if len(group) != 4: continue
        expected = pd.date_range(bucket, periods=4, freq="h")
        if not group.index.equals(expected): continue
        rows.append({"timestamp": bucket, "open": float(group.open.iloc[0]), "high": float(group.high.max()),
            "low": float(group.low.min()), "close": float(group.close.iloc[-1]), "volume": float(group.volume.sum()), "source_hour_count": 4})
    result = pd.DataFrame(rows)
    if result.empty: raise TrendReplayError("NO_COMPLETE_4H_BARS")
    result["timestamp"] = pd.to_datetime(result.timestamp, utc=True)
    result = result.set_index("timestamp", drop=False)
    if not (result.source_hour_count == 4).all(): raise TrendReplayError("INCOMPLETE_4H_BUCKET")
    if not (result.index.to_series().diff().dropna() == pd.Timedelta(hours=4)).all(): raise TrendReplayError("GAP_IN_4H_BARS")
    return result

def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    d = frame.copy()
    previous_close = d.close.shift(1)
    tr = pd.concat([(d.high-d.low), (d.high-previous_close).abs(), (d.low-previous_close).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    d["ema200"] = d.close.ewm(span=EMA_PERIOD, adjust=False, min_periods=EMA_PERIOD).mean()
    d["ema200_slope_up"] = d.ema200 > d.ema200.shift(EMA_SLOPE_LOOKBACK)
    d["donchian_entry_high"] = d.high.shift(1).rolling(DONCHIAN_ENTRY, min_periods=DONCHIAN_ENTRY).max()
    d["donchian_exit_low"] = d.low.shift(1).rolling(DONCHIAN_EXIT, min_periods=DONCHIAN_EXIT).min()
    d["buy_signal"] = (d.close > d.donchian_entry_high) & (d.close > d.ema200) & d.ema200_slope_up & d.atr14.notna()
    d["breakout_strength"] = (d.close-d.donchian_entry_high)/d.atr14
    d["channel_exit_signal"] = d.close < d.donchian_exit_low
    return d

def cost_prices(reference_price: float, cost: dict[str, float], side: str) -> tuple[float, float]:
    if not math.isfinite(reference_price) or reference_price <= 0: raise TrendReplayError("INVALID_FILL_REFERENCE_PRICE")
    adverse = (cost["spread_bps"]/2 + cost["slippage_bps"])/10000
    if side == "BUY": return reference_price*(1+adverse), cost["fee_rate"]
    if side == "SELL": return reference_price*(1-adverse), cost["fee_rate"]
    raise TrendReplayError("UNKNOWN_ORDER_SIDE")

def _stop_reference_fill(open_price: float, low_price: float, stop_price: float) -> float | None:
    """Return conservative OHLC stop fill; gaps execute at open, never at a better stop price."""
    if not all(math.isfinite(v) and v > 0 for v in (open_price, low_price, stop_price)): raise TrendReplayError("INVALID_STOP_INPUT")
    if low_price > stop_price: return None
    return min(open_price, stop_price)

def _exit_position(position: dict[str, Any], reference_price: float, timestamp: pd.Timestamp, reason: str,
                   cash: float, cost: dict[str, float], trades: list[dict[str, Any]]) -> float:
    fill, fee_rate = cost_prices(reference_price, cost, "SELL")
    gross_proceeds = position["quantity"]*fill
    fees = gross_proceeds*fee_rate
    net_proceeds = gross_proceeds-fees
    net_return = net_proceeds/position["entry_cash_debit"]-1.0
    trades.append({"symbol": position["symbol"], "signal_time": position["signal_time"].isoformat(),
        "entry_time": position["entry_time"].isoformat(), "exit_time": timestamp.isoformat(),
        "entry_reference_open": position["entry_reference_open"], "entry_fill_price_ex_fee": position["entry_fill_price"],
        "exit_reference_price": reference_price, "exit_fill_price_ex_fee": fill, "quantity": position["quantity"],
        "entry_cash_debit": position["entry_cash_debit"],
        "gross_price_return_pct": 100*(reference_price/position["entry_reference_open"]-1),
        "net_trade_return_pct": 100*net_return, "net_proceeds": net_proceeds, "exit_reason": reason, "fee_rate_per_side": fee_rate})
    return cash+net_proceeds

def simulate(frames: dict[str, pd.DataFrame], cost: dict[str, float], initial: float = INITIAL_USDT) -> dict[str, Any]:
    indicators = {symbol: add_indicators(aggregate_4h(frames[symbol])) for symbol in ("BTCUSDT", "ETHUSDT")}
    common = indicators["BTCUSDT"].index.intersection(indicators["ETHUSDT"].index)
    if len(common) < EMA_PERIOD+DONCHIAN_ENTRY+10: raise TrendReplayError("INSUFFICIENT_4H_HISTORY")
    indicators = {s: indicators[s].loc[common].copy() for s in indicators}
    cash = float(initial); position = None; pending_exit = None; trades = []; equity_rows = []
    rejected = {"position_open": 0, "rolling_entry_limit": 0, "competing_signal": 0, "after_exit_guard": 0}
    entries = deque(); events = []
    equity_rows.append({"timestamp": common[0].isoformat(), "bar_start": common[0].isoformat(), "cash_usdt": cash,
        "position_symbol": None, "position_quantity": 0.0, "equity_net_liquidation_usdt": cash, "pending_exit": "NONE"})
    for i, ts in enumerate(common):
        exited_at_open = False
        # A channel exit was only known at the prior closed bar; execute at this bar's open.
        if position is not None and pending_exit is not None:
            row = indicators[position["symbol"]].loc[ts]
            exited_symbol = position["symbol"]
            cash = _exit_position(position, float(row.open), ts, "DONCHIAN20_NEXT_OPEN", cash, cost, trades)
            events.append({"timestamp": ts.isoformat(), "event": "EXIT", "symbol": exited_symbol, "reason": "DONCHIAN20_NEXT_OPEN"})
            position = None; pending_exit = None; exited_at_open = True
        # Signals for this open come exclusively from the previous completed candle.
        candidates = []
        if i > 0:
            for symbol, d in indicators.items():
                prev = d.iloc[i-1]
                if bool(prev.buy_signal): candidates.append((float(prev.breakout_strength), symbol, prev))
        if position is not None:
            rejected["position_open"] += len(candidates)
        elif exited_at_open:
            rejected["after_exit_guard"] += len(candidates)
        elif i > 0 and candidates:
            candidates.sort(key=lambda x: (-x[0], x[1]))
            strength, symbol, signal_row = candidates[0]
            while entries and (ts-entries[0]) > pd.Timedelta(days=30): entries.popleft()
            if len(entries) >= MAX_ENTRIES_ROLLING_30D:
                rejected["rolling_entry_limit"] += len(candidates)
            else:
                if len(candidates) > 1: rejected["competing_signal"] += len(candidates)-1
                reference = float(indicators[symbol].iloc[i].open)
                fill, fee_rate = cost_prices(reference, cost, "BUY")
                budget = cash*POSITION_FRACTION
                quantity = budget/(fill*(1+fee_rate))
                debit = quantity*fill*(1+fee_rate)
                if debit <= 0 or debit > cash+1e-9: raise TrendReplayError("BUY_ACCOUNTING_INVARIANT_FAILED")
                stop = reference-ATR_STOP_MULTIPLE*float(signal_row.atr14)
                if stop <= 0: raise TrendReplayError("ATR_STOP_NON_POSITIVE")
                position = {"symbol": symbol, "signal_time": signal_row.name+pd.Timedelta(hours=4),
                    "entry_time": ts, "entry_reference_open": reference, "entry_fill_price": fill, "quantity": quantity,
                    "entry_cash_debit": debit, "stop_price": stop, "atr_at_signal": float(signal_row.atr14)}
                cash -= debit; entries.append(ts)
                events.append({"timestamp": ts.isoformat(), "event": "ENTRY", "symbol": symbol,
                    "reason": "DONCHIAN40_BREAKOUT_EMA200_UP", "signal_strength_atr": strength})
        # Intrabar stop uses a conservative OHLC proxy; a gap through the stop fills at the open.
        if position is not None:
            bar = indicators[position["symbol"]].loc[ts]
            stop_ref = _stop_reference_fill(float(bar.open), float(bar.low), float(position["stop_price"]))
            if stop_ref is not None:
                ref = stop_ref
                timestamp = ts if ref == float(bar.open) else ts+pd.Timedelta(hours=4)
                reason = "ATR3_STOP_GAP" if ref == float(bar.open) and float(bar.open) < position["stop_price"] else "ATR3_STOP"
                cash = _exit_position(position, ref, timestamp, reason, cash, cost, trades)
                events.append({"timestamp": timestamp.isoformat(), "event": "EXIT", "symbol": position["symbol"], "reason": reason})
                position = None; pending_exit = None
            elif bool(bar.channel_exit_signal):
                pending_exit = "DONCHIAN20_NEXT_OPEN"
                events.append({"timestamp": (ts+pd.Timedelta(hours=4)).isoformat(), "event": "EXIT_SIGNAL",
                    "symbol": position["symbol"], "reason": "DONCHIAN20_NEXT_OPEN"})
            else:
                pending_exit = None
        # Open positions are marked at modeled net liquidation value; this mark is not a booked SELL fill.
        equity = float(cash)
        if position is not None:
            mark_fill, _ = cost_prices(float(indicators[position["symbol"]].loc[ts].close), cost, "SELL")
            equity += position["quantity"]*mark_fill*(1-cost["fee_rate"])
        if not math.isfinite(equity) or equity < 0: raise TrendReplayError("EQUITY_INVALID")
        equity_rows.append({"timestamp": (ts+pd.Timedelta(hours=4)).isoformat(), "bar_start": ts.isoformat(),
            "cash_usdt": cash, "position_symbol": position["symbol"] if position else None,
            "position_quantity": position["quantity"] if position else 0.0, "equity_net_liquidation_usdt": equity,
            "pending_exit": pending_exit or "NONE"})
    if position is not None:
        last = common[-1]; ref = float(indicators[position["symbol"]].loc[last].close); terminal_symbol = position["symbol"]
        cash = _exit_position(position, ref, last+pd.Timedelta(hours=4), "END_OF_ARCHIVE_LIQUIDATION_PROXY", cash, cost, trades)
        events.append({"timestamp": (last+pd.Timedelta(hours=4)).isoformat(), "event": "EXIT",
            "symbol": terminal_symbol, "reason": "END_OF_ARCHIVE_LIQUIDATION_PROXY"})
        position = None
        equity_rows[-1]["cash_usdt"] = cash; equity_rows[-1]["position_symbol"] = None
        equity_rows[-1]["position_quantity"] = 0.0; equity_rows[-1]["equity_net_liquidation_usdt"] = cash
        equity_rows[-1]["pending_exit"] = "NONE"
    eq = pd.DataFrame(equity_rows); eq["timestamp"] = pd.to_datetime(eq.timestamp, utc=True)
    eq["peak_equity"] = eq.equity_net_liquidation_usdt.cummax()
    eq["drawdown_pct"] = 100*(eq.equity_net_liquidation_usdt/eq.peak_equity-1)
    trade_frame = pd.DataFrame(trades)
    metrics = {"cost_scenario": "UNSET", "initial_usdt": initial, "final_usdt": float(eq.equity_net_liquidation_usdt.iloc[-1]),
        "net_return_pct": 100*(float(eq.equity_net_liquidation_usdt.iloc[-1])/initial-1), "max_drawdown_pct": float(eq.drawdown_pct.min()),
        "entries": len(trades), "winning_trades": int((trade_frame.net_trade_return_pct > 0).sum()) if len(trades) else 0,
        "win_rate_pct": 100*int((trade_frame.net_trade_return_pct > 0).sum())/len(trades) if len(trades) else 0.0,
        "mean_trade_net_pct": float(trade_frame.net_trade_return_pct.mean()) if len(trades) else None,
        "median_trade_net_pct": float(trade_frame.net_trade_return_pct.median()) if len(trades) else None,
        "positive_trades": int((trade_frame.net_trade_return_pct > 0).sum()) if len(trades) else 0,
        "exit_reasons": trade_frame.exit_reason.value_counts().to_dict() if len(trades) else {},
        "rejected_signals": rejected, "bar_count_4h": len(common), "open_position_at_end": position is not None}
    return {"metrics": metrics, "trades": trades, "equity": eq.to_dict(orient="records"), "events": events, "indicator_data": indicators}

def run_replay(dataset: Path, output: Path, source_sha: str = "LOCAL_UNCOMMITTED"):
    if output.exists() and any(output.iterdir()): raise TrendReplayError("OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    bundle, frames, raw_sha, missing_availability, unknown_finality = load_dataset(dataset)
    h4frames = {symbol: aggregate_4h(frames[symbol]) for symbol in ("BTCUSDT", "ETHUSDT")}
    scenario_results = {}; all_trades = []; all_equity = []
    for name, cost in COSTS.items():
        result = simulate(frames, cost, INITIAL_USDT); result["metrics"]["cost_scenario"] = name
        scenario_results[name] = {"metrics": result["metrics"], "trade_count": len(result["trades"]), "events_count": len(result["events"])}
        for j, trade in enumerate(result["trades"], 1): all_trades.append({"cost_scenario": name, "trade_number": j, **trade})
        for row in result["equity"]: all_equity.append({"cost_scenario": name, **row})
    info = {"schema_version": 1, "study": "TREND_MODE_PRACTICE_V1",
        "status": "HISTORICAL_OHLC_ENGINE_REPLAY_NOT_EXECUTABLE_PAPER_PROOF", "decision": "NO_TRADE",
        "source_git_sha": source_sha, "dataset_logical_sha256": bundle["dataset_sha256"],
        "dataset_configuration_sha256": bundle["configuration_sha256"], "dataset_raw_file_sha256": raw_sha,
        "time_start": str(frames["BTCUSDT"].index[0]), "time_end": str(frames["BTCUSDT"].index[-1]),
        "hourly_bars_per_asset": {s: len(frames[s]) for s in frames},
        "four_hour_bars_after_drop_partial": {s: len(h4frames[s]) for s in frames},
        "missing_available_at_count_by_asset": missing_availability, "unknown_finality_count_by_asset": unknown_finality,
        "parameters": {"timeframe": "4h", "donchian_entry_lookback": DONCHIAN_ENTRY, "donchian_exit_lookback": DONCHIAN_EXIT,
            "ema_period": EMA_PERIOD, "ema_slope_lookback": EMA_SLOPE_LOOKBACK, "atr_period": ATR_PERIOD,
            "atr_stop_multiple": ATR_STOP_MULTIPLE, "initial_usdt": INITIAL_USDT, "position_fraction": POSITION_FRACTION,
            "max_entries_per_rolling_30d": MAX_ENTRIES_ROLLING_30D, "single_open_position_across_BTC_ETH": True,
            "reentry_and_fill_timing": "signal on bar close, execute at next bar open proxy",
            "stop_gap_policy": "if low crosses stop, use min(open, stop), then apply costs"},
        "costs": COSTS, "scenario_results": scenario_results, "python_version": platform.python_version(),
        "numpy_version": np.__version__, "pandas_version": pd.__version__,
        "limitations": ["All historical available_at values are missing and candle finality is UNKNOWN; archived bars cannot prove historical point-in-time availability.",
            "OHLC-only replay uses next 4h open proxies; it has no historical bid/ask quotes and cannot test real order fills.",
            "Fractional quantities are simulated; historical exchange lot/step/min-notional metadata were not reconstructed.",
            "This exact mode is an engineering rehearsal on previously inspected history, not an untouched holdout or claim of profitable edge."]}
    (output/"result.json").write_text(json.dumps(info, indent=2, sort_keys=True, default=json_default, allow_nan=False)+"\n")
    pd.DataFrame(all_trades).to_csv(output/"trades.csv", index=False, float_format="%.12g")
    pd.DataFrame(all_equity).to_csv(output/"equity.csv", index=False, float_format="%.12g")
    monthly = []
    for name in COSTS:
        q = pd.DataFrame([x for x in all_equity if x["cost_scenario"] == name])
        q["timestamp"] = pd.to_datetime(q.timestamp, utc=True); q = q.set_index("timestamp")
        for month, group in q.groupby(q.index.tz_localize(None).to_period("M")):
            key = str(month); month_start = pd.Timestamp(key+"-01T00:00:00Z"); next_month = month_start+pd.offsets.MonthBegin(1)
            start_idx = int(q.index.searchsorted(month_start, side="left")); end_idx = int(q.index.searchsorted(next_month, side="left"))
            complete = start_idx < len(q) and end_idx < len(q) and q.index[start_idx] == month_start and q.index[end_idx] == next_month
            if start_idx >= len(q): continue
            first_eq = float(q.equity_net_liquidation_usdt.iloc[start_idx])
            last_eq = float(q.equity_net_liquidation_usdt.iloc[end_idx]) if complete else float(group.equity_net_liquidation_usdt.iloc[-1])
            bh = {}
            for symbol in ("BTCUSDT", "ETHUSDT"):
                h4 = h4frames[symbol]; start_bar = int(h4.index.searchsorted(month_start, side="left")); end_bar = int(h4.index.searchsorted(next_month, side="left"))
                if start_bar < len(h4) and end_bar < len(h4):
                    entry_ref = float(h4.open.iloc[start_bar]); exit_ref = float(h4.open.iloc[end_bar])
                    buy_fill, buy_fee = cost_prices(entry_ref, COSTS[name], "BUY"); qty = INITIAL_USDT/(buy_fill*(1+buy_fee))
                    sell_fill, sell_fee = cost_prices(exit_ref, COSTS[name], "SELL"); final = qty*sell_fill*(1-sell_fee)
                    bh[symbol] = 100*(final/INITIAL_USDT-1)
                else: bh[symbol] = None
            monthly.append({"month": key, "cost_scenario": name, "is_complete_calendar_month": bool(complete),
                "month_start_equity_usdt": first_eq, "month_end_equity_usdt": last_eq,
                "strategy_return_pct": 100*(last_eq/first_eq-1) if first_eq else None, "cash_return_pct": 0.0,
                "btc_monthly_buy_hold_return_pct": bh["BTCUSDT"], "eth_monthly_buy_hold_return_pct": bh["ETHUSDT"],
                "episodes_closed": int(sum(1 for t in all_trades if t["cost_scenario"] == name and pd.Timestamp(t["exit_time"]).strftime("%Y-%m") == key))})
    monthly_frame = pd.DataFrame(monthly)
    monthly_frame.to_csv(output/"monthly-returns.csv", index=False, float_format="%.12g")
    monthly_summary = []
    for name in COSTS:
        d = monthly_frame[(monthly_frame.cost_scenario == name) & monthly_frame.is_complete_calendar_month]
        monthly_summary.append({"cost_scenario": name, "complete_months": int(len(d)),
            "positive_strategy_months": int((d.strategy_return_pct > 0).sum()), "strategy_beats_cash_months": int((d.strategy_return_pct > d.cash_return_pct).sum()),
            "strategy_beats_btc_monthly_hold_months": int((d.strategy_return_pct > d.btc_monthly_buy_hold_return_pct).sum()),
            "strategy_beats_eth_monthly_hold_months": int((d.strategy_return_pct > d.eth_monthly_buy_hold_return_pct).sum()),
            "mean_monthly_return_pct": float(d.strategy_return_pct.mean()) if len(d) else None,
            "median_monthly_return_pct": float(d.strategy_return_pct.median()) if len(d) else None,
            "worst_month_pct": float(d.strategy_return_pct.min()) if len(d) else None})
    pd.DataFrame(monthly_summary).to_csv(output/"monthly-summary.csv", index=False, float_format="%.12g")
    benchmarks = []
    for symbol, frame in frames.items():
        for name, cost in COSTS.items():
            buy, fee = cost_prices(float(frame.open.iloc[0]), cost, "BUY"); units = INITIAL_USDT/(buy*(1+fee))
            sell, fee = cost_prices(float(frame.close.iloc[-1]), cost, "SELL"); final = units*sell*(1-fee)
            benchmarks.append({"benchmark": symbol+"_BUY_HOLD", "cost_scenario": name, "initial_usdt": INITIAL_USDT,
                "final_usdt": final, "return_pct": 100*(final/INITIAL_USDT-1)})
    for name in COSTS: benchmarks.append({"benchmark": "CASH", "cost_scenario": name, "initial_usdt": INITIAL_USDT, "final_usdt": INITIAL_USDT, "return_pct": 0.0})
    pd.DataFrame(benchmarks).to_csv(output/"benchmarks.csv", index=False, float_format="%.12g")
    lines = ["# MODO 1 — TENDENCIA: histórico de práctica V1", "",
        "**Decisión económica: NO_TRADE.**", "",
        f"Histórico lógico SHA-256: {bundle['dataset_sha256']}  ", f"SHA-256 de archivo: {raw_sha}  ",
        f"Periodo horario: {info['time_start']} — {info['time_end']}  ",
        "15.360 velas horarias por activo, consolidadas a velas 4h completas. Todas las 30.720 velas carecen de available_at y tienen finality desconocida; este replay no prueba lo que era realmente observable en cada instante.", "",
        "## Regla congelada", "",
        "- Comprar solo tras una vela 4h cerrada que rompa el máximo Donchian de las 40 velas anteriores, cotice por encima de EMA200 y tenga EMA200 superior a su valor de cinco velas antes.",
        "- Señal a cierre; fill proxy en apertura de la siguiente vela 4h. Solo una posición total entre BTC y ETH. En señales simultáneas, priorizar mayor breakout/ATR; empate alfabético por símbolo.",
        "- Stop inicial a 3×ATR14 del precio de entrada, con ATR calculado en la vela de señal; si el mínimo intrabar lo cruza, usar el peor de apertura/stop. Salida secundaria: cierre por debajo del mínimo Donchian de las 20 velas previas, ejecutada en la apertura siguiente.",
        "- Máximo 95% del efectivo disponible y cinco entradas en ventana móvil de 30 días; 31 USDT ficticios, sin leverage/short. Comisiones, spread y slippage son escenarios, no precios históricos medidos.", "",
        "## Resultados por costes", "",
        "| Escenario | Final USDT | Retorno neto | Máx. drawdown | Entradas | Ganadoras | Win rate | Señales ignoradas por posición abierta | Rechazos límite 30d |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, result in scenario_results.items():
        m = result["metrics"]
        lines.append(f"| {name} | {m['final_usdt']:.4f} | {m['net_return_pct']:+.3f}% | {m['max_drawdown_pct']:+.3f}% | {m['entries']} | {m['winning_trades']} | {m['win_rate_pct']:.1f}% | {m['rejected_signals']['position_open']} | {m['rejected_signals']['rolling_entry_limit']} |")
    lines += ["", "## Benchmark de mantener una posición durante todo el histórico", "",
        "| Benchmark | Costes | Retorno neto |", "|---|---|---:|"]
    for benchmark in benchmarks: lines.append(f"| {benchmark['benchmark']} | {benchmark['cost_scenario']} | {benchmark['return_pct']:+.3f}% |")
    lines += ["", "## Comparación mensual contra cash y buy-and-hold", "",
        "| Costes | Meses completos | Meses positivos | Supera cash | Supera BTC mensual | Supera ETH mensual | Retorno medio mensual | Mediana mensual | Peor mes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for m in monthly_summary:
        lines.append(f"| {m['cost_scenario']} | {m['complete_months']} | {m['positive_strategy_months']} | {m['strategy_beats_cash_months']} | {m['strategy_beats_btc_monthly_hold_months']} | {m['strategy_beats_eth_monthly_hold_months']} | {m['mean_monthly_return_pct']:+.3f}% | {m['median_monthly_return_pct']:+.3f}% | {m['worst_month_pct']:+.3f}% |")
    lines += ["", "monthly-returns.csv conserva cada mes y los tres escenarios, incluidas las ventanas parciales marcadas; el resumen usa solo meses calendario completos.", "",
        "## Qué comprueba este replay", "",
        "- El código solo usa indicadores causales (Donchian excluye la vela en curso, EMA/ATR disponibles al cierre) y la entrada usa la apertura siguiente; las pruebas anti-sufijo validan que añadir datos futuros no cambia señales pasadas.",
        "- Simula comisiones, spread/slippage, posición única, sizing, tope de entradas, stop con gap adverso, salida de canal al siguiente open, equity neta de liquidación y drawdown.",
        "- No prueba el adaptador PAPER de punta a punta, el libro histórico bid/ask, lot-size/min-notional históricos ni el punto-en-tiempo de las velas. Para eso se necesita una captura futura validada.",
        "- Se trata de un histórico ya examinado, no de un holdout independiente. Que el código pase las pruebas no demuestra rentabilidad futura.", "",
        "## Dictamen", "",
        "La prueba responde a la corrección del motor de práctica sobre OHLC histórico; la rentabilidad se informa de forma descriptiva y bajo supuestos. NO_TRADE permanece; no hay envío de órdenes al exchange."]
    (output/"REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    manifest = {"schema_version": 1, "study": "TREND_MODE_PRACTICE_V1", "status": info["status"], "decision": "NO_TRADE",
        "source_git_sha": source_sha, "dataset_logical_sha256": bundle["dataset_sha256"], "dataset_raw_file_sha256": raw_sha,
        "configuration_sha256": bundle["configuration_sha256"], "hourly_bars_per_asset": info["hourly_bars_per_asset"],
        "four_hour_bars_after_drop_partial": info["four_hour_bars_after_drop_partial"],
        "missing_available_at_count_by_asset": missing_availability, "unknown_finality_count_by_asset": unknown_finality,
        "scenario_results": scenario_results, "files": {}}
    for f in sorted(output.iterdir()):
        if f.is_file(): manifest["files"][f.name] = {"size_bytes": f.stat().st_size, "sha256": sha256_bytes(f.read_bytes())}
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=json_default)+"\n")
    for name, meta in manifest["files"].items():
        payload = (output/name).read_bytes()
        if len(payload) != meta["size_bytes"] or sha256_bytes(payload) != meta["sha256"]: raise TrendReplayError("OUTPUT_HASH_MISMATCH:"+name)
    return info, manifest

def main():
    parser = argparse.ArgumentParser(description="Historical OHLC replay of fixed Trend Mode; never sends orders")
    parser.add_argument("--dataset", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-sha", default="LOCAL_UNCOMMITTED")
    args = parser.parse_args()
    info, _ = run_replay(args.dataset, args.output_dir, args.source_sha)
    for name, result in info["scenario_results"].items():
        m = result["metrics"]
        print(f"{name}: return={m['net_return_pct']:+.4f}% drawdown={m['max_drawdown_pct']:+.4f}% trades={m['entries']}")
    print("DECISION: NO_TRADE")
    return 0
if __name__ == "__main__": raise SystemExit(main())
