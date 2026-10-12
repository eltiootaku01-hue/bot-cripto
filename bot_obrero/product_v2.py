"""Product V2: public Binance Spot scanner and conservative virtual research.

The configured ARS budget is never silently treated as USDT. No verified
ARS-to-USDT route means CONVERSION_UNKNOWN and blocks affordability claims.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Callable, Mapping, Sequence

from .binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from .binance_spot import BinanceSpotRestConfig
from .virtual_backtest import BacktestConfig, BacktestError, BacktestResult, _indicators, _observation, _validate_history, run_virtual_backtest

D = Decimal
ZERO, ONE = D("0"), D("1")
FEE, SLIPPAGE_BPS = D("0.001"), D("5")
BASE_URL, TIMEOUT = "https://data-api.binance.vision", 12.0
MAX_UNIVERSE = 12
STABLE_BASE_ASSETS = {"USDC", "USDE", "FDUSD", "BUSD", "TUSD", "DAI", "USDP", "USD1", "USDS", "USDD", "PYUSD", "RLUSD", "USTC", "EUR", "AEUR", "EURI", "BFUSD"}
SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")


class ProductV2Error(RuntimeError):
    """Public market-data or product-evaluation failure."""


def exact_decimal(value: Any, field: str) -> Decimal:
    try:
        result = D(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProductV2Error(f"invalid public value for {field}") from exc
    if not result.is_finite():
        raise ProductV2Error(f"non-finite public value for {field}")
    return result


def public_json(path: str) -> Any:
    request = urllib.request.Request(
        BASE_URL + path,
        headers={"Accept": "application/json", "User-Agent": "bot-cripto-product-v2/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if not 200 <= response.status < 300:
                raise ProductV2Error(f"Binance public HTTP {response.status} at {path}")
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProductV2Error(f"Binance public HTTP {exc.code} at {path}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductV2Error(f"Binance public endpoint unavailable/invalid at {path}: {exc}") from exc


@dataclass(frozen=True)
class InstrumentFilters:
    min_qty: Decimal | None
    step_size: Decimal | None
    min_notional: Decimal | None


@dataclass(frozen=True)
class UniverseInstrument:
    base_asset: str
    filters: InstrumentFilters


@dataclass(frozen=True)
class Ticker:
    symbol: str
    price: Decimal
    quote_volume: Decimal
    bid: Decimal
    ask: Decimal

    @property
    def spread_pct(self) -> Decimal | None:
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return None
        mid = (self.bid + self.ask) / D("2")
        return (self.ask - self.bid) / mid * D("100")


def parse_filters(raw: Any) -> InstrumentFilters:
    if not isinstance(raw, list):
        return InstrumentFilters(None, None, None)
    minimum = step = notional = None
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        try:
            if entry.get("filterType") == "LOT_SIZE":
                minimum = exact_decimal(entry.get("minQty"), "minQty")
                step = exact_decimal(entry.get("stepSize"), "stepSize")
            elif entry.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
                if entry.get("minNotional") is not None:
                    notional = exact_decimal(entry["minNotional"], "minNotional")
        except ProductV2Error:
            return InstrumentFilters(None, None, None)
    return InstrumentFilters(minimum, step, notional)


def active_usdt_universe(payload: Any) -> dict[str, UniverseInstrument]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("symbols"), list):
        raise ProductV2Error("ExchangeInfo missing symbols array")
    universe: dict[str, UniverseInstrument] = {}
    for row in payload["symbols"]:
        if not isinstance(row, Mapping):
            continue
        symbol, base = row.get("symbol"), row.get("baseAsset")
        if (
            not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol)
            or row.get("quoteAsset") != "USDT" or row.get("status") != "TRADING"
            or row.get("isSpotTradingAllowed") is not True
        ):
            continue
        if not isinstance(base, str) or base.endswith(("UP", "DOWN", "BULL", "BEAR")) or re.search(r"\d+[LS]$", base):
            continue
        universe[symbol] = UniverseInstrument(base_asset=base, filters=parse_filters(row.get("filters")))
    return universe


def ticker_snapshots(universe: Mapping[str, Any], ticker24: Any, books: Any) -> dict[str, Ticker]:
    if not isinstance(ticker24, list) or not isinstance(books, list):
        raise ProductV2Error("ticker response shape invalid")
    daily = {}
    for row in ticker24:
        if isinstance(row, Mapping) and isinstance(row.get("symbol"), str):
            try:
                daily[row["symbol"]] = (
                    exact_decimal(row.get("lastPrice"), "lastPrice"),
                    exact_decimal(row.get("quoteVolume"), "quoteVolume"),
                )
            except ProductV2Error:
                continue
    result = {}
    for row in books:
        if not isinstance(row, Mapping):
            continue
        symbol = row.get("symbol")
        if symbol not in universe or symbol not in daily:
            continue
        try:
            result[symbol] = Ticker(
                symbol, daily[symbol][0], daily[symbol][1],
                exact_decimal(row.get("bidPrice"), "bidPrice"),
                exact_decimal(row.get("askPrice"), "askPrice"),
            )
        except ProductV2Error:
            continue
    return result


def return_series(items: Sequence[Any]) -> dict[datetime, Decimal]:
    result = {}
    for before, after in zip(items, items[1:]):
        if before.payload.close <= 0:
            raise ProductV2Error("non-positive historical close")
        result[after.payload.start] = after.payload.close / before.payload.close - ONE
    return result


def correlation_beta(
    asset: Mapping[datetime, Decimal], btc: Mapping[datetime, Decimal], periods: int
) -> tuple[float | None, float | None]:
    common = sorted(set(asset) & set(btc))[-periods:]
    if len(common) < periods:
        return None, None
    xs, ys = [float(btc[t]) for t in common], [float(asset[t]) for t in common]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx, vy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None, None
    covariance = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return covariance / math.sqrt(vx * vy), covariance / vx


def classify_btc_regime(
    last_closed_close: Decimal,
    fast_ema: Decimal,
    slow_ema: Decimal,
    rsi: Decimal,
    rsi_buy_min: Decimal,
) -> str:
    """Classify BTC using only the same final closed 4h bar and indicators."""
    if last_closed_close > fast_ema and last_closed_close > slow_ema and fast_ema > slow_ema and rsi >= rsi_buy_min:
        return "BULLISH"
    if last_closed_close < fast_ema and last_closed_close < slow_ema and fast_ema < slow_ema and rsi < D("50"):
        return "BEARISH"
    return "NEUTRAL"


def btc_relationship_reasons(
    corr30d: float | None,
    corr90d: float | None,
    beta90d: float | None,
    relative_strength30d: Decimal | None,
    minimum_corr30d: Decimal,
    minimum_corr90d: Decimal,
) -> list[str]:
    """Return explicit, fail-closed BTC relationship admission reasons."""
    metrics = (corr30d, corr90d, beta90d)
    if (
        any(value is None or not math.isfinite(value) for value in metrics)
        or relative_strength30d is None
        or not relative_strength30d.is_finite()
    ):
        return ["BTC_METRICS_INSUFFICIENT"]
    reasons = []
    if D(str(corr30d)) < minimum_corr30d:
        reasons.append("BTC_CORRELATION_30D_BELOW_MINIMUM")
    if D(str(corr90d)) < minimum_corr90d:
        reasons.append("BTC_CORRELATION_90D_BELOW_MINIMUM")
    return reasons


def histories_temporally_aligned(asset_items: Sequence[Any], btc_items: Sequence[Any]) -> bool:
    """Require the same candle-open timestamps for the compared historical window."""
    return [item.payload.start for item in asset_items] == [item.payload.start for item in btc_items]


def exposure_pct(items: Sequence[Any], fills: Sequence[Any]) -> Decimal:
    """Percent of bars with a virtual position, based only on timestamped fills."""
    if not items:
        return D("0")
    ordered = sorted(fills, key=lambda fill: fill.timestamp)
    fill_index = 0
    in_position = False
    exposed = 0
    for item in items:
        while fill_index < len(ordered) and ordered[fill_index].timestamp <= item.payload.start:
            in_position = ordered[fill_index].side == "BUY"
            fill_index += 1
        if in_position:
            exposed += 1
    return D(exposed) / D(len(items)) * D("100")


def modeled_spread_slippage_cost(fills: Sequence[Any], spread_bps: Decimal, slippage_bps: Decimal) -> Decimal:
    """Approximate cost in quote currency; spread is assumed full width, half per fill."""
    rate = (spread_bps / D("2") + slippage_bps) / D("10000")
    return sum((fill.quantity * fill.price * rate for fill in fills), ZERO)



def modeled_round_trip_breakeven_pct(
    fee_rate: Decimal, spread_bps: Decimal, slippage_bps: Decimal
) -> Decimal:
    """Approximate gross market return needed to offset modeled entry/exit costs."""
    cost_per_side = (spread_bps / D("2") + slippage_bps) / D("10000")
    buy_cost_multiplier = (ONE + cost_per_side) * (ONE + fee_rate)
    sell_proceeds_multiplier = (ONE - cost_per_side) * (ONE - fee_rate)
    if sell_proceeds_multiplier <= ZERO:
        raise ProductV2Error("modeled exit costs leave no positive proceeds")
    return (buy_cost_multiplier / sell_proceeds_multiplier - ONE) * D("100")


def return_pct(items: Sequence[Any], bars: int) -> Decimal | None:
    if len(items) <= bars or items[-bars - 1].payload.close <= 0:
        return None
    return (items[-1].payload.close / items[-bars - 1].payload.close - ONE) * D("100")


def fetch_history(adapter: BinanceMetadataBackedAdapter, symbol: str, days: int, now: datetime) -> list[Any]:
    start = now - timedelta(days=days + 3)  # cover alignment and a possibly open final 4h candle
    count = days * 6 + 3  # 6 four-hour bars/day; reserve a bounded pagination margin.
    items = adapter.fetch_historical_market_data(
        symbol=symbol, interval="4h", start_time=int(start.timestamp() * 1000),
        end_time=int(now.timestamp() * 1000), page_limit=1000,
        max_requests=max(2, (count + 999) // 1000 + 1),
    )
    accepted, _, _, _ = _validate_history(items, "4h")
    return accepted


def backtest(
    symbol: str, items: Sequence[Any], bars: int, filters: InstrumentFilters | None = None, *,
    initial_quote_balance: Decimal = D("1000"),
    fee_rate: Decimal = FEE, spread_bps: Decimal = D("10"), slippage_bps: Decimal = SLIPPAGE_BPS,
    fast_ema_period: int = 12, slow_ema_period: int = 26, rsi_period: int = 14,
    rsi_buy_min: Decimal = D("50"), rsi_sell_at: Decimal = D("70"),
    position_fraction: Decimal = D("0.95"),
    evaluation_start: datetime | None = None, evaluation_end: datetime | None = None,
    entry_admission: Callable[[int, Sequence[Any]], tuple[bool, str]] | None = None,
) -> BacktestResult:
    config = BacktestConfig(
        symbol=symbol, interval="4h", candles=bars, fast_ema_period=fast_ema_period,
        slow_ema_period=slow_ema_period, rsi_period=rsi_period, rsi_buy_min=rsi_buy_min, rsi_sell_at=rsi_sell_at,
        initial_quote_balance=initial_quote_balance, position_fraction=position_fraction,
        fee_rate=fee_rate, slippage_bps=slippage_bps, spread_bps=spread_bps, max_entries_per_30d=5,
        min_qty=filters.min_qty if filters else None,
        quantity_step=filters.step_size if filters else None,
        min_notional=filters.min_notional if filters else None,
    )
    return run_virtual_backtest(items, config, evaluation_start=evaluation_start,
                                evaluation_end=evaluation_end, entry_admission=entry_admission)


def historical_btc_entry_admission(
    candidate_items: Sequence[Any], btc_items: Sequence[Any], *, fast_ema_period: int,
    slow_ema_period: int, rsi_period: int, rsi_buy_min: Decimal,
    minimum_corr30d: Decimal, minimum_corr90d: Decimal,
) -> Callable[[int, Sequence[Any]], tuple[bool, str]]:
    """Fail-closed entry gate using only aligned 4h bars up to each signal close."""
    if not candidate_items or len(candidate_items) != len(btc_items) or not histories_temporally_aligned(candidate_items, btc_items):
        raise ProductV2Error("candidate and BTC histories are not fully aligned")
    btc_observations = [_observation(item) for item in btc_items]
    indicator_config = BacktestConfig(
        symbol="BTCUSDT", interval="4h", candles=1080,
        fast_ema_period=fast_ema_period, slow_ema_period=slow_ema_period,
        rsi_period=rsi_period, rsi_buy_min=rsi_buy_min,
    )
    cache: dict[int, tuple[bool, str]] = {}

    def admit(index: int, _candidate_observations: Sequence[Any]) -> tuple[bool, str]:
        if index in cache:
            return cache[index]
        if index < 90 * 6:
            result = (False, "BTC_METRICS_INSUFFICIENT: 90-day warm-up incomplete")
            cache[index] = result
            return result
        candidate_prefix, btc_prefix = candidate_items[:index + 1], btc_items[:index + 1]
        if not histories_temporally_aligned(candidate_prefix, btc_prefix):
            result = (False, "BTC_HISTORY_NOT_TEMPORALLY_ALIGNED")
            cache[index] = result
            return result
        def admissible_gate_candle(item: Any) -> bool:
            quality = getattr(item, "quality", None)
            envelope_completeness = getattr(item, "completeness", None)
            candle = getattr(item, "payload", None)
            state = getattr(getattr(candle, "candle_state", None), "value", getattr(candle, "candle_state", None))
            candle_completeness = getattr(getattr(candle, "completeness", None), "value", getattr(candle, "completeness", None))
            finality = getattr(getattr(candle, "finality", None), "value", getattr(candle, "finality", None))
            envelope_value = getattr(envelope_completeness, "value", envelope_completeness)
            quality_value = getattr(quality, "value", quality)
            # The canonical historical adapter may leave envelope completeness UNKNOWN.
            # The actual Candle must still be explicitly CLOSED and COMPLETE; a partial
            # envelope or explicitly non-final candle is never admissible.
            return (
                quality_value == "VALID"
                and envelope_value in ("UNKNOWN", "COMPLETE")
                and state == "CLOSED"
                and candle_completeness == "COMPLETE"
                and finality not in (None, "NOT_FINAL")
            )

        if any(not admissible_gate_candle(item) for item in (*candidate_prefix, *btc_prefix)):
            result = (False, "BTC_METRICS_INSUFFICIENT: historical data quality/completeness unknown or inadmissible")
            cache[index] = result
            return result
        asset_returns, btc_returns = return_series(candidate_prefix), return_series(btc_prefix)
        corr30, _ = correlation_beta(asset_returns, btc_returns, 30 * 6)
        corr90, beta90 = correlation_beta(asset_returns, btc_returns, 90 * 6)
        asset_return30, btc_return30 = return_pct(candidate_prefix, 30 * 6), return_pct(btc_prefix, 30 * 6)
        strength = asset_return30 - btc_return30 if asset_return30 is not None and btc_return30 is not None else None
        reasons = btc_relationship_reasons(corr30, corr90, beta90, strength, minimum_corr30d, minimum_corr90d)
        btc_values = _indicators(btc_observations[:index + 1], indicator_config)
        regime = classify_btc_regime(
            btc_prefix[-1].payload.close, btc_values["fast_ema"], btc_values["slow_ema"],
            btc_values["rsi"], rsi_buy_min,
        )
        if regime != "BULLISH":
            reasons.append("BTC_REGIME_NOT_BULLISH")
        detail = (f"historical BTC gate regime={regime}; corr30d={corr30}; corr90d={corr90}; "
                  f"thresholds={minimum_corr30d}/{minimum_corr90d}; reasons={','.join(reasons) if reasons else 'none'}")
        result = (not reasons, detail)
        cache[index] = result
        return result

    return admit


def normalized_affordability(
    price: Decimal, filters: InstrumentFilters, budget_usdt: Decimal, *,
    fee_rate: Decimal, spread_bps: Decimal, slippage_bps: Decimal,
    position_fraction: Decimal,
) -> tuple[bool | None, str]:
    """Estimate affordability at current public ticker, only for normalized research."""
    if price <= ZERO or filters.min_qty is None or filters.step_size is None or filters.min_notional is None:
        return None, "FILTERS_OR_PRICE_UNKNOWN"
    entry_cost_rate = (spread_bps / D("2") + slippage_bps) / D("10000")
    unit_cost = price * (ONE + entry_cost_rate) * (ONE + fee_rate)
    raw_qty = budget_usdt * position_fraction / unit_cost
    qty = (raw_qty / filters.step_size).to_integral_value(rounding=ROUND_DOWN) * filters.step_size
    if qty < filters.min_qty:
        return False, "MIN_QTY"
    if qty * price < filters.min_notional:
        return False, "MIN_NOTIONAL"
    if qty * unit_cost > budget_usdt:
        return False, "COSTS_EXCEED_BUDGET"
    return True, "PASS"


def ars_capital_scenarios() -> tuple[dict[str, Any], ...]:
    """Never infer ARS-to-USDT conversion or claim funds available."""
    return tuple({
        "scenario": label, "capital_ars": amount,
        "conversion": "CONVERSION_UNKNOWN", "usdt_net": "UNKNOWN",
        "conversion_costs": "UNKNOWN", "fees_reserve": "UNKNOWN",
        "max_usable": "UNKNOWN", "uninvested": "UNKNOWN",
        "affordable_candidates": "UNKNOWN", "full_trade_cost_ars": "UNKNOWN",
        "historical_net_return": "NOT_COMPUTABLE", "drawdown": "NOT_COMPUTABLE",
        "decision": "NO_TRADE",
    } for label, amount in (("A", D("10000")), ("B", D("25000")), ("C", D("50000"))))


def buy_hold_net_return(
    items: Sequence[Any], fee_rate: Decimal = FEE, spread_bps: Decimal = D("10"),
    slippage_bps: Decimal = SLIPPAGE_BPS,
) -> Decimal:
    """Buy-and-hold reference after the same fee, spread and slippage assumptions."""
    first, last = items[0].payload.close, items[-1].payload.close
    half_spread_and_slippage = (slippage_bps + spread_bps / D("2")) / D("10000")
    entry = first * (ONE + half_spread_and_slippage)
    exit_price = last * (ONE - half_spread_and_slippage)
    return ((exit_price * (ONE - fee_rate)) / (entry * (ONE + fee_rate)) - ONE) * D("100")



VALIDATION_WINDOW_DAYS = 60
VALIDATION_WINDOW_COUNT = 3
VALIDATION_COST_SCENARIOS = (
    ("BASE", D("0.001"), D("10"), D("5")),
    ("STRESS_1_FEE_0.1_SPREAD_25_SLIP_10", D("0.001"), D("25"), D("10")),
    ("STRESS_2_FEE_0.2_SPREAD_50_SLIP_20", D("0.002"), D("50"), D("20")),
)


def validation_window_bounds(total_items: int, warmup_bars: int, evaluation_bars: int,
                              validation_bars: int, window_count: int = VALIDATION_WINDOW_COUNT
                              ) -> tuple[tuple[int, int], ...]:
    """Half-open windows; require the end-boundary candle even for the last window."""
    if total_items < 0 or min(warmup_bars, evaluation_bars, validation_bars, window_count) <= 0:
        raise ProductV2Error("invalid validation-window dimensions")
    first = warmup_bars + evaluation_bars
    required = first + validation_bars * window_count + 1
    if total_items < required:
        raise ProductV2Error(f"history insufficient for {window_count} validation windows: need {required} bars including final boundary, received {total_items}")
    return tuple((first + i * validation_bars, first + (i + 1) * validation_bars) for i in range(window_count))


def buy_hold_metrics(items: Sequence[Any], fee_rate: Decimal, spread_bps: Decimal,
                     slippage_bps: Decimal) -> tuple[Decimal, Decimal]:
    """Buy-and-hold net return and close-marked drawdown with modeled costs."""
    if len(items) < 2:
        raise ProductV2Error("insufficient bars for buy-and-hold benchmark")
    cost = (spread_bps / D("2") + slippage_bps) / D("10000")
    entry = items[0].payload.close * (ONE + cost) * (ONE + fee_rate)
    if entry <= ZERO:
        raise ProductV2Error("invalid buy-and-hold entry price")
    qty = ONE / entry
    equity = [x.payload.close * (ONE - cost) * (ONE - fee_rate) * qty for x in items]
    peak, max_dd = equity[0], ZERO
    for value in equity:
        peak = max(peak, value)
        if peak > ZERO:
            max_dd = min(max_dd, value / peak - ONE)
    return (equity[-1] - ONE) * D("100"), max_dd * D("100")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BTC regime + liquid USDT altcoin scan and 4h virtual backtest.")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--universe-limit", type=int, default=5)
    parser.add_argument("--initial-ars", type=exact_arg, default=D("10000"))
    parser.add_argument("--hypothetical-usdt-balance", type=exact_arg, default=D("31"),
                        help="hypothetical USDT balance for virtual research; never an ARS conversion")
    parser.add_argument("--min-quote-volume", type=exact_arg, default=D("1000000"))
    parser.add_argument("--max-spread-pct", type=exact_arg, default=D("0.50"))
    parser.add_argument("--min-price", type=exact_arg, default=D("0.001"))
    parser.add_argument("--max-price", type=exact_arg, default=D("5"))
    parser.add_argument("--fee-rate", type=exact_arg, default=FEE, help="fraction charged per fill; default 0.001")
    parser.add_argument("--spread-bps", type=exact_arg, default=D("10"), help="assumed full spread in basis points")
    parser.add_argument("--slippage-bps", type=exact_arg, default=SLIPPAGE_BPS, help="slippage per fill in basis points")
    parser.add_argument("--fast-ema-period", type=int, default=12)
    parser.add_argument("--slow-ema-period", type=int, default=26)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--rsi-buy-min", type=exact_arg, default=D("50"))
    parser.add_argument("--rsi-sell-at", type=exact_arg, default=D("70"))
    parser.add_argument("--position-fraction", type=exact_arg, default=D("0.95"))
    parser.add_argument("--min-btc-corr-30d", type=exact_arg, default=D("0.50"), help="minimum 30-day return correlation with BTC; research threshold")
    parser.add_argument("--min-btc-corr-90d", type=exact_arg, default=D("0.60"), help="minimum 90-day return correlation with BTC; research threshold")
    return parser


def exact_arg(text: str) -> Decimal:
    try:
        value = D(text)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an exact decimal") from exc
    if not value.is_finite():
        raise argparse.ArgumentTypeError("must be finite")
    return value


def _validate_args(args: argparse.Namespace) -> None:
    if args.days < 180:
        raise ProductV2Error("--days must be >= 180 for 30/90-day metrics and a held-out window")
    if not 1 <= args.universe_limit <= MAX_UNIVERSE:
        raise ProductV2Error(f"--universe-limit must be between 1 and {MAX_UNIVERSE}")
    if min(args.initial_ars, args.min_quote_volume, args.max_spread_pct, args.min_price) <= 0 or args.max_price <= args.min_price:
        raise ProductV2Error("budget/thresholds must be positive and price bounds ordered")
    if not args.hypothetical_usdt_balance.is_finite() or args.hypothetical_usdt_balance <= ZERO:
        raise ProductV2Error("--hypothetical-usdt-balance must be a finite positive Decimal")
    if args.fast_ema_period <= 0 or args.slow_ema_period <= args.fast_ema_period or args.rsi_period <= 0:
        raise ProductV2Error("indicator periods must be positive and slow EMA must exceed fast EMA")
    if not D("0") <= args.rsi_buy_min <= D("100") or not D("0") <= args.rsi_sell_at <= D("100"):
        raise ProductV2Error("RSI thresholds must be between 0 and 100")
    if not D("0") < args.position_fraction <= D("1"):
        raise ProductV2Error("position fraction must be > 0 and <= 1")
    if not D("0") <= args.fee_rate <= D("0.05") or not D("0") <= args.spread_bps <= D("1000") or not D("0") <= args.slippage_bps <= D("1000"):
        raise ProductV2Error("fee rate must be 0..0.05 and spread/slippage 0..1000 bps")
    if not D("-1") <= args.min_btc_corr_30d <= D("1") or not D("-1") <= args.min_btc_corr_90d <= D("1"):
        raise ProductV2Error("BTC correlation minimums must be between -1 and 1")


def run(args: argparse.Namespace) -> int:
    _validate_args(args)
    now = datetime.now(timezone.utc)
    history_days = args.days + 90 + VALIDATION_WINDOW_DAYS * VALIDATION_WINDOW_COUNT + 3  # warm-up + evaluation + validation + margin
    universe = active_usdt_universe(public_json("/api/v3/exchangeInfo"))
    ticker = ticker_snapshots(
        universe, public_json("/api/v3/ticker/24hr"), public_json("/api/v3/ticker/bookTicker")
    )
    if "BTCUSDT" not in ticker:
        raise ProductV2Error("BTCUSDT ticker/book data unavailable")
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    btc_items = fetch_history(adapter, "BTCUSDT", history_days, now)
    bars = args.days * 6
    warmup_bars, validation_bars = 90 * 6, VALIDATION_WINDOW_DAYS * 6
    validation_bounds = validation_window_bounds(len(btc_items), warmup_bars, bars, validation_bars)
    required_bars = warmup_bars + bars + VALIDATION_WINDOW_COUNT * validation_bars + 1
    evaluation_start = btc_items[warmup_bars].payload.start
    evaluation_end = btc_items[warmup_bars + bars].payload.start
    validation_start = btc_items[validation_bounds[0][0]].payload.start
    validation_end = btc_items[validation_bounds[0][1]].payload.start
    btc_result = backtest("BTCUSDT", btc_items, bars, universe.get("BTCUSDT").filters if universe.get("BTCUSDT") else None, initial_quote_balance=args.hypothetical_usdt_balance, fee_rate=args.fee_rate, spread_bps=args.spread_bps, slippage_bps=args.slippage_bps, fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period, rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min, rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction, evaluation_start=evaluation_start, evaluation_end=evaluation_end)
    btc_returns = return_series(btc_items)
    btc_last_closed = btc_items[-1]
    btc_last_closed_close = btc_last_closed.payload.close
    btc_latest_values = _indicators([_observation(item) for item in btc_items],
        BacktestConfig(symbol="BTCUSDT", interval="4h", candles=bars,
            fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period,
            rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min, rsi_sell_at=args.rsi_sell_at))
    regime = classify_btc_regime(btc_last_closed_close, btc_latest_values["fast_ema"],
        btc_latest_values["slow_ema"], btc_latest_values["rsi"], args.rsi_buy_min)
    btc_hold = buy_hold_net_return(btc_items[warmup_bars:warmup_bars + bars], args.fee_rate, args.spread_bps, args.slippage_bps)

    print("BOT-CRIPTO PRODUCT V2 — PUBLIC BINANCE SPOT")
    print(f"Observation UTC: {now.isoformat()}; timeframe=4h; history={history_days} days; warm-up=90d; evaluation={args.days}d; walk-forward validation=3x60d")
    print(f"BTC panel: regime={regime}; last closed 4h close={btc_last_closed_close} USDT at {btc_last_closed.payload.end.isoformat()}; EMA{args.fast_ema_period}={btc_latest_values['fast_ema']}; EMA{args.slow_ema_period}={btc_latest_values['slow_ema']}; RSI{args.rsi_period}={btc_latest_values['rsi']}")
    print(f"BTC spot ticker (separate snapshot, not regime input): price={ticker['BTCUSDT'].price} USDT; observed UTC={now.isoformat()}")
    print(f"BTC relationship thresholds (research defaults, not validated): corr30d>={args.min_btc_corr_30d}; corr90d>={args.min_btc_corr_90d}")
    print(f"BTC evaluation bars={btc_result.candle_count}; range={btc_result.first_candle.isoformat()} .. {btc_result.last_candle.isoformat()}; independent walk-forward validation=3x60d; windows={'; '.join(f'{btc_items[a].payload.start.isoformat()}..{btc_items[b-1].payload.start.isoformat()}' for a, b in validation_bounds)}")
    print("Temporal caveat: historical available_at may be UNKNOWN; the simulator explicitly models decision at bar close and fills no earlier than the next bar open.")
    print(f"BTC buy-and-hold net reference after fee/spread/slippage assumptions={btc_hold}%; cash reference=0%")
    print(f"Capital reference: {args.initial_ars} ARS; conversion=CONVERSION_UNKNOWN")
    print(f"Hypothetical simulation balance: {args.hypothetical_usdt_balance} USDT; provenance=HYPOTHETICAL_SCENARIO_NOT_ARS_CONVERSION")
    print("The ARS reference amount and hypothetical USDT balance are independent; they are never added, equated, or automatically converted.")
    print(f"ARS budget={args.initial_ars}; conversion=CONVERSION_UNKNOWN; ARS-to-USDT route is unverified; the filtered USDT Spot universe is not an exhaustive fiat/P2P conversion search")
    print("Conversion evidence: no live ARS/USDT offer, payment method, executable price, applicable fee, limit, or net USDT was verified; affordability remains blocked.")
    print("WARNING: no verified ARS-to-USDT route; affordability is blocked. Normalized USDT scenarios are separate research assumptions, not conversions.")
    print(f"Strategy: EMA{args.fast_ema_period}/EMA{args.slow_ema_period} crossover + RSI{args.rsi_period}; RSI buy minimum={args.rsi_buy_min}; sell threshold={args.rsi_sell_at}; position fraction={args.position_fraction}; fee/fill={args.fee_rate}; assumed full spread={args.spread_bps} bps; slippage/fill={args.slippage_bps} bps; max 5 BUY entries/rolling 30 days.")
    print(f"Cost assumptions: fee/fill={args.fee_rate}; full spread={args.spread_bps} bps; slippage/fill={args.slippage_bps} bps.")
    print(f"Modeled round-trip break-even return (approximate, not a forecast): {modeled_round_trip_breakeven_pct(args.fee_rate, args.spread_bps, args.slippage_bps)}% of traded notional.")
    print("Exposure metric: percentage of evaluated 4h bars spent in a virtual position; candidate-level exposure appears when its history is admissible.")
    print("Historical spread is not observable from klines; the configured spread is a model assumption, not a measured historical spread.")
    print("No BNB discount, private endpoints, API keys, real orders, leverage, or financial runtime.")

    excluded: list[tuple[str, str]] = []
    ranked = sorted(
        ((sym, tick) for sym, tick in ticker.items() if sym in universe and sym != "BTCUSDT"),
        key=lambda pair: pair[1].quote_volume, reverse=True,
    )
    eligible_snapshot = []
    for symbol, tick in ranked:
        instrument = universe[symbol]
        filt = instrument.filters
        reasons = []
        if instrument.base_asset in STABLE_BASE_ASSETS:
            reasons.append("STABLECOIN_OR_FIAT_BASE_NOT_ALTCOIN")
        if tick.quote_volume < args.min_quote_volume:
            reasons.append("LOW_QUOTE_VOLUME")
        if tick.spread_pct is None or tick.spread_pct > args.max_spread_pct:
            reasons.append("SPREAD_UNKNOWN_OR_WIDE")
        if not args.min_price <= tick.price <= args.max_price:
            reasons.append("NOMINAL_PRICE_OUT_OF_RANGE")
        if filt.min_qty is None or filt.step_size is None or filt.min_notional is None:
            reasons.append("INSTRUMENT_FILTERS_UNKNOWN")
        if reasons:
            excluded.append((symbol, ",".join(reasons)))
        else:
            eligible_snapshot.append((symbol, tick))
    candidates = eligible_snapshot[:args.universe_limit]
    print(f"Universe active USDT Spot={len(universe)}; valid ticker snapshots={len(ticker)}; candidates passing filters={len(eligible_snapshot)}; candidates backtested={len(candidates)}")
    print("\nARS CAPITAL SENSITIVITY — CONVERSION UNKNOWN")
    print("Capital ARS | Conversion verificada | USDT netos estimados | Candidatas pasan filtros | Candidatas asequibles | Coste operación completa | Retorno neto histórico | Drawdown | Decisión")
    for scenario in ars_capital_scenarios():
        print(f"{scenario['capital_ars']} | {scenario['conversion']} | {scenario['usdt_net']} | {len(eligible_snapshot)} | {scenario['affordable_candidates']} | {scenario['full_trade_cost_ars']} | {scenario['historical_net_return']} | {scenario['drawdown']} | {scenario['decision']}")

    btc_by_start = {item.payload.start: item for item in btc_items}
    rows = []
    normalized_rows: list[tuple[Decimal, str, bool | None, str, BacktestResult, BacktestResult]] = []
    validation_rows: list[dict[str, Any]] = []
    normalized_budgets = tuple(dict.fromkeys((args.hypothetical_usdt_balance, D("100"), D("250"), D("500"))))
    evaluation_rows: list[dict[str, Any]] = []
    for symbol, tick in candidates:
        try:
            items = fetch_history(adapter, symbol, history_days, now)
            if len(items) < required_bars:
                excluded.append((symbol, f"INSUFFICIENT_HISTORY_FOR_WARMUP_EVALUATION_VALIDATION:{len(items)}<{required_bars}"))
                continue
            if not histories_temporally_aligned(items, btc_items):
                excluded.append((symbol, "BTC_HISTORY_NOT_TEMPORALLY_ALIGNED"))
                continue
            asset_returns = return_series(items)
            c30, _ = correlation_beta(asset_returns, btc_returns, 30 * 6)
            c90, beta90 = correlation_beta(asset_returns, btc_returns, 90 * 6)
            relative, btc30 = return_pct(items, 30 * 6), return_pct(btc_items, 30 * 6)
            relative_strength = relative - btc30 if relative is not None and btc30 is not None else None
            gate = historical_btc_entry_admission(items, btc_items,
                fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period,
                rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min,
                minimum_corr30d=args.min_btc_corr_30d, minimum_corr90d=args.min_btc_corr_90d)
            common = dict(fee_rate=args.fee_rate, spread_bps=args.spread_bps, slippage_bps=args.slippage_bps,
                fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period, rsi_period=args.rsi_period,
                rsi_buy_min=args.rsi_buy_min, rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction)
            full = backtest(symbol, items, bars, universe[symbol].filters, initial_quote_balance=args.hypothetical_usdt_balance, **common,
                evaluation_start=evaluation_start, evaluation_end=evaluation_end)
            integrated = backtest(symbol, items, bars, universe[symbol].filters, initial_quote_balance=args.hypothetical_usdt_balance, **common,
                evaluation_start=evaluation_start, evaluation_end=evaluation_end, entry_admission=gate)
            oos = backtest(symbol, items, validation_bars, universe[symbol].filters, initial_quote_balance=args.hypothetical_usdt_balance, **common,
                evaluation_start=validation_start, evaluation_end=validation_end)
            integrated_oos = backtest(symbol, items, validation_bars, universe[symbol].filters, initial_quote_balance=args.hypothetical_usdt_balance, **common,
                evaluation_start=validation_start, evaluation_end=validation_end, entry_admission=gate)
            # Fixed-parameter 180-day evaluation under each isolated cost scenario.
            for cost_name, cost_fee, cost_spread, cost_slip in VALIDATION_COST_SCENARIOS:
                eval_kw = dict(initial_quote_balance=args.hypothetical_usdt_balance, fee_rate=cost_fee,
                    spread_bps=cost_spread, slippage_bps=cost_slip,
                    fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period,
                    rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min,
                    rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction)
                eval_baseline = backtest(symbol, items, bars, universe[symbol].filters, **eval_kw,
                    evaluation_start=evaluation_start, evaluation_end=evaluation_end)
                eval_integrated = backtest(symbol, items, bars, universe[symbol].filters, **eval_kw,
                    evaluation_start=evaluation_start, evaluation_end=evaluation_end, entry_admission=gate)
                eval_bh, eval_bhdd = buy_hold_metrics(btc_items[warmup_bars:warmup_bars + bars], cost_fee, cost_spread, cost_slip)
                evaluation_rows.append(dict(symbol=symbol, cost=cost_name, fee=cost_fee,
                    spread=cost_spread, slip=cost_slip, baseline=eval_baseline, integrated=eval_integrated,
                    btc_return=eval_bh, btc_dd=eval_bhdd))
            # Three non-overlapping validation windows, fixed parameters, independent balances.
            for window_no, (start_i, end_i) in enumerate(validation_bounds, start=1):
                start_at, end_at = btc_items[start_i].payload.start, btc_items[end_i].payload.start
                asset_window, btc_window = items[start_i:end_i], btc_items[start_i:end_i]
                if len(asset_window) != validation_bars or len(btc_window) != validation_bars:
                    raise ProductV2Error(f"validation window {window_no} has insufficient closed bars")
                for cost_name, cost_fee, cost_spread, cost_slip in VALIDATION_COST_SCENARIOS:
                    kw = dict(initial_quote_balance=args.hypothetical_usdt_balance,
                        fee_rate=cost_fee, spread_bps=cost_spread, slippage_bps=cost_slip,
                        fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period,
                        rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min,
                        rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction)
                    baseline_v = backtest(symbol, items, validation_bars, universe[symbol].filters, **kw,
                        evaluation_start=start_at, evaluation_end=end_at)
                    integrated_v = backtest(symbol, items, validation_bars, universe[symbol].filters, **kw,
                        evaluation_start=start_at, evaluation_end=end_at, entry_admission=gate)
                    br = (baseline_v.final_equity / baseline_v.config.initial_quote_balance - ONE) * D("100")
                    ir = (integrated_v.final_equity / integrated_v.config.initial_quote_balance - ONE) * D("100")
                    bh, bhdd = buy_hold_metrics(btc_window, cost_fee, cost_spread, cost_slip)
                    validation_rows.append(dict(symbol=symbol, window=window_no, start=start_at,
                        end=btc_items[end_i-1].payload.start, cost=cost_name, fee=cost_fee,
                        spread=cost_spread, slip=cost_slip, baseline=baseline_v, integrated=integrated_v,
                        baseline_return=br, integrated_return=ir, btc_return=bh, btc_dd=bhdd,
                        baseline_entries=sum(f.side=="BUY" for f in baseline_v.fills),
                        integrated_entries=sum(f.side=="BUY" for f in integrated_v.fills),
                        rejections=len(integrated_v.entry_rejections)))
            for normalized_budget in normalized_budgets:
                affordable, affordability_reason = normalized_affordability(
                    tick.price, universe[symbol].filters, normalized_budget,
                    fee_rate=args.fee_rate, spread_bps=args.spread_bps,
                    slippage_bps=args.slippage_bps, position_fraction=args.position_fraction,
                )
                normalized_result = backtest(symbol, items, bars, universe[symbol].filters,
                    initial_quote_balance=normalized_budget, **common,
                    evaluation_start=evaluation_start, evaluation_end=evaluation_end)
                normalized_integrated = backtest(symbol, items, bars, universe[symbol].filters,
                    initial_quote_balance=normalized_budget, **common,
                    evaluation_start=evaluation_start, evaluation_end=evaluation_end, entry_admission=gate)
                normalized_rows.append((normalized_budget, symbol, affordable, affordability_reason, normalized_result, normalized_integrated))
            reasons = []
            if regime != "BULLISH":
                reasons.append("BTC_REGIME_NOT_BULLISH")
            reasons.extend(btc_relationship_reasons(
                c30, c90, beta90, relative_strength,
                args.min_btc_corr_30d, args.min_btc_corr_90d,
            ))
            if tick.spread_pct is None or tick.spread_pct > args.max_spread_pct:
                reasons.append("SPREAD_TOO_WIDE")
            ret = (full.final_equity / full.config.initial_quote_balance - ONE) * D("100")
            oos_ret = (oos.final_equity / oos.config.initial_quote_balance - ONE) * D("100")
            integrated_ret = (integrated.final_equity / integrated.config.initial_quote_balance - ONE) * D("100")
            integrated_oos_ret = (integrated_oos.final_equity / integrated_oos.config.initial_quote_balance - ONE) * D("100")
            if not integrated.fills:
                reasons.append("NO_INTEGRATED_STRATEGY_ENTRY")
            if integrated_ret <= 0:
                reasons.append("INTEGRATED_NET_FULL_PERIOD_RETURN_NOT_POSITIVE")
            if integrated_oos_ret <= 0:
                reasons.append("INDEPENDENT_VALIDATION_RETURN_NOT_POSITIVE")
            rows.append((symbol, tick, universe[symbol].filters, c30, c90, beta90, relative_strength, full, integrated, oos, integrated_oos, ret, oos_ret, integrated_ret, integrated_oos_ret, reasons, items[warmup_bars:warmup_bars + bars]))
        except (ProductV2Error, BacktestError, RuntimeError, ValueError) as exc:
            excluded.append((symbol, f"DATA_OR_BACKTEST_ERROR:{type(exc).__name__}:{exc}"))

    print("\nALTCOIN RESULTS")
    if not rows:
        print("No candidate passed the data prerequisites.")
    for row in sorted(rows, key=lambda x: (not x[15], x[1].quote_volume), reverse=True):
        sym, tick, filt, c30, c90, beta90, strength, full, integrated, oos, integrated_oos, ret, oos_ret, integrated_ret, integrated_oos_ret, reasons, candidate_items = row
        print(f"{sym}: price={tick.price} USDT; quoteVolume24h={tick.quote_volume}; spread={tick.spread_pct}%")
        print(f"  corr30d={c30}; corr90d={c90}; beta90d={beta90}; relativeStrength30d={strength}%")
        print(f"  minQty={filt.min_qty}; stepSize={filt.step_size}; minNotional={filt.min_notional}")
        print(f"  BASELINE EMA/RSI 180d: return={ret}%; drawdown={full.max_drawdown * D('100')}%; entries={sum(f.side == 'BUY' for f in full.fills)}; exits={sum(f.side == 'SELL' for f in full.fills)}; exposure={exposure_pct(candidate_items, full.fills)}%; fees={full.total_fees} USDT; modeled spread+slippage={modeled_spread_slippage_cost(full.fills, args.spread_bps, args.slippage_bps)} USDT")
        print(f"  INTEGRATED BTC-GATED 180d: return={integrated_ret}%; drawdown={integrated.max_drawdown * D('100')}%; entries={sum(f.side == 'BUY' for f in integrated.fills)}; exits={sum(f.side == 'SELL' for f in integrated.fills)}; exposure={exposure_pct(candidate_items, integrated.fills)}%; fees={integrated.total_fees} USDT; rejected by regime={sum('BTC_REGIME_NOT_BULLISH' in r for r in integrated.entry_rejections)}; rejected by correlation={sum('BTC_CORRELATION_' in r for r in integrated.entry_rejections)}; rejected by unknown metrics/data={sum('BTC_METRICS_INSUFFICIENT' in r for r in integrated.entry_rejections)}")
        print(f"  Benchmark: cash=0%; BTC buy-and-hold net={btc_hold}% over the same 180d evaluation")
        print(f"  INDEPENDENT VALIDATION 60d: baseline return={oos_ret}%; DD={oos.max_drawdown * D('100')}%; integrated return={integrated_oos_ret}%; DD={integrated_oos.max_drawdown * D('100')}%; integrated entries={sum(f.side == 'BUY' for f in integrated_oos.fills)}; BTC rejections={len(integrated_oos.entry_rejections)}; BTC buy-and-hold net={buy_hold_net_return(btc_items[warmup_bars + bars:warmup_bars + bars + validation_bars], args.fee_rate, args.spread_bps, args.slippage_bps)}%; cash=0%")
        print(f"  research filter={'PASS' if not reasons else 'REJECT'}; reasons={','.join(reasons) if reasons else 'none'}")
    print("\nWALK-FORWARD VALIDATION — THREE CONSECUTIVE 60-DAY WINDOWS")
    print("Parameters fixed before validation; no tuning on validation outcomes. Spreads are modeled assumptions, not observed history.")
    for v in validation_rows:
        b, i = v["baseline"], v["integrated"]
        regime_rejections = sum("BTC_REGIME_NOT_BULLISH" in reason for reason in i.entry_rejections)
        correlation_rejections = sum("BTC_CORRELATION_" in reason for reason in i.entry_rejections)
        data_rejections = sum(any(code in reason for code in ("BTC_METRICS_INSUFFICIENT", "BTC_HISTORY_NOT_TEMPORALLY_ALIGNED")) for reason in i.entry_rejections)
        for strategy_name, result, result_return in (("BASELINE", b, v["baseline_return"]), ("INTEGRATED", i, v["integrated_return"])):
            buys = sum(fill.side == "BUY" for fill in result.fills)
            sells = sum(fill.side == "SELL" for fill in result.fills)
            net_pnl = result.final_equity - result.config.initial_quote_balance
            modeled_cost = modeled_spread_slippage_cost(result.fills, v["spread"], v["slip"])
            order_rejections = sum("ORDER_REJECTED:" in reason for reason in result.entry_rejections)
            print(f"VALIDATION window={v['window']} symbol={v['symbol']} range={v['start'].isoformat()}..{v['end'].isoformat()} cost={v['cost']} fee_rate={v['fee']} spread_bps={v['spread']} slippage_bps={v['slip']} strategy={strategy_name} initial_usdt={result.config.initial_quote_balance} final_equity_usdt={result.final_equity} net_pnl_usdt={net_pnl} return={result_return}% drawdown={result.max_drawdown*D('100')}% buys={buys} sells={sells} fills={len(result.fills)} fees_usdt={result.total_fees} modeled_spread_slippage_usdt={modeled_cost} uninvested_quote_usdt={result.final_quote_balance} order_rejections={order_rejections} gate_or_data_rejections={len(result.entry_rejections)-order_rejections}")
        print(f"  BENCHMARK window={v['window']} cost={v['cost']} cash_return=0% cash_drawdown=0% BTC_buy_hold_return={v['btc_return']}% BTC_buy_hold_drawdown={v['btc_dd']}% hypothetical_initial_usdt={args.hypothetical_usdt_balance}")
        for strategy_name, result in (("BASELINE", b), ("INTEGRATED", i)):
            for fill in result.fills:
                print(f"  VALIDATION_FILL window={v['window']} symbol={v['symbol']} cost={v['cost']} strategy={strategy_name} side={fill.side} timestamp={fill.timestamp.isoformat()} price={fill.price} quantity={fill.quantity} fee={fill.fee} realized_pnl={fill.realized_pnl} quote_balance_after={fill.quote_balance_after} reason={fill.reason}")
        for strategy_name, result in (("BASELINE", b), ("INTEGRATED", i)):
            for rejection in result.entry_rejections:
                print(f"  VALIDATION_REJECTION window={v['window']} symbol={v['symbol']} cost={v['cost']} strategy={strategy_name} detail={rejection}")
    print(f"Walk-forward rows={len(validation_rows)}; maximum expected={len(candidates)*VALIDATION_WINDOW_COUNT*len(VALIDATION_COST_SCENARIOS)}.")

    print("\n180-DAY EVALUATION — HYPOTHETICAL CAPITAL AND COST SCENARIOS")
    print(f"All strategy results below start with the explicit hypothetical USDT balance; {args.initial_ars} ARS remains CONVERSION_UNKNOWN.")
    for e in evaluation_rows:
        for strategy_name, result in (("BASELINE", e["baseline"]), ("INTEGRATED", e["integrated"])):
            net_pnl = result.final_equity - result.config.initial_quote_balance
            ret = net_pnl / result.config.initial_quote_balance * D("100")
            buys = sum(fill.side == "BUY" for fill in result.fills)
            sells = sum(fill.side == "SELL" for fill in result.fills)
            modeled_cost = modeled_spread_slippage_cost(result.fills, e["spread"], e["slip"])
            order_rejections = sum("ORDER_REJECTED:" in reason for reason in result.entry_rejections)
            print(f"EVALUATION symbol={e['symbol']} cost={e['cost']} fee_rate={e['fee']} spread_bps={e['spread']} slippage_bps={e['slip']} strategy={strategy_name} initial_usdt={result.config.initial_quote_balance} final_equity_usdt={result.final_equity} net_pnl_usdt={net_pnl} return={ret}% drawdown={result.max_drawdown*D('100')}% buys={buys} sells={sells} fills={len(result.fills)} fees_usdt={result.total_fees} modeled_spread_slippage_usdt={modeled_cost} uninvested_quote_usdt={result.final_quote_balance} order_rejections={order_rejections} gate_or_data_rejections={len(result.entry_rejections)-order_rejections} cash_benchmark_return=0% BTC_buy_hold_return={e['btc_return']}% BTC_buy_hold_drawdown={e['btc_dd']}%")
            for rejection in result.entry_rejections:
                print(f"  EVALUATION_REJECTION symbol={e['symbol']} cost={e['cost']} strategy={strategy_name} detail={rejection}")

    print(f"Research candidates passing current-snapshot non-budget controls: {sum(1 for row in rows if not row[15])}")
    print("Comparison: BASELINE EMA/RSI is ungated reference; INTEGRATED BTC-GATED applies historical regime/correlation admission at each entry. Exits are never blocked by entry filters.")
    print("Split: 90d warm-up; 180d evaluation; three consecutive independent 60d validation windows with fixed parameters; validation is never used for fitting.")
    print("\nNORMALIZED USDT CAPITAL SENSITIVITY — NOT ARS CONVERSION")
    print(f"The {args.hypothetical_usdt_balance} USDT scenario plus 100/250/500 USDT sensitivity values are hypothetical computational scenarios only; none is an ARS conversion.")
    print("Hypothetical USDT | Candidate | BTC relationship controls | Affordable at current snapshot | Affordability reason | Virtual BUY entries | Round-trip break-even | Net return | Max drawdown | Fees USDT | Decision")
    for budget, symbol, affordable, reason, result, integrated_result in normalized_rows:
        net_return = (result.final_equity / result.config.initial_quote_balance - ONE) * D("100")
        integrated_net_return = (integrated_result.final_equity / integrated_result.config.initial_quote_balance - ONE) * D("100")
        buys = sum(fill.side == "BUY" for fill in result.fills)
        integrated_buys = sum(fill.side == "BUY" for fill in integrated_result.fills)
        affordability_label = "UNKNOWN" if affordable is None else ("YES" if affordable else "NO")
        candidate_row = next((candidate for candidate in rows if candidate[0] == symbol), None)
        relationship_label = "UNKNOWN" if candidate_row is None else ("REJECT" if (any(code in candidate_row[15] for code in ("BTC_METRICS_INSUFFICIENT", "BTC_CORRELATION_30D_BELOW_MINIMUM", "BTC_CORRELATION_90D_BELOW_MINIMUM", "BTC_REGIME_NOT_BULLISH")) or any(code in rejection for rejection in candidate_row[8].entry_rejections for code in ("BTC_METRICS_INSUFFICIENT", "BTC_CORRELATION_", "BTC_REGIME_NOT_BULLISH", "BTC_HISTORY_NOT_TEMPORALLY_ALIGNED"))) else "PASS")
        decision = ("REJECTED_NO_TRADE" if relationship_label == "REJECT" else
                    ("NO_TRADE" if not integrated_buys or integrated_net_return <= ZERO else "RESEARCH_ONLY"))
        break_even = modeled_round_trip_breakeven_pct(args.fee_rate, args.spread_bps, args.slippage_bps)
        print(f"{budget} | {symbol} | BTC-controls={relationship_label} | affordable={affordability_label} ({reason}) | baseline BUY={buys}, return={net_return}%, DD={result.max_drawdown * D('100')}%, fees={result.total_fees} | integrated BUY={integrated_buys}, return={integrated_net_return}%, DD={integrated_result.max_drawdown * D('100')}%, fees={integrated_result.total_fees}, BTC-rejections={len(integrated_result.entry_rejections)} | break-even={break_even}% | decision={decision}")
    print("\nEXCLUSIONS (first 30)")
    for symbol, reason in excluded[:30]:
        print(f"  {symbol}: {reason}")
    print("\nFINAL DECISION: NO_TRADE")
    print(f"Budget gate: CONVERSION_UNKNOWN. No candidate is declared affordable with {args.initial_ars} ARS; ARS→USDT route and costs were not verified.")
    print("Research backtests are not evidence of future profitability. No real order is placed.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
