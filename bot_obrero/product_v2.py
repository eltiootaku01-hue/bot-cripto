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
from typing import Any, Mapping, Sequence

from .binance_instruments import BinanceMetadataBackedAdapter, BinanceSpotInstrumentMetadata
from .binance_spot import BinanceSpotRestConfig
from .virtual_backtest import BacktestConfig, BacktestError, BacktestResult, _validate_history, run_virtual_backtest

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
    return run_virtual_backtest(items, config)


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BTC regime + liquid USDT altcoin scan and 4h virtual backtest.")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--universe-limit", type=int, default=5)
    parser.add_argument("--initial-ars", type=exact_arg, default=D("10000"))
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
    universe = active_usdt_universe(public_json("/api/v3/exchangeInfo"))
    ticker = ticker_snapshots(
        universe, public_json("/api/v3/ticker/24hr"), public_json("/api/v3/ticker/bookTicker")
    )
    if "BTCUSDT" not in ticker:
        raise ProductV2Error("BTCUSDT ticker/book data unavailable")
    metadata = BinanceSpotInstrumentMetadata(config=BinanceSpotRestConfig(endpoint="/api/v3/exchangeInfo"))
    adapter = BinanceMetadataBackedAdapter(metadata=metadata)
    btc_items = fetch_history(adapter, "BTCUSDT", args.days, now)
    bars = args.days * 6
    if len(btc_items) < bars:
        raise ProductV2Error(f"BTC history insufficient: requested at least {bars}, received {len(btc_items)}")
    btc_result = backtest("BTCUSDT", btc_items, bars, universe.get("BTCUSDT").filters if universe.get("BTCUSDT") else None, fee_rate=args.fee_rate, spread_bps=args.spread_bps, slippage_bps=args.slippage_bps, fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period, rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min, rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction)
    btc_returns = return_series(btc_items)
    btc_last_closed = btc_items[-1]
    btc_last_closed_close = btc_last_closed.payload.close
    regime = classify_btc_regime(
        btc_last_closed_close, btc_result.final_fast_ema, btc_result.final_slow_ema,
        btc_result.final_rsi, args.rsi_buy_min,
    )
    btc_hold = buy_hold_net_return(btc_items[-bars:], args.fee_rate, args.spread_bps, args.slippage_bps)

    print("BOT-CRIPTO PRODUCT V2 — PUBLIC BINANCE SPOT")
    print(f"Observation UTC: {now.isoformat()}; timeframe=4h; requested history={args.days} days")
    print(f"BTC panel: regime={regime}; last closed 4h close={btc_last_closed_close} USDT at {btc_last_closed.payload.end.isoformat()}; EMA{args.fast_ema_period}={btc_result.final_fast_ema}; EMA{args.slow_ema_period}={btc_result.final_slow_ema}; RSI{args.rsi_period}={btc_result.final_rsi}")
    print(f"BTC spot ticker (separate snapshot, not regime input): price={ticker['BTCUSDT'].price} USDT; observed UTC={now.isoformat()}")
    print(f"BTC relationship thresholds (research defaults, not validated): corr30d>={args.min_btc_corr_30d}; corr90d>={args.min_btc_corr_90d}")
    print(f"BTC closed bars={btc_result.candle_count}; range={btc_result.first_candle.isoformat()} .. {btc_result.last_candle.isoformat()}")
    print("Temporal caveat: historical available_at may be UNKNOWN; the simulator explicitly models decision at bar close and fills no earlier than the next bar open.")
    print(f"BTC buy-and-hold net reference after fee/spread/slippage assumptions={btc_hold}%; cash reference=0%")
    print(f"ARS budget={args.initial_ars}; conversion=CONVERSION_UNKNOWN; ARS-to-USDT route is unverified; the filtered USDT Spot universe is not an exhaustive fiat/P2P conversion search")
    print("Conversion evidence: no live ARS/USDT offer, payment method, executable price, applicable fee, limit, or net USDT was verified; affordability remains blocked.")
    print("WARNING: no verified ARS-to-USDT route; affordability is blocked. Normalized USDT scenarios are separate research assumptions, not conversions.")
    print(f"Strategy: EMA{args.fast_ema_period}/EMA{args.slow_ema_period} crossover + RSI{args.rsi_period}; RSI buy minimum={args.rsi_buy_min}; sell threshold={args.rsi_sell_at}; position fraction={args.position_fraction}; fee/fill={args.fee_rate}; assumed full spread={args.spread_bps} bps; slippage/fill={args.slippage_bps} bps; max 5 BUY entries/rolling 30 days.")
    print(f"Cost assumptions: fee/fill={args.fee_rate}; full spread={args.spread_bps} bps; slippage/fill={args.slippage_bps} bps.")
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
    normalized_rows: list[tuple[Decimal, str, bool | None, str, BacktestResult]] = []
    normalized_budgets = (D("100"), D("250"), D("500"))
    for symbol, tick in candidates:
        try:
            items = fetch_history(adapter, symbol, args.days, now)
            if len(items) < bars:
                excluded.append((symbol, "INSUFFICIENT_HISTORY"))
                continue
            if not histories_temporally_aligned(items[-bars:], btc_items[-bars:]):
                excluded.append((symbol, "BTC_HISTORY_NOT_TEMPORALLY_ALIGNED"))
                continue
            asset_returns = return_series(items)
            c30, _ = correlation_beta(asset_returns, btc_returns, 30 * 6 - 1)
            c90, beta90 = correlation_beta(asset_returns, btc_returns, 90 * 6 - 1)
            relative, btc30 = return_pct(items, 30 * 6), return_pct(btc_items, 30 * 6)
            relative_strength = relative - btc30 if relative is not None and btc30 is not None else None
            full = backtest(symbol, items, bars, universe[symbol].filters, fee_rate=args.fee_rate, spread_bps=args.spread_bps, slippage_bps=args.slippage_bps, fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period, rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min, rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction)
            # A fixed-parameter 60-day held-out slice is reported separately from the 180-day result.
            oos = backtest(symbol, items[-60 * 6:], 60 * 6, universe[symbol].filters, fee_rate=args.fee_rate, spread_bps=args.spread_bps, slippage_bps=args.slippage_bps, fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period, rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min, rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction)
            for normalized_budget in normalized_budgets:
                affordable, affordability_reason = normalized_affordability(
                    tick.price, universe[symbol].filters, normalized_budget,
                    fee_rate=args.fee_rate, spread_bps=args.spread_bps,
                    slippage_bps=args.slippage_bps, position_fraction=args.position_fraction,
                )
                normalized_result = backtest(
                    symbol, items, bars, universe[symbol].filters,
                    initial_quote_balance=normalized_budget, fee_rate=args.fee_rate,
                    spread_bps=args.spread_bps, slippage_bps=args.slippage_bps,
                    fast_ema_period=args.fast_ema_period, slow_ema_period=args.slow_ema_period,
                    rsi_period=args.rsi_period, rsi_buy_min=args.rsi_buy_min,
                    rsi_sell_at=args.rsi_sell_at, position_fraction=args.position_fraction,
                )
                normalized_rows.append((normalized_budget, symbol, affordable, affordability_reason, normalized_result))
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
            if not full.fills:
                reasons.append("NO_STRATEGY_ENTRY")
            if ret <= 0:
                reasons.append("NET_FULL_PERIOD_RETURN_NOT_POSITIVE")
            if oos_ret <= 0:
                reasons.append("HELD_OUT_RETURN_NOT_POSITIVE")
            rows.append((symbol, tick, universe[symbol].filters, c30, c90, beta90, relative_strength, full, oos, ret, oos_ret, reasons, items[-bars:]))
        except (ProductV2Error, BacktestError, RuntimeError, ValueError) as exc:
            excluded.append((symbol, f"DATA_OR_BACKTEST_ERROR:{type(exc).__name__}:{exc}"))

    print("\nALTCOIN RESULTS")
    if not rows:
        print("No candidate passed the data prerequisites.")
    for row in sorted(rows, key=lambda x: (not x[11], x[1].quote_volume), reverse=True):
        sym, tick, filt, c30, c90, beta90, strength, full, oos, ret, oos_ret, reasons, candidate_items = row
        print(f"{sym}: price={tick.price} USDT; quoteVolume24h={tick.quote_volume}; spread={tick.spread_pct}%")
        print(f"  corr30d={c30}; corr90d={c90}; beta90d={beta90}; relativeStrength30d={strength}%")
        print(f"  minQty={filt.min_qty}; stepSize={filt.step_size}; minNotional={filt.min_notional}")
        print(f"  180d net research return={ret}%; max drawdown={full.max_drawdown * D('100')}%; exposure={exposure_pct(candidate_items, full.fills)}%; entries={sum(f.side == 'BUY' for f in full.fills)}; fees={full.total_fees} USDT; estimated spread+slippage={modeled_spread_slippage_cost(full.fills, args.spread_bps, args.slippage_bps)} USDT")
        print(f"  held-out 60d return={oos_ret}%; held-out drawdown={oos.max_drawdown * D('100')}%; held-out exposure={exposure_pct(items[-60 * 6:], oos.fills)}%; held-out entries={sum(f.side == 'BUY' for f in oos.fills)}; fees={oos.total_fees} USDT; estimated spread+slippage={modeled_spread_slippage_cost(oos.fills, args.spread_bps, args.slippage_bps)} USDT")
        print(f"  research filter={'PASS' if not reasons else 'REJECT'}; reasons={','.join(reasons) if reasons else 'none'}")
    print(f"Research candidates passing non-budget controls: {sum(1 for row in rows if not row[11])}")
    print("\nNORMALIZED USDT CAPITAL SENSITIVITY — NOT ARS CONVERSION")
    print("The 100/250/500 USDT budgets are hypothetical computational scenarios only; no exchange rate or ARS affordability is implied.")
    print("Hypothetical USDT | Candidate | BTC relationship controls | Affordable at current snapshot | Affordability reason | Virtual BUY entries | Net return | Max drawdown | Fees USDT | Decision")
    for budget, symbol, affordable, reason, result in normalized_rows:
        net_return = (result.final_equity / result.config.initial_quote_balance - ONE) * D("100")
        buys = sum(fill.side == "BUY" for fill in result.fills)
        affordability_label = "UNKNOWN" if affordable is None else ("YES" if affordable else "NO")
        decision = "NO_TRADE" if not buys or net_return <= ZERO else "RESEARCH_ONLY"
        candidate_row = next((candidate for candidate in rows if candidate[0] == symbol), None)
        relationship_label = "UNKNOWN" if candidate_row is None else ("PASS" if not any(code in candidate_row[11] for code in ("BTC_METRICS_INSUFFICIENT", "BTC_CORRELATION_30D_BELOW_MINIMUM", "BTC_CORRELATION_90D_BELOW_MINIMUM")) else "REJECT")
        print(f"{budget} | {symbol} | {relationship_label} | {affordability_label} | {reason} | {buys} | {net_return}% | {result.max_drawdown * D('100')}% | {result.total_fees} | {decision}")
    print("\nEXCLUSIONS (first 30)")
    for symbol, reason in excluded[:30]:
        print(f"  {symbol}: {reason}")
    print("\nFINAL DECISION: NO_TRADE")
    print("Budget gate: CONVERSION_UNKNOWN. No candidate is declared affordable with 10000 ARS; ARS→USDT route and costs were not verified.")
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
