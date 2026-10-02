# FASE 1.20.1 — Verificación E2E real Binance Spot REST mediante GitHub Actions

## Propósito

Esta fase añade una vía manual y aislada para ejecutar el pipeline real del repositorio contra la API pública de Binance desde un runner de GitHub Actions.

El workflow es independiente del CI normal y solo utiliza:

- `workflow_dispatch`;
- checkout del repositorio;
- Python 3.12;
- `python -m pip install -e ".[test]"`;
- `scripts/phase1_20_binance_e2e.py`.

No utiliza secrets, bases de datos, Redis, persistencia, polling ni procesos residentes.

## Separación de evidencia

### Evidencia offline

El workflow `.github/workflows/tests.yml` continúa ejecutando exclusivamente:

```text
checkout
↓
Python 3.12
↓
pytest -q
```

Sus tests deterministas y mocks no constituyen evidencia de acceso real a Binance.

### Evidencia real

La evidencia E2E de esta fase solo puede obtenerse de un run de:

```text
phase1.20-binance-e2e
```

El script usa las implementaciones productivas:

```text
BinanceSpotInstrumentMetadata
↓
resolve_active()
↓
BinanceMetadataBackedAdapter
↓
BinanceSpotRestAdapter
↓
ProviderRecord / canonical normalization
↓
MarketData
```

La instrumentación de requests delega cada llamada al HTTP implementation productivo; no crea un segundo cliente HTTP ni sustituye Binance por mocks.

## Escenarios

Obligatorios:

- BTCUSDT / 1m — live;
- ETHUSDT / 5m — live;
- BTCUSDT / 1m — historical acotado.

La prueba opcional BTCUSDT / 1h no se incluye para mantener el tráfico deliberadamente pequeño.

## Validaciones

Para los escenarios live se verifican:

- metadata real y resolución activa;
- `instrument_id`;
- símbolo canónico derivado de `baseAsset`/`quoteAsset`;
- mercado SPOT;
- `source_id=binance-spot-rest`;
- `provider=binance`;
- `venue=BINANCE`;
- timestamps timezone-aware en UTC;
- `observed_at == payload.start`;
- `available_at is None`;
- intervalo validado por la frontera Binance existente;
- ausencia de `timeZone` en el request;
- OHLCV canónico presente;
- calidad, completeness, candle state y finality presentes.

La prueba histórica verifica orden cronológico, identidad lógica sin duplicados, rango solicitado y, si existen varias páginas, el cursor real:

```text
last candle close + 1 ms
```

No se fabrica una segunda página.

## Límite de tráfico

El script impone un máximo de 8 requests totales y un máximo histórico de 2 requests.

La ejecución esperada es:

- 2 requests de ExchangeInfo, uno por símbolo;
- 2 requests live de klines;
- hasta 2 requests históricos.

No hay polling ni stress testing.

## Fallos

Un fallo de red, timeout, HTTP error, rate limit, payload inválido o discrepancia de contrato hace fallar el job.

El script imprime explícitamente:

```text
RESULT: REQUIRES CEREBRO REVIEW
```

para fallos de ejecución y no los convierte en `skipped`.

## Evidencia del run

La documentación de cierre debe completarse después de un run real con:

```text
workflow:
run:
commit:
status:
conclusion:
job:
requests:
symbols/intervals:
```

No se considera cerrada la FASE 1.20 hasta disponer de ese run real y verificar sus logs.

## Contrato canónico

Esta subfase no modifica:

- `bot_obrero/market_data.py`;
- `bot_obrero/temporal.py`;
- `bot_obrero/availability.py`.

Tampoco modifica la arquitectura productiva de Binance.

## Estado

La conclusión permanece:

```text
REQUIRES CEREBRO REVIEW
```

hasta que GitHub Actions ejecute satisfactoriamente el workflow manual contra Binance real.
