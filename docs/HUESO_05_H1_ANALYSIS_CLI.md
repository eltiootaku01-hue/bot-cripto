# HUESO 05-H1 — Binance Spot analysis CLI

## Purpose and boundary

This module is a read-only local consumer of public Binance Spot market data. It calculates SMA, EMA and RSI using the repository's canonical market-data, temporal-consumption and analysis contracts. It does not produce trading signals, financial proposals, reservations, orders or execution requests.

## Usage

\`\`\`bash
python -m bot_obrero.analysis_cli --symbol BTCUSDT --interval 1m
\`\`\`

Optional arguments:

- \`--candles\`: closed observations to analyze; default 100, maximum 999 (one extra response slot is reserved for a potentially open candle).
- \`--sma-period\`: default 20.
- \`--ema-period\`: default 20.
- \`--rsi-period\`: default 14.

All periods must be positive. The requested candle count must cover SMA/EMA periods and exceed the RSI period.

## Data admission

The CLI resolves an active Spot instrument through public ExchangeInfo and requests public klines only. It captures a UTC decision timestamp after acquisition and evaluates every selected MarketData item with \`REQUIRE_AVAILABLE\`. It never substitutes \`received_at\` or \`observed_at\` for missing \`available_at\`.

It requires valid quality, complete MarketData and candle payloads, closed candles, chronological order, unique starts, and a continuous interval sequence. A final open/partial/non-final candle is excluded with a warning. Internal gaps, duplicates, disorder, invalid quality, unknown availability, or insufficient observations stop the analysis.

SMA uses \`WindowSpecification.last_n(period)\`; EMA and RSI use \`WindowSpecification.all()\`. Each algorithm runs separately and its result reports its own observation IDs, identity/version, period, source and decision timestamp.

## Tests and CI

The tests use deterministic fixtures and do not contact Binance. The normal GitHub Actions workflow remains unchanged and runs the full \`pytest -q\` suite. Local pytest was not run in the network-restricted terminal; the authoritative remote result must be read from the workflow associated with the exact PR head SHA.

## Explicit non-goals

No credentials, private endpoints, exchange orders, StrategyRuntime, FinancialExecutionRuntime, TradeProposal, Risk, reservations, execution, reconciliation, virtual simulation, background polling or persistent state are introduced.
