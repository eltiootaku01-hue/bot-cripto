# FASE 1.22 — Acquisition Operation Evidence Contract v1.0

## Propósito

FASE 1.22 añade un contrato provider-neutral para describir cómo se realizó una operación de adquisición, sin añadir información operacional a `MarketData`.

Separación:

```text
provider
   ↓
acquisition
   ↓
AcquisitionOperationEvidence
   ↓
MarketData
```

`MarketData` continúa representando únicamente el dato canónico.

## Contrato

El módulo `bot_obrero/acquisition_evidence.py` define:

- `AcquisitionMode`: `LIVE`, `HISTORICAL`, `REPLAY`.
- `AcquisitionStatus`: `SUCCESS`, `PARTIAL`, `FAILED`.
- `AcquisitionErrorEvidence`: error final estructurado y acotado.
- `AcquisitionOperationEvidence`: evidencia final inmutable.
- `AcquisitionOperationRecorder`: recorder temporal usado mientras una operación está en curso.

La evidencia final utiliza `dataclass(frozen=True)`.

El recorder es mutable únicamente durante la operación y es local a esa operación; no existe estado global.

## operation_id

`operation_id` identifica una operación lógica completa.

No cambia por retry ni por página.

Ejemplo:

```text
operation_id = A

page 1 / attempt 1
page 1 / attempt 2
page 2 / attempt 1
```

Todos pertenecen a la misma operación `A`.

No se introduce un sistema distribuido de tracing ni IDs independientes para cada request físico.

## Alcance solicitado

La evidencia conserva símbolo, intervalo, `requested_start`, `requested_end` y `limit`.

Los timestamps de alcance son `datetime` timezone-aware.

En la integración Binance, los timestamps históricos se convierten desde los milisegundos UTC definidos por el contrato temporal existente. No se usan estos timestamps operacionales para modificar `observed_at`, `received_at` ni `available_at`.

## Provider y source

Los campos son explícitos:

- `provider`
- `source_id`
- `venue`
- `market`

No se infiere uno a partir de otro.

## Contadores

La evidencia distingue:

- `request_count`: requests HTTP físicos realmente ejecutados.
- `retry_count`: intentos físicos adicionales dentro de un mismo request lógico.
- `page_count`: páginas lógicas completadas con éxito.

Ejemplo:

```text
page 1 → HTTP 500
page 1 → retry → 200
page 2 → 200
```

produce:

```text
page_count    = 2
request_count = 3
retry_count   = 1
```

El contador de requests de evidencia se registra en el mismo callback de `BinanceRetryPolicy` que precede al request físico y después de que `BinanceRequestBudget` autoriza el intento. Si el budget bloquea un intento, ese intento no cuenta como request físico porque no hubo I/O.

No se mantiene un segundo contador de evidencia separado del flujo real de attempts.

## Historial y cursor

La evidencia histórica registra `requested_start`, `requested_end`, `page_count`, `request_count`, `retry_count`, `last_cursor` y `next_cursor`.

El cursor representa progreso de adquisición y nunca identidad de candle.

No se escribe en `MarketData.candle_identity` ni en `source_sequence`.

En una operación histórica fallida después de una o más páginas válidas, los contadores y cursores conservan el progreso exitoso anterior y el estado final es `FAILED`.

La paginación existente sigue utilizando `last candle close + 1 ms`, sin modificación semántica.

## Estados

### SUCCESS

La operación completó su objetivo definido.

### PARTIAL

Estado disponible en el contrato para una operación que produjo un resultado válido pero no completó todo el alcance solicitado.

En esta fase no se introduce una nueva semántica de adquisición parcial en Binance: la paginación actual continúa propagando el fallo cuando una página posterior no puede completarse. Por tanto, una histórica con página 1 exitosa y página 2 fallida queda documentada como `FAILED` aunque exista progreso válido previo.

### FAILED

La operación no alcanzó su objetivo.

La excepción original se conserva sin envolver en otra excepción. Cuando se usa una API `*_with_evidence`, la evidencia final se adjunta a la misma instancia mediante `acquisition_evidence`.

## Errores

`AcquisitionErrorEvidence` conserva únicamente información operacional estructurada:

- categoría/clase;
- mensaje;
- código de error del provider, si está disponible;
- HTTP status, si existe;
- `outcome_unknown`, si existe.

No se almacenan cuerpos HTTP ni payloads de respuesta completos.

## Timestamps operacionales

La evidencia conserva `started_at` y `finished_at`, ambos timezone-aware.

`duration` es una propiedad calculada como `finished_at - started_at`.

El tiempo operacional no modifica ninguna cronología de market data.

## Integración Binance Spot

### One-shot / LIVE

`BinanceSpotRestAdapter.fetch_market_data_with_evidence()` devuelve `MarketData[] + AcquisitionOperationEvidence`.

La evidencia incluye provider, source, símbolo, intervalo, contadores, estado y timestamps.

La semántica de retry existente se observa sin crear una política secundaria.

### Histórico / HISTORICAL

`BinanceSpotRestAdapter.fetch_historical_market_data_with_evidence()` devuelve `MarketData[] + AcquisitionOperationEvidence`.

La evidencia comparte un único `operation_id` entre todas las páginas.

El request count representa attempts físicos; el page count representa páginas históricas completadas.

### ExchangeInfo / Instrument metadata

`BinanceSpotInstrumentMetadata.fetch_with_evidence()` expone evidencia de la operación de metadata.

En esta operación `page_count` permanece en cero porque ExchangeInfo no forma parte de una paginación de candles.

## Retry

FASE 1.22 no cambia `max_attempts`, backoff, `Retry-After`, 429, 418, 5xx ni `outcome_unknown`.

La evidencia observa los attempts reales producidos por esa política.

Ejemplo:

```text
500 → 200

SUCCESS
request_count = 2
retry_count   = 1
```

Agotamiento:

```text
500 → 500 → 500

FAILED
request_count = 3
retry_count   = 2
outcome_unknown = true
```

## Contrato canónico

No se modifica `bot_obrero/market_data.py`, `bot_obrero/temporal.py` ni `bot_obrero/availability.py`.

Tampoco se agregan a `MarketData` request IDs, retry counts, HTTP status, cursors ni duración de adquisición.

La evidencia permanece fuera del dominio canónico.

## API de evidencia

Las operaciones normales existentes mantienen sus retornos originales.

Las APIs nuevas son explícitas y opt-in:

```text
fetch_market_data_with_evidence(...)
fetch_historical_market_data_with_evidence(...)
BinanceSpotInstrumentMetadata.fetch_with_evidence(...)
```

En operaciones fallidas, estas APIs mantienen la excepción original y adjuntan `exception.acquisition_evidence`.

Así no se modifica la jerarquía ni la clasificación de errores existente.

## Límites de FASE 1.22

Esta fase no introduce WebSocket, streaming, persistencia, base de datos, scheduler, workers, estrategia, indicadores, backtesting, paper trading, execution, órdenes, trading, nuevos providers ni tracing distribuido.

`REPLAY` existe como modo contractual para futuras adquisiciones, pero no se implementa ninguna fuente de replay en esta fase.

## CI

El workflow `.github/workflows/tests.yml` no se modifica.

El cierre de la fase depende de una ejecución real de GitHub Actions sobre el resultado final y del registro de run, commit, job, conclusión y número exacto de tests.