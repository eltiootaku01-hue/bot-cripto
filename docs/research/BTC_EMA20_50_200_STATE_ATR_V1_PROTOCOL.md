# BTC_EMA20_50_200_STATE_ATR_V1 — frozen state-transition replay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/165
- Isolated branch: \`research/btc-ema20-50-200-state-atr-v1\`
- State-transition hypothesis: EMA20/EMA50/EMA200 bullish stack with price above EMA200; entry only on transition into the state, exit on loss of fast/medium alignment or price below EMA200.
- No parameter sweep. Reuses the previously frozen ATR14 sizing overlay from Issue #143 without changing its parameters.

## Frozen signal and sizing
Use BTCUSDT daily Spot candles in UTC. EMA20/50/200 are recursive EMAs, alpha=2/(period+1), seeded from the first close. No state signal before 200 completed candles. Bullish state at t: close[t] > EMA200[t] AND EMA20[t] > EMA50[t] > EMA200[t]. Entry on false-to-true transition; first valid EMA200 state can be treated as first transition from no prior valid regime. Exit when EMA20[t] <= EMA50[t] OR close[t] < EMA200[t]. Decide after close, execute next daily open. Terminal liquidation at final completed close of each window.

ATR14 is the simple 14-bar arithmetic mean of true range, with TR=max(high-low,abs(high-prior_close),abs(low-prior_close)). At signal close, weight=min(95%, 20%/((ATR14/close)*sqrt(365))); freeze through position, no intratrade rebalance. One Spot long, no shorts/leverage, maximum five accepted entries per rolling 30 days.

## Data/cost contract
Public Binance Spot BTCUSDT daily candles UTC expected on [2018-11-27, 2026-10-22), with 400 warmup bars before 2020-01-01. Require exact daily timestamps, no gaps/duplicates, valid OHLCV, correct close times; any failure blocks the replay, never interpolate/drop rows silently. Persist dataset/indicator SHA-256, counts, quality metadata, current ExchangeInfo filter snapshot/hash, fills/equity, summaries and frozen gates. Historic point-in-time availability, historical filters, and executable historical quote costs remain UNKNOWN.

Capital resets to 60 and 100 USDT in every window. BASE: fee 10 bps/fill, full spread assumed 10 bps, slippage 5 bps/side; STRESS_1: 10/25/10; STRESS_2: 20/50/20. Spread/slippage are modeled assumptions, not observed historical quotes. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL [2020-01-01,2026-10-22); EARLIER [2020-01-01,2023-01-01); RECENT [2023-01-01,2026-10-22); 2020–2025 annual windows and 2026 YTD [2026-01-01,2026-10-22).

## Frozen gate
At STRESS_2, both capitals must have positive PnL in FULL/EARLIER/RECENT/2026YTD; at least five complete round trips in each principal window; max drawdown no worse than -15% in FULL/EARLIER/RECENT and all annual/YTD windows; 2022 PnL>=0; at least four positive annual/YTD windows; RECENT PnL above cash and BTC buy-and-hold. Tests cover recursive EMA arithmetic, transition-only entry, exit condition, causal inputs, ATR sizing and signal warmup. Any failed gate = FAIL/REJECTED/NO_TRADE; no post-result tuning. Historic pass still needs prospective shadow evidence. main and TOP1/PRIME remain untouched.
