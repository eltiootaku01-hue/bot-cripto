# BTC/ETH prospective public-data capture V1

**Implementation status:** capture code and regression tests are merged to `main`; strict JSON schema/sequence validation was hardened by PR #93. A successful real 24-hour capture has **not** yet been recorded or independently accepted.  
**Target dataset:** Binance Spot, public WebSocket Kline streams, `BTCUSDT` and `ETHUSDT`, 1-hour candles.  
**Safety boundary:** market data only; no API keys, private endpoints, orders, real money, or LIVE execution.

## Why this component exists

The existing `BinanceSpotWebSocketAdapter.listen` method is a bounded acquisition boundary. It was intentionally written without a durable streaming loop, reconnection policy, persistence, or 24-hour lifecycle manager. Binance's official Spot WebSocket documentation says one connection is valid for only 24 hours, describes the `serverShutdown` event, documents ping/pong requirements, and limits connection attempts and incoming control messages. A long-running capture must therefore record events incrementally and treat connection rotation, disconnects, missing candles, and persistence errors as evidence—not as success.

Sources checked on 2026-10-17:
- Binance Spot market-data streams: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
- CCXT manual, exception handling and rate limits: https://docs.ccxt.com/es/docs/manual
- Freqtrade lookahead analysis: https://docs.freqtrade.io/en/2026.5/lookahead-analysis/

These external reports establish known failure patterns; they do not establish that the same incident occurred in BOT-CRIPTO.

## Run on a persistent machine

This command is designed for a persistent terminal such as a local machine or a configured Codespace. A GitHub-hosted CI run only tests the code; it is not evidence that 24 hours of market capture occurred.

From the repository root:

```powershell
python -m bot_obrero.prospective_capture capture --output-dir evidence/prospective-btc-eth --symbols BTCUSDT ETHUSDT --interval 1h --duration-seconds 86400
```

By default, the collector waits for the next UTC midnight to start. It then captures for at least 24 elapsed hours and allows up to five additional minutes for the final hour to close and arrive. If the stream cannot demonstrate the acceptance criteria within that grace period, the run is `BLOCKED` and cannot be used to start the annual prospective holdout. To start immediately instead, pass `--no-align-next-utc-midnight`; an unaligned run may not contain a complete UTC day and can correctly end `BLOCKED`.

The default stream chunk is 1,000 messages. The existing adapter closes each connection after a bounded chunk; the capture runner rotates connections at those boundaries and uses capped exponential backoff after transport errors. A `serverShutdown` control event is treated as reconnectable transport, not as a Kline payload. Each event is handed to the journal and fsynced before the stream continues by default. A persistence or malformed-payload failure blocks the capture and stops both stream workers.

## Output and provenance

Each run creates a unique directory containing:
- `BTCUSDT.jsonl` and `ETHUSDT.jsonl`: one canonical `MarketData` envelope per received event; records include sequence number, run identity and a per-record SHA-256.
- `manifest.json`: UTC schedule/start/end, requested duration, effective configuration and hash, per-symbol counts, final-candle chronology, inter-event gaps, transport errors/reconnects, and SHA-256 hashes for each dataset file.

Intermediate updates to the same candle are retained; the stream can emit multiple updates per candle. A duplicate is flagged, not silently removed. Candle finality is taken from the official Binance Kline `k.x` flag as represented by the existing canonical adapter. `available_at` is required and must be based on an explicit consumer-handoff clock; it is never fabricated from event time or candle close.

By default, a gap greater than 10 seconds between accepted stream messages is counted as a continuity break. This threshold is deliberately strict because the Spot Kline stream normally updates more frequently than once per hour. Reconnects are retained in the manifest; reconnecting does not erase a data gap.

## Acceptance gate

A run is accepted only when every condition holds:

1. `manifest.json` has status `COMPLETED`.
2. Actual elapsed capture time is at least 86,400 seconds.
3. Both symbols contain at least one entire UTC day of 24 unique, aligned final hourly candles, from 00:00 through 23:00 UTC.
4. No missing hourly bars, duplicate final bars, out-of-order final bars, unaligned final bars, inter-event gaps above the configured limit, or local clock regressions are recorded.
5. No fatal parsing, availability, or persistence error occurred; every accepted record has a valid open/partial/non-final or closed/complete/final Kline state contract.
6. Every record's receive timestamp is within the declared capture interval, and event/receive/availability timestamps are ordered.
7. Capture start/end wall-clock duration agrees with the monotonic duration within the defined tolerance; a record cannot be outside the run window even if a manifest is edited.
8. File sizes, record counts/sequences, per-record hashes, configuration hash, and whole-file SHA-256 values pass independent validation.

Validate a run:

```powershell
python -m bot_obrero.prospective_capture validate evidence/prospective-btc-eth/<run-id>/manifest.json
```

The validator must print `status: VALID` and returns exit code zero only for an accepted manifest with unchanged dataset files. It independently recomputes the file/record hashes, sequence numbers, source identity, candle-state contract, receive timestamps versus the run start/end, daily coverage and continuity gates; it does not simply trust the manifest's `accepted` flag. A directory left `RUNNING` after a crash is not a completed run. Do not manually change its status to `COMPLETED`.

## What this does not prove

A passing capture verifies a new period of acquisition integrity and delivery timing. It does not establish that a strategy is profitable, that past historical candles were available in real time, or that a market signal should be executed. The annual BTC→ETH strategy must stay `NO_TRADE` until the frozen strategy, costs and benchmarks pass the independent evaluation protocol and any held-out validation. No annual evaluation T0 should be assigned before the capture manifest has been accepted and the dataset/configuration hashes have been recorded in the study evidence.

Large JSONL datasets should not be committed as source code or used to inflate code-completion percentages. Preserve them as immutable GitHub Actions artifacts, a versioned release asset, or another durable evidence store, and record their hashes and location in the project state.


## Strict schema typing (PR #93)

The validator requires `schema_version` to be the exact JSON integer `1` in the manifest, configuration and each JSONL event envelope. Each envelope `sequence` must be an exact integer starting at 1; JSON booleans such as `true` are rejected even though Python compares `True == 1`. This check is covered by regression tests. Capture-validator fix SHA from PR #93: `8c325d9899ae4a15b06240012b05473f2cd34824`.

CI evidence for that change: pre-merge suite [39100827937](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100827937) passed 1608 tests, and post-merge main suite [39100954733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954733) passed 1608 tests. A passing suite validates this software contract; it does not mean the real 24-hour capture has been completed.


## Current main continuity (18 October 2026)

The capture schema validation change from PR #93 remains in main. PR #96 separately hardens the PAPER economic replay's handling of malformed fill ordering; it does not change the capture collector or its acceptance criteria. Latest functional main SHA: `bb4cf093e4798a9ce80a770281a6003dfb4d2f9b`. Post-merge main suite [39101918101](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918101) passed 1610 tests; [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918074) and [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918092) passed. These tests still do not substitute for a real accepted 24-hour capture.


## Companion capture: prospective bookTicker evidence

The Kline collector above proves candle closure and hourly continuity. It does **not** by itself provide an executable quote for every decision. BTC/ETH V2 also needs point-in-time best bid/ask observations, with receive/availability timestamps and immutable hashes.

Main now includes:
- `bot_obrero/binance_book_ticker.py`: bounded public Spot `bookTicker` WebSocket adapter, exact approved-symbol guard, decimal and crossed-market validation, update ID validation, separate local receive/handoff clocks, and a hash-bound quote contract.
- `bot_obrero/prospective_book_ticker_capture.py`: JSONL per-symbol persistence, fsync, run manifest, reconnect backoff, SHA-256 file/record/configuration checks, and a validator that recomputes acceptance from stored records.
- The quote adapter streams directly into the durable sink without keeping a second full in-memory copy of results. Update-ID integrity uses constant-memory counters rather than a set growing with every quote. The journal flushes each JSONL line to the OS and fsyncs at least every 1,000 records or one second; manifest summaries update at most once per second except when an anomaly or batch threshold triggers an earlier update. A single connection is kept open for the run and is re-opened after a real transport failure or provider shutdown rather than after every small message batch.
- Regression tests in `tests/test_binance_book_ticker.py` and `tests/test_prospective_book_ticker_capture.py`. The latter uses synthetic quote events to validate the 24-hour persistence contract; it is not evidence of a real capture.

**Important timestamp limit:** Binance Spot's documented raw bookTicker payload includes update ID, symbol, bid/ask prices and quantities, but does not normally include an exchange event timestamp. The adapter therefore records local receive time as the observation clock when no provider event timestamp exists, and separately records local consumer availability. It never fabricates a past exchange timestamp. Downstream execution/replay must still reject a quote that is not available after the decision or exceeds the preregistered freshness limit.

### Run both public-data captures together

Start both commands in separate terminals **before the next UTC midnight** on a persistent machine. Both default to aligning the run to the next UTC midnight; use the same target start for the Kline and bookTicker smoke captures.

Terminal A — hourly klines:

```powershell
python -m bot_obrero.prospective_capture capture --output-dir evidence/prospective-btc-eth --symbols BTCUSDT ETHUSDT --interval 1h --duration-seconds 86400
python -m bot_obrero.prospective_capture validate evidence/prospective-btc-eth/<run-id>/manifest.json
```

Terminal B — best bid/ask quotes:

```powershell
python -m bot_obrero.prospective_book_ticker_capture capture --output-dir evidence/prospective-btc-eth-bookticker --symbols BTCUSDT ETHUSDT --duration-seconds 86400
python -m bot_obrero.prospective_book_ticker_capture validate evidence/prospective-btc-eth-bookticker/<run-id>/manifest.json
```

Replace `<run-id>` with the directory created by that run. Each validator must print `status: VALID`; for the quote capture, the manifest must also have `status: COMPLETED`, 24-hour elapsed duration, no timestamp regressions, no duplicate/out-of-order update IDs, no observed inter-event gap above policy, and receive coverage from the beginning to the end of the run. If either stream is blocked or invalid, the smoke test is not accepted and T0 must not be set.

Keep the two manifests, dataset SHA-256 values, configuration hashes and run identifiers as immutable evidence outside the source tree (for example, a versioned GitHub Actions artifact or release asset). Do not commit large raw JSONL datasets to `main`. A green synthetic test suite is necessary software evidence, not a substitute for the real overlapping capture.
