# BTC_MOMENTUM_90D_5PCT_SMA200_ATR14_TARGET20 — full-regime robustness extension

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/163
- Source strategy and prior result: Issue #143, commit `144170ab4094ef7117824ee721f016da920d5f8f`.
- The candidate was selected after observing 2023–2026; this is retrospective robustness, not a pristine holdout or prospective evidence.
- Entry/exit and risk-sizing rules are copied unchanged. Only evaluation windows and frozen gates are expanded.

## Frozen strategy (no changes)
BTCUSDT daily Spot. Enter at the next daily open after a completed close when 90-day momentum first crosses above +5% and close>SMA200. Exit at the next daily open after a completed close when momentum<=0 or close<SMA200. Terminal liquidation occurs at each window's final completed close. ATR14 is a simple mean of 14 true-range values, with TR=max(high-low,abs(high-prior_close),abs(low-prior_close)). Weight is frozen at signal close: `min(95%, 20%/((ATR14/close)*sqrt(365)))`. One Spot long position, no leverage/shorts; max five accepted entries per rolling 30 days. No intratrade rebalance.

## Data and costs
Public Binance Spot BTCUSDT daily klines, UTC, expected range `[2018-11-27, 2026-10-22)` (400 warm-up candles before 2020-01-01). Require exact expected starts, no gaps/duplicates, valid OHLCV and accepted history contracts. Any gap/malformed candle blocks evaluation. Record canonical dataset SHA-256, quality metadata, current public ExchangeInfo filter snapshot and hash, fills/equity/summaries. Current filters do not establish historic filters. Point-in-time availability and historic executable order-book quotes remain UNKNOWN.

Capital resets independently to 60 and 100 USDT for each window. BASE: fee 10 bps/fill, full spread assumed 10 bps and slippage 5 bps/side. STRESS_1: 10/25/10. STRESS_2: 20/50/20. Spread/slippage are assumptions, not observed historic quotes. Compare against cash and matched-cost BTC buy-and-hold.

## Frozen windows
- FULL_2020_2026: `[2020-01-01, 2026-10-22)`
- EARLIER_2020_2022: `[2020-01-01, 2023-01-01)`
- RECENT_2023_2026: `[2023-01-01, 2026-10-22)`
- Calendar years 2020–2025 and 2026_YTD: `[2026-01-01, 2026-10-22)`
Each window starts a separate cash-reset account; indicators use only candles available at each simulated point.

## Frozen gate
At STRESS_2, for both capitals: net PnL >0 in FULL/EARLIER/RECENT/2026_YTD; at least five closed round trips in each principal window; maximum drawdown no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 net PnL >=0; at least four positive windows among 2020–2025 and 2026 YTD; and RECENT net PnL greater than cash and matched-cost BTC buy-and-hold.

Any failure is FAIL/REJECTED/NO_TRADE. Do not adjust signal/sizing after results. The related signal/archive were previously inspected and no historical pass qualifies as a clean holdout; future promotion needs prospective shadow validation. Never touch main or ETH_BBMR_168_EMA720 TOP1/PRIME.
