# FASE 1.7.2 — Canonical Market Data Contract v1.0

## Alcance

Esta implementación añade contratos de identidad, fuente, MarketData y Candle canónico. No incorpora ingestión de proveedores, persistencia histórica, datasets, revisiones formales, análisis, estrategia, riesgo ni ejecución.

## Contratos

- `InstrumentIdentity`: identidad lógica estable con `instrument_id`, `symbol`, `market` y el único tipo permitido en v1.0: `CRYPTO_SPOT`.
- `SourceIdentity`: `source_id` y `provider` obligatorios; `venue` opcional y separado de ambos.
- `MarketData`: dato normalizado recibido, con identidad propia, referencias de instrumento/fuente, tipo, tiempos, payload, calidad, completitud y secuencia externa opcional.
- `Candle`: payload canónico con OHLCV Decimal, estado, completitud y finality independientes.

Los contratos canónicos están en `bot_obrero.market_data`.

## Numéricos y serialización

OHLCV acepta `Decimal`, texto decimal o enteros exactos. Rechaza float, bool, NaN e infinitos; no aplica escala ni rounding durante la normalización. La constante `DEFAULT_MARKET_DATA_ROUNDING` expone `ROUND_HALF_EVEN` para operaciones futuras que requieran redondeo explícito.

La serialización JSON representa OHLCV como texto decimal y conserva su escala. `from_json` reconstruye los valores como `Decimal`.

## Temporalidad

`observed_at` y `received_at` son timestamps timezone-aware obligatorios. `available_at` es timezone-aware o `None` (UNKNOWN). No se infiere desde observed_at ni received_at. MarketData no contiene decision_at.

`MarketData.evidence_at(decision_at)` reutiliza `EvidenceTimestamp`. Si available_at es UNKNOWN, no produce evidencia temporal; si es posterior a decision_at, la defensa existente rechaza el look-ahead.

## Estados

Quality: VALID, INVALID, UNKNOWN. Completeness: COMPLETE, PARTIAL, UNKNOWN. Son dimensiones independientes.

Candle state: OPEN, CLOSED, UNKNOWN. Finality: NOT_FINAL, FINAL, UNKNOWN. CLOSED no implica FINAL. Una vela PARTIAL no es por ello INVALID.

## Identidad y provenance

La identidad lógica de Candle es `(instrument_id, timeframe, start)`; source_id no forma parte de ella. Cada MarketData conserva su propio market_data_id y source_id/source identity.

`to_market_observation()` permite promover datos con calidad VALID y available_at conocido al contrato de FASE 1.6. La referencia de provenance apunta a market_data_id; observation_id se genera por separado. Datos con quality INVALID o UNKNOWN, o con disponibilidad UNKNOWN, no se promueven. La observación reutiliza EvidenceTimestamp para el control temporal.

## Correcciones y gaps

Candle y MarketData son dataclasses inmutables; no existe API de mutación/corrección, versionado, revision_id ni supersession. No hay generador de velas para gaps ni valores sintéticos cero.

## Compatibilidad legacy

`bot_obrero.data.Candle` se conserva sin cambios para los consumidores legacy de FASE 1.2. El contrato canónico nuevo es `bot_obrero.market_data.Candle`. Esta separación evita cambiar la firma posicional legacy (close/closed) ni migrar indiscriminadamente float a Decimal. Los tests legacy permanecen activos.

## Fuera de alcance

No se implementan conectores, WebSocket, Binance/CCXT, datasets, DataSlice, replay, storage histórico, versionado formal, Risk, Financial State, Strategy, Backtesting, Paper Trading, OrderIntent ni Execution.
