[object Object]

---

# PRODUCT V2 — BTC, ALTCOIN SCANNER, ARS BUDGET AND SELECTIVE BACKTEST

Status at this documentation update: **IMPLEMENTATION IN PR #63; VERIFY LIVE BRANCH SHA AND WORKFLOWS DIRECTLY BEFORE CLAIMING TEST SUCCESS**.

- PR: https://github.com/eltiootaku01-hue/bot-cripto/pull/63
- Working branch: `feat/hueso-05-h1-analysis-cli`
- Entry point added: `python -m bot_obrero.product_v2`
- Existing virtual backtest extended for 1,200 closed candles, modeled full spread, and a maximum of five BUY entries in any rolling 30-day window.
- Product V2 research defaults to 180 days of 4-hour bars, uses public ExchangeInfo/ticker/bookTicker plus the existing metadata-backed historical adapter, and reports BTC regime, liquidity/spread screening, 30/90-day correlation, 90-day beta, relative strength, instrument filters, full-period backtest and a separate fixed-parameter 60-day slice.
- The scanner's normalized research ledger is 1,000 USDT and is explicitly separate from the owner's 10,000 ARS budget. It does **not** assert affordability: the final product decision is `NO_TRADE` while the ARS→USDT budget gate is `CONVERSION_UNKNOWN` because a representative executable route, timestamp and conversion costs are not verified.
- Historical spread is not present in kline history; the virtual backtest uses an explicitly labeled 10 bps full-spread assumption plus 5 bps slippage per fill and 0.1% fees per fill. This is a model scenario, not a measured historical spread or account-specific Binance fee.
- Public E2E workflow: `.github/workflows/virtual-backtest-real-e2e.yml`; its result and artifacts must be checked for the exact PR HEAD. Local pytest remains unrun in the previously DNS-blocked terminal; remote CI must not be described as a local run.
- No private API keys, private endpoints, real orders, StrategyRuntime, FinancialExecutionRuntime, leverage, or changes to the financial execution boundary are part of this product path.
- The scanner/backtest are research tools, not a validated profitable strategy. Walk-forward/OOS reporting, costs and data-quality caveats must be evaluated; no result should be described as ready for real-money use.


---

# UNIVERSE-RECOVERY-R1 — BTC_ALT_CROSS_ASSET_RESEARCH_V1

- **Checkpoint date:** 2026-10-16.
- **Main SHA observed before this documentation branch:** `b9e34f2d836b99f711d6227f88c95c7b5ba7bd85`.
- **Report:** [`docs/audits/BTC_ALT_CROSS_ASSET_RESEARCH_V1_UNIVERSE_RECOVERY_R1.md`](../docs/audits/BTC_ALT_CROSS_ASSET_RESEARCH_V1_UNIVERSE_RECOVERY_R1.md).
- **Latest report revision commit on documentation branch:** `0fb6dbfdabf17f73959f64f3cc3ae21ebc44a9f7` (branch: `docs/btc-alt-cross-asset-universe-recovery-r1`; review in PR #66).
- **R5 branch / commit:** `research/product-v2-r5-independent-strategies` / `dae415947a5d45340e4af96644328a4ba95cb9bd`.
- **R5 artifact:** `11812924702`, workflow run `38399941811`; downloaded ZIP SHA-256 matched GitHub's published digest `035624ca0a86b1ab1863f85660fe37a8c103d6a39a1f180d88422bd20d9b293f`.
- **Finding:** the verified R5 artifact describes `XRPUSDT`, `STRKUSDT`, `WUSDT`, `SUIUSDT`, `ENAUSDT` as an `EXPLORATORY_ONLY` panel and `BTCUSDT` as benchmark. It does not identify or approve the remaining fourteen altcoins.
- **Acceptance review:** `AC-03A` (recover original list) = `NOT MET / UNKNOWN`; `AC-03B` (document negative finding and search limits accurately) = `PASS`; `AC-07` (report and continuity checkpoint published in reviewable PR) = `PASS`. These statuses refer only to documentary acceptance and do not approve the universe.
- **Automatic CI observed for PR HEAD `74ef1cd293120af2bc3eac83138756cbccb17350` (event `pull_request`):** `tests` run `38939511004` = `completed/success`; `phase1.31-sma-real-e2e` run `38939510983` = `completed/success`; `phase1.34-indicators-real-e2e` run `38939510952` = `completed/success`.
- **Automatic CI observed for the subsequent PR HEAD `e459525fa8e71fb8d369db366b1c6ae9b7c1e288` (event `pull_request`):** `tests` run `38941042561` = `completed/success`; `phase1.31-sma-real-e2e` run `38941042604` = `completed/success`; `phase1.34-indicators-real-e2e` run `38941042627` = `completed/success`. See [tests](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38941042561), [SMA E2E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38941042604) and [indicators E2E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38941042627). BOT OBRERO did not start, rerun or cancel these workflows. They apply only to the listed SHAs and do not validate the canonical universe or economic performance.
- **Post-checkpoint note:** publishing this continuity update may trigger another automatic `pull_request` workflow batch for the next HEAD. Verify that SHA and its run states separately; do not assume the results above apply to a newer SHA or start workflows manually.
- **State:** `PARTIAL`; `BLOCKED_UNIVERSE_DEFINITION` remains. The canonical twenty-instrument universe is unconfirmed.
- **Next step:** CEREBRO/owner must locate primary evidence of the exact original list and its approval, or explicitly authorize a new universe without representing it as historical.
- **Guardrails:** no symbols inferred; no backtests/simulations initiated manually by BOT OBRERO; no functional changes, no workflow edits, no financial operations, no PR merge. Record automated CI separately; publication is not universe approval.
