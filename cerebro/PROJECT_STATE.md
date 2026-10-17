# BOT-CRIPTO — ESTADO VERIFICADO DE CONTINUIDAD

**Actualizado:** 2026-10-17  
**Fuente de verdad:** repositorio GitHub y evidencia asociada al SHA exacto; este documento es una síntesis de continuidad, no un sustituto de la inspección del estado vivo.  
**Baseline de main al sincronizar esta continuidad (antes del commit documental):** `fd51e0dd1ff3f710680a98b685a6b9369c74a600`.

## 1. Estado operativo actual

- **Decisión económica vigente:** `NO_TRADE`. La investigación disponible no acredita una ventaja neta robusta y repetible.
- **LIVE:** no implementado/habilitado. No usar dinero real, claves privadas ni endpoints privados.
- **Product V2:** el cambio de PR #63 está fusionado en main (merge commit histórico `8bbd9fc9511690db171c163b4a978a07e8aeb222`). El escáner/backtest sigue siendo investigación; no convierte sus resultados en señales de ejecución.
- **PR #64 — PAPER runtime:** abierta y sin fusionar. HEAD observado durante la auditoría: `8e6ad980481c39dd1504481724e014384ac7187f`. Sus pruebas remotas recientes terminaron en éxito, pero eso no cierra los bloqueos de reconciliación ni la frontera de cuenta/riesgo.
- **PR #69 — investigación BTC/ETH V1:** abierta, draft, sin fusionar. Dictamen declarado: `NO_ROBUST_EDGE_FOUND / NO_TRADE`; el propio informe mantiene desconocida la temporalidad/finality de las observaciones y documenta solapamiento parcial con estudios anteriores.
- **PR #70 — BTC_ETH_STRATEGIES_V2:** abierta, draft, sin fusionar. Estado `DESIGNED / NOT RUN`. No asignar T0 ni iniciar evaluación hasta congelar código/configuración y superar 24 horas de captura continua validada.
- **PR #66 — recuperación del universo BTC/altcoins:** abierta, sin fusionar. Se mantiene bloqueada la reconstrucción de la lista histórica canónica mientras falte una fuente primaria que confirme exactamente el universo aprobado.

## 2. Bloqueos que gobiernan el siguiente paso

1. **Productor de señal→PAPER aún ausente.** El puente canónico `Signal → TradeProposal` está integrado, pero Product V2 sigue terminando en `NO_TRADE`; aún no se demuestra un ciclo operativo que adquiera velas públicas recientes de 4h cerradas, genere una `Signal` canónica y la entregue a PAPER.
2. **Cuenta y riesgo.** El snapshot completo de la cuenta PAPER se proyecta a `CanonicalAccountState`; antes de una nueva admisión se exige conciliación `apply=False`. Cualquier discrepancia permanece `UNKNOWN/BLOCKED`.
3. **Cotización y autorización.** `PaperQuoteEvidence` versionada/hash-bound viaja con los términos persistidos; el adaptador calcula el fill desde esa evidencia y la reconciliación comprueba que coincide con la evidencia referenciada por riesgo.
4. **Reconciliación económica B1-R.** Se integró `paper_economic_replay.py` y `paper_runtime_reconciliation.py`. Una prueba simula el fallo tras el commit del fill, reabre PAPER/reserva/binding/ledger y ejecuta dos reconciliaciones, verificando que no haya fills, débitos, consumos ni versiones duplicadas. Los archivos SQLite separados no son atómicos como conjunto: las reparaciones parciales siguen siendo reintentables y fail-closed.
5. **Captura prospectiva de mercado.** La captura continua de 24 h, integridad diaria, timestamp de primera recepción, reconexión y recuperación de huecos siguen pendientes antes de fijar T0 para la evaluación prospectiva V2.
6. **SQLite.** El adaptador PAPER y el ledger usan `PRAGMA journal_mode=DELETE`, con prueba automatizada. El caso público WAL-reset motivó el cambio; no hay evidencia de corrupción de BOT-CRIPTO.
7. **CI y mantenimiento.** Los workflows en main usan Actions first-party compatibles con Node 24. Los checks deben evaluarse por SHA; el éxito de un commit no valida automáticamente sus descendientes.

## 3. Política económica y de seguridad que se conserva

- El presupuesto de referencia de **10.000 ARS** no equivale a USDT. Sin ruta de conversión verificable, hora, fuente y costes, mantener `CONVERSION_UNKNOWN`.
- El ledger hipotético de investigación de **1.000 USDT** y la semilla PAPER propuesta de **31 USDT** no representan fondos reales ni deben confundirse con ARS.
- Product V2 declara costes modelados —incluida una hipótesis de spread completo de 10 bps, deslizamiento de 5 bps y comisión de 0,1 % por fill en ciertos backtests—; no son spread histórico medido ni tarifa individual garantizada.
- No promover estrategias, ajustar parámetros para forzar operaciones ni considerar un backtest como garantía de rentabilidad futura.
- Mantener `UNKNOWN` como bloqueo, sin reintentar efectos ambiguos ni aumentar exposición sin evidencia.
- Las fechas usadas como evidencia deben tener `tzinfo` y un `utcoffset()` real; un objeto de zona horaria sin offset no satisface el contrato de datetime consciente.

## 4. Evidencia CI verificada

- **Main merge commit:** `fd51e0dd1ff3f710680a98b685a6b9369c74a600`, PR #71 fusionada. GitHub Actions `tests` run [39069869061](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069869061) terminó `completed/success`; log exacto: **1561 passed in 10.11s**.
- **Punta de integración antes del merge:** `0d139afdf7719d86b7d938ef0cb2a2e8dac949dc`. Suite completa en [39069780762](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780762) y [39069776697](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069776697), ambas `success`. SMA E2E [39069780537](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780537), indicadores E2E [39069780522](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780522) y backtest público/Product V2 [39069780401](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780401), todos `completed/success` sobre ese mismo SHA.
- Estos E2E comprueban adquisición/backtests públicos y contratos relacionados; no son pruebas LIVE ni de rentabilidad futura.
- La decisión económica sigue siendo `NO_TRADE`; esta evidencia no habilita LIVE.

## 5. Enlaces de continuidad

- [PR #71 — integración PAPER reconciliada (mergeada)](https://github.com/eltiootaku01-hue/bot-cripto/pull/71)
- [PR #64 — rama PAPER original (cerrada como supersedida)](https://github.com/eltiootaku01-hue/bot-cripto/pull/64)
- [PR #66 — recuperación del universo BTC/altcoins](https://github.com/eltiootaku01-hue/bot-cripto/pull/66)
- [PR #69 — investigación BTC/ETH V1](https://github.com/eltiootaku01-hue/bot-cripto/pull/69)
- [PR #70 — protocolo prospectivo BTC/ETH V2](https://github.com/eltiootaku01-hue/bot-cripto/pull/70)
- [Informe de auditoría externa y riesgos](docs/audits/MAIN_EXTERNAL_TECHNICAL_AUDIT_2026-10-17.md)

---

## PRODUCT V2 — especificación histórica conservada

Product V2 utiliza la entrada `python -m bot_obrero.product_v2`, datos públicos Spot y backtests virtuales. Sus filtros/métricas históricos incluyen BTC regime, filtros de instrumento, liquidez/spread, correlación 30/90 días, beta 90 días, fuerza relativa y una partición fija de 60 días separada. El camino termina en `NO_TRADE`; no hay puente operativo demostrado desde el resultado del escáner.

Los datos de klines históricos no incluyen el spread histórico. Los escenarios de backtest deben etiquetar las hipótesis de spread/deslizamiento/comisión y utilizar los mismos supuestos para las estrategias y los benchmarks comparables. No inferir asequibilidad de ARS a partir de un ledger virtual en USDT.

## Regla de continuidad

Antes de cualquier cambio material, volver a verificar el HEAD vivo, ramas/PR abiertas, archivos afectados y checks asociados al SHA exacto. Registrar por separado `OBSERVED`, `TESTED`, `INFERRED`, `UNKNOWN` y `BLOCKED`. No actualizar esta memoria para declarar completada una fase sin evidencia.