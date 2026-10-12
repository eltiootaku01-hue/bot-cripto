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
