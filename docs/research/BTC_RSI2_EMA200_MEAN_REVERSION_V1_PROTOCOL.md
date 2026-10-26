# BTC_RSI2_EMA200_MEAN_REVERSION_V1 — frozen replay
- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/172
- Branch: research/btc-rsi2-ema200-mean-reversion-v1.
- Family: short-horizon mean reversion with RSI(2) oversold conditions within an EMA200 bull regime. This is distinct from breakout, KAMA, Ichimoku, DMI/ADX and prior volume-shock reversal hypotheses.
- ATR14 sizing reuses the previously frozen formula unchanged.

## Frozen rules
BTCUSDT Spot daily UTC. RSI2 uses Wilder smoothing: seed first two close changes with arithmetic average gain/loss, then smooth recursively using period 2. RSI=100-100/(1+avg_gain/avg_loss); if gain/loss are both zero RSI=50, if loss alone is zero RSI=100, if gain alone is zero RSI=0. EMA200 recursive alpha=2/201; SMA5 average of five completed closes including current candle.

Enter at completed close when RSI2<=10 and close>EMA200; fill at next daily open. Exit at completed close when close>SMA5 OR RSI2>=70 OR close<EMA200; fill at next open. One Spot long, no shorts/leverage, repeated entries while invested ignored; terminal liquidation at each window's final close.

ATR14 is simple mean of 14 true ranges. Frozen weight=min(95%,20%/((ATR14/close)*sqrt(365))), fixed at entry and not rebalanced intratrade. Maximum five accepted entries per rolling 30 days; enforce current public step/minQty/minNotional.

## Data/costs/windows
Public BTCUSDT daily UTC [2018-11-27,2026-10-22), 400 warm-up candles. Require exact timestamps, no gaps/duplicates, valid OHLCV and close times; block on failure without interpolation. Preserve canonical dataset SHA-256, indicator hash, quality metadata, current public filter snapshot/hash, fills/equity/results/gates. Historic point-in-time availability, historical filters and executable historical quotes remain UNKNOWN.

Capital resets independently at 60 and 100 USDT per window. BASE fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are assumptions. Compare cash and matched-cost BTC buy-and-hold.

Windows: FULL [2020-01-01,2026-10-22); EARLIER [2020-01-01,2023-01-01); RECENT [2023-01-01,2026-10-22); years 2020–2025 and 2026 YTD [2026-01-01,2026-10-22).

## Frozen gates
At STRESS_2 for both capitals: positive PnL in FULL/EARLIER/RECENT/2026YTD; at least five round trips in every principal window; max drawdown no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 PnL>=0; at least four positive annual/YTD windows; RECENT PnL exceeds cash and matched-cost BTC buy-and-hold. Tests cover RSI seed/recursion/edge cases, SMA5/EMA200 warm-up, signal conditions, causal values, next-open ledger, ATR sizing and terminal liquidation.

Any gate failure=FAIL/REJECTED/NO_TRADE; no post-result tuning. Historical archive is not a pristine holdout; any pass still requires prospective shadow validation. main and ETH_BBMR_168_EMA720 TOP1/PRIME stay untouched.
