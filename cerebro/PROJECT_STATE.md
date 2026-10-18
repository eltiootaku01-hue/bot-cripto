# BOT-CRIPTO — ESTADO VERIFICADO DE CONTINUIDAD

**Actualizado:** 2026-10-18  
**Fuente de verdad:** repositorio GitHub y evidencia asociada al SHA exacto; este documento es una síntesis de continuidad, no un sustituto de la inspección del estado vivo.  
**Último SHA funcional con tests, CodeQL y auditoría de dependencias en verde:** `0913bbee0c6090bc182d9955bd711e4a5130ffca`. En él, [tests aprobaron 1645 casos](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436364), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436371) y [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436379) terminaron `success`. Ese SHA contiene captura bookTicker con memoria acotada, fsync por tiempo/lote, `validate-pair` para comprobar el solapamiento entre Kline y bookTicker y rechazo controlado de tipos JSON de evento inesperados. Los commits documentales posteriores se verifican por su propio SHA; la captura real Kline+bookTicker de 24 h y el holdout prospectivo siguen pendientes.

## 1. Estado operativo actual

## Preparación demostrada hacia el objetivo >90%

- **Evaluación ponderada actual:** **76/100**, según [PROJECT_READINESS_V1_2026-10-17.md](../docs/audits/PROJECT_READINESS_V1_2026-10-17.md). No es un porcentaje de líneas de código ni una estimación de beneficio; puntúa capacidades con evidencia de prueba y de estrategia. El umbral >90 no está alcanzado.
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
- **Último SHA funcional de código:** `0913bbee0c6090bc182d9955bd711e4a5130ffca`. CI exacto: [1645 tests success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436364), [CodeQL success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436371), [pip-audit success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436379). El HEAD documental posterior tiene sus propias ejecuciones y debe revalidarse antes de llamarlo completamente verde.
- **BookTicker:** `bot_obrero/binance_book_ticker.py` registra el bid/ask público, update ID, precio/cantidad, fuente, reloj de recepción y disponibilidad; no inventa un timestamp de evento del exchange cuando Binance no lo envía. `bot_obrero/prospective_book_ticker_capture.py` escribe JSONL/manifest/hash, usa memoria constante para comparar update IDs, no retiene lotes de eventos en RAM y ejecuta `validate-pair` sobre ambos manifiestos.
- **Bloqueo operativo siguiente:** [issue #98 — ejecutar/aceptar captura real Kline + bookTicker de 24 h](https://github.com/eltiootaku01-hue/bot-cripto/issues/98).
- **Runbook actualizado:** `docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md` contiene los comandos para captura Kline y bookTicker, validación individual y conjunta. Las pruebas de 24 horas son sintéticas y prueban el software; ninguna captura real se ha ejecutado o aceptado.
- **V1:** replay histórico reproducible en main; salida `NO_ROBUST_EDGE_FOUND / NO_TRADE`. Los datos históricos conservan `available_at/finality` UNKNOWN. No convertir ese replay en una estrategia operable.
- **V2:** protocolo preregistrado en main; `DESIGNED / NOT RUN`. El paso real es ejecutar ambas capturas desde una máquina persistente, aceptar los dos manifiestos y pasar `validate-pair`. Solo después fijar T0 en un commit de preregistro y dejar correr el periodo futuro sin modificar parámetros o costes.
- **Universo original BTC/altcoins:** informes R1/R2 integrados, definición canónica bloqueada por falta de fuente primaria. No inferir símbolos.
- **Fallos y mitigaciones:** `docs/research/EXTERNAL_FAILURE_CASE_STUDIES.md` documenta casos upstream de SQLite/WAL, Binance LOT_SIZE/MARKET_LOT_SIZE, lookahead, GitHub Actions Node 24, CodeQL desincronizado, pytest, websocket-client y pip-audit, además de hallazgos locales de captura.
- **Bloqueo >90:** la rúbrica solo acredita 5/20 en evidencia económica, por lo que el máximo aritmético sigue siendo 85/100 hasta que se pruebe una ventaja neta independiente suficiente. La captura real y el holdout siguen pendientes; `NO_TRADE` sigue siendo la decisión correcta. No habilitar LIVE, claves privadas ni dinero real.
- **Auditoría de cierre:** [PROJECT_FINALIZATION_AUDIT_2026-10-18.md](../docs/audits/PROJECT_FINALIZATION_AUDIT_2026-10-18.md).
