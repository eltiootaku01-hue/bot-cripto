# FASE 1.24 — Binance Spot WebSocket Kline Acquisition Boundary v1.0

## Purpose

FASE 1.24 adds the first bounded production boundary for Binance Spot
WebSocket Kline/Candlestick data.

The boundary is intentionally one-shot and testable:

```text
Binance raw WebSocket
        ↓
transport
        ↓
Kline protocol parser
        ↓
metadata-backed instrument resolution
        ↓
ProviderRecord / normalization
        ↓
explicit availability evidence
        ↓
canonical MarketData
        ↓
Phase 1.23 consumption boundary
```

It does not create a resident worker or a permanent WebSocket bot.

## Endpoint

Only Binance's raw Spot WebSocket stream is used:

```text
wss://stream.binance.com:9443/ws/{symbol}@kline_{interval}
```

The stream symbol is lowercase in the URL. Combined streams and dynamic
SUBSCRIBE messages are not implemented.

Example:

```text
BTCUSDT + 1m
→ wss://stream.binance.com:9443/ws/btcusdt@kline_1m
```

The requested interval is validated through the existing
`validate_binance_spot_interval` contract. No second interval list exists.

## Dependency

The project declares:

```text
websocket-client==1.9.2
```

The normal test workflow remains offline with respect to Binance.

## Transport boundary

The adapter injects a small `WebSocketConnection` / `WebSocketFactory`
surface. Tests use a fake connection; CI does not connect to Binance.

The default factory uses `websocket-client`.

The public operation is bounded:

```python
listen(
    symbol="BTCUSDT",
    interval="1m",
    max_messages=1,
)
```

`max_messages` is a deterministic hard upper bound. The adapter does not
start a background worker and does not contain an unbounded `while True`
consumer loop.

The connection timeout is explicit. Timeout, connection-close, and other
transport failures are classified as `BinanceWebSocketTransportError`.
They are not converted into MarketData.

## Kline payload

The parser requires the raw Kline event envelope fields:

```text
e, E, s, k
```

and the Kline fields:

```text
t, T, s, i, o, c, h, l, v, n, x
```

The event must be `e == "kline"`. The event symbol and nested Kline symbol must
match the requested provider symbol exactly.

The payload interval is validated with the existing Binance Spot interval
contract and must exactly match the requested interval.

Malformed JSON/envelopes are protocol errors. Malformed Kline fields or
canonicalization failures are payload errors.

## Instrument identity

The adapter resolves the requested provider symbol through the existing
Binance Spot ExchangeInfo metadata boundary. Active metadata is required.

Canonical symbol identity is therefore derived from explicit Binance
`baseAsset` / `quoteAsset` metadata rather than by splitting a provider
symbol such as `BTCUSDT`.

The WebSocket source is deliberately distinct from REST:

```text
provider = binance
venue = BINANCE
market = SPOT
source_id = binance-spot-websocket
```

## Time semantics

### Event time

Binance `E` is the WebSocket event time.

For WebSocket Kline data:

```text
observed_at = E
```

converted from milliseconds to timezone-aware UTC.

The candle start remains:

```text
payload.start = k.t
```

and the candle end remains:

```text
payload.end = k.T
```

The adapter never substitutes candle start for `observed_at`.

### Received time

`received_at` is obtained from the adapter's injected local clock
immediately after `recv()` returns.

It is never derived from `E`, `k.t`, or `k.T`.

### Availability

Availability is conservative.

Without an explicitly injected `consumer_handoff_clock`:

```text
available_at = None
```

No event timestamp, receive timestamp, candle start, or candle close is used
as an availability surrogate.

When the explicit handoff clock exists, the adapter creates the same
`CONSUMER_HANDOFF` evidence type used by the REST boundary and applies the
pre-return timestamp after canonicalization. The clock is not called before
canonical validation succeeds.

## Candle state and completeness

The Binance Kline field `x` is the provider-specific evidence for candle
state:

```text
x = false → CandleState.OPEN
x = true  → CandleState.CLOSED
```

For the candle completeness field:

```text
x = false → PARTIAL
x = true  → COMPLETE
```

This is only a Binance WebSocket translation. The global
`DataCompleteness` definition is not changed.

`x == true` does not imply finality. Finality remains:

```text
CandleFinality.UNKNOWN
```

No finality is invented from timestamps.

The market-data-level completeness field remains `UNKNOWN`.

## Decimal and canonicalization

OHLCV values are parsed from Binance decimal strings into exact
`Decimal` values.

The adapter then reuses the existing provider-neutral normalization and
canonicalization boundary. Canonical `MarketData` validation remains the
authority for structural OHLCV consistency.

Invalid payloads do not become MarketData.

## Multiple updates of one candle

A live Kline stream can publish several updates for the same logical candle.
FASE 1.24 explicitly allows:

```text
same candle identity
OPEN
  ↓
OPEN
  ↓
OPEN
  ↓
CLOSED
```

Each accepted update creates a new `MarketData` instance.

The logical candle identity remains:

```text
(instrument_id, timeframe, candle.start)
```

These live updates are not treated as historical duplicates and do not invoke
historical gap detection or historical deduplication.

Each update leaves `source_sequence = None`; no universal sequence is
invented from event time or candle start.

## Errors

The boundary exposes three distinct WebSocket failure classes:

- `BinanceWebSocketTransportError`
- `BinanceWebSocketProtocolError`
- `BinanceWebSocketPayloadError`

All derive from `BinanceWebSocketError`.

Transport errors are propagated. There is no WebSocket retry policy in this
phase.

## Deliberate exclusions

FASE 1.24 does not implement:

- automatic reconnect;
- WebSocket retry/backoff;
- connection rotation or failover;
- 24-hour lifecycle handling;
- heartbeat manager;
- REST fallback;
- combined streams;
- dynamic SUBSCRIBE;
- order/user-data streams;
- depth, aggTrade, ticker or other stream families;
- persistence or datasets;
- background workers, schedulers, or daemon processes;
- strategy, indicators, risk, execution, or trading;
- formal `AcquisitionOperationEvidence` integration for WebSocket lifecycle.

The Phase 1.22 evidence counters are not repurposed to describe a streaming
connection.

## Real Binance E2E

Real Binance WebSocket traffic is intentionally not required for phase closure.
The online validation boundary is reserved for the separate FASE 1.24.1.

## Compatibility

Produced objects are ordinary canonical `MarketData` and can be passed to
the Phase 1.23 `evaluate_market_data_consumption(...)` function without any
Binance-specific consumer logic.
