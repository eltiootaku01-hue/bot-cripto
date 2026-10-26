# BTC/ETH Rotation — ATR target 15% (candidate record)

**Status:** HISTORICAL PASS / SHADOW-CANDIDATE ONLY / `NO_TRADE` outside shadow.

## Source of truth
- Candidate issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/181
- Branch: `research/btc-eth-rotation-15pct-atr-sensitivity`
- Frozen replay script: `research/replay_btc_eth_rotation_15pct_atr_sensitivity.py`
- Deterministic sizing tests: `tests/test_btc_eth_rotation_15pct_atr_sensitivity.py`
- Workflow: `.github/workflows/btc-eth-rotation-15pct-atr-sensitivity.yml`
- Replay: https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647 (completed / success)
- General regression suite: https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916354 (completed / success)
- Evidence artifact: https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40356916647/artifacts/12667605121
- Tested HEAD: `aa7874a8c4b2dd7c3bd0708fa6e2a6fe91efdc73`
- ZIP digest: `2dc23cad1334c5eeaa97a40c8715f57487fe005cfd4510aa838190c4cd53bee0` (1,304,842 bytes)

## Frozen trading rule (no AI/ML)
1. Trade Binance Spot BTCUSDT or ETHUSDT; long or cash only, no shorts, leverage, or live orders.
2. Use closed 4-hour candles. Every 42 completed evaluation bars, calculate each asset's trailing 180-bar return.
3. An asset is eligible only when its trailing 180-bar return is positive and its close is above its own EMA200. Pick the eligible asset with the stronger return; otherwise hold cash.
4. Place virtual rebalance at the next 4-hour open. Close any previous holding before opening the new target. Liquidate at the end of each independent evaluation window.
5. At signal close, compute ATR14 as the simple rolling mean of true range, then set the target quote weight to `min(50%, 15% / ((ATR14 / close) * sqrt(365 * 6)))`. Freeze that weight until exit. The only experimental difference from #173's previously tested variant is the target changed from 10% to 15%.
6. Model matched execution costs: BASE fee 10 bps/fill, full spread 10 bps, slippage 5 bps/side; STRESS_1 fee 10 bps, spread 25 bps, slippage 10 bps/side; STRESS_2 fee 20 bps, spread 50 bps, slippage 20 bps/side.

## STRESS_2 historical results

| Window | Starting capital | Net PnL | Return | Max drawdown | Completed rotations | Fees |
|---|---:|---:|---:|---:|---:|---:|
| Combined 2025–2026 YTD | 60 USDT | +9.9913 USDT | +16.65% | −9.42% | 11 | 0.6478 USDT |
| Combined 2025–2026 YTD | 100 USDT | +16.7947 USDT | +16.79% | −9.46% | 11 | 1.0877 USDT |
| 2025 | 60 USDT | +7.3814 USDT | +12.30% | −6.09% | 6 | 0.3515 USDT |
| 2025 | 100 USDT | +12.4061 USDT | +12.41% | −6.14% | 6 | 0.5919 USDT |
| 2026 YTD through 2026-10-21 | 60 USDT | +3.7692 USDT | +6.28% | −3.45% | 5 | 0.2837 USDT |
| 2026 YTD through 2026-10-21 | 100 USDT | +6.2324 USDT | +6.23% | −3.43% | 5 | 0.4741 USDT |

All five preregistered historical gates passed: positive net PnL for each window/capital, drawdown no worse than -15%, at least five rotations in combined/YTD, higher net PnL than the 10% sizing version for combined/YTD, and YTD above matched cash/BTC/ETH buy-and-hold. This is a historical sensitivity selected after reviewing the same period, not an independent holdout.

## Data provenance and caveats
- 4,254 BTC and 4,254 ETH 4-hour bars; exact OHLCV candles match the verified 10% sizing artifact candle-for-candle.
- Full record hashes include a capture-time field and differ between runs. Canonical OHLCV hashes: BTC `4365a38250fa4c267e32c8b8512f0039d792c888f9c0c9d2678ed1e2906a5c82`; ETH `c026290290a7fa21a114c96c81002a21b70f6779ff5262e8a0494d36c6000d07`.
- Full source record hashes: BTC `b5e3f7e490af598f44885c5b24983c16d6141a3d7c3cfe6f469ff95712eceb66`; ETH `4fc086bbb600b4a51ee2881c5f54cc4121de583b83797e00207a2e63122dc392`; combined `ab3d502ee849274926fd1b3fbb4a2e04f51c38b66249c161bb4f8584e2964a9e`; risk-weight series `531c286416450f86686cb60f4a40d50c7e981ecd244d0c8e9c3211b7397e81b7`.
- Data-completeness metadata is UNKNOWN on all bars. Historical point-in-time availability and historical executable quotes are UNKNOWN; current filter snapshots do not prove historical filter equivalence.
- Costs are modeled assumptions, not reconstructed historical book/ticker fills. Results are before any taxes, conversion fees, withdrawal costs, or personal operational overhead.
- This candidate's sizing target was selected after retrospective inspection, so the report is exploratory. It must not be described as a robust or proven future edge.

## Disposition
`SHADOW CANDIDATE ONLY`. No further increase in ATR target on these same dates. A next-stage test must freeze this exact rule and use a prospective timestamped shadow ledger with no orders. `NO_TRADE` operationally remains in force. `main` and `ETH_BBMR_168_EMA720` PRIME stay untouched.