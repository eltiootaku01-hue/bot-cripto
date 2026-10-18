# BOT-CRIPTO — ESTADO VERIFICADO DE CONTINUIDAD

**Actualizado:** 2026-10-18  
**Fuente de verdad:** repositorio GitHub y evidencia asociada al SHA exacto; este documento es una síntesis de continuidad, no un sustituto de la inspección del estado vivo.  
**HEAD de `main` observado en la verificación viva inmediatamente anterior a esta actualización:** `7cb6abc3d45207446e4b1adb70c000a945eaf8ef`. En ese SHA exacto, pytest pasó con **1.704 pruebas en cada Python 3.11, 3.12, 3.13 y 3.14** ([run 39158367733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158367733)); CodeQL terminó `success` ([run 39158367758](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158367758)) y pip-audit terminó `success` ([run 39158367771](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158367771)). Este commit sincroniza solo la documentación; después de escribirlo, los nuevos checks deberán verificarse en el SHA resultante. La issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) sigue abierta: captura real no aceptada, preparación **77/100**, V2 `DESIGNED / NOT RUN`, sin T0, `NO_TRADE`, sin LIVE.

## 1. Estado operativo actual

## Preparación demostrada hacia el objetivo >90%

- **Evaluación ponderada actual:** **77/100**, según [PROJECT_READINESS_V1_2026-10-17.md](../docs/audits/PROJECT_READINESS_V1_2026-10-17.md). No es un porcentaje de líneas de código ni una estimación de beneficio; puntúa capacidades con evidencia de prueba y de estrategia. El umbral >90 no está alcanzado.
- **CI histórico de main tras PR #87:** [run 39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044), `success`, **1604 pruebas aprobadas en 14.62 s**, SHA `43d2cd70b2a350cf2987210166df7711d932e41b`. CodeQL [39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033) y `pip-audit` [39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089) también son `success` en el SHA exacto. Los E2E de SMA/indicadores pasaron en la punta de PR #87.
- **Próximos gates de mayor prioridad:** ejecutar y validar una captura real continua de al menos 24 horas; extender la invariancia anti-lookahead a los artefactos del estudio anual BTC→ETH; y demostrar edge neto en un holdout independiente. La herramienta de captura ya está integrada, pero no cuenta como evidencia de haberla ejecutado. `NO_TRADE` se mantiene.

- **Decisión económica vigente:** `NO_TRADE`. La investigación disponible no acredita una ventaja neta robusta y repetible.
- **PR #67 — contrato de investigación BTC/ETH:** cerrado; el contrato y su auditoría de adquisición se preservaron en `docs/research/` como históricos. **PR #65** se cerró porque su handoff ETH describía un estado anterior y podía inducir a usar instrucciones obsoletas.
- **LIVE:** no implementado/habilitado. No usar dinero real, claves privadas ni endpoints privados.
- **Product V2:** el cambio de PR #63 está fusionado en main (merge commit histórico `8bbd9fc9511690db171c163b4a978a07e8aeb222`). El escáner/backtest sigue siendo investigación; no convierte sus resultados en señales de ejecución.
- **PR #64 — PAPER runtime:** cerrada como supersedida; el runtime PAPER y la reconciliación se integraron por PR #71 (`fd51e0dd1ff3f710680a98b685a6b9369c74a600`).
- **PR #69 — BTC/ETH V1:** cerrada como supersedida; código, reporte y workflow de replay están en `main`. El replay anual pasó en [run 39103827728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103827728), pero conserva `NO_ROBUST_EDGE_FOUND / NO_TRADE`; `available_at/finality` históricos siguen UNKNOWN.
- **PR #70 — BTC_ETH_STRATEGIES_V2:** cerrada como supersedida; el protocolo vigente está integrado en `docs/research/BTC_ETH_STRATEGIES_V2_PROTOCOL.md`, estado `DESIGNED / NOT RUN`. No fijar T0 hasta aceptar ambas capturas reales Kline+bookTicker de 24 h.
- **PR #66 — universo BTC/altcoins:** cerrada como supersedida después de preservar R1/R2 en `docs/audits/`; el universo canónico sigue `BLOCKED_UNIVERSE_DEFINITION` a falta de fuente primaria.

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


## Actualización de continuidad — PR #93 (2026-10-18)

- **Main de código:** `8c325d9899ae4a15b06240012b05473f2cd34824`; PR [#93](https://github.com/eltiootaku01-hue/bot-cripto/pull/93) fusionada.
- **Hallazgo corregido:** `bool` es subtipo de `int` en Python; por tanto JSON `true` podía compararse igual a versión de esquema `1` o a la primera secuencia. El validador ahora exige tipo entero exacto para la versión del manifiesto, configuración, envelope por registro y secuencia.
- **Regresiones:** 4 tests que alteran los valores JSON y, en las pruebas de JSONL, recalculan hashes exteriores para asegurarse de que se ejercita la validación de contrato.
- **CI post-merge:** [39100954733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954733) `success`, 1608 pruebas en 15.66 s; [CodeQL 39100954774](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954774) y [pip-audit 39100954741](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954741) `success`. CI y E2E exactos de la punta pre-merge también pasaron.
- La puntuación ponderada sigue **73/100**: integridad del formato mejorada no equivale a captura real ni a edge rentable. `NO_TRADE` se mantiene.


## Actualización de continuidad — PR #96 (18 de octubre de 2026)

- **Main funcional verificado:** `bb4cf093e4798a9ce80a770281a6003dfb4d2f9b`; PR [#96](https://github.com/eltiootaku01-hue/bot-cripto/pull/96) fusionada.
- **Hallazgo:** `replay_paper_economics()` ordenaba fills antes de validar las claves. Un `filled_at` o `client_order_id` con un tipo JSON inesperado podía arrojar `TypeError` y salir del contrato de replay seguro.
- **Corrección:** validar mapping, tipos de claves y timestamp con zona horaria antes de comparar; ordenar por instante UTC y luego por ID. Dos regresiones verifican salida `UNKNOWN` en vez de excepción.
- PR #96: [1610 pruebas antes del merge](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821969), [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821961), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821964), [SMA E2E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821960) e [indicadores E2E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821955): todos `success`.
- Main post-merge exacto: [1610 pruebas en 11.67 s](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918101), [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918092) y [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918074): todos `success`.
- La puntuación permanece **73/100**. El arreglo aumenta resiliencia, pero no acredita captura real de 24 h ni edge independiente; `NO_TRADE` sigue vigente.


## Actualización de mantenimiento y cierre — 2026-10-18

- **Cambios de mantenimiento ya en main:** commit funcional `fdb90440a89e4284ba425689c1c2ec177902a0cf`. CodeQL `init` y `analyze` usan el mismo SHA oficial de v4.39.0; Dependabot agrupa esas actualizaciones; `pyproject.toml` incorpora pytest 9.1.1 y setuptools 84.0.0.
- **CI exacto en ese SHA:** tests [39103016285](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016285), CodeQL [39103016289](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016289) y pip-audit [39103016266](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016266) terminaron `success`. Los PR #88, #89, #90 y #91 se cerraron como supersedidos tras dejar sus cambios en main; los comentarios de cierre conservan la evidencia y la causa de los fallos de CodeQL.
- **Dictamen económico:** se mantiene `73/100` y `NO_TRADE`. La matriz vigente solo asigna 5/20 a evidencia económica; su máximo aritmético actual es 85/100 mientras esa categoría no mejore. No declarar >90 o 100% hasta aceptar la captura prospectiva real y la evaluación independiente preregistrada, y demostrar las demás condiciones operativas requeridas.
- **Auditoría consolidada:** [PROJECT_FINALIZATION_AUDIT_2026-10-18.md](../docs/audits/PROJECT_FINALIZATION_AUDIT_2026-10-18.md), con incidentes públicos, cambios aplicados, CI por SHA, disposición de PR y gates de salida.


## Estado de construcción actualizado — 2026-10-18

- **Preparación documentada revisada:** **76/100** (+3 frente a 73): +1 por integridad temporal de quotes, +1 por el colector de quotes durable y +1 por validar en conjunto que la captura Kline y bookTicker tiene ≥86.400 s de solapamiento y un día UTC común. El endurecimiento de tipos JSON se cubre con regresiones, pero no añade puntos fuera de la categoría de integridad. No se asignan puntos económicos por pruebas sintéticas.
- **Último SHA probado tras la implementación de gracia:** `375009b96278b6ad44240ae7359f3c483958895b`: [1650 tests success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870878), [CodeQL success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870780), [pip-audit success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870782). Los commits documentales posteriores tienen ejecuciones independientes.
- **BookTicker:** `bot_obrero/binance_book_ticker.py` registra el bid/ask público, update ID, precio/cantidad, fuente, reloj de recepción y disponibilidad; no inventa un timestamp de evento del exchange cuando Binance no lo envía. `bot_obrero/prospective_book_ticker_capture.py` escribe JSONL/manifest/hash, usa memoria constante para comparar update IDs, no retiene lotes de eventos en RAM y ejecuta `validate-pair` sobre ambos manifiestos. La captura bookTicker añade por defecto 300 s de gracia (86.700 s de tiempo total) para absorber pequeñas diferencias entre terminales; el validador sigue exigiendo ≥86.400 s de solapamiento real.
- **Bloqueo operativo siguiente:** [issue #98 — ejecutar/aceptar captura real Kline + bookTicker de 24 h](https://github.com/eltiootaku01-hue/bot-cripto/issues/98).
- **Runbook actualizado:** `docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md` contiene los comandos para captura Kline y bookTicker, validación individual y conjunta. Las pruebas de 24 horas son sintéticas y prueban el software; ninguna captura real se ha ejecutado o aceptado.
- **V1:** replay histórico reproducible en main; salida `NO_ROBUST_EDGE_FOUND / NO_TRADE`. Los datos históricos conservan `available_at/finality` UNKNOWN. No convertir ese replay en una estrategia operable.
- **V2:** protocolo preregistrado en main; `DESIGNED / NOT RUN`. El paso real es ejecutar ambas capturas desde una máquina persistente, aceptar los dos manifiestos y pasar `validate-pair`. Solo después fijar T0 en un commit de preregistro y dejar correr el periodo futuro sin modificar parámetros o costes.
- **Universo original BTC/altcoins:** informes R1/R2 integrados, definición canónica bloqueada por falta de fuente primaria. No inferir símbolos.
- **Fallos y mitigaciones:** `docs/research/EXTERNAL_FAILURE_CASE_STUDIES.md` documenta casos upstream de SQLite/WAL, Binance LOT_SIZE/MARKET_LOT_SIZE, lookahead, GitHub Actions Node 24, CodeQL desincronizado, pytest, websocket-client y pip-audit, además de hallazgos locales de captura.
- **Bloqueo >90:** la rúbrica solo acredita 5/20 en evidencia económica, por lo que el máximo aritmético sigue siendo 85/100 hasta que se pruebe una ventaja neta independiente suficiente. La captura real y el holdout siguen pendientes; `NO_TRADE` sigue siendo la decisión correcta. No habilitar LIVE, claves privadas ni dinero real.
- **Auditoría de cierre:** [PROJECT_FINALIZATION_AUDIT_2026-10-18.md](../docs/audits/PROJECT_FINALIZATION_AUDIT_2026-10-18.md).


## Snapshot vivo de continuidad — 2026-10-18

- **Fuente de verdad:** la rama `main` y sus checks exactos, no los SHA históricos citados en secciones antiguas.
- **HEAD comprobado antes de esta actualización documental:** `5c058db7c96c09d6df36c29087c0f9b5991bf558`. Tras el informe documental añadido, el HEAD avanzará por un commit de documentación; no se deben atribuir automáticamente a ese commit los checks del padre.
- **Checks que pasaron en el SHA funcional verificado:** tests [39106093676](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39106093676), CodeQL [39106093663](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39106093663) y pip-audit [39106093739](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39106093739).
- **PR abiertas:** ninguna según la lectura viva de GitHub.
- **Bloqueo externo P0:** [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98), captura prospectiva real conjunta BTCUSDT/ETHUSDT Kline + bookTicker durante al menos 24 h, validación independiente y hashes preservados. No se han sustituido esos requisitos por resultados sintéticos ni se ha marcado la captura como realizada.
- **Preparación:** 77/100 según [rúbrica vigente](../docs/audits/PROJECT_READINESS_V1_2026-10-17.md). No es un porcentaje de código ni una predicción de ganancias. La categoría económica limita la puntuación a 85/100 hasta nueva evidencia; el objetivo >90 todavía no se ha alcanzado.
- **Decisión y límites:** `NO_TRADE`; sin LIVE, claves privadas, endpoints privados ni dinero real. El próximo aumento legítimo requiere captura aceptada, preregistro, periodo prospectivo independiente y evidencia económica neta robusta. Cualquier nueva prueba debe estar asociada al SHA exacto que la ejecutó.


## Actualización viva — orquestador de captura conjunta (18-10-2026)

- Se añadió `bot_obrero/prospective_capture_pair.py`, con comando recomendado `python -m bot_obrero.prospective_capture_pair capture --output-dir evidence/prospective-btc-eth-pair`. Ejecuta Kline y bookTicker en paralelo desde una sola terminal, alinea el arranque UTC, supervisa ambas ejecuciones, detiene ambas si una falla y acepta el par únicamente al pasar la validación independiente/conjunta. Guarda logs y `pair-manifest.json`.
- Runbook: [PROSPECTIVE_BTC_ETH_CAPTURE_V1.md](../docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md). Bloqueo registrado: [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98).
- CI verde sobre `b1a21381a54e64db87f89265bd351d9db0370a65`: tests [39108357014](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39108357014), **1655 pruebas**; CodeQL [39108356962](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39108356962); pip-audit [39108357017](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39108357017).
- **Preparación revisada: 77/100.** La mejora de un punto cubre coordinación/supervisión local probada, no operación prolongada real. Ninguna captura real se ha ejecutado o aceptado. `NO_TRADE` continúa; sin LIVE, claves privadas, endpoints privados ni dinero real.
- El techo aritmético de la rúbrica continúa en 85/100 hasta que mejore la evidencia económica, actualmente 5/20. Para superar 90 hará falta evidencia neta independiente en un holdout preregistrado; no se modifica la puntuación para simular avance.


## Actualización de continuidad — procedencia estricta de la captura (18-10-2026)

- **Commit funcional:** cec4e8e6dd58871a04dd1f1171eb985a6926ea37; tests (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109569250), 1661 pruebas; CodeQL (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109569231); pip-audit (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109569376), los tres success.
- El orquestador vincula el hash de configuración a HEAD y origin/main, la rama y la comprobación de fuente. Exige estar en la raíz del repo, en rama main, con HEAD == origin/main y sin archivos modificados/untracked en bot_obrero/ y pyproject.toml. La salida bloqueada persiste la causa y no abre sockets.
- **Puntuación sin cambio: 77/100.** Esta defensa mejora reproducibilidad y evita capturas con código local ambiguo, pero no añade evidencia económica o datos reales. Issue #98 permanece abierta hasta ejecutar la captura real Kline+bookTicker durante al menos 24 h aceptadas. NO_TRADE y la prohibición de LIVE se mantienen.


## Revalidación de procedencia de captura — 18-10-2026

En SHA 76e770cf86f02359a7bbd1353d1c5aea0094eeec, tests (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109859114) pasaron 1663 casos; CodeQL (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109859051) y pip-audit (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109859053) deben quedar en success en ese mismo SHA antes de finalizar.

El supervisor vuelve a comparar HEAD, origin/main, rama y source status inmediatamente antes de lanzar los sockets y cuando las capturas finalizan. Un cambio durante la espera UTC o durante la corrida impide aceptar la pareja. No hubo incremento de puntuación: permanece 77/100, porque este control no sustituye la captura real ni la validación económica prospectiva.

## Verificación de procedencia al finalizar la captura — 18-10-2026

- Se añadió en `tests/test_prospective_capture_pair.py` una regresión que simula ambos procesos de captura terminados correctamente, pero cambia el SHA de la fuente antes de aceptar los manifiestos.
- Resultado esperado y comprobado: la pareja termina `BLOCKED`, conserva el manifiesto, no invoca el validador de aceptación y no permite usar datos recogidos bajo una revisión de código distinta de la revisión inicial.
- SHA funcional probado: `33979f0f96afdd54189834cc64996fd2c220fb9a`. Tests: [run 39111442312](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39111442312), **1664 passed in 15.78s**; CodeQL: [run 39111442302](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39111442302), success; pip-audit: [run 39111442300](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39111442300), success. Los tres checks corresponden al mismo SHA.
- **Preparación sin cambio: 77/100.** La prueba cierra una brecha de cobertura de una guarda existente, pero no representa una captura real ni evidencia de edge. La issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) continúa abierta; `NO_TRADE` sigue vigente y LIVE continúa deshabilitado.


## Valoración neta en el motor virtual — 18-10-2026

- **Hallazgo local:** `bot_obrero/virtual_backtest.py` marcaba posiciones abiertas con `quote + cantidad × cierre` bruto mientras el benchmark buy-and-hold de Product V2 descontaba spread, slippage y comisión hipotética de salida. La auditoría diaria calculaba también el PnL no realizado bruto; eso podía subestimar drawdown y desalinear el PnL de la equity neta.
- **Corrección en main:** el motor calcula equity abierta al valor neto estimado de liquidación en cada cierre. Si la configuración no liquida al final, no crea un SELL ni registra la comisión hipotética como realizada. La auditoría diaria deriva PnL no realizado de esa misma equity neta.
- **Tests y seguridad sobre `939a862e01de598da392d687b790d83017a9321c`:** 1675 tests [run 39113220900](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220900), CodeQL [run 39113220869](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220869) y pip-audit [run 39113220777](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220777); los tres exitosos sobre el mismo SHA.
- **Resultado económico:** el replay V1 sigue `NO_ROBUST_EDGE_FOUND / NO_TRADE`. Los datos históricos no certifican `available_at` ni finality punto-en-tiempo; la captura real Kline+bookTicker sigue pendiente en [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98).
- **Preparación:** se conserva **77/100**. La corrección mejora la veracidad de métricas, pero no aporta evidencia independiente de rentabilidad. Sin LIVE, claves privadas, endpoints privados ni dinero real.


## Trigger del replay virtual anual en main — 18-10-2026

Se corrigió `.github/workflows/btc-eth-follow-research.yml`: antes solo reaccionaba a cambios en la rama histórica `feat/paper-operational-runtime-v1`, por lo que modificaciones del motor virtual en `main` no arrancaban este replay anual automáticamente. Ahora el workflow escucha `main` para los cambios relevantes de backtest, Product V2, su suite y el propio workflow. El artefacto se etiqueta explícitamente `HISTORICAL_EXPLORATORY`, `PROSPECTIVE_HOLDOUT=false` y señala que `available_at` histórico es UNKNOWN; el replay no podrá confundirse con la captura prospectiva ni cerrar issue #98. La ejecución exacta de main [run 39113863156](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113863156) terminó `success`, junto con 1675 tests [39113863132](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113863132), CodeQL [39113863057](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113863057) y pip-audit [39113863258](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113863258), los cuatro sobre SHA `977977f8ab5170cd8d17a44bc82586217c325e14`.

El estudio obtuvo 8.760 velas de evaluación entre `2025-04-16T09:00:00Z` y `2026-04-16T09:00:00Z`, con warm-up de 2160 barras y 3 folds de validación. Dataset SHA-256 `d6701552136b4d3e373dbe18a4e3b2e7e481159673a37082a1f342ff09454d08`; configuración SHA-256 `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`. El replay offline dio los mismos hashes y la misma decisión.

| Estrategia | BASE | STRESS_1 | STRESS_2 | Resultado |
|---|---:|---:|---:|---|
| `BASELINE` EMA/RSI | -26,8599 % | -35,9698 % | -54,8336 % | No pasa |
| `BTC_GATED` | -10,0802 % | -20,6412 % | -42,7490 % | No pasa |
| Cash | 0,0000 % | 0,0000 % | 0,0000 % | Referencia |
| Buy-and-hold BTC | -11,0290 % | -11,2511 % | -11,8261 % | Referencia |
| Buy-and-hold ETH | +48,5559 % | +48,1850 % | +47,2249 % | Referencia |

En BASE, `BTC_GATED` perdió capital y quedó muy por debajo de cash y de buy-and-hold ETH. Sus validaciones BASE fueron +2,9616 %, -6,4983 % y -2,4657 %; no son consistentes, y los tres folds STRESS_2 fueron negativos. Resultado: `NO_ROBUST_EDGE_FOUND / NO_TRADE`.

Clasificación del artefacto: `HISTORICAL_EXPLORATORY`, `PROSPECTIVE_HOLDOUT=false`. En el dataset histórico se registran `available_at=UNKNOWN` para 15.360 barras de cada símbolo; la identidad SHA prueba los bytes, no su disponibilidad al momento de la decisión. Este run no cierra issue #98, no fija T0 y no sustituye los 365 días prospectivos. El artifact `btc-eth-follow-365d-historical-977977f8ab5170cd8d17a44bc82586217c325e14` se conserva en Actions por 90 días, por lo que no se considera almacenamiento duradero permanente.


## Continuidad tras PR #99 — 2026-10-18

La punta de `main` `15d4f5ba3e1169ce35cfb08c0f79355ada316e4a` tiene 1.676 pruebas aprobadas, CodeQL y pip-audit en verde (runs 39116193628, 39116193746 y 39116193641, mismo SHA). PR #99 aisló una prueba de fallo de subproceso del estado Git remoto mutable; los tres workflows están verificados después del merge. Este cambio corrige determinismo de CI, no aporta observaciones nuevas del mercado ni mejora la evidencia económica. Captura real #98 pendiente, V2 `DESIGNED / NOT RUN`, `NO_TRADE`.


## Continuidad tras PR #101 — 2026-10-18

Se integró [PR #101](https://github.com/eltiootaku01-hue/bot-cripto/pull/101) en `main` como `3590947d6fceaebfc437cbc4d784d548693dff8e`. El problema era que dos procesos lanzados secuencialmente tenían cada uno objetivo de 86.400 s mientras la aceptación exigía 86.400 s de solapamiento real. Kline ahora recibe un guard adicional de 60 s, con objetivo total de 86.460 s; bookTicker permanece en 86.400 s y ambos conservan 300 s de gracia. La configuración y su hash registran los objetivos individuales, guard y mínimo de solapamiento. CI post-merge del SHA: 1.676 tests [39136102138](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39136102138), CodeQL [39136102076](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39136102076), pip-audit [39136102147](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39136102147), todos success. La preparación se mantiene en **77/100**; no hay datos de captura nueva ni edge económico demostrado. Issue #98 permanece abierta hasta que una máquina persistente produzca manifiestos reales aceptados; no fijar T0 y mantener `NO_TRADE`.


## Sincronización tras PR #103 — validación estricta del manifiesto Kline (2026-10-18)

- **SHA de código en main:** `6f2512f9dc6d0e00b202291606258c6894e84420`. PR #103: [tests 1.678/1.678](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39140269519), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39140269490) y [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39140269472) exitosos sobre el mismo SHA. La punta de PR #103 fue verificada además con E2E SMA e indicadores.
- El validador no acepta más cadenas como `"86400"` o `"86403.0"` para campos de duración; requiere un entero exacto para `target_duration_seconds` en manifest/configuración y un número JSON finito para `actual_elapsed_seconds`. Las regresiones mantienen coherente el hash del config para probar el contrato de tipos, no solo el hash.
- No se sube la puntuación: **77/100**, evidencia económica 5/20. La captura real de 24 h aún no está aceptada y el holdout prospectivo independiente no se ha ejecutado. Issue #98 abierta; `NO_TRADE`, sin T0, sin LIVE y sin fondos reales.


## Sincronización tras PR #105 — integridad estructural del manifiesto Kline (2026-10-18)

- **Main:** `2bf52675d7a8c26943a469f13dc2adbc7947bb77`, PR #105 fusionada.
- El validador Kline exige ahora `configuration.symbols` como lista de cadenas; `fatal_errors` debe existir, ser una lista y estar vacío; los metadatos `bytes` y `records` deben ser enteros JSON exactos; e `instrument.symbol` se valida antes de usar métodos de texto.
- Se añadieron nueve regresiones para listas malformadas, ausencia/null de `fatal_errors`, conteos en float y símbolos de instrumento con tipo incorrecto. La punta exacta de PR pasó 1.687 pruebas, CodeQL, pip-audit y ambos E2E públicos. Main post-merge pasó tests, CodeQL y pip-audit en SHA exacto.
- La preparación permanece **77/100**: no se otorgan puntos económicos por endurecer validación. Captura real #98 pendiente, protocolo V2 `DESIGNED / NOT RUN`, sin T0 y `NO_TRADE`.


## Sincronización tras PR #107 — tipos canónicos del manifiesto bookTicker (2026-10-18)

- **Main:** `47a16b80c9c75804479dbb506693ee328792f907`, PR #107 fusionada.
- El validador exige ahora que `configuration.target_duration_seconds` y `configuration.capture_grace_seconds` sean enteros JSON exactos y coincidan con los campos del manifiesto. Los contadores `records`, `duplicate_update_ids`, `out_of_order_update_ids`, `inter_event_gap_count` y `clock_regressions` en `symbols_summary` también exigen enteros exactos.
- Se añadieron siete regresiones, dos para configuraciones numéricas codificadas como floats y cinco para contadores de resumen. La punta exacta de PR pasó 1.694 pruebas, CodeQL, pip-audit y E2E SMA/indicadores; main post-merge también pasó tests, CodeQL y pip-audit.
- No se modifica el score: **77/100**. Sigue faltando la captura prospectiva real #98, el holdout independiente V2 está `DESIGNED / NOT RUN`, no se fija T0 y `NO_TRADE` permanece.


## Revalidación exacta de main — 2026-10-18

- **SHA inspeccionado antes de esta actualización documental:** `5b67db204569cdba9cfa96ad81b06c2eade15a1c`.
- **Tests:** 1.694 aprobados, [GitHub Actions 39142481052](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39142481052).
- **CodeQL:** success, [run 39142481038](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39142481038).
- **pip-audit:** success, [run 39142481017](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39142481017).
- La ejecución de tests no adjuntó artefactos de captura. La issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) permanece abierta porque los manifiestos reales Kline + bookTicker de una ventana común no están disponibles para aceptación.
- **No cambiar el gate:** no fijar T0 ni seleccionar una estrategia ganadora hasta aceptar el smoke real de ≥86.400 s de solapamiento y después completar el holdout independiente. `NO_TRADE` permanece.


## Continuidad verificada inmediatamente antes de la presente actualización documental — 2026-10-18

- **SHA de main observado antes de esta actualización:** 947eefcdb35da19d7a990b7b69fc98088c42cfb1.
- **Checks en ese SHA exacto:** [pytest](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39144538447) success (1.694 pruebas), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39144538441) success, [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39144538444) success.
- **Estado de PRs:** ninguna abierta en la consulta actual. **Estado de issue #98:** abierta/bloqueada, sin evidencia verificable de una captura real adjunta a la issue o a los checks consultados.
- **Preparación:** 77/100; NO_TRADE; V2 DESIGNED / NOT RUN; ningún T0 fijado. La puntuación no se aumenta por sincronizar documentación ni por tests sintéticos.
- **Restricciones infraestructurales:** los jobs de runners hospedados de GitHub tienen un límite de 6 horas; los artefactos y logs se retienen por defecto 90 días. Referencias oficiales: [límites](https://docs.github.com/en/actions/reference/limits) y [retención de artefactos](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts). Ejecutar la pareja Kline + bookTicker en una máquina persistente y conservar la evidencia anual en almacenamiento durable, no solo en artefactos temporales.
- **Siguiente paso verificable:** ejecutar python -m bot_obrero.prospective_capture_pair capture --output-dir evidence/prospective-btc-eth-pair en un clon limpio y actualizado de main, con Python/dependencias instalados y equipo sin suspensión; aceptar solo el pair-manifest.json final con status VALID, ≥86.400 s de solapamiento y hashes/verificadores correctos. Ante salida distinta de cero o estado BLOCKED, guardar logs y diagnosticar sin editar manifiestos para forzar aceptación.


## Continuidad posterior a la corrección del resumen bookTicker — 2026-10-18

- **SHA funcional observado:** `85e0aaf8080345630b35d7c6e1d4db0ed85291b0`. Incluye la regresión que evita que `maximum_inter_event_gap_seconds: true` satisfaga por igualdad numérica el valor recalculado de 1.0, y exige tipo `float` finito/no negativo antes de comparar la métrica persistida.
- **Matriz CI ampliada:** `.github/workflows/tests.yml` prueba Python 3.11, 3.12, 3.13 y 3.14. En ese SHA, los cuatro jobs pytest y `pip-audit` terminaron `success`; la consulta inicial de CodeQL seguía en progreso. Verificar siempre checks del SHA exacto antes de afirmar que están todos verdes.
- **Referencias upstream:** la documentación de Python explica el comportamiento booleano en comparaciones numéricas ([modelo de tipos](https://docs.python.org/3/reference/datamodel.html#the-standard-type-hierarchy)); pytest 9.1.1 incluye soporte oficial de Python 3.14 y corrige regresiones de parametrización/descubrimiento ([changelog oficial](https://docs.pytest.org/en/latest/changelog.html)); `websocket-client` 1.9.2 corrige una race de cierre en `WebSocketApp.close()` ([changelog del mantenedor](https://github.com/websocket-client/websocket-client/blob/master/ChangeLog)). Este último punto es un arreglo upstream de la dependencia, no un bug local confirmado: la adquisición de BOT-CRIPTO usa `websocket.create_connection`, no `WebSocketApp`.
- **Evaluación económica y operación:** matriz de preparación se mantiene en 77/100. No hay captura real aceptada para issue #98, no se ha fijado T0 para V2 ni existe un holdout anual ejecutado; mantener `NO_TRADE`. No hay dinero real ni LIVE.


## Snapshot CI verificado en main — SHA 61dc0695c3802deba36461480389eea1f16e9000 — 2026-10-18

- **Checks del SHA exacto `61dc0695c3802deba36461480389eea1f16e9000`:** suite [run 39157016778](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39157016778) `success`, 1.695 pruebas aprobadas en cada uno de los cuatro jobs; Python 3.11 [job 117762214489](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39157016778/job/117762214489), Python 3.12 [job 117762214298](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39157016778/job/117762214298), Python 3.13 [job 117762214520](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39157016778/job/117762214520) y Python 3.14 [job 117762214501](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39157016778/job/117762214501). `pip-audit` [run 39157016656](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39157016656) `success`; CodeQL [run 39157016780](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39157016780) `success`.
- **Corrección funcional incorporada en el ancestro `85e0aaf8080345630b35d7c6e1d4db0ed85291b0`:** el validador de bookTicker exige que `maximum_inter_event_gap_seconds` sea un float finito no negativo y rechaza un booleano que, por igualdad de Python, podría compararse como 1.0. La regresión está en `tests/test_prospective_book_ticker_capture.py`; CI pasó sobre el SHA exacto descrito arriba.
- **Workflow de pruebas:** ahora ejerce la matriz Python 3.11–3.14 porque `pyproject.toml` declara `requires-python >=3.11`; el test workflow conserva las Actions first-party fijadas por SHA.
- **Estado que no cambia:** preparación demostrada **77/100**, evidencia económica 5/20; issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) abierta sin pareja de manifiestos reales aceptada; V2 `DESIGNED / NOT RUN`, sin T0, sin holdout de 365 días, decisión `NO_TRADE`. El CI verde demuestra pruebas de software y auditoría de dependencias, no captura real ni edge económico.
- **Próximo bloqueo externo:** ejecutar el colector emparejado en una máquina persistente; aceptar únicamente `pair-manifest.json` con `status: VALID`, solapamiento real ≥86.400 s, día UTC común de 24 velas finales por activo y hashes verificados. Hasta entonces no cerrar la issue, no afirmar 100%, no declarar ganador ni habilitar LIVE.


## Herramienta de entrega de evidencia — commit ba9641c — 2026-10-18

- **Herramienta funcional añadida:** [commit `ba9641c`](https://github.com/eltiootaku01-hue/bot-cripto/commit/ba9641c463d9699015deab85c14781031f388575) añade `bot_obrero/prospective_capture_bundle.py` y `tests/test_prospective_capture_bundle.py`.
- **Objetivo:** convertir una corrida de captura ya aceptada en un ZIP con SHA-256 sidecar para transferencia; antes de empaquetar, revalidar la captura, hashes, solapamiento y día UTC común; rechazar status distinto de `VALID`, rutas inseguras, archivos `.tmp`, provenance cambiada y overwrite.
- **CI en el SHA funcional exacto:** 1.703 pruebas aprobadas en Python 3.11, 3.12, 3.13 y 3.14; `pip-audit` success. CodeQL seguía en curso cuando se registró la instantánea inicial; consultar siempre el check del SHA exacto.
- **Uso tras una captura real aceptada:** `python -m bot_obrero.prospective_capture_bundle create --run-dir evidence/prospective-btc-eth-pair/<pair-run-id>`. El resultado entrega ruta del ZIP y sidecar. La guía está en `docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md`.
- **No confundir:** las nuevas pruebas sintéticas validan el empaquetador, no la autenticidad de datos de mercado. La [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) sigue abierta y bloqueada hasta que los manifiestos y datos de una captura real se reciban y acepten.
- **Estado económico inalterado:** 77/100, evidencia económica 5/20, V2 `DESIGNED / NOT RUN`, sin T0 ni holdout, `NO_TRADE`, sin LIVE ni fondos reales.


## Continuidad técnica tras el endurecimiento de empaquetado — 281302d — 2026-10-18

- **SHA funcional verificado antes de esta actualización documental:** `281302deebdfcdf3b23734f651386fed35bf85bf`.
- **CI en ese SHA exacto:** [pytest](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158157860) success, 1.704 pruebas en cada versión Python 3.11, 3.12, 3.13 y 3.14; [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158157840) success; [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158157935) success.
- **Herramienta de transferencia de captura:** `bot_obrero/prospective_capture_bundle.py` revalida la pareja y produce ZIP + SHA-256 sidecar; rechaza manifests que no estén `VALID`, procedencia cambiada, rutas inseguras, symlinks, archivos temporales, y archivos ajenos al allowlist. La allowlist exige los dos manifiestos, cuatro JSONL y dos logs junto con `pair-manifest.json`.
- **Regresión de privacidad/alcance:** `tests/test_prospective_capture_bundle.py::test_bundle_refuses_unexpected_files_instead_of_packaging_them` confirma que un archivo ajeno no se comparte. Los fixtures siguen siendo sintéticos y no satisfacen la aceptación de mercado.
- **Siguiente gate:** ejecutar una captura real en PC persistente y compartir el paquete del directorio resultante. La issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) está abierta; no se dispone aún de manifiestos reales aceptados.
- **Dictamen económico:** preparación **77/100**; evidencia económica 5/20; BTC_ETH_STRATEGIES_V2 `DESIGNED / NOT RUN`, sin T0 y sin holdout prospectivo de 365 días; `NO_TRADE`; sin LIVE ni fondos reales.
