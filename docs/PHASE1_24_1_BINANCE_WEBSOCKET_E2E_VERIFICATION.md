# FASE 1.24.1 — Real Binance Spot WebSocket E2E Verification

## Purpose

This phase is an online verification only. It proves that the production
FASE 1.24 WebSocket adapter can connect to Binance Spot, receive a real Kline
event, normalize it into canonical `MarketData`, and pass that object through
the existing Phase 1.23 consumption boundary.

It does not add a new WebSocket implementation.

## Workflow

Manual GitHub Actions workflow:

```text
.github/workflows/phase1_24_1_binance_websocket_e2e.yml
```

Trigger:

```yaml
on:
  workflow_dispatch:
```

The workflow is never triggered automatically by normal pushes.

Python version:

```text
3.12
```

Installation:

```text
python -m pip install -e ".[test]"
```

Job timeout:

```text
5 minutes
```

## E2E script

```text
scripts/phase1_24_1_binance_websocket_e2e.py
```

The script uses the production:

- `BinanceSpotWebSocketAdapter`;
- `build_binance_spot_kline_stream_url`;
- `BinanceSpotInstrumentMetadata`;
- `MarketData`;
- `evaluate_market_data_consumption(...)`.

The script does not duplicate Kline parsing or implement another WebSocket
client. Its recording wrapper delegates transport creation and reception to
the production module's WebSocket factory so the received raw message can be
correlated directly with the production adapter output.

## Real endpoint

The adapter must generate:

```text
wss://stream.binance.com:9443/ws/btcusdt@kline_1m
```

The script verifies the URL passed to its injected factory is exactly the
production-generated URL.

## Traffic bound

The E2E uses:

```text
1 connection
1 symbol
1 interval
maximum 3 messages
```

Symbol:

```text
BTCUSDT
```

Interval:

```text
1m
```

No combined stream, dynamic subscription, second symbol, or permanent stream
is used.

The adapter receives at most three messages and then closes the connection.

## Global timeout

The script applies a strict 60-second POSIX wall-clock alarm.

The production adapter uses a 10-second receive/connect timeout for this E2E.

A timeout or connection failure exits non-zero and cannot be interpreted as
success.

## Real metadata resolution

The E2E creates `BinanceSpotInstrumentMetadata` without an injected HTTP fake.
The requested symbol therefore resolves against real Binance ExchangeInfo
metadata before the WebSocket connection is opened.

Expected canonical identity:

```text
instrument_id=binance:SPOT:BTCUSDT
symbol=BTC/USDT
market=SPOT
```

## Real Kline evidence

For each accepted real message, the script verifies:

- event type is `kline`;
- the recorded raw event field `E` equals `MarketData.observed_at`;
- `received_at`, candle start, candle end, and observed time are timezone-aware UTC;
- timeframe is `1m`;
- OHLCV values are `Decimal`;
- candle identity is `(instrument_id, timeframe, candle.start)`;
- `x=false` produces `OPEN/PARTIAL`;
- `x=true` produces `CLOSED/COMPLETE`;
- finality remains `UNKNOWN`;
- market-data completeness remains `UNKNOWN`;
- `source_sequence` is not fabricated;
- `available_at` remains `None` because no consumer handoff clock is supplied.

The E2E does not require the first live update to be closed.

## Consumption

The first real `MarketData` is evaluated through:

```text
evaluate_market_data_consumption(...)
```

with:

```text
policy=REQUIRE_AVAILABLE
decision_timestamp=UTC timestamp at or after receipt
```

Because `available_at` remains unknown, the expected result is:

```text
CONSUMPTION_STATUS=UNKNOWN
```

No provider-specific logic is introduced into the consumption contract.

## Multiple updates

The script permits up to three real updates. When more than one message is
returned, the output exposes all accepted messages through the message count
and validates each message independently.

It does not require a particular OPEN/OPEN/CLOSED sequence. Binance may emit
any valid state during the short verification window.

Repeated updates of the same candle remain live updates and are not treated
as historical duplicates.

## Output

A successful run prints evidence similar to:

```text
FASE 1.24.1 — REAL BINANCE SPOT WEBSOCKET E2E

endpoint=wss://stream.binance.com:9443/ws/btcusdt@kline_1m
symbol=BTCUSDT
interval=1m

CONNECTED=YES
MESSAGE_COUNT=...
EVENT_TYPE=kline
EVENT_TIME=...
RAW_E=...
RECEIVED_AT=...

INSTRUMENT_ID=binance:SPOT:BTCUSDT
SYMBOL=BTC/USDT
MARKET=SPOT
SOURCE_ID=binance-spot-websocket

CANDLE_START=...
CANDLE_END=...
CANDLE_STATE=...
CANDLE_COMPLETENESS=...
FINALITY=UNKNOWN

AVAILABLE_AT=None

MARKET_DATA_ID=...
CANDLE_IDENTITY=...

CONSUMPTION_STATUS=UNKNOWN

RESULT: REAL E2E PASS
```

## Normal CI

The normal workflow remains:

```text
.github/workflows/tests.yml
```

It is not modified by this phase and remains offline with respect to Binance.

The normal suite must be run again after this phase's files are added.

## Result recording

Final GitHub Actions evidence must be recorded here:

```text
Workflow: phase1.24.1-binance-websocket-e2e
Run ID: __________________
Commit SHA: __________________
Job: real-binance-websocket-e2e
Conclusion: __________________
```

Normal CI evidence:

```text
Workflow: tests
Run ID: __________________
Commit SHA: __________________
Tests: __________________
Conclusion: __________________
```

## Deliberate limitations

This phase demonstrates only:

```text
real connectivity
+
real Kline receipt
+
production parsing
+
metadata-backed identity
+
canonical MarketData creation
+
consumption UNKNOWN under missing availability evidence
```

It does not demonstrate:

- hour-scale stability;
- reconnect;
- retry;
- backoff;
- heartbeat management;
- failover;
- persistence;
- multiple permanent streams;
- resilience after disconnection;
- strategy, indicators, risk, backtesting, execution, or trading.

Those guarantees require separate phases.
