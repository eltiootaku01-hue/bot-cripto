# BTC_ETH_ROTATION_10PCT_ATR_TARGET_2025_2026 — frozen risk overlay

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/173
- Isolated branch: \`research/btc-eth-rotation-10pct-atr-target-2025-2026\`
- Parent signal frozen from Issue #155: 4h BTC/ETH Spot relative-strength rotation every 42 completed evaluation bars. Select the stronger trailing 180-bar return only if positive and close above its own recursive EMA200; else cash. Signal at completed close, execute next 4h open.
- Only change: per-entry risk allocation. At signal close weight = \`min(50%,10%/((ATR14/close)*sqrt(365*6)))\`, where ATR14 is the simple mean of 14 true ranges on 4h candles. Freeze weight through the position; no intratrade rebalance. Invalid ATR => cash. This is a volatility proxy, not a guarantee of realized volatility. No parameter sweep.
- The historical archive was inspected during earlier candidate selection; this is retrospective robustness research, not a pristine holdout or operational approval.

## Data, costs and windows
Public Binance Spot BTCUSDT/ETHUSDT 4h UTC; 300 warm-up bars before 2025-01-01. Evaluate COMBINED [2025-01-01,2026-10-22), 2025 [2025-01-01,2026-01-01), and 2026 YTD [2026-01-01,2026-10-22). Require exact expected timestamps and cross-asset UTC alignment, valid OHLCV and close-time contract. Fail closed on gaps; no interpolation/silent omissions. Preserve BTC/ETH/combined hashes, current filter snapshots/hashes, risk-weight series hash, fills, equity and gates. Historic point-in-time availability, old filters and actual executable quotes remain UNKNOWN.

Capital 60 and 100 USDT separately reset by window. BASE fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side; STRESS_1 10/25/10; STRESS_2 20/50/20. Spread/slippage are assumptions. Buy-and-hold benchmarks stay at 95% notional with matched modeled costs.

## Frozen promotion gates
At STRESS_2 both capitals require positive net PnL in COMBINED/2025/2026 YTD; >=5 completed round trips in all three windows; max drawdown no worse than -15% in all three; and 2026 YTD PnL greater than both matched-cost BTC and ETH buy-and-hold. Any failure=FAIL/REJECTED/NO_TRADE; no tuning 10% target, 50% cap, signal, cadence or gates after results. Any historic pass requires prospective shadow validation. main and ETH_BBMR_168_EMA720 TOP1/PRIME stay untouched.
