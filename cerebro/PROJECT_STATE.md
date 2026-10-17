# BOT-CRIPTO — ESTADO VERIFICADO DE CONTINUIDAD

**Actualizado:** 2026-10-17  
**Fuente de verdad:** repositorio GitHub y evidencia asociada al SHA exacto; este documento es una síntesis de continuidad, no un sustituto de la inspección del estado vivo.  
**Baseline observado al comenzar esta auditoría:** `ff05bb6d70845ba51784da2bceee0ecf99b3e11a` en `main`.

## 1. Estado operativo actual

- **Decisión económica vigente:** `NO_TRADE`. La investigación disponible no acredita una ventaja neta robusta y repetible.
- **LIVE:** no implementado/habilitado. No usar dinero real, claves privadas ni endpoints privados.
- **Product V2:** el cambio de PR #63 está fusionado en main (merge commit histórico `8bbd9fc9511690db171c163b4a978a07e8aeb222`). El escáner/backtest sigue siendo investigación; no convierte sus resultados en señales de ejecución.
- **PR #64 — PAPER runtime:** abierta y sin fusionar. HEAD observado durante la auditoría: `8e6ad980481c39dd1504481724e014384ac7187f`. Sus pruebas remotas recientes terminaron en éxito, pero eso no cierra los bloqueos de reconciliación ni la frontera de cuenta/riesgo.
- **PR #69 — investigación BTC/ETH V1:** abierta, draft, sin fusionar. Dictamen declarado: `NO_ROBUST_EDGE_FOUND / NO_TRADE`; el propio informe mantiene desconocida la temporalidad/finality de las observaciones y documenta solapamiento parcial con estudios anteriores.
- **PR #70 — BTC_ETH_STRATEGIES_V2:** abierta, draft, sin fusionar. Estado `DESIGNED / NOT RUN`. No asignar T0 ni iniciar evaluación hasta congelar código/configuración y superar 24 horas de captura continua validada.
- **PR #66 — recuperación del universo BTC/altcoins:** abierta, sin fusionar. Se mantiene bloqueada la reconstrucción de la lista histórica canónica mientras falte una fuente primaria que confirme exactamente el universo aprobado.

## 2. Bloqueos que gobiernan el siguiente paso

1. **PAPER no es todavía un ciclo operativo completo.** La rama de PR #64 no demuestra que una señal real de mercado se convierta en una `Signal` canónica y después en una `TradeProposal` económica completa a través de los contratos existentes.
2. **Cuenta PAPER y estado de riesgo.** El balance/fills persistidos por el adaptador no se han aprobado como una fuente única que alimente `CanonicalAccountState` junto al almacenamiento canónico de reservas. Cualquier discrepancia debe producir `UNKNOWN/BLOCKED`.
3. **Identidad de cotización.** La documentación de PAPER identifica que el quote se obtiene durante el envío, después de la autorización de riesgo; mientras el intent persistido no vincule la misma evidencia de cotización, no se puede afirmar que riesgo y fill usaron el mismo bid/ask.
4. **Reconciliación económica B1-R.** El replay de fills de PAPER valida la contabilidad de la cuenta local; por su propio contrato no reconcilia todavía reservas, bindings de ejecución y proyección del ledger de idempotencia. No tratarlo como reconciliación global.
5. **Captura prospectiva de mercado.** La prueba de humo continua de 24 h, integridad de velas, timestamps de primera recepción, manejo de reconexión y recuperación de huecos tienen que quedar probados antes de fijar T0 para los 365 días de V2.
6. **SQLite/WAL en PR #64.** El informe de casos externos de esa rama afirma que el adaptador usa journal rollback `DELETE`; el código inspeccionado en el HEAD de PR #64 aún contiene `PRAGMA journal_mode=WAL`. Alinear código y documentación y validar la versión efectiva de SQLite/concurrencia antes de fusionar.
7. **Actions en main.** Esta actualización alinea los workflows actuales con las versiones de first-party Actions que usan Node 24. La ejecución de CI sobre el SHA de este commit debe revisarse aparte; no se da por aprobada antes de existir ese resultado.

## 3. Política económica y de seguridad que se conserva

- El presupuesto de referencia de **10.000 ARS** no equivale a USDT. Sin ruta de conversión verificable, hora, fuente y costes, mantener `CONVERSION_UNKNOWN`.
- El ledger hipotético de investigación de **1.000 USDT** y la semilla PAPER propuesta de **31 USDT** no representan fondos reales ni deben confundirse con ARS.
- Product V2 declara costes modelados —incluida una hipótesis de spread completo de 10 bps, deslizamiento de 5 bps y comisión de 0,1 % por fill en ciertos backtests—; no son spread histórico medido ni tarifa individual garantizada.
- No promover estrategias, ajustar parámetros para forzar operaciones ni considerar un backtest como garantía de rentabilidad futura.
- Mantener `UNKNOWN` como bloqueo, sin reintentar efectos ambiguos ni aumentar exposición sin evidencia.
- Las fechas usadas como evidencia deben tener `tzinfo` y un `utcoffset()` real; un objeto de zona horaria sin offset no satisface el contrato de datetime consciente.

## 4. Evidencia CI observada antes de esta actualización

- Main baseline `ff05bb6d70845ba51784da2bceee0ecf99b3e11a`: GitHub Actions `tests`, run [38959108299](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/38959108299), `completed/success` (16-10-2026).
- PR #64 HEAD `8e6ad980481c39dd1504481724e014384ac7187f`: `tests` run [39063159426](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39063159426), y ejecuciones E2E [39063159437](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39063159437), [39063159453](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39063159453), [39063159493](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39063159493): todas `completed/success` en ese SHA.
- Estos resultados no son tests locales, no prueban reconciliación económica global, no habilitan LIVE y no validan automáticamente un SHA posterior.

## 5. Enlaces de continuidad

- [PR #64 — adaptador PAPER](https://github.com/eltiootaku01-hue/bot-cripto/pull/64)
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