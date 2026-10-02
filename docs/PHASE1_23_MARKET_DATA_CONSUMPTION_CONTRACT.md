# FASE 1.23 — Market Data Consumption Contract v1.0

## Propósito

FASE 1.23 crea una frontera provider-neutral entre `MarketData` y sus consumidores. La adquisición produce el dato; esta capa decide si ese dato puede utilizarse en un instante de decisión explícito.

```text
ACQUISITION
    ↓
CANONICAL MARKET DATA
    ↓
CONSUMPTION DECISION
```

El consumidor no modifica `MarketData` y no interpreta `received_at` ni `observed_at` como disponibilidad.

## Decision timestamp

Toda decisión exige `decision_timestamp` explícito y timezone-aware.

No se infiere desde:

- `received_at`;
- `observed_at`;
- `available_at`;
- `datetime.now()`.

El método `evaluate_market_data_consumption()` rechaza timestamps sin zona horaria.

## Políticas

### REQUIRE_AVAILABLE

El consumidor requiere evidencia temporal de disponibilidad.

- `available_at is None` → `UNKNOWN`.
- `available_at <= decision_timestamp` → `ACCEPTED`.
- `available_at > decision_timestamp` → `REJECTED`.

### ALLOW_UNKNOWN

La política permite que el flujo reciba un dato cuya disponibilidad es desconocida, pero no transforma esa ausencia de evidencia en disponibilidad demostrada.

Por tanto:

- `available_at is None` → `UNKNOWN`.

El resultado conserva la política utilizada y la razón `AVAILABLE_AT_UNKNOWN`.

## Estados del resultado

`MarketDataConsumptionStatus` define:

- `ACCEPTED`: existe evidencia suficiente y la disponibilidad ya ocurrió en relación con el `decision_timestamp`.
- `REJECTED`: existe evidencia suficiente para concluir que todavía no estaba disponible; la regla temporal existente identifica el look-ahead.
- `UNKNOWN`: no existe evidencia suficiente para afirmar disponibilidad.

El resultado es inmutable y mantiene una referencia directa al `MarketData` original.

## MarketData identity

`MarketDataConsumptionResult` no crea un nuevo `MarketData`, `Candle` o identificador canónico.

Expone referencias derivadas al objeto original:

- `market_data_id`;
- `candle_identity`.

`result.market_data is original_market_data` permanece verdadero.

## EvidenceTimestamp

FASE 1.23 reutiliza la semántica temporal existente.

`evaluate_market_data_consumption()` delega la comparación efectiva a:

```text
MarketData.evidence_at(decision_timestamp)
        ↓
EvidenceTimestamp
```

No se duplica la regla `available_at <= decision_timestamp` dentro de varios consumidores.

Cuando `available_at` es `None`, la frontera de consumo clasifica explícitamente `UNKNOWN` antes de intentar construir `EvidenceTimestamp`, porque el contrato temporal existente requiere una disponibilidad concreta.

Cuando `available_at` es posterior al decision timestamp, `EvidenceTimestamp` rechaza la combinación por look-ahead y la frontera la clasifica como `REJECTED`.

## Acquisition Operation Evidence

El resultado puede conservar opcionalmente una referencia a `AcquisitionOperationEvidence` de FASE 1.22.

Esto no hace que la evidencia de adquisición sea obligatoria para construir `MarketData` ni para definir su identidad.

La separación permanece:

```text
AcquisitionOperationEvidence
        ↓
MarketData
        ↓
MarketDataConsumptionResult
```

El contrato de consumo no depende de Binance y puede recibir cualquier `MarketData` canónico producido por `LIVE`, `HISTORICAL` o futuras fuentes `REPLAY`.

## Ausencia de inferencia

El consumo no rellena `available_at` ni copia:

```text
received_at → available_at
observed_at → available_at
```

Que el dato haya llegado a la función no constituye evidencia de disponibilidad.

## Límites

Esta fase no implementa:

- WebSocket;
- streaming;
- estrategia;
- indicadores;
- señales;
- riesgo;
- backtesting;
- paper trading;
- ejecución u órdenes;
- persistencia;
- bases de datos;
- scheduler/workers;
- event bus;
- nuevos providers.

La frontera es síncrona y pequeña: recibe `MarketData`, un timestamp de decisión y una política explícita, y devuelve un resultado explícito.

## CI

`.github/workflows/tests.yml` permanece sin cambios.

El cierre exige una ejecución real de GitHub Actions sobre el resultado final.