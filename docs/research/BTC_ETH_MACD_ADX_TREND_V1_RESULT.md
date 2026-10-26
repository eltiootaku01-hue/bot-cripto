# BTC/ETH MACD + Wilder ADX trend continuation V1 — result

**Final decision: `FAIL_NO_TRADE` / `REJECTED`.** Deterministic rule, no AI or machine learning.

## Reproducibility
- Preregistration: https://github.com/eltiootaku01-hue/bot-cripto/issues/182
- Research branch: `research/btc-eth-macd-adx-trend-v1`
- Script: `research/btc_eth_macd_adx_trend_v1.py`
- Deterministic tests: `research/test_btc_eth_macd_adx_trend_v1.py`
- Workflow: `.github/workflows/btc-eth-macd-adx-trend-v1.yml`
- Final tested HEAD: `26e465741606c1abb6bcc825c707a69fda3da485`
- Replay: https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40370962464 (completed / success)
- General suite: https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40370962308 (completed / success, same HEAD)
- Artifact: https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/40370962464/artifacts/12671554221
- ZIP artifact digest: `4c29da6d34e7ffe77a145273ae7925f647fc340bb7a24f07703e3db106cf4eaa`, size 230,673 bytes.
- Source bundle SHA-256: `779e3630a8156f8b9386465d4449112cea6b585124edb2be97e6b16b8d90cc9e`.
- Strategy configuration SHA-256: `01a37c6d2e98eda1928f7bb6f789efb97e24a3b69c0cd5ca8ebc0bbb61230823`.
- Indicators/signals SHA-256: `f5aaddd1361bd8a56c1df587f07c9d8782893226d72731531afd4df4cb4c3c8e`.

## Rule tested
- BTCUSDT and ETHUSDT Binance Spot, completed UTC 4h bars; long/cash only, no leverage or live orders.
- Entry after a completed-bar bullish MACD(12,26) cross above its EMA9 signal, MACD above zero, close above EMA200, EMA50 above EMA200, close above EMA50, and Wilder ADX14 >= 20.
- If both assets qualify, choose larger MACD histogram divided by simple ATR14; alphabetical tie-break.
- Exit on a completed-bar bearish MACD cross or close <= EMA50; exit at next 4h open. One position at a time.
- Sizing target `min(50%, 15% / ((ATR14/close) × sqrt(365×6)))`, fixed at signal close until exit. Costs and filters matched to the saved #181 candidate.

## Integrity
- Exactly 4,254 bars for each asset; candle continuity and exchange record validations passed.
- Canonical BTC OHLCV SHA-256: `a508cb02259c15ec67e6df8c1c8be406b1d5f64350cae17a33c463e728793d9d`.
- Canonical ETH OHLCV SHA-256: `d75f1b78f0c11ba889c81458801abf0d14758329819c55d6eda6efb0151a8510`.
- Both canonical candle hashes match the saved #181 data exactly; the fetched record-level hashes differ because capture timestamps are regenerated.
- The four targeted tests passed: future-suffix invariance, indicator warmup and ADX range, conservative modeled costs/lot rounding, and virtual ledger invariants.
- Workflow independently verified all 26 gate records and SHA-256 hashes for each report artifact.

## STRESS_2 results

| Window | Capital | Net PnL | Return | Max drawdown | Round trips | Fees |
|---|---:|---:|---:|---:|---:|---:|
| Combined 2025–2026 YTD | 60 USDT | −5.8436 USDT | −9.74% | −10.64% | 27 | 1.8051 USDT |
| Combined 2025–2026 YTD | 100 USDT | −9.8612 USDT | −9.86% | −10.76% | 27 | 3.0384 USDT |
| 2025 | 60 USDT | −2.5319 USDT | −4.22% | −4.88% | 16 | 1.1370 USDT |
| 2025 | 100 USDT | −4.2798 USDT | −4.28% | −4.95% | 16 | 1.9161 USDT |
| 2026 YTD through 2026-10-21 | 60 USDT | −3.4852 USDT | −5.81% | −6.09% | 11 | 0.7037 USDT |
| 2026 YTD through 2026-10-21 | 100 USDT | −5.8403 USDT | −5.84% | −6.12% | 11 | 1.1774 USDT |

## Comparison with saved ATR15 candidate (#181)
- Combined: −5.8436 / −9.8612 USDT versus +9.9913 / +16.7947 USDT for 60 / 100 USDT.
- 2026 YTD: −3.4852 / −5.8403 USDT versus +3.7692 / +6.2324 USDT.
- The new strategy did not beat cash or BTC buy-and-hold in 2026 YTD; it beat ETH buy-and-hold only because ETH itself performed worse.

## Frozen gate outcome
- PASS: 12 / 26.
- FAIL: 14 / 26.
- PnL positivity failed across all three windows and both capital levels; it also failed the comparison against #181 in combined and YTD, cash, and BTC buy-and-hold for YTD.
- Maximum drawdown remained better than the -15% ceiling, and the sample had enough completed rotations. Those passes do not offset the failed profitability gates.

## Disposition
Reject this exact MACD/ADX rule. Do not rescue it with post-hoc changes to MACD periods, ADX threshold, or exit rules on these same dates. Preserve the artifact as a negative result to avoid repeating the same failed experiment.

The 15% ATR sizing version in Issue #181 remains the best historical candidate found here, but it is still only a retrospective shadow candidate, not proven future edge. `NO_TRADE` operationally remains in force. `main` and `ETH_BBMR_168_EMA720` PRIME were not modified.