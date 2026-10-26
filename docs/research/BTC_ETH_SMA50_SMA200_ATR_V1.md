# BTC/ETH 4h SMA50/SMA200 crossover — ATR15 no-AI research candidate

**Preregistered issue:** [#187](https://github.com/eltiootaku01-hue/bot-cripto/issues/187)
**Prior family evidence:** [Issue #140](https://github.com/eltiootaku01-hue/bot-cripto/issues/140) had positive BTC-daily YTD PnL but only one round trip, so it failed the preregistered minimum-sample gate. This experiment changes the timeframe/universe, not parameters selected from the new result.

## Frozen strategy rules
- Symbols: Binance Spot BTCUSDT and ETHUSDT; long/cash only. No AI/ML, shorts, leverage, or real orders.
- Bars: closed UTC 4h candles, 300 warm-up bars; evaluate every six completed 4h bars, signal at close, fill at next 4h open.
- Calculate normal-close SMA50 and SMA200. Bullish regime iff SMA50 > SMA200 and normal close > SMA200.
- Entry at a daily decision requires the bullish regime to have been false at the previous daily sample (i-6) and true now, plus positive trailing 42-bar return. If both BTC and ETH qualify, select the stronger 42-bar return.
- Hold while normal close remains above both SMA50 and SMA200; when either fails, target cash at the next open. Re-entry requires a new bearish-to-bullish regime transition at a later daily sample.
- Position sizing: `min(50%, 15% / ((ATR14 / close) × sqrt(365 × 6)))`; frozen from the signal close until target changes.
- Sell before buying in switches; apply fee/spread/slippage to each fill; terminal liquidate every independent window.

## Costs and evaluation windows
Capitals 60 and 100 USDT reset independently for COMBINED 2025-01-01 to 2026-10-22, 2025, and 2026 YTD to 2026-10-22. BASE: 10 bps fee/fill, 10 bps full spread, 5 bps slippage/side; STRESS_1: 10/25/10 bps; STRESS_2: 20/50/20 bps. Compare against cash, BTC buy-and-hold, ETH buy-and-hold, and Issue #181 under matching periods/capital/costs.

## Frozen promotion gates (STRESS_2)
1. Positive PnL across all windows and both capitals.
2. Max drawdown no worse than -15% for every window/capital.
3. At least five complete round trips in COMBINED and 2026 YTD.
4. Beat #181 in COMBINED and 2026 YTD for both capital levels.
5. Beat cash, BTC buy-and-hold and ETH buy-and-hold in 2026 YTD.
6. SMA calculation, causal-prefix, size/cap, fill accounting and output hash checks pass.

Any failed gate means `REJECTED / NO_TRADE`; no post-hoc SMA/cadence/lookback changes on this sample. Historical PASS only qualifies for consideration in prospective shadow validation.

Data completeness and point-in-time availability may be UNKNOWN; historical executable quotes and historic filter equivalence are UNKNOWN; spread/slippage remain modeled. Keep `main` and ETH_BBMR_168_EMA720 PRIME untouched.