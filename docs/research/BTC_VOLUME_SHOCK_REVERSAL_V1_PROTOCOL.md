# BTC_VOLUME_SHOCK_REVERSAL_V1 — preregistered protocol

- Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/157
- Branch: research/btc-volume-shock-reversal-multiregime
- State before replay: hypothesis frozen; not promoted; NO_TRADE
- Main and TOP1/PRIME changes are prohibited.

## Hypothesis and fixed rules

Research the idea that a large negative BTC daily return on abnormal volume may be followed by a short Spot rebound. This event-driven signal is different from the project's previous EMA/RSI/Donchian grids, BTC/ETH rotations, ETH Bollinger mean reversion and cost-aware ML candidate. This rationale is an inference, not a claim of edge.

- Market: BTCUSDT Spot; completed UTC daily candles.
- Source interval: [2019-01-01 00:00 UTC, 2026-10-22 00:00 UTC), including warm-up. Require exactly one open timestamp for every UTC date, aligned close time and valid OHLCV. Any gap, duplicate, unexpected candle or schema problem blocks the economic replay. No interpolation, fabrication, silent omission or bridging of gaps.
- Signal after close t: close[t]/close[t-1]-1 <= -2.5% and volume[t] >= 1.5 times median(volume[t-20:t]); the 20-bar median excludes candle t.
- Entry fill occurs at next daily open. One long Spot position maximum; signals while invested are ignored.
- Position size is fixed at 20% of available quote cash per new entry, rounded down to current public LOT_SIZE step. Enforce current public minQty and minNotional. Record hash of public exchangeInfo. Historical lot rules remain UNKNOWN.
- Exit signal after a completed close if close >= executed entry fill x 1.025, or close <= executed entry fill x 0.975, or five completed daily holding candles have elapsed. Exit at next daily open. No intrabar stop assumptions. Liquidate a remaining position at the last close of each evaluation window with modeled costs.
- Use Decimal cash accounting; apply fees to each execution. Effective fill price includes half the assumed full spread plus side-specific slippage. Equity curves mark any open position at estimated net liquidation value after exit friction and fee.

## Dataset, costs and provenance

- Public Binance Spot Klines provide OHLCV. Public exchangeInfo is used only for present-day quantity filters.
- Record raw response SHA-256 hashes, canonical dataset SHA-256, candle count, first/last timestamps, quality checks, present-day filter response hash, all fills, equity curve, summary and gate results.
- Historical point-in-time availability, historical order-book quotes and past exchange filters are UNKNOWN.
- Spread/slippage are modeled assumptions, not historical quotes:
  - BASE: fee 10 bps per execution, assumed full spread 10 bps, slippage 5 bps per side.
  - STRESS_1: fee 10 bps, full spread 25 bps, slippage 10 bps per side.
  - STRESS_2: fee 20 bps, full spread 50 bps, slippage 20 bps per side.
- Benchmarks: cash and BTC buy-and-hold at 95% initial-capital allocation with matched modeled costs.
- Each run resets capital independently to 60 and 100 USDT.

## Frozen windows

- FULL_2020_2026: [2020-01-01, 2026-10-22)
- EARLIER_2020_2022: [2020-01-01, 2023-01-01)
- RECENT_2023_2026: [2023-01-01, 2026-10-22)
- Calendar years 2020–2025 and 2026_YTD [2026-01-01, 2026-10-22).

These dates have already been inspected in earlier project research; they do not form a pristine prospective holdout.

## Frozen promotion gates

Under STRESS_2, both capital sizes must satisfy every gate:
1. Net PnL > 0 in FULL, EARLIER, RECENT and 2026_YTD.
2. At least five closed round trips in each of those four principal windows.
3. Maximum drawdown no worse than -15% in FULL, EARLIER, RECENT and every annual/YTD window.
4. 2022 PnL nonnegative.
5. At least four positive windows among annual 2020–2025 and 2026_YTD.
6. RECENT net PnL exceeds cash and matched-cost BTC buy-and-hold.
7. Data continuity, causal signal timing, Decimal cash accounting, lot rounding and terminal liquidation pass unit tests.

Any failed gate rejects the candidate; no parameter rescue on these windows. A successful workflow proves only code completion, not profitability. A historical pass still requires independently preregistered prospective shadow validation. NO_TRADE remains. No private API access, credentials, real orders, leverage or main changes.
