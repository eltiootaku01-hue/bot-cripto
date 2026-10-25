# BTC_VOLATILITY_SQUEEZE_BREAKOUT_V1 — frozen protocol

- Issue #158: https://github.com/eltiootaku01-hue/bot-cripto/issues/158
- Branch: research/btc-volatility-squeeze-breakout-v1
- Code reuses the audited public-data and Decimal execution core from the failed isolated V1 candidate; it changes only the signal family and frozen exit levels.
- Main and TOP1/PRIME remain unchanged; NO_TRADE.

## Frozen hypothesis

BTCUSDT may break upward after a period of contracted true-range volatility. This is an event-driven volatility-squeeze breakout, not a parameter sweep, not the preceding volume-shock reversal, and not the ETH Bollinger candidate.

Signal decision at the close of completed daily candle t:
1. Breakout condition: close[t] > max(high[t-20:t]); current candle is excluded from the prior high.
2. Compression condition measured before the breakout candle: ATR14[t-1]/close[t-1] <= 0.75 times the median of ATR14[j]/close[j] over j=t-100 through t-1 inclusive.
3. ATR14 is the simple mean of the 14 true ranges ending at the relevant completed candle. True range uses max(high-low, abs(high-prev_close), abs(low-prev_close)).
4. Both conditions must pass. Entry at next daily open, no intrabar lookahead.

Exit after a completed close if close >= executed entry fill x 1.04, close <= entry fill x 0.97, or ten completed holding daily candles have elapsed. Fill next daily open; terminal liquidation at final close. One long Spot position at a time; each entry allocates 20% of available cash. No shorting, leverage, private API or real order.

## Data and accounting

- Source: public Binance Spot BTCUSDT daily Klines, [2019-01-01, 2026-10-22) UTC. Validate every expected timestamp, duplicates, close time, OHLCV consistency. Any gap or corruption blocks replay, with no interpolation/silent dropping.
- Reuses the hash manifest, raw page response hashes, canonical dataset SHA-256, current exchangeInfo filter snapshot, fills, daily equity curve, and BASE/STRESS_1/STRESS_2 scenarios from the existing research engine.
- BASE: fee 10 bps per fill, assumed full spread 10 bps, slippage 5 bps/side. STRESS_1: fee 10 bps, spread 25 bps, slippage 10 bps/side. STRESS_2: fee 20 bps, spread 50 bps, slippage 20 bps/side. Apply half-spread and per-side slippage on each fill; fees charged on quote notional.
- Enforce current LOT_SIZE step/minQty/minNotional; historic exchange filter equivalence UNKNOWN. Historical point-in-time availability and historic order-book quotes UNKNOWN.
- Compare cash and matched-cost BTC buy-and-hold at 95% allocation.

## Windows and gates

Capital resets independently to 60 and 100 USDT. Windows:
- FULL_2020_2026 [2020-01-01, 2026-10-22)
- EARLIER_2020_2022 [2020-01-01, 2023-01-01)
- RECENT_2023_2026 [2023-01-01, 2026-10-22)
- calendar 2020–2025 and 2026_YTD [2026-01-01, 2026-10-22).

Under STRESS_2, both capitals must have positive PnL in FULL/EARLIER/RECENT/2026_YTD, at least five closed round trips in those four windows, drawdown no worse than -15% in all principal and annual/YTD windows, nonnegative 2022 PnL, at least four positive annual/YTD windows, and RECENT outperformance of both cash and matched-cost BTC buy-and-hold. Unit tests must pass for ATR/true range, signal causality, next-open execution, Decimal accounting, quantity steps and terminal liquidation.

A failed gate means rejection and NO_TRADE; no post-hoc changes on this history. Even a historical pass would require an independently preregistered prospective shadow holdout because this archive was inspected previously.
