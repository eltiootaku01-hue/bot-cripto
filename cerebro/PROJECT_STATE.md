# BOT-CRIPTO — ESTADO VERIFICADO DE CONTINUIDAD

**Actualizado:** 2026-10-17  
**Fuente de verdad:** repositorio GitHub y evidencia asociada al SHA exacto; este documento es una síntesis de continuidad, no un sustituto de la inspección del estado vivo.  
**Último SHA con cambios de código/workflows verificado:** `43d2cd70b2a350cf2987210166df7711d932e41b` (PR #87: CodeQL, `pip-audit`, Dependabot y pytest parcheado; incorpora PR #86 anti-lookahead anual). El CI post-merge exacto de main [39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044) terminó `success`, 1604 pruebas en 14.62 s; CodeQL [39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033) y auditoría de dependencias [39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089) también finalizaron `success`. Este archivo es una instantánea de continuidad: antes de modificar código, verificar el HEAD vivo de `main`. La ejecución real de 24 h sigue pendiente.

## 1. Estado operativo actual

## Preparación demostrada hacia el objetivo >90%

- **Evaluación ponderada actual:** **73/100**, según [PROJECT_READINESS_V1_2026-10-17.md](../docs/audits/PROJECT_READINESS_V1_2026-10-17.md). No es un porcentaje de líneas de código ni una estimación de beneficio; puntúa capacidades con evidencia de prueba y de estrategia. El umbral >90 no está alcanzado.
- **CI actual de main tras PR #87:** [run 39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044), `success`, **1604 pruebas aprobadas en 14.62 s**, SHA `43d2cd70b2a350cf2987210166df7711d932e41b`. CodeQL [39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033) y `pip-audit` [39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089) también son `success` en el SHA exacto. Los E2E de SMA/indicadores pasaron en la punta de PR #87.
- **Próximos gates de mayor prioridad:** ejecutar y validar una captura real continua de al menos 24 horas; extender la invariancia anti-lookahead a los artefactos del estudio anual BTC→ETH; y demostrar edge neto en un holdout independiente. La herramienta de captura ya está integrada, pero no cuenta como evidencia de haberla ejecutado. `NO_TRADE` se mantiene.

- **Decisión económica vigente:** `NO_TRADE`. La investigación disponible no acredita una ventaja neta robusta y repetible.
- **LIVE:** no implementado/habilitado. No usar dinero real, claves privadas ni endpoints privados.
- **Product V2:** el cambio de PR #63 está fusionado en main (merge commit histórico `8bbd9fc9511690db171c163b4a978a07e8aeb222`). El escáner/backtest sigue siendo investigación; no convierte sus resultados en señales de ejecución.
- **PR #64 — PAPER runtime:** cerrada como supersedida; el runtime PAPER y la reconciliación se integraron por PR #71 (`fd51e0dd1ff3f710680a98b685a6b9369c74a600`).
- **PR #69 — investigación BTC/ETH V1:** abierta, draft, sin fusionar. Dictamen declarado: `NO_ROBUST_EDGE_FOUND / NO_TRADE`; el propio informe mantiene desconocida la temporalidad/finality de las observaciones y documenta solapamiento parcial con estudios anteriores.
- **PR #70 — BTC_ETH_STRATEGIES_V2:** abierta, draft, sin fusionar. Estado `DESIGNED / NOT RUN`. No asignar T0 ni iniciar evaluación hasta congelar código/configuración y superar 24 horas de captura continua validada.
- **PR #66 — recuperación del universo BTC/altcoins:** abierta, sin fusionar. Se mantiene bloqueada la reconstrucción de la lista histórica canónica mientras falte una fuente primaria que confirme exactamente el universo aprobado.

## 2. Bloqueos que gobiernan el siguiente paso

1. **Composición señal→riesgo→PAPER integrada con guardas explícitas.** PR #74 añade `bot_obrero/market_signal_paper_runtime.py`: solo pasa `PROPOSAL_READY` a `FinancialExecutionRuntime`, exige un `PaperExecutionAdapter` local, `RiskEvaluationContext` completo y enlazado a la misma señal/análisis/cuenta, cotización pública Binance Spot y evidencia de disponibilidad consistente. `NO_TRADE`, datos inválidos y errores previos bloquean; excepciones/salidas inciertas no se reintentan. El productor no mantiene por sí solo una captura 24 h ni valida edge económico.
2. **Cuenta y riesgo.** El snapshot completo de la cuenta PAPER se proyecta a `CanonicalAccountState`; antes de una nueva admisión se exige conciliación `apply=False`. Cualquier discrepancia permanece `UNKNOWN/BLOCKED`.
3. **Cotización y autorización.** `PaperQuoteEvidence` versionada/hash-bound viaja con los términos persistidos; el adaptador calcula el fill desde esa evidencia y la reconciliación comprueba que coincide con la evidencia referenciada por riesgo.
4. **Reconciliación económica B1-R.** Se integró `paper_economic_replay.py` y `paper_runtime_reconciliation.py`. Una prueba simula el fallo tras el commit del fill, reabre PAPER/reserva/binding/ledger y ejecuta dos reconciliaciones, verificando que no haya fills, débitos, consumos ni versiones duplicadas. Los archivos SQLite separados no son atómicos como conjunto: las reparaciones parciales siguen siendo reintentables y fail-closed.
5. **Captura prospectiva de mercado.** PR #78 integra `bot_obrero/prospective_capture.py`: persistencia JSONL evento a evento, fsync, hash por registro/archivo, manifest, reconexión con backoff, evento `serverShutdown`, coherencia de timestamps y validador independiente. **Solo el código y sus pruebas están verificados**; ninguna captura real de 24 h se ha ejecutado ni aceptado. No fijar T0 hasta que el manifest real quede `COMPLETED` y pase `validate`.
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

- **PAPER main merge commit:** `fd51e0dd1ff3f710680a98b685a6b9369c74a600`, PR #71; run [39069869061](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069869061) terminó `success`, 1561 pruebas.
- **Punta de integración antes del merge:** `0d139afdf7719d86b7d938ef0cb2a2e8dac949dc`. Suite completa en [39069780762](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780762) y [39069776697](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069776697), ambas `success`. SMA E2E [39069780537](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780537), indicadores E2E [39069780522](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780522) y backtest público/Product V2 [39069780401](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780401), todos `completed/success` sobre ese mismo SHA.
- Estos E2E comprueban adquisición/backtests públicos y contratos relacionados; no son pruebas LIVE ni de rentabilidad futura.

- **Productor de señal y finality:** PR #72 fusionada en `90bc281b70ec3a50fc2e4b24a37b6a8b037637f3`; en su SHA previo al merge, tests [39071436733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436733) y [39071432824](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071432824) terminaron `success` con 1574 pruebas; E2E SMA [39071436664](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436664), indicadores [39071436711](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436711) y backtest público [39071436661](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436661) terminaron `success`.
- **Procedencia de finality:** PR #73 fusionada en `99424107ccaafe1a0985bb956523fa342e66285c`. CI de la punta exacta [39071644011](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071644011) terminó `success`, **1576 passed in 10.04s**. El CI de main tras el merge [run 39071689923](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071689923) terminó `success`: 1576 pruebas aprobadas en 10.52s sobre `99424107ccaafe1a0985bb956523fa342e66285c`.
- La decisión económica sigue siendo `NO_TRADE`; esta evidencia no habilita LIVE.

## 5. Enlaces de continuidad

- [PR #71 — integración PAPER reconciliada (mergeada)](https://github.com/eltiootaku01-hue/bot-cripto/pull/71)
- [PR #72 — productor de señal de velas cerradas y finality (mergeada)](https://github.com/eltiootaku01-hue/bot-cripto/pull/72)
- [PR #73 — procedencia de fuente/finality fail-closed (mergeada)](https://github.com/eltiootaku01-hue/bot-cripto/pull/73)
- [PR #74 — integración de señal con riesgo/admisión/PAPER (mergeada)](https://github.com/eltiootaku01-hue/bot-cripto/pull/74)
- [PR #76 — invariancia de sufijo futuro anti-lookahead (mergeada)](https://github.com/eltiootaku01-hue/bot-cripto/pull/76)
- [PR #78 — captura prospectiva BTC/ETH durable (mergeada)](https://github.com/eltiootaku01-hue/bot-cripto/pull/78)
- [Protocolo y comandos de captura prospectiva](../docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md)
- [CI de main tras PR #78, 1593 pruebas](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076567728)
- [CI de main con pruebas anti-lookahead, 1580 aprobadas](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073489775)
- [CI post-merge de main, 1578 pruebas](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073000856)
- [PR #64 — rama PAPER original (cerrada como supersedida)](https://github.com/eltiootaku01-hue/bot-cripto/pull/64)
- [PR #66 — recuperación del universo BTC/altcoins](https://github.com/eltiootaku01-hue/bot-cripto/pull/66)
- [PR #69 — investigación BTC/ETH V1](https://github.com/eltiootaku01-hue/bot-cripto/pull/69)
- [PR #70 — protocolo prospectivo BTC/ETH V2](https://github.com/eltiootaku01-hue/bot-cripto/pull/70)
- [Informe de auditoría externa y riesgos](docs/audits/MAIN_EXTERNAL_TECHNICAL_AUDIT_2026-10-17.md)
- [Evaluación ponderada de preparación hacia >90%](docs/audits/PROJECT_READINESS_V1_2026-10-17.md)
- [Casos externos de fallos y mitigaciones](docs/research/EXTERNAL_FAILURE_CASE_STUDIES.md)

---

## PRODUCT V2 — especificación histórica conservada

Product V2 utiliza la entrada `python -m bot_obrero.product_v2`, datos públicos Spot y backtests virtuales. Sus filtros/métricas históricos incluyen BTC regime, filtros de instrumento, liquidez/spread, correlación 30/90 días, beta 90 días, fuerza relativa y una partición fija de 60 días separada. No se debe elevar su resultado a ejecución: si el escáner devuelve `NO_TRADE`, la nueva composición no crea propuesta ni inicia el runtime.

Los datos de klines históricos no incluyen el spread histórico. Los escenarios de backtest deben etiquetar las hipótesis de spread/deslizamiento/comisión y utilizar los mismos supuestos para las estrategias y los benchmarks comparables. No inferir asequibilidad de ARS a partir de un ledger virtual en USDT.

## Regla de continuidad

Antes de cualquier cambio material, volver a verificar el HEAD vivo, ramas/PR abiertas, archivos afectados y checks asociados al SHA exacto. Registrar por separado `OBSERVED`, `TESTED`, `INFERRED`, `UNKNOWN` y `BLOCKED`. No actualizar esta memoria para declarar completada una fase sin evidencia.

## Actualización de continuidad — PR #74 (2026-10-17)

- **Main:** `a00e78a634ea7bac4ad266a3ae7b3394c709b6e0`; PR #74 fusionada.
- **CI post-merge:** [39073000856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073000856), `success`, 1578 pruebas aprobadas en 10.30 s.
- **Cambio probado:** `market_signal_paper_runtime.py` compone la salida canónica de mercado/señal/propuesta con contexto, política de riesgo y runtime PAPER existente. El E2E usa cotización/evidencias sintéticas; no son datos de rendimiento en vivo.
- **Preparación ponderada revisada antes de PR #76:** 71/100 por el ciclo señal→riesgo→PAPER. La revisión actual suma un punto adicional por pruebas de invariancia a sufijo futuro en backtest y gate BTC→ETH; no se otorgan puntos por ventaja económica o captura prospectiva aún no demostradas.
- **Preparación actual:** 72/100; [CI post-merge PR #76](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073489775) pasó 1580 pruebas.
- **Bloqueos preservados:** `NO_TRADE`, verificación anti-lookahead de todo el reporte/artefactos BTC→ETH, 24 h de captura con integridad/finality/recuperación, supervisión sostenida y ausencia de atomicidad global entre SQLite independientes. Sin LIVE, claves privadas ni dinero real.


## Actualización anti-lookahead — PR #76 (2026-10-17)

- PR #76 se fusionó en `9e10c34566ed4e068c6360297d49ee6d4dc13e3f`. Añade una prueba que perturba solo las barras posteriores al periodo evaluado y exige que el resultado del backtest virtual no cambie; otra prueba altera el sufijo posterior al instante de decisión y exige que el gate histórico BTC→ETH preserve la misma decisión/motivo.
- CI de main para ese SHA: [run 39073489775](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073489775), `success`, **1580 pruebas en 8.21 s**. Los E2E SMA/indicadores y backtest público también aprobaron en el head de PR #76.
- Puntuación ponderada: **72/100** (antes 71). El punto adicional corresponde a esta defensa causal de regresión, no a edge económico demostrado.
- Aún no existe un ensayo de invariancia sobre todos los outputs/artefactos del estudio anual como ejecución completa, ni prueba de 24 h de captura prospectiva íntegra. `NO_TRADE` se mantiene.


## Actualización — PR #78, infraestructura de captura prospectiva (2026-10-17)

- Main: `92b8b50a0722eea68c435717f870ef361a2927e8`; PR #78 fusionada.
- CI post-merge [39076567728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076567728): `success`, 1593 pruebas aprobadas en 11.13 s.
- El colector persiste de forma incremental la pareja pública BTCUSDT/ETHUSDT 1h, mantiene hashes/manifiesto y rechaza duración insuficiente, velas faltantes/duplicadas/desordenadas, gaps por encima del umbral, regresiones de reloj y errores de persistencia.
- Preparación revisada: 73/100 (+1 por infraestructura implementada y probada). No se conceden puntos por captura prospectiva real, porque aún no existe artefacto real de 24 h aceptado; edge neto independiente sigue sin demostrarse.
- Próximo gate: ejecutar en una máquina persistente el comando descrito en `docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md`, conservar sus ficheros inmutables y validar el manifest. `NO_TRADE` permanece; sin LIVE, dinero real ni claves privadas.


## Sincronización final tras PR #79 (2026-10-17)

- Main: `a196651842a84cdfc31fde2f3230cd27ff61bf55`; [PR #79 fusionada](https://github.com/eltiootaku01-hue/bot-cripto/pull/79).
- CI post-merge del SHA exacto: [run 39076940903](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076940903), `success`, **1595 pruebas aprobadas en 10.39 s**.
- El validador de captura comprueba que inicio/fin de la corrida, horario programado, estados de vela, hashes y timestamps de recepción sean coherentes con los registros reales. Pruebas nuevas cubren vela con finality incoherente y manipulación del intervalo del manifest.
- Score de preparación: 73/100. No está acreditada una captura real de 24 horas ni un edge económico neto independiente. No declarar >90, no asignar T0, no abandonar `NO_TRADE` sin superar esos gates.


## Sincronización final tras PR #82 y #83 — 17 de octubre de 2026

- **Último SHA con cambios de código/workflows:** [`a07b8aac15970f3349f9de54f021cfbeb71e4e20`](https://github.com/eltiootaku01-hue/bot-cripto/commit/a07b8aac15970f3349f9de54f021cfbeb71e4e20). La PR #84 solo sincronizó documentación en `4a28961c0c0139e72455b626a8136d5381c155f0`; CI exacto [39079895257](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079895257) pasó 1598 pruebas en 10.94 s.
- **PR #82:** replay económico PAPER ahora reconstruye el payload durable de la intención, recalcula el hash canónico y vuelve a validar/cotejar la evidencia completa de cotización contra los campos del fill. Se rechazan `fill_count` booleanos/tipos inválidos. [CI de main post-merge #82](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079511218): success, 1598 pruebas en 11.85 s.
- **PR #83:** ocho workflows fijados a SHA completos de releases oficiales verificadas de `actions/checkout`, `actions/setup-python` y `actions/upload-artifact`. [CI completo post-merge exacto](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079694856): success, 1598 pruebas en 11.22 s; también se verificaron los E2E públicos en la punta de PR #83.
- **Preparación:** permanece en 73/100. No se adjudica puntos por auditorías/código sin nuevas evidencias operativas o económicas.
- **Bloqueos:** sin captura real de 24 h aceptada, sin holdout prospectivo independiente con ventaja neta robusta y sin prueba de invariancia sobre todos los artefactos del estudio anual. Mantener `NO_TRADE`, sin LIVE, endpoints privados, claves privadas ni dinero real.


## Aclaración de cabeza de código y cabeza documental

- PR #82 introdujo el cambio funcional PAPER (merge `6728f9097bb6093c980e758ee68f5222d1443f9b`); PR #83 añadió pinning inmutable a workflows (merge `a07b8aac15970f3349f9de54f021cfbeb71e4e20`). Ambos permanecen en main.
- PR #84 es documental únicamente, merge `4a28961c0c0139e72455b626a8136d5381c155f0`. Su CI exacto [39079895257](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079895257) terminó success con 1598 tests en 10.94 s.
- La referencia viva de main prevalece siempre sobre este resumen; no tomar el hash de un párrafo histórico como el HEAD actual sin consultar el repositorio.


## Actualización final — PR #86 y #87 fusionadas (18 de octubre de 2026)

- **Main funcional:** [`43d2cd70b2a350cf2987210166df7711d932e41b`](https://github.com/eltiootaku01-hue/bot-cripto/commit/43d2cd70b2a350cf2987210166df7711d932e41b).
- **PR #86:** amplía pruebas anti-lookahead para métricas y filas de fill del informe por ventana (evaluación + tres ventanas de validación; baseline y BTC-gated) al modificar únicamente el sufijo de precios posterior a cada ventana. También rechaza `schema_version: true`. CI antes de merge: [39080745206](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39080745206), success, 1603 pruebas; merge SHA `96295e6c59286ded396789b615e4f14ed3300516`.
- **PR #87:** añadió CodeQL para Python/Actions, `pip-audit`, Dependabot semanal y actualizó pytest a `9.0.3` o posterior en 9.0.x. Merge SHA `43d2cd70b2a350cf2987210166df7711d932e41b`. CI exacto de main: [tests 39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044) success, 1604 pruebas; [CodeQL 39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033) success; [pip-audit 39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089) success. E2E SMA e indicadores de la punta PR #87 también pasaron.
- **Hallazgo de seguridad tratado:** la primera ejecución de `pip-audit` encontró `PYSEC-2026-1845` en pytest 8.4.2; el mantenedor corrige CVE-2025-71176 en pytest 9.0.3. Tras actualizar, la auditoría declara `No known vulnerabilities found` para las dependencias auditables. El paquete local `bot-obrero` no está en PyPI y `pip-audit` lo identifica como no auditable en ese índice; su código propio se cubre con tests y CodeQL, sin que eso garantice ausencia de todos los defectos.
- **Preparación:** permanece en 73/100. La extensión anti-lookahead es sintética por ventana y no sustituye el dataset prospectivo ni la validación económica; CodeQL y el auditor de dependencias refuerzan el proceso, no la rentabilidad.
- **Gates que siguen abiertos:** captura real y validada de BTC/ETH durante 24 h; prueba de edge neto prospectivo en holdout independiente; operación/supervisión sostenida; revisar la atomicidad entre los archivos SQLite independientes. Mantener `NO_TRADE`, sin LIVE, endpoints privados, claves privadas ni dinero real.
