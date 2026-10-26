# BTC/ETH 4h rotation — ATR target 15% (shadow candidate)

**Disposition:** historical candidate only; do not enable operational trading. NO_TRADE remains outside a separately authorized shadow test.

## Purpose

Freeze the most promising non-ML configuration tested so far. This is a deterministic strategy: no AI, no model inference, no leverage, no shorts, and no real orders.

## Exact signal rule

- Universe: Binance Spot BTCUSDT and ETHUSDT, 4-hour UTC candles.
- Every 42 completed 4-hour evaluation bars, compute trailing 180-bar close-to-close momentum for each asset.
- An asset is eligible only when its momentum is positive and its close is above its own EMA200.
- Select the eligible asset with the larger momentum; if neither qualifies, target cash.
- Execute portfolio changes at the next 4-hour open, after the signal candle has closed.
- Freeze the chosen position weight at signal time:
  weight = min(50%, 15% / ((ATR14 / close) × sqrt(365 × 6))).
- ATR14 is the simple mean of the last 14 true ranges. Weight does not change until the next rebalance/exit.
- Spot only, long or cash. No leverage, no short, no AI/ML, no live order execution.
- At each independent test window end, liquidate the position using the backtest terminal accounting rule.

## Historical evidence

Source: [Issue #181](https://github.com/eltiootaku01-hue/bot-cripto/issues/181).

- Replay workflow: [#40356916647](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647), terminal completed / success.
- General test suite: [#40356916354](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916354), terminal completed / success, same HEAD aa7874a8c4b2dd7c3bd0708fa6e2a6fe91efdc73.
- Evidence artifact: [ZIP from GitHub Actions](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647/artifacts/12667605121).
- Artifact size: 1,304,842 bytes; GitHub ZIP digest SHA-256: 2dc23cad1334c5eeaa97a40c8715f57487fe005cfd4510aa838190c4cd53bee0.

### STRESS_2 results (fees and modeled spread/slippage deducted)

| Window | Initial USDT | Net PnL USDT | Return | Max drawdown | Closed rotations | Fees USDT |
|---|---:|---:|---:|---:|---:|---:|
| Combined 2025–2026 YTD (through 2026-10-21) | 60 | +9.9913 | +16.65% | -9.42% | 11 | 0.6478 |
| Combined 2025–2026 YTD (through 2026-10-21) | 100 | +16.7947 | +16.79% | -9.46% | 11 | 1.0877 |
| 2025 | 60 | +7.3814 | +12.30% | -6.09% | 6 | 0.3515 |
| 2025 | 100 | +12.4061 | +12.41% | -6.14% | 6 | 0.5919 |
| 2026 YTD (through 2026-10-21) | 60 | +3.7692 | +6.28% | -3.45% | 5 | 0.2837 |
| 2026 YTD (through 2026-10-21) | 100 | +6.2324 | +6.23% | -3.43% | 5 | 0.4741 |

Cost assumptions:
- BASE: fee 10 bps/fill; assumed full spread 10 bps; slippage 5 bps/side.
- STRESS_1: fee 10 bps/fill; assumed full spread 25 bps; slippage 10 bps/side.
- STRESS_2: fee 20 bps/fill; assumed full spread 50 bps; slippage 20 bps/side.

### Relative to the 10% ATR candidate

On the identical canonical OHLCV candles, the 15% target increased historical STRESS_2 PnL:
- Combined: +3.6321 USDT (capital 60), +5.7098 USDT (capital 100).
- 2026 YTD: +1.2517 USDT (capital 60), +2.0270 USDT (capital 100).

Maximum combined drawdown increased from around 6.5% to around 9.5%. This is more return at greater risk—not a free improvement.

## Data and execution limitations

- BTC and ETH each contain 4,254 4-hour bars. Canonical candle OHLCV matches the 10% candidate exactly.
- Completeness metadata is UNKNOWN for all 4,254 candles per asset. The stored record finality flag is not an exchange guarantee of historical point-in-time availability.
- Historical symbol filters and executable historical quotes remain UNKNOWN. Fees, spread and slippage are modeled, not reconstructed from an old order book.
- Only 11 closed rotations appear in the combined sample; that is a limited sample.
- The 15% target was selected after inspecting the same historical period, so this is not a clean out-of-sample result or proof of a future edge.

## Required next step

Freeze these rules and use a prospective shadow run with timestamped, hash-bound data, identical cost assumptions and precommitted stop conditions. Do not increase the ATR target again on these same dates. A historical PASS only qualifies it for consideration in shadow; it does not authorize PAPER/LIVE.

## Repository safety

This document belongs on the research branch only. Do not merge it to main or alter ETH_BBMR_168_EMA720 PRIME. Main was observed at c523c47364b6fb4febd9c48385bc63bcde87ea61, with branch protection disabled (protected: false) at last verification.