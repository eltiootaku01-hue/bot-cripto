# BTC_ETH_FOLLOW_RESEARCH_V1 — Auditoría de adquisición histórica

- Fecha de revisión: 2026-10-16
- Modo: solo lectura de código/documentación; publicación únicamente de este informe.
- Estado del dictamen: **PARTIAL — ADAPTER_REUSABLE / ANNUAL_RESEARCH_BLOCKED**
- Decisión operativa: **NO_TRADE**
- Rama de referencia: `research/btc-eth-follow-v1`
- PR documental: [#67](https://github.com/eltiootaku01-hue/bot-cripto/pull/67)
- HEAD de `main` observado: `b9e34f2d836b99f711d6227f88c95c7b5ba7bd85`
- No se modificaron código, tests ni workflows. No se ejecutaron pruebas, solicitudes de mercado ni backtests.

## 1. Dictamen ejecutivo

**Sí existe un adaptador histórico reutilizable.** `bot_obrero/binance_spot.py` implementa adquisición paginada del endpoint público de klines de Binance Spot, con límite por página, presupuesto de solicitudes, cursor temporal, detección de duplicados, orden incorrecto, paginación estancada y huecos. `BinanceMetadataBackedAdapter` delega en ese adaptador; el consumidor `fetch_history` de Product V2 ya lo reutiliza.

**No está demostrado que el camino completo de investigación pueda evaluar un año horario.** La simulación `BacktestConfig` restringe `candles` a 1–1.200; un año de velas 1h requiere aproximadamente 8.760 velas por símbolo, además del calentamiento y los límites de evaluación. El adaptador resuelve paginación de adquisición, pero no elimina este límite del simulador. No se debe afirmar que BTC_ETH_FOLLOW_V1 ya puede hacer un backtest anual end-to-end.

**Hay un segundo bloqueo temporal:** el adaptador canónico representa `available_at=None` por defecto, y los metadatos de velas incluyen `finality=UNKNOWN` y `market_data_completeness=UNKNOWN`. El validador del backtest rechaza varios estados incompletos y gaps, pero permite continuar contabilizando finality/completeness desconocidas en el envelope y modela el cierre como instante disponible bajo una suposición explícita. La validez de esa suposición para un estudio anual aún necesita un contrato específico y revisión; no se ha probado con datos reales en esta auditoría.

## 2. Evidencia inspeccionada

| Ref / archivo | Evidencia de código observada |
|---|---|
| `main:bot_obrero/binance_spot.py` — blob `261638105bd5778d8b5836115419045a48e6f49c` | Adaptador público REST de klines; `limit` permitido 1–1.000; `fetch_historical_market_data` y variante con evidencia; paginación por cursor. |
| `main:bot_obrero/binance_instruments.py` — blob `cc849015b9d9902619b36946a0975b4d56af9c39` | `BinanceMetadataBackedAdapter.fetch_historical_market_data` delega al adaptador por símbolo. |
| `main:bot_obrero/binance_resilience.py` — blob `c1da53e419f4372ab42bb1ed74d52938cf8ced7c` | Política de reintentos acotados (por defecto 3 intentos) y `BinanceRequestBudget` para limitar solicitudes físicas. |
| `main:bot_obrero/product_v2.py` — blob `6ddd6dd546ec910b078638d8d224cca905a38a65` | `fetch_history` solicita 4h mediante el adaptador existente, `page_limit=1000`, y calcula `max_requests` según los días solicitados. No es todavía un consumidor horario BTC+ETH. |
| `main:bot_obrero/virtual_backtest.py` — blob `4e7f73aaeae0d7e6f0fcea033d05c6caab0c405d` | `_MAX_CANDLES=1200`; validación temporal/continuidad y backtest con velas cerradas, decisión al cierre y fill en apertura siguiente. |
| `main:tests/test_phase1_15_binance_historical.py` — blob `b14946528e6507d9c5322a8f7c8a08bbd408c06a` | Casos existentes para paginación entre páginas, cursor `last close + 1 ms`, duplicados, gaps y límites. |
| `main:tests/test_phase1_19_binance_kline_temporal.py` — blob `9b7d614fe2abf714480f0986a5d525430b81defa` | Casos existentes para timestamps milisegundo/UTC y contrato temporal de klines. |
| `main:tests/test_phase1_21_binance_resilience.py` — blob `b519ab307c33a5f0cb6929a3739a4f32e91ccc2d` | Casos existentes para reintentos acotados y errores de transporte/rate limit. |
| `main:tests/test_phase1_22_acquisition_evidence.py` — blob `3235b60bb69954ea36ddf50f1f5bf010a6e1ba79` | Contrato de evidencia de adquisición y cobertura de estados/error. |

Los blobs y contenidos se consultaron de forma remota; los tests enumerados son **OBSERVED (existencia/código)**, no **TESTED** en esta ejecución.

## 3. Hallazgos por requisito

### Paginación

- **OBSERVED:** `build_query` valida el intervalo y permite un `limit` máximo de 1.000 por solicitud.
- **OBSERVED:** `_fetch_historical_market_data` avanza el cursor a `last_item.payload.end + 1 ms`, conserva los registros de páginas en orden y detiene la consulta al superar `end_time` o recibir una página menor que `page_limit`.
- **OBSERVED:** el presupuesto `max_requests` es explícito; los reintentos consumen el presupuesto de solicitudes físicas. La política por defecto permite hasta 3 intentos por solicitud lógica.
- **UNKNOWN:** no se hizo ninguna adquisición real ni se midió el volumen/duración de un año horario para BTCUSDT y ETHUSDT. La paginación está implementada en código, pero la capacidad end-to-end de la investigación anual no se ha verificado.

### Límites

- **OBSERVED:** API adapter: hasta 1.000 klines por página; `fetch_historical_market_data` usa `max_requests=1000` por defecto, sujeto al presupuesto explícito.
- **OBSERVED:** Product V2 ya calcula un presupuesto de páginas para su uso de 4h. No hay evidencia inspeccionada de un consumidor BTC/ETH horario específico en el flujo Product V2 actual.
- **OBSERVED:** el simulador impone `candles <= 1200`. A 1h, 365 días son 8.760 velas por símbolo (sin contar warm-up); 1.200 velas representan 50 días horarios. La adquisición por páginas no cambia esta restricción.
- **BLOCKED:** no se puede ejecutar la evaluación de 365 días horarios completa con el contrato actual del simulador sin un diseño revisado que permita ventanas/evaluación anual y preserve indicadores, contabilidad y métricas globales. No se autoriza cambiar el límite en esta auditoría.

### Timestamps y disponibilidad

- **OBSERVED:** los timestamps Binance se convierten desde milisegundos a datetimes conscientes de zona UTC; las consultas usan `startTime` y `endTime` en milisegundos.
- **OBSERVED:** el adaptador puede devolver `available_at=None` cuando no se configura una evidencia explícita de handoff. El payload de klines registra `finality=UNKNOWN` y envelope `market_data_completeness=UNKNOWN`.
- **OBSERVED:** `_validate_history` comprueba orden, duplicados, timeframe, estado de vela, integridad, calidad y continuidad. Rechaza velas internas abiertas/parciales/no finales; excluye una última vela abierta/parcial; si `available_at` existe, debe ser timezone-aware y no posterior al cierre modelado.
- **OBSERVED:** el mismo validador permite seguir con finality desconocida y contabiliza desconocidos; cuando `available_at` no existe, `_observation` modela la disponibilidad como el cierre de la vela para la simulación, manteniendo la disponibilidad canónica como `UNKNOWN`.
- **INFERRED:** ese modelo puede servir como hipótesis de backtest documentada, pero no demuestra el instante histórico real en que cada dato se volvió observable. La política para BTC y ETH debe congelarse y documentarse antes del test OOS.
- **BLOCKED:** no declarar evidencia de disponibilidad punto-en-tiempo verificada mientras la procedencia temporal sea desconocida.

### Huecos, orden y duplicados

- **OBSERVED:** el adaptador rechaza identidades de vela duplicadas, orden cronológico inesperado, cursor que no avanza y huecos entre el cierre anterior más 1 ms y el inicio siguiente.
- **OBSERVED:** el validador del backtest vuelve a verificar timestamps ordenados y únicos y exige continuidad según la duración del intervalo.
- **UNKNOWN:** los controles existen en código, pero no se verificó una serie histórica concreta ni se contaron huecos/duplicados para el periodo solicitado.

### Reutilización del adaptador

- **OBSERVED:** el camino recomendado es reutilizar `BinanceSpotRestAdapter` a través de `BinanceMetadataBackedAdapter`, con el endpoint público ya configurado. Product V2 usa ese camino en su función `fetch_history`.
- **INFERRED:** la investigación BTC_ETH debería adquirir ambos símbolos mediante el mismo adaptador y contrato, con ventanas UTC explícitas, mismo intervalo y validación de timestamps exactos antes de combinarlos. No crear una segunda implementación de paginación.
- **BLOCKED:** aún falta definir cómo se entrega al simulador un año de datos sin violar el límite de 1.200 velas y cómo se conservan correctamente el warm-up y las métricas agregadas entre ventanas.

## 4. Riesgos y acciones previas necesarias

1. Diseñar y revisar el contrato de dataset horario BTC/ETH: rango UTC semiabierto, alineación por timestamp de apertura, regla de cierre, tratamiento de la última vela, warm-up y hashes/configuración.
2. Resolver el límite de 1.200 del simulador mediante diseño revisable antes de implementar; no fragmentar el backtest de forma que reinicie saldos/indicadores o falsee drawdown y liquidación.
3. Definir una política explícita para `available_at=None` y `finality=UNKNOWN`. Si la validez del experimento depende de disponibilidad real no demostrable, bloquear o etiquetar la limitación; no convertir la suposición en evidencia observada.
4. Congelar controles de integridad: timestamps UTC estrictamente crecientes, unicidad por `(instrument_id, timeframe, start)`, continuidad exacta, mismo conjunto temporal BTC/ETH y rechazo de huecos/duplicados.
5. Solo después de revisión y autorización separada, implementar cambios mínimos y ejecutar tests dirigidos. Esta auditoría no autoriza tests, adquisición de mercado ni backtests.

## 5. Estado final

- Paginación: **OBSERVED — implementada y acotada**.
- Límite de página y presupuesto de solicitudes: **OBSERVED — configurables/validados**.
- Detección de huecos y duplicados: **OBSERVED — implementada; no ejecutada aquí**.
- Timestamps UTC: **OBSERVED — conversión/camino de código existente**.
- Disponibilidad histórica punto-en-tiempo: **UNKNOWN/BLOCKED — no probada y puede ser UNKNOWN**.
- Adaptador existente: **OBSERVED — reutilizable**.
- Backtest anual 1h end-to-end: **BLOCKED — límite de 1.200 velas del simulador y contrato temporal pendiente**.
- Pruebas/backtests nuevos: **NO EJECUTADOS**.
- Código/tests/workflows/main: **SIN CAMBIOS**.
- Decisión operativa: **NO_TRADE**.
