# BTC_FALSE_BREAKDOWN_WICK_REJECTION_V1 — frozen protocol

- Preregistered Issue: https://github.com/eltiootaku01-hue/bot-cripto/issues/159
- Isolated branch: \`research/btc-false-breakdown-wick-rejection-v1\`
- Parent: current \`main\` at preregistration time. Main and \`ETH_BBMR_168_EMA720\` TOP1/PRIME remain unchanged.
- This candidate reuses the previously audited public-data and Decimal ledger core from the isolated BTC daily research; new signal code and tests are separate. The strategy is research-only; decision remains \`NO_TRADE\`.

## Frozen hypothesis

On BTCUSDT daily candles, enter long Spot only when all five conditions are met at completed close t:
1. close[t] > recursively computed EMA100[t], alpha = 2/(100+1), seeded from the first close; no signals before 100 completed bars.
2. low[t] < min(low[t-5:t]), excluding current candle from the prior-low reference.
3. close[t] >= open[t].
4. lower_wick = min(open[t], close[t]) - low[t] is at least 55% of high[t] - low[t].
5. (close[t]-low[t])/(high[t]-low[t]) >= 0.75.
The hypothesis is a false-breakdown rejection/reclaim setup and does not use volume. Entry happens at the next daily open, with a single position at most and 20% of available cash allocated. Signal generation uses only data at or before close t.

## Exits and execution

- After entry, take profit if a completed close is at least executed entry fill x 1.04.
- Stop condition if a completed close is at most executed entry fill x 0.975.
- Time exit after 7 completed holding daily candles.
- Exit fills use the next daily open; no intrabar stop assumption. Positions still open at a window boundary are sold at the final completed close with modeled costs.
- Decimal accounting, public LOT_SIZE step/minQty/minNotional, per-fill commissions, modeled spread/slippage, rejections and terminal liquidation reuse the shared research engine.

## Dataset contract

- Binance public Spot BTCUSDT daily klines from 2019-01-01 inclusive to 2026-10-22 exclusive in UTC.
- Require exactly every expected UTC daily open timestamp, correct close times, unique timestamps, positive and coherent OHLC, valid volume, and no gaps. Missing or malformed candles block all economic results; no interpolation or silent removal.
- Record raw-response hashes, canonical data SHA-256, current public exchangeInfo/filter hash, first/last timestamp and candle counts.
- Historical point-in-time availability, historical lot-filter equivalence, and historical executable order-book spread/slippage are UNKNOWN. Spread/slippage remain assumptions, not observed quotes. The historical archive has previously been inspected; it is not a pristine holdout.

## Capital, costs and evaluation windows

- Independent capital resets: 60 and 100 USDT.
- Costs per fill: BASE fee 10 bps, full spread 10 bps, slippage 5 bps/side; STRESS_1 fee 10 bps, full spread 25 bps, slippage 10 bps/side; STRESS_2 fee 20 bps, full spread 50 bps, slippage 20 bps/side. Apply half-spread plus side-specific slippage per fill.
- Windows: FULL_2020_2026 [2020-01-01, 2026-10-22), EARLIER_2020_2022 [2020-01-01, 2023-01-01), RECENT_2023_2026 [2023-01-01, 2026-10-22), each calendar year 2020–2025, and 2026_YTD [2026-01-01, 2026-10-22).
- Compare against cash and matched-cost BTC buy-and-hold using 95% starting allocation.

## Frozen gates

At STRESS_2, both capitals must satisfy every rule: positive PnL in FULL/EARLIER/RECENT/2026_YTD; at least five completed round trips in each of these four windows; maximum drawdown no worse than -15% in FULL/EARLIER/RECENT and every annual/YTD window; 2022 PnL >= 0; at least four positive windows among 2020–2025 plus 2026_YTD; and RECENT PnL greater than cash and matched-cost BTC buy-and-hold. Deterministic unit tests cover EMA math, false-breakdown/wick shape, trend/body filters, causality, next-open execution, exact ledger balance, quantity rules and terminal liquidation.

Any failed gate means FAIL/REJECTED/NO_TRADE. Do not modify the rule after observing performance. Workflow success certifies only that the research code ran. A historical pass still requires a separate preregistered prospective shadow test before operational consideration.
