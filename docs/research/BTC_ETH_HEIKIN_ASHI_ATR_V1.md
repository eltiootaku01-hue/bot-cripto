# BTC/ETH 4h Heikin-Ashi + EMA200 — ATR15 no-AI research candidate

**Preregistered issue:** [#186](https://github.com/eltiootaku01-hue/bot-cripto/issues/186)
**Status:** Research only; no production approval. Candidate must pass frozen gates and then a prospective shadow holdout.

## Frozen signal rules
- Instruments: Binance Spot BTCUSDT and ETHUSDT; long or cash only; no AI/ML, shorts, leverage, or real orders.
- Data: closed UTC 4h candles; calculate at candle close; execute virtual target changes at next 4h open.
- Heikin-Ashi close = (O+H+L+C)/4; first HA open=(O+C)/2; later HA open=(previous HA open+previous HA close)/2. Bullish state iff HA close > HA open.
- Rebalance every six completed 4h bars (daily cadence). For any open holding, remain invested while HA is bullish and normal close is above its own EMA200; otherwise target cash unless the other asset has a fresh entry signal.
- Entry signal: previous daily sample at i-6 was HA bearish and current completed candle is HA bullish; normal close above its own EMA200; positive 42-bar momentum. If both BTC and ETH qualify, choose the larger 42-bar momentum. If neither qualifies, cash.
- Size on the signal close using `min(50%, 15% / ((ATR14/close) × sqrt(365×6)))`; freeze that weight until the next target change.
- Sell before buying on a switch; apply fee, half-spread and slippage separately to every fill; terminal-liquidate each evaluation window. Do not interpolate or ignore candle gaps.

## Costs and windows
Independent initial capitals 60/100 USDT. Windows are COMBINED 2025-01-01 to 2026-10-22, calendar 2025, and 2026 YTD to 2026-10-22. BASE: fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side. STRESS_1: 10/25/10 bps. STRESS_2: 20/50/20 bps. Compare with cash, BTC/ETH buy-and-hold, and the #181 15% ATR rotation under identical conditions.

## Promotion gates (STRESS_2)
1. Positive net PnL in every window for both capital levels.
2. Max drawdown >= -15% in every window and capital.
3. At least five round trips in COMBINED and 2026 YTD.
4. Net PnL greater than Issue #181 in COMBINED and 2026 YTD at both capital levels.
5. Net PnL greater than cash, BTC buy-and-hold and ETH buy-and-hold in 2026 YTD.
6. Deterministic HA formulas, future-suffix causal invariance, sizing, cap, fill accounting and output-hash verification pass.

Any failed gate means `REJECTED / NO_TRADE`; never alter HA, EMA, cadence, or momentum lengths to rescue this historical sample. A historical PASS only permits consideration for prospective shadow testing.

## Data caveats
Completeness and point-in-time availability metadata may be UNKNOWN; historic exchange-filter equivalence and executable quotes remain UNKNOWN; spread/slippage are modeled assumptions. `main` and ETH_BBMR_168_EMA720 PRIME remain untouched.