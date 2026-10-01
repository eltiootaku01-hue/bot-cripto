# BOT OBRERO — FASE 1.7.0 — HISTORICAL / MARKET DATA CONTRACT DECISION PACK

Fecha de auditoría: 2026-10-01

## 0. Estado de partida y criterio de cierre

### [EXISTENTE]

Repositorio auditado:

- `eltiootaku01-hue/bot-cripto`
- branch: `main`
- HEAD inicial verificado: `0f4dd787f4441675e94ffba57aa3c982c0d7643c`
- el HEAD coincide exactamente con el cierre autorizado de FASE 1.6.
- el commit `0f4dd787...` tiene como padre `455c700e...`.

La inspección se realizó contra el árbol del commit de partida y los archivos relevantes del branch `main`.

### [VERIFICAR]

La interfaz GitHub utilizada para esta auditoría permite inspeccionar el estado versionado del repositorio, pero no expone un working tree local no confirmado. Por ello, esta auditoría no afirma ausencia de modificaciones locales no publicadas.

### [DECISIÓN DEL CEREBRO]

Esta fase no autoriza implementación de infraestructura de datos. El presente documento es un Decision Pack para revisión posterior.

---

# 1. Resumen ejecutivo

El repositorio ya contiene primitivas suficientes para demostrar tres ideas que deben conservarse al diseñar datos históricos:

1. existe una primitiva temporal común, `EvidenceTimestamp`;
2. FASE 1.6 ya distingue `OBSERVED` de `DERIVED` y conserva trazabilidad por IDs;
3. existe un `Candle` mínimo y una `LiquidityModel`, pero ninguno constituye todavía un contrato histórico de mercado completo.

El gap principal no es solamente "faltan OHLCV". El gap es la ausencia de una representación explícita del **dato de mercado como evidencia temporal y reproducible**, incluyendo disponibilidad, recepción, secuencia, calidad, completitud, procedencia y estado de cierre.

La futura FASE 1.7 debería introducir primero contratos de datos, no conectores ni estrategia.

Cadena conceptual propuesta:

```
Source / Instrument
        ↓
MarketData
        ↓
Dataset / Observation provenance
        ↓
MarketObservation
        ↓
AnalysisResult
        ↓
Hypothesis
        ↓
Signal
```

La cadena no debe crear ninguna ruta:

```
MarketData → OrderIntent
Signal → OrderIntent
```

---

# 2. Inventario factual

| Componente | Existe | Ubicación | Uso actual | Problemas / límites | Reutilizable |
|---|---|---|---|---|---|
| `MarketObservation` | Sí | `bot_obrero/analysis_contracts.py` | Observación genérica FASE 1.6 | No modela dataset, timeframe, secuencia ni calidad/completitud | Sí |
| `AnalysisResult` | Sí | `bot_obrero/analysis_contracts.py` | Resultado derivado trazable | Referencia IDs de observaciones, pero no define almacenamiento histórico | Sí |
| `Hypothesis` | Sí | `bot_obrero/analysis_contracts.py` | Hipótesis derivada | No es un contrato de datos | Sí, sin ampliarlo aquí |
| `Signal` | Sí | `bot_obrero/analysis_contracts.py` | Señal derivada | No debe conectarse a ejecución en esta fase | Sí, sin modificar |
| `EvidenceTimestamp` | Sí | `bot_obrero/temporal.py` | Control temporal / look-ahead | Semántica actual es mínima y debe seguir siendo la referencia común | Sí |
| `Candle` | Sí | `bot_obrero/data.py` | Candle mínimo | Solo start/end/close/closed/availability; no OHLCV completo | Parcial |
| `LiquidityModel` | Sí | `bot_obrero/data.py` | Validación simple de liquidez para ejecución/modelado | No es histórico ni order book completo | Parcial / aislado |
| `BacktestAssumptions` | Sí | `bot_obrero/data.py` | Precondiciones declarativas de backtest | No existe backtester | No es contrato de MarketData |
| `ReconciliationState` / `Snapshot` | Sí | `bot_obrero/reconciliation.py` | Comparación de snapshots de ejecución | Pertenece a reconciliación, no a histórico de mercado | No ampliar aquí |
| `ClockService` / snapshots | Sí | `bot_obrero/clock.py` | Salud temporal local/exchange | Orientado a reloj operativo; no sustituye timestamps de datos | Conceptualmente reutilizable |
| `OrderConstraints` | Sí | `bot_obrero/constraints.py` | Reglas de precio/cantidad de órdenes | No representa datos históricos | No |
| `SQLiteIdempotencyLedger` | Sí | `bot_obrero/persistent_ledger.py` | Idempotencia de órdenes/fills | Es operacional y financiero-adjacent, no dataset histórico | No |
| Evidencia de ejecución | Sí | `bot_obrero/execution.py` | EvidenceBundle/EvidenceRecord para autorización | Semántica ligada a intent/execution | Conceptualmente, no reutilizar como MarketData |
| Tests temporales | Sí | `tests/test_phase14_execution_boundary.py`, `tests/test_phase16_analysis_contracts.py` | Prueban frescura, procedencia y límites | Cobertura histórica específica todavía inexistente | Sí como patrón |
| Documentación de fases | Sí | `docs/PHASE1_*.md` | Registra contratos y límites | No existe aún Decision Pack de MarketData | Sí |

### [OBSERVADO]

El repositorio no contiene un contrato histórico completo para OHLCV ni un modelo explícito de dataset, fuente, instrumento, secuencia, calidad y completitud.

No se detectó una segunda implementación temporal equivalente a `EvidenceTimestamp` en los archivos auditados relevantes.

---

# 3. Contratos existentes reutilizables

## 3.1 EvidenceTimestamp

### [EXISTENTE]

`bot_obrero/temporal.py` define:

- `event_timestamp`
- `available_timestamp`
- `decision_timestamp`
- `execution_timestamp` opcional.

La defensa actual rechaza:

`available_timestamp > decision_timestamp`

y también rechaza ejecución anterior a decisión.

### [DECISIÓN DEL CEREBRO]

La futura infraestructura histórica debe reutilizar esta semántica conceptual y no crear un segundo sistema temporal incompatible.

---

## 3.2 MarketObservation

### [EXISTENTE]

FASE 1.6 define `MarketObservation` como dato observado con:

- `symbol`;
- timestamp de observación;
- timestamp de disponibilidad;
- tipo de observación;
- valores;
- provenance;
- venue opcional;
- ID.

Su provenance debe ser `OBSERVED`.

### [OBSERVADO]

Es un buen punto de entrada para datos de mercado, pero todavía es demasiado genérico para representar un dataset histórico reproducible.

### [PROPUESTO]

No reemplazar `MarketObservation`. Evolucionarlo mediante referencias explícitas o contratos auxiliares futuros para:

- dataset;
- instrumento;
- fuente;
- timeframe;
- secuencia;
- calidad;
- completitud;
- estado de cierre;
- procedencia del registro.

---

# 4. Estado actual de Market Data

## 4.1 Candle

### [EXISTENTE]

`bot_obrero/data.py` contiene:

- `start`;
- `end`;
- `close`;
- `closed`;
- `availability_timestamp`.

También existe `require_closed()` y `available_at()`.

### [OBSERVADO]

El modelo no representa:

- open;
- high;
- low;
- volume;
- volumen de trades;
- identificador de fuente;
- instrumento/mercado;
- timeframe explícito;
- secuencia;
- recepción;
- calidad;
- completitud;
- estado de dato histórico definitivo.

### [DECISIÓN DEL CEREBRO]

No convertir este `Candle` en un "monster Candle" mezclando indiscriminadamente todas las necesidades futuras. La futura FASE 1.7 debe definir un contrato de MarketData con composición clara.

---

# 5. Propuesta de contrato futuro

### [PROPUESTO]

Separar conceptualmente al menos estas capas:

## 5.1 Instrument

Identidad del instrumento negociable/observable:

- `symbol`;
- `venue` o mercado cuando corresponda;
- tipo de instrumento;
- identificador estable de instrumento;
- metadata de contrato cuando sea necesaria.

No debe contener señales ni reglas de estrategia.

## 5.2 MarketData

Representa el contenido de mercado observado:

- instrumento;
- timeframe, cuando aplique;
- OHLCV;
- timestamp del periodo/dato;
- estado abierto/cerrado;
- fuente;
- disponibilidad;
- recepción;
- secuencia;
- calidad;
- completitud;
- provenance.

## 5.3 Dataset / DataSlice

Representa un conjunto reproducible de datos:

- `dataset_id`;
- versión o identificador inmutable;
- fuente;
- instrumento/s;
- rango temporal;
- granularidad;
- cobertura esperada;
- cobertura recibida;
- reglas de calidad;
- estado de completitud;
- referencia de procedencia.

## 5.4 MarketObservation

Debe seguir siendo la frontera entre el dato observado y el análisis.

Puede referenciar:

- `dataset_id`;
- `market_data_id`;
- `instrument_id`;
- `observation_sequence`;
- metadatos de calidad/completitud;
- fuente y provenance.

### [DECISIÓN DEL CEREBRO]

Estos nombres y relaciones son propuestas, no contratos aprobados todavía.

---

# 6. OHLCV y semántica de valores

### [PROPUESTO]

Para una vela futura:

- `open`;
- `high`;
- `low`;
- `close`;
- `volume`.

Cuando exista información adicional, no asumir que pertenece al OHLCV base. Datos como número de trades, quote volume u order-book depth deben conservar su naturaleza específica.

### [OBSERVADO]

El repositorio actual utiliza `float` en `Candle` y `LiquidityModel`, mientras `OrderConstraints` utiliza `Decimal`.

### [DECISIÓN DEL CEREBRO]

Esta fase no autoriza una migración global de `float` a `Decimal`. La representación numérica de MarketData deberá decidirse explícitamente antes de implementación.

### [VERIFICAR]

Determinar en una futura revisión si el contrato histórico requiere:

- `Decimal`;
- representación textual exacta;
- enteros escalados;
- otra representación reproducible.

---

# 7. Temporalidad

## 7.1 Semántica propuesta

### [PROPUESTO]

`observed_at`

Momento que representa el hecho de mercado observado o el timestamp propio del dato.

`received_at`

Momento en que el sistema recibió el dato.

`available_at`

Momento desde el cual el dato podía ser utilizado legítimamente por el proceso de decisión.

`decision_at`

Momento de la decisión o evaluación que consume el dato.

`expires_at`

Momento después del cual una observación/evidencia deja de ser válida para el uso previsto, cuando corresponda.

## 7.2 Regla crítica

### [DECISIÓN DEL CEREBRO]

La respuesta a:

> "¿Exactamente cuándo estaba disponible este dato para el sistema?"

debe ser `available_at`.

La condición mínima contra look-ahead es:

`available_at <= decision_at`.

`observed_at` por sí solo no demuestra disponibilidad.

Ejemplo conceptual:

```
observed_at  = 10:00:00
received_at  = 10:00:03
available_at = 10:00:03
decision_at  = 10:00:01
```

Ese dato no puede utilizarse para la decisión, aunque su timestamp de mercado sea anterior.

### [DECISIÓN DEL CEREBRO]

No introducir una semántica temporal paralela a `EvidenceTimestamp`. La futura implementación deberá mapear sus timestamps al contrato temporal común.

---

# 8. Abierto, parcial, cerrado y definitivo

### [PROPUESTO]

Distinguir explícitamente:

- **OPEN/PARTIAL**: periodo todavía en formación;
- **CLOSED/CONFIRMED**: periodo finalizado según la fuente;
- **FINAL/HISTORICAL**: dato considerado definitivo dentro del dataset utilizado.

### [OBSERVADO]

`Candle.closed` ya expresa una parte de esta semántica.

### [DECISIÓN DEL CEREBRO]

No asumir que "cerrado" significa automáticamente "históricamente inmutable". Una fuente puede corregir o republicar datos.

Para análisis histórico reproducible debe poder distinguirse:

```
periodo cerrado
        !=
dato histórico definitivo/reproducible
```

### [VERIFICAR]

La futura implementación debe definir qué evidencia permite declarar un dato como definitivo y cómo se conserva una corrección posterior sin sobrescribir silenciosamente el dataset anterior.

---

# 9. Datos faltantes y UNKNOWN

### [PROPUESTO]

El contrato futuro debe distinguir como estados diferentes:

- dato presente;
- ausencia confirmada;
- gap;
- fuente sin respuesta;
- dato parcial;
- dato desconocido;
- dato no aplicable;
- dataset incompleto.

Regla obligatoria:

`UNKNOWN != ZERO`

Por tanto:

- volumen desconocido no equivale a volumen cero;
- precio desconocido no equivale a precio cero;
- ausencia de trades no equivale a volumen cero;
- ausencia de respuesta de una fuente no equivale a ausencia de mercado.

### [DECISIÓN DEL CEREBRO]

Los consumidores analíticos deberán recibir suficiente metadata para saber si un campo falta, si fue confirmado ausente o si simplemente no se conoce.

---

# 10. Calidad y completitud

### [PROPUESTO]

Separar **quality** de **completeness**.

Quality puede describir, por ejemplo:

- timestamps válidos;
- orden temporal válido;
- valores estructuralmente válidos;
- duplicados detectados;
- consistencia interna.

Completeness puede describir:

- cantidad esperada;
- cantidad recibida;
- intervalos cubiertos;
- gaps;
- datos pendientes;
- estado final de cobertura.

Un registro puede ser individualmente válido y aun así pertenecer a un dataset incompleto.

### [PROPUESTO]

Un futuro contrato de dataset debería poder expresar al menos:

```
EXPECTED
RECEIVED
MISSING
DUPLICATE
OUT_OF_ORDER
INVALID_TIMESTAMP
PARTIAL
COMPLETE
UNKNOWN
```

Los nombres exactos quedan pendientes.

### [DECISIÓN DEL CEREBRO]

No permitir que `complete=True` sea una afirmación sin procedencia en una futura capa de ingestión.

---

# 11. Provenance y reproducibilidad

### [PROPUESTO]

La trazabilidad futura debería poder recorrer:

```
AnalysisResult
    ↓
MarketObservation
    ↓
MarketData / Dataset
    ↓
Source + Instrument + Time
```

Para ello se recomienda que existan identificadores estables para:

- `analysis_id`;
- `observation_id`;
- `market_data_id`;
- `dataset_id`;
- `instrument_id`;
- `source_id`;
- secuencia o posición temporal cuando aplique.

### [OBSERVADO]

FASE 1.6 ya conserva:

- `AnalysisResult.observation_ids`;
- `Hypothesis.supporting_analysis_ids`;
- `Signal.hypothesis_id`;
- provenance.

### [GAP]

Actualmente un `AnalysisResult` puede demostrar qué `observation_ids` utilizó, pero el contrato no proporciona todavía una representación histórica completa del dataset que originó cada observación.

### [DECISIÓN DEL CEREBRO]

No implementar todavía hashes, content-addressing, event sourcing o almacenamiento de datasets. Debe decidirse primero qué nivel de reproducibilidad requiere el proyecto.

---

# 12. Multi-asset

### [PROPUESTO]

El contrato debe permitir múltiples instrumentos sin introducir estrategia.

Ejemplos:

- BTC;
- ETH;
- otros activos.

Cada observación debe mantener su propia identidad de instrumento/símbolo.

### [DECISIÓN DEL CEREBRO]

BTC puede existir como:

- observación;
- contexto de mercado;
- componente de un dataset.

Eso no lo convierte automáticamente en:

- predictor;
- feature;
- señal;
- regla;
- activo preferido.

No se define ninguna relación estratégica BTC → otro activo en esta fase.

---

# 13. Multi-source

### [PROPUESTO]

La fuente debe ser una dimensión explícita del dato.

Metadata futura mínima:

- `source_id`;
- proveedor/venue;
- tipo de fuente;
- instrumento según esa fuente;
- versión/dataset;
- timestamp de recepción;
- referencia externa cuando exista;
- reglas de normalización aplicadas.

### [DECISIÓN DEL CEREBRO]

No seleccionar todavía Binance, Coinbase, CCXT ni ningún proveedor concreto.

El contrato debe poder representar varias fuentes sin codificar una fuente como verdad universal.

### [OBSERVADO]

La existencia de `venue` opcional en `MarketObservation` es un punto de partida, pero no sustituye un modelo completo de source/dataset.

---

# 14. Historical vs Real-Time

## Historical Data

### [PROPUESTO]

Debe priorizar:

- reproducibilidad;
- identidad de dataset;
- cobertura conocida;
- orden temporal;
- completitud;
- versión/inmutabilidad lógica;
- trazabilidad de origen;
- semántica de cierre.

## Live Market Observation

### [PROPUESTO]

Debe priorizar:

- recepción;
- disponibilidad;
- latencia;
- secuencia;
- datos parciales;
- reconexión/gaps;
- estado actual de la fuente.

## Garantías compartidas

Ambos deben compartir:

- identidad del instrumento;
- timestamps;
- provenance;
- calidad;
- distinción observed/derived;
- reglas anti-look-ahead.

### [DECISIÓN DEL CEREBRO]

Historical y Live pueden tener adaptadores distintos en el futuro, pero no deben producir semánticas incompatibles para el mismo concepto de evidencia.

---

# 15. Anti-lookahead

### [DECISIÓN DEL CEREBRO]

La garantía mínima debe ser estructural:

`available_at <= decision_at`

No basta con:

`observed_at <= decision_at`

También deben impedirse:

- consumo de registros recibidos posteriormente;
- uso de revisiones futuras del dataset;
- uso accidental de información posterior al corte;
- mezcla de datos de train/test sin frontera temporal;
- incorporación de features derivados de datos posteriores.

### [PROPUESTO]

Cada futura operación de análisis que consuma datos debe tener una referencia temporal de decisión y validar la disponibilidad de todos sus inputs.

FASE 1.6 ya demuestra este patrón con `AnalysisResult.from_observations()` y `EvidenceTimestamp`.

### [DECISIÓN DEL CEREBRO]

El futuro backtesting deberá incorporar aislamiento temporal de datasets, pero ese backtester no se diseña ni implementa aquí.

---

# 16. Contaminación train/test

### [PROPUESTO]

A nivel de contrato histórico, el sistema debe poder identificar:

- dataset;
- rango temporal;
- versión;
- partición o rol del dataset, si posteriormente se adopta esa noción;
- procedencia.

No debe asumirse que una partición es segura solamente porque está almacenada en otro archivo.

### [DECISIÓN DEL CEREBRO]

La definición concreta de train/test, validation, OOS y walk-forward queda fuera de esta fase.

---

# 17. Observed vs Derived

### [EXISTENTE]

FASE 1.6 establece:

- `OBSERVED`: dato externo observado;
- `DERIVED`: resultado calculado.

### [PROPUESTO]

Un dato derivado futuro debería conservar:

- su propio ID;
- tipo de derivación;
- timestamp de cálculo;
- decision timestamp;
- provenance;
- IDs de inputs;
- versión de la transformación cuando sea necesario.

### [DECISIÓN DEL CEREBRO]

Nunca presentar como observado:

- promedio;
- indicador;
- feature;
- score;
- estimación;
- inferencia.

La trazabilidad debe permitir reconstruir de qué observaciones procede un derivado.

---

# 18. Relación con componentes existentes

### [OBSERVADO]

`LiquidityModel` expresa disponibilidad de cantidad y completitud para un uso ligado a liquidez. No debe convertirse automáticamente en el modelo histórico de volumen.

`Reconciliation.Snapshot` compara posición, órdenes, fills y balances. No es un snapshot de mercado y no debe reutilizarse como tal.

`SQLiteIdempotencyLedger` es una barrera de idempotencia operacional. No es un ledger histórico de MarketData.

`ClockService` proporciona salud y offset temporal, pero no sustituye `available_at`.

`OrderConstraints` describe reglas de órdenes, no propiedades del dataset.

### [DECISIÓN DEL CEREBRO]

Mantener estas fronteras. No fusionar contratos solo porque comparten timestamps o conceptos de cantidad.

---

# 19. Gaps prioritarios

| Gap | Estado | Impacto futuro |
|---|---|---|
| OHLCV completo | [OBSERVADO] | Alto |
| Instrument identity | [OBSERVADO] | Alto |
| Source identity | [OBSERVADO] | Alto |
| Dataset identity/version | [OBSERVADO] | Alto |
| received_at | [OBSERVADO] | Alto |
| available_at común | [EXISTENTE parcialmente] | Alto |
| sequence | [OBSERVADO] | Medio/alto |
| quality state | [OBSERVADO] | Alto |
| completeness state | [OBSERVADO] | Alto |
| gap representation | [OBSERVADO] | Alto |
| duplicate/out-of-order semantics | [OBSERVADO] | Medio/alto |
| historical finality/versioning | [OBSERVADO] | Alto |
| reproducible dataset reference | [OBSERVADO] | Alto |
| historical/live distinction | [PROPUESTO] | Alto |
| multi-source identity | [PROPUESTO] | Alto |
| multi-asset identity | [PROPUESTO] | Medio/alto |
| train/test isolation contract | [PROPUESTO] | Futuro backtest |

---

# 20. Decisiones abiertas

## [DECISIÓN DEL CEREBRO]

Quedan deliberadamente sin resolver:

1. representación numérica exacta de OHLCV;
2. identidad canónica de instrumento;
3. esquema final de Source;
4. esquema final de Dataset;
5. política de versionado/corrección histórica;
6. semántica exacta de sequence;
7. taxonomía final de quality/completeness;
8. política de datos faltantes;
9. definición de "final" para datos históricos;
10. si `MarketObservation` se amplía directamente o referencia contratos auxiliares;
11. persistencia de datasets;
12. hashes/checksums y nivel de reproducibilidad;
13. política de normalización entre fuentes;
14. contrato específico para live streams;
15. contrato específico para historical batches.

Estas decisiones no deben ser tomadas silenciosamente por el Bot Obrero.

---

# 21. Riesgos

### R1 — Timestamp incorrecto

Usar `observed_at` como si fuera disponibilidad puede introducir look-ahead.

### R2 — Dataset incompleto tratado como completo

Un análisis puede producir resultados aparentemente válidos sobre datos con gaps no declarados.

### R3 — UNKNOWN convertido en cero

Puede fabricar artificialmente volumen, actividad o continuidad de mercado.

### R4 — Fuente no identificada

Dos datasets con valores diferentes podrían quedar indistinguibles.

### R5 — Corrección histórica silenciosa

Sobrescribir datos sin versionar puede destruir reproducibilidad.

### R6 — Duplicados/out-of-order

Pueden alterar agregaciones y análisis sin que el consumidor lo sepa.

### R7 — Candle sobredimensionado

Un único objeto que mezcle observación, dataset, calidad, fuente, estrategia y ejecución aumentaría acoplamiento y dificultaría evolución.

### R8 — Confusión entre MarketData y Financial State

Los datos observados no representan propiedad, balances, posiciones ni estado financiero canónico.

### R9 — BTC convertido en estrategia implícita

Permitir BTC como contexto no debe introducir reglas predictivas no autorizadas.

### R10 — Provenance insuficiente

Un `observation_id` sin referencia al dataset/fuente puede ser insuficiente para reproducir el resultado años después.

---

# 22. Alcance sugerido para futura FASE 1.7 de implementación

### [PROPUESTO]

La futura implementación debería limitarse inicialmente a contratos, por ejemplo:

1. contrato de identidad de instrumento;
2. contrato de fuente;
3. contrato de MarketData/OHLCV;
4. contrato de Dataset o DataSlice;
5. metadata de calidad/completitud;
6. secuencia y estado de cierre;
7. integración mínima con `MarketObservation`;
8. mapeo temporal a `EvidenceTimestamp`;
9. tests anti-look-ahead;
10. tests de UNKNOWN/gaps/incompletitud;
11. tests de provenance y trazabilidad.

### [DECISIÓN DEL CEREBRO]

No incluir en esa futura fase, salvo autorización expresa:

- clientes de exchanges;
- descarga de datos externos;
- Binance/CCXT u otro proveedor concreto;
- indicadores;
- Strategy Engine;
- Risk Engine;
- backtester;
- paper trading;
- trading real;
- Signal → OrderIntent;
- modificaciones de Canonical Financial State;
- modificaciones de ExecutionBoundary.

---

# 23. Lo que NO se decide en FASE 1.7.0

Esta auditoría no decide:

- RSI;
- MACD;
- medias móviles;
- momentum;
- mean reversion;
- trend following;
- scalping;
- swing;
- BUY/SELL;
- rentabilidad;
- activo preferido;
- frecuencia de trading;
- sizing;
- leverage;
- stop loss;
- take profit;
- parámetros de riesgo.

---

# 24. Criterio de aceptación para la futura implementación

### [PROPUESTO]

Una futura implementación de MarketData debería poder demostrar como mínimo:

```
dato
  ↓
instrumento identificado
  ↓
fuente identificada
  ↓
timestamp del dato
  ↓
received_at
  ↓
available_at
  ↓
quality/completeness
  ↓
provenance
  ↓
MarketObservation
  ↓
AnalysisResult
```

Y debería poder responder sin ambigüedad:

> ¿Qué dato fue utilizado?

> ¿De qué fuente vino?

> ¿A qué instrumento corresponde?

> ¿Cuándo ocurrió?

> ¿Cuándo se recibió?

> ¿Cuándo estuvo disponible?

> ¿Estaba completo?

> ¿Estaba cerrado/confirmado?

> ¿Qué dataset/version lo contiene?

> ¿Qué análisis lo consumió?

La ausencia de una respuesta verificable a cualquiera de estas preguntas deberá considerarse un gap de contrato o provenance, no algo que el consumidor deba adivinar.

---

# 25. Resultado de FASE 1.7.0

### [EXISTENTE]

- HEAD inicial: `0f4dd787f4441675e94ffba57aa3c982c0d7643c`
- branch: `main`
- repositorio: `eltiootaku01-hue/bot-cripto`

### [CREADO]

- `docs/PHASE1_7_0_MARKET_DATA_DECISION_PACK.md`

### [NO MODIFICADO]

No se modificaron:

- `bot_obrero/analysis_contracts.py`;
- `bot_obrero/data.py`;
- `bot_obrero/temporal.py`;
- `bot_obrero/reconciliation.py`;
- `bot_obrero/execution.py`;
- `bot_obrero/persistent_ledger.py`;
- tests;
- adapters;
- ExecutionBoundary;
- ExecutionOrchestrator;
- Risk/Murphy/Readiness;
- Signal;
- Hypothesis;
- AnalysisResult;
- MarketObservation.

### [CAMBIO DE CÓDIGO]

Cero.

### [TESTS]

La fase es documental. No se modificaron tests ni se necesitó ejecutar una nueva suite para validar código.

### [ALCANCE]

No se descargaron datos externos, no se crearon conectores, no se implementaron indicadores, backtesting, paper trading, Strategy Engine, Risk Engine ni trading.

---

# 26. Cierre

Este Decision Pack deja preparada la revisión del CEREBRO sin convertir propuestas en arquitectura aprobada.

La frontera recomendada permanece:

```
Market Data
    ↓
Market Observation
    ↓
Analysis
    ↓
Hypothesis
    ↓
Signal
    ↓
[frontera todavía cerrada]
    ↓
Execution
```

La futura infraestructura de datos debe reforzar la reproducibilidad y la temporalidad sin contaminar el estado financiero canónico ni abrir una ruta de ejecución.

**FASE 1.7.0 = AUDITORÍA + DISEÑO.**

**NO estrategia. NO ejecución. NO exchange. NO trading.**
