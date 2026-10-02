# FASE 1.18 — Canonicalización y validación de intervalos Binance Spot

## Fuente oficial

La lista se verificó contra la documentación oficial vigente de Binance Spot para `GET /api/v3/klines`. Binance declara que el parámetro `interval` es un ENUM y que sus valores soportados son sensibles a mayúsculas/minúsculas. La documentación actual enumera: `1s`; `1m`, `3m`, `5m`, `15m`, `30m`; `1h`, `2h`, `4h`, `6h`, `8h`, `12h`; `1d`, `3d`; `1w`; `1M`.

Fuente oficial: https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#klines

Las klines se identifican por open time. `startTime` y `endTime` se interpretan en UTC, y `limit` tiene máximo 1000. Esta fase no modifica esas reglas.

## Intervalos soportados

| Familia | Valores | Semántica de duración |
|---|---|---|
| segundos | `1s` | 1 segundo exacto |
| minutos | `1m`, `3m`, `5m`, `15m`, `30m` | duración fija exacta |
| horas | `1h`, `2h`, `4h`, `6h`, `8h`, `12h` | duración fija exacta |
| días | `1d`, `3d` | duración fija exacta |
| semanas | `1w` | 7 días exactos |
| meses | `1M` | intervalo de calendario; no se convierte a `timedelta` fijo |

`1M` es deliberadamente distinto de `1m`. No se normalizan mayúsculas/minúsculas.

## Frontera provider-specific

Se añade `bot_obrero/binance_intervals.py` con:

- `BinanceSpotInterval`;
- `BinanceSpotIntervalUnit`;
- `BinanceSpotIntervalError`;
- `validate_binance_spot_interval()`.

La estructura es inmutable y solamente acepta valores de la lista oficial.

El resolver devuelve exactamente el token que recibe. No existe conversión entre timeframes de proveedores.

## Política de input

La fase utiliza exactitud estricta:

- `1m` → válido;
- `1M` → válido y distinto de `1m`;
- ` 1m` → inválido;
- `1m ` → inválido;
- `1H` → inválido;
- `1S` → inválido;
- `None`, boolean, número, lista u objeto → inválido;
- tokens no documentados → inválidos.

No se aplica `.strip()`, `.lower()` ni conversión heurística.

Esto evita ocultar errores del caller y respeta la sensibilidad a mayúsculas/minúsculas documentada por Binance.

## Duraciones

Las duraciones se representan solamente cuando una `timedelta` expresa exactamente la semántica del intervalo.

Ejemplos:

- `1s` → 1 segundo;
- `1m` → 1 minuto;
- `1h` → 1 hora;
- `1d` → 1 día;
- `1w` → 7 días;
- `1M` → `None`.

No se afirma que un mes sea 30 días ni 2.592.000 segundos.

## ProviderRecord.timeframe

El adapter valida el intervalo antes de construir la consulta HTTP. El mismo valor validado se envía a Binance y se conserva en `ProviderRecord.timeframe`.

Por ejemplo:

```text
caller: 1M
   ↓
BinanceSpotInterval(value='1M', month, duration=None)
   ↓
HTTP interval=1M
   ↓
ProviderRecord.timeframe='1M'
```

No se modifica el significado existente de `timeframe`.

## Live

El camino permanece:

```text
metadata resolution
        ↓
validated Binance interval
        ↓
BinanceSpotRestAdapter.fetch_market_data()
        ↓
ProviderRecord
        ↓
MarketData
```

No se crea otro cliente HTTP ni otro parser.

## Historical

El método histórico llama a la misma frontera de validación mediante `build_query()` y después reutiliza `fetch_market_data()` para cada página.

Por lo tanto live e histórico no tienen listas diferentes de intervalos.

La paginación de FASE 1.15 permanece intacta.

## BTCUSDT / ETHUSDT

La validación se ejecuta después de la resolución metadata-backed de FASE 1.17 y antes de la adquisición.

Los tests verifican:

- BTCUSDT con `1m`;
- ETHUSDT con `5m`;
- ambos conservan su `InstrumentIdentity` metadata-backed;
- el timeframe coincide exactamente con el intervalo validado.

No se reintroduce ningún mapping manual.

## Availability

El resolver de intervalos no participa en la evidencia temporal de availability.

Se conservan sin cambios:

- `observed_at`;
- `received_at`;
- `available_at`;
- `consumer_handoff_clock`;
- `AvailabilityEvidence`;
- `EvidenceTimestamp`.

No se usa `interval`, `close_time` ni una duración calculada para fabricar `available_at`.

## Contrato canónico

Esta fase no modifica:

- `bot_obrero/market_data.py`;
- `bot_obrero/temporal.py`;
- `bot_obrero/availability.py`.

Canonical Market Data Contract v1.0 permanece sin cambios.

## Tests

`tests/test_phase1_18_binance_intervals.py` cubre:

- lista oficial completa;
- segundos, minutos, horas, días, semanas y meses;
- duración fija cuando es exacta;
- ausencia de duración fija para `1M`;
- strings vacíos y whitespace;
- `None`;
- booleanos;
- números y otros tipos;
- tokens inexistentes;
- mayúsculas/minúsculas incorrectas;
- live;
- historical;
- BTCUSDT;
- ETHUSDT;
- `ProviderRecord.timeframe`;
- rechazo antes de HTTP;
- availability.

## Integración real

Una comprobación real puede realizarse contra `GET /api/v3/klines` para un intervalo documentado, pero no forma parte de CI. No se utiliza polling ni ningún proceso residente.

## Fuera de alcance

No se implementa:

- WebSocket;
- streams;
- trading;
- órdenes;
- balances;
- API keys;
- firmas;
- persistencia;
- Redis;
- scheduler;
- workers;
- catálogo universal de timeframes;
- equivalencias cross-provider;
- FASE 1.19.
