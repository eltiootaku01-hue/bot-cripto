# BTC/ETH 4h RSI(14) Trend-State — ATR15, no-AI

**Preregistered issue:** [#188](https://github.com/eltiootaku01-hue/bot-cripto/issues/188)
**Status:** research only; `NO_TRADE` remains unless all frozen gates pass and a separate prospective shadow validation is approved.

## Frozen strategy rules
- Instruments: Binance Spot BTCUSDT and ETHUSDT. Long/cash only; no leverage, shorts, AI/ML, or real orders.
- Candle schedule: closed UTC 4h bars, 300 warm-up bars. Signals at completed close, virtual fills at the next 4h open. Reject gaps/no alignment; never interpolate.
- RSI: standard Wilder RSI(14) using only past and current completed closes. Entry threshold 50, exit threshold 40.
- Sample decisions every six completed 4h bars (daily cadence). Entry requires the previously sampled RSI <= 50 and current sampled RSI > 50, normal close > own EMA200, and positive trailing 42-bar return. If both assets qualify, choose the larger 42-bar return.
- Hold while RSI(14) > 40 and close > own EMA200; otherwise target cash at next open. A new entry requires a fresh cross above 50 at a later daily sample and the remaining filters.
- Position sizing at signal close: `min(50%, 15% / ((ATR14 / close) × sqrt(365 × 6)))`, frozen until target changes.
- Sell before buying when switching assets; charge costs for every fill; terminal liquidate every window.

## Costs/windows/benchmarks
Independent starting capital 60/100 USDT. Windows: COMBINED 2025-01-01 to 2026-10-22, 2025, and 2026 YTD to 2026-10-22. BASE: fee 10 bps/fill, assumed full spread 10 bps, slippage 5 bps/side. STRESS_1: 10/25/10 bps. STRESS_2: 20/50/20 bps. Benchmarks under identical periods/capital/costs: cash, BTC buy-and-hold, ETH buy-and-hold, and Issue #181's 15% ATR rotation.

## Frozen promotion gates (STRESS_2)
1. Net PnL > 0 in all three windows for both capitals.
2. Maximum drawdown >= -15% in all six window/capital combinations.
3. At least five round trips in COMBINED and 2026 YTD at both capitals.
4. Beat Issue #181 in COMBINED and 2026 YTD at both capitals.
5. Beat cash, BTC buy-and-hold and ETH buy-and-hold in 2026 YTD.
6. RSI formula/warm-up, causal suffix invariance, entry/exit thresholds, sizing cap, regression suite and output hashes pass.

Any failed gate means `REJECTED / NO_TRADE`; do not change the RSI thresholds, momentum window, EMA period or cadence after seeing the result. A historical PASS is not future-edge proof and only allows considering a prospective shadow test.

## Data limitations
Source completeness/point-in-time availability metadata may be UNKNOWN. Historical filter equivalence and executable historical quotes are UNKNOWN; spread/slippage are modeled assumptions, not reconstructed order-book fills. Preserve `main` and ETH_BBMR_168_EMA720 PRIME.