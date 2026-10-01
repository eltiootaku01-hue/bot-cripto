# FASE 1.6 — Contratos de Observación, Análisis, Hipótesis y Señales

## Alcance

Esta fase introduce solamente el lenguaje estructural para representar:

`MarketObservation → AnalysisResult → Hypothesis → Signal`.

Los contratos son inmutables, trazables y temporalmente verificables. No implementan una estrategia, indicadores, backtesting, performance, riesgo, exchange ni ejecución.

## Contratos

### MarketObservation

Representa un dato observado externamente. Contiene símbolo, venue opcional, tipo de observación, valores, timestamp de observación, timestamp de disponibilidad y provenance.

Su provenance debe ser `OBSERVED`. No representa estado financiero canónico.

### AnalysisResult

Representa un resultado derivado. Conserva los `observation_ids` utilizados, el tipo de análisis, sus valores, timestamps y provenance `DERIVED`.

`AnalysisResult.from_observations()` valida la disponibilidad temporal de cada observación mediante el `EvidenceTimestamp` existente. Una observación disponible después de `decision_timestamp` produce un error de look-ahead.

### Hypothesis

Representa una hipótesis de mercado, no una orden. Conserva las referencias a los análisis que la sustentan, dirección y horizonte como campos descriptivos generales, condiciones de invalidación, timestamps, provenance y expiración opcional.

No establece una estrategia ni parámetros de rentabilidad.

### Signal

Representa una señal derivada de una hipótesis. Conserva la referencia a `hypothesis_id`, dirección descriptiva, evidencia, timestamps, provenance, validez y expiración opcional.

La validez desconocida no se convierte silenciosamente en válida. Una señal expirada tampoco se considera válida.

## Observado vs. derivado

`ArtifactNature.OBSERVED` se reserva para observaciones externas.

`ArtifactNature.DERIVED` se reserva para resultados producidos a partir de otras evidencias.

Esto evita presentar un indicador, score u otro cálculo futuro como si fuera una fuente externa.

## Temporalidad

Los contratos nuevos reutilizan `bot_obrero.temporal.EvidenceTimestamp`. La regla de disponibilidad es:

`available_timestamp <= decision_timestamp`.

No se crea una segunda implementación paralela de timestamps.

## Trazabilidad

La cadena estructural queda:

`MarketObservation → AnalysisResult → Hypothesis → Signal`.

Cada etapa conserva identificadores de sus inputs relevantes. La fase no crea persistencia, event sourcing ni un ledger financiero.

## Límite de ejecución

No existe dependencia desde `Signal` hacia `OrderIntent`, `ExecutionBoundary` o un exchange.

Por diseño, esta fase termina en `Signal`.

## Fuera de alcance

- estrategia de trading;
- RSI, MACD, EMA, SMA, ATR, Bollinger u otros indicadores;
- BTC/multi-asset analysis;
- noticias/sentimiento;
- machine learning o LLM predictivo;
- backtesting, replay, walk-forward u OOS;
- paper/virtual trading;
- Risk Engine;
- PnL/performance;
- exchange o dinero real.

Las fases futuras deberán definir esos componentes por separado y conservar esta frontera.
