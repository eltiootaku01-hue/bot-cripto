# BTC/ETH 4h Wilder DMI/ADX14 + EMA200 — ATR15 no-AI

**Preregistered issue:** [#189](https://github.com/eltiootaku01-hue/bot-cripto/issues/189)
**Status:** research only; no production approval. `NO_TRADE` remains unless the frozen gates pass and a prospective shadow holdout is separately approved.

## Frozen rules
- Instruments: Binance Spot BTCUSDT and ETHUSDT; long/cash only. No AI/ML, short, leverage, or real orders.
- Closed UTC 4h candles, 300 warm-up bars, decisions once every six completed bars. Signals at the completed close and virtual fills at the next open. Reject gaps and unaligned timestamps, no interpolation.
- Wilder DMI/ADX(14): TR, +DM and -DM standard comparisons; Wilder-smoothed directional movement and true range; +DI/-DI; DX; ADX with 14 valid DX warmup values. No future rows are used.
- Entry at a daily sample when +DI crosses above -DI from the prior daily sample, ADX >= 20, close > own EMA200, and 42-bar return > 0. If both assets qualify, choose the larger 42-bar return.
- Hold while +DI > -DI, ADX >= 15, and close > EMA200. Otherwise target cash. A new position requires a fresh +DI crossover and all entry filters.
- Sizing at signal close: `min(50%, 15% / ((ATR14/close) × sqrt(365 × 6)))`, frozen until target changes. Sell before buy during rotations; each window is terminal-liquidated.

## Costs and matching windows
Independent capital 60/100 USDT. Windows: COMBINED 2025-01-01 to 2026-10-22, 2025, and 2026 YTD to 2026-10-22. BASE 10 bps fee/fill, 10 bps full spread, 5 bps slippage/side; STRESS_1 10/25/10 bps; STRESS_2 20/50/20 bps. Benchmarks: cash, BTC buy-and-hold, ETH buy-and-hold, and the Issue #181 15% ATR rotation under matching cost assumptions.

## Promotion gates (STRESS_2)
1. Positive net PnL for both capitals in all windows.
2. Max drawdown >= -15% in every window/capital.
3. At least five round trips in COMBINED and 2026 YTD at both capital levels.
4. Beat Issue #181 in COMBINED and 2026 YTD at both capital levels.
5. Beat cash, BTC buy-and-hold and ETH buy-and-hold in 2026 YTD.
6. Deterministic Wilder warmup, causal-prefix, DMI cross/hold, position-sizing, and output hash tests pass.

Any failed gate means `REJECTED / NO_TRADE`; no parameter rescue on the same dates. Historical PASS can only qualify for prospective shadow consideration, not PAPER/LIVE.

## Caveats
Source completeness and point-in-time availability can remain UNKNOWN. Historical executable quotes and filter equivalence remain UNKNOWN; spread/slippage are modeled assumptions. Keep `main` and ETH_BBMR_168_EMA720 PRIME intact.