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
from decimal import Decimal, InvalidOperation
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
    fee_rate: Decimal = FEE, spread_bps: Decimal = D("10"), slippage_bps: Decimal = SLIPPAGE_BPS,
    fast_ema_period: int = 12, slow_ema_period: int = 26, rsi_period: int = 14,
    rsi_buy_min: Decimal = D("50"), rsi_sell_at: Decimal = D("70"),
    position_fraction: Decimal = D("0.95"),
) -> BacktestResult:
    config = BacktestConfig(
        symbol=symbol, interval="4h", candles=bars, fast_ema_period=fast_ema_period,
        slow_ema_period=slow_ema_period, rsi_period=rsi_period, rsi_buy_min=rsi_buy_min, rsi_sell_at=rsi_sell_at,
        initial_quote_balance=D("1000"), position_fraction=position_fraction,
        fee_rate=fee_rate, slippage_bps=slippage_bps, spread_bps=spread_bps, max_entries_per_30d=5,
        min_qty=filters.min_qty if filters else None,
        quantity_step=filters.step_size if filters else None,
        min_notional=filters.min_notional if filters else None,
    )
    return run_virtual_backtest(items, config)


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
    regime = "BULLISH" if btc_result.final_fast_ema > btc_result.final_slow_ema and btc_result.final_rsi >= args.rsi_buy_min else (
        "BEARISH" if btc_result.final_fast_ema < btc_result.final_slow_ema and btc_result.final_rsi < D("50") else "NEUTRAL"
    )
    btc_hold = buy_hold_net_return(btc_items[-bars:], args.fee_rate, args.spread_bps, args.slippage_bps)

    print("BOT-CRIPTO PRODUCT V2 — PUBLIC BINANCE SPOT")
    print(f"Observation UTC: {now.isoformat()}; timeframe=4h; requested history={args.days} days")
    print(f"BTC panel: regime={regime}; price={ticker['BTCUSDT'].price} USDT; EMA{args.fast_ema_period}={btc_result.final_fast_ema}; EMA{args.slow_ema_period}={btc_result.final_slow_ema}; RSI{args.rsi_period}={btc_result.final_rsi}")
    print(f"BTC closed bars={btc_result.candle_count}; range={btc_result.first_candle.isoformat()} .. {btc_result.last_candle.isoformat()}")
    print("Temporal caveat: historical available_at may be UNKNOWN; the simulator explicitly models decision at bar close and fills no earlier than the next bar open.")
    print(f"BTC buy-and-hold net reference after fee/spread/slippage assumptions={btc_hold}%; cash reference=0%")
    ars_market = ticker.get("ARSUSDT")
    if ars_market is None:
        print(f"ARS budget={args.initial_ars}; direct ARSUSDT Spot quote not found in active public universe; conversion=CONVERSION_UNKNOWN")
    else:
        print(f"ARS budget={args.initial_ars}; indicative ARSUSDT public spot price={ars_market.price} USDT/ARS; bid={ars_market.bid}; ask={ars_market.ask}; spread={ars_market.spread_pct}%; route/funding/fees=UNKNOWN; conversion=CONVERSION_UNKNOWN")
    print("WARNING: no verified ARS-to-USDT route; affordability is blocked. Research backtests use a separate normalized 1000 USDT, not the ARS budget.")
    print(f"Strategy: EMA{args.fast_ema_period}/EMA{args.slow_ema_period} crossover + RSI{args.rsi_period}; RSI buy minimum={args.rsi_buy_min}; sell threshold={args.rsi_sell_at}; position fraction={args.position_fraction}; fee/fill={args.fee_rate}; assumed full spread={args.spread_bps} bps; slippage/fill={args.slippage_bps} bps; max 5 BUY entries/rolling 30 days.")
    print("Historical spread is not observable from klines; 10 bps is a conservative configurable model assumption, not a measured historical spread.")
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
    print(f"Universe active USDT Spot={len(universe)}; valid ticker snapshots={len(ticker)}; candidates backtested={len(candidates)}")
    btc_by_start = {item.payload.start: item for item in btc_items}
    rows = []
    for symbol, tick in candidates:
        try:
            items = fetch_history(adapter, symbol, args.days, now)
            if len(items) < bars:
                excluded.append((symbol, "INSUFFICIENT_HISTORY"))
                continue
            if [x.payload.start for x in items[-bars:]] != [x.payload.start for x in btc_items[-bars:]]:
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
            reasons = []
            if regime != "BULLISH":
                reasons.append("BTC_REGIME_NOT_BULLISH")
            if c30 is None or c90 is None or beta90 is None or relative_strength is None:
                reasons.append("BTC_METRICS_INSUFFICIENT")
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
            rows.append((symbol, tick, universe[symbol].filters, c30, c90, beta90, relative_strength, full, oos, ret, oos_ret, reasons))
        except (ProductV2Error, BacktestError, RuntimeError, ValueError) as exc:
            excluded.append((symbol, f"DATA_OR_BACKTEST_ERROR:{type(exc).__name__}:{exc}"))

    print("\nALTCOIN RESULTS")
    if not rows:
        print("No candidate passed the data prerequisites.")
    for row in sorted(rows, key=lambda x: (not x[11], x[1].quote_volume), reverse=True):
        sym, tick, filt, c30, c90, beta90, strength, full, oos, ret, oos_ret, reasons = row
        print(f"{sym}: price={tick.price} USDT; quoteVolume24h={tick.quote_volume}; spread={tick.spread_pct}%")
        print(f"  corr30d={c30}; corr90d={c90}; beta90d={beta90}; relativeStrength30d={strength}%")
        print(f"  minQty={filt.min_qty}; stepSize={filt.step_size}; minNotional={filt.min_notional}")
        print(f"  180d net research return={ret}%; max drawdown={full.max_drawdown * D('100')}%; entries={sum(f.side == 'BUY' for f in full.fills)}; fees={full.total_fees} USDT")
        print(f"  held-out 60d return={oos_ret}%; held-out drawdown={oos.max_drawdown * D('100')}%; held-out entries={sum(f.side == 'BUY' for f in oos.fills)}")
        print(f"  research filter={'PASS' if not reasons else 'REJECT'}; reasons={','.join(reasons) if reasons else 'none'}")
    print(f"Research candidates passing non-budget controls: {sum(1 for row in rows if not row[11])}")
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
