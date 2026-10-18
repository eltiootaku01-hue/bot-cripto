# BOT-CRIPTO — Evaluación de preparación técnica y ruta hacia >90%

**Evaluación:** 2026-10-18  
**Composición señal→riesgo/PAPER:** `a00e78a634ea7bac4ad266a3ae7b3394c709b6e0`; **replay PAPER (PR #82):** `6728f9097bb6093c980e758ee68f5222d1443f9b`; **anti-lookahead (PR #86):** `96295e6c59286ded396789b615e4f14ed3300516`; **seguridad automatizada (PR #87):** `43d2cd70b2a350cf2987210166df7711d932e41b`; **última corrección del validador de captura (PR #93):** `8c325d9899ae4a15b06240012b05473f2cd34824`. CI post-merge exacto de main [39100954733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954733) pasó 1608 pruebas en 15.66 s; CodeQL [39100954774](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954774) y `pip-audit` [39100954741](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954741) finalizaron `success`. PR #93 también tuvo tests/E2E/CodeQL/audit en verde antes del merge.
**Actualización de puntuación:** 2026-10-18, revisada tras PR #86 (tests anti-lookahead por ventana) y PR #87 (seguridad automatizada y remediación de pytest). La puntuación sigue 73/100: son mejoras verificadas de la calidad del proceso, pero no producen evidencia operativa de 24 h ni ventaja económica independiente.
**Propósito del porcentaje:** preparación demostrada para un bot de investigación y PAPER; no es porcentaje de líneas de código, probabilidad de beneficio ni autorización para operar con dinero real.

## Dictamen

**Preparación operativa basada en evidencia: 73/100. El umbral >90 todavía no está alcanzado.**

El porcentaje usa pesos publicados y puntúa solo capacidades con evidencia observada. La decisión económica sigue siendo `NO_TRADE`. Un buen CI no acredita una estrategia rentable; y tener un colector probado no equivale a disponer de una captura prospectiva real, aceptada y continua de 24 horas.

## Matriz de aceptación ponderada

| Área | Peso | Puntaje | Evidencia para el puntaje | Brecha que impide puntaje completo |
|---|---:|---:|---|---|
| Integridad del repositorio y CI reproducible | 10 | 10 | PR #71–#87 integradas; CI post-merge exacto en `43d2cd70b2a350cf2987210166df7711d932e41b`: 1604 pruebas; E2E SMA/indicadores y CodeQL/pip-audit pasaron; Actions fijadas por SHA y Dependabot semanal | Seguir exigiendo CI por SHA y revisar/triagear alertas de CodeQL/dependencias |
| Adquisición pública y contratos temporales | 15 | 12 | Adaptadores Binance REST/WebSocket, validación de vela cerrada y continuidad, finality diferenciada por fuente y procedencia conservada | Finality REST derivada del reloj local; `available_at`/`finality` históricos pueden seguir UNKNOWN; falta captura prospectiva validada de 24 h |
| Análisis, backtest y defensa anti-lookahead | 15 | 14 | SMA/EMA/RSI canónicos y cálculo incremental; PR #76 cubre invariancia de backtest/gate; PR #86 añade métricas y fills invariantes por ventana para evaluación + 3 validaciones en baseline/BTC-gated al perturbar solo el sufijo posterior; suite exacta pasa 1604 pruebas | Falta validar sobre el dataset/artefactos reales completos del estudio anual y con evidencia temporal prospectiva; los tests nuevos usan datos sintéticos |
| Evidencia económica de estrategia | 20 | 5 | Backtests, benchmarks cash/buy-and-hold y evaluación walk-forward están implementados; el resultado conserva honestamente `NO_TRADE` | Las estrategias evaluadas perdieron dinero neto o no demostraron robustez; falta una validación independiente que justifique avance |
| Ciclo mercado→Signal→TradeProposal→riesgo→PAPER | 15 | 14 | PR #74 compone la salida de la pipeline canónica con contexto de riesgo completo, snapshot PAPER persistido, admisión/reserva y runtime PAPER existente; E2E sintético cubre fill y conciliación. `NO_TRADE` no invoca el runtime | El caller aún debe proporcionar la observación pública de libro, context factory, política y evidencia de readiness; no hay captura sostenida de producción y la estrategia no está validada económicamente |
| Persistencia económica y recuperación PAPER | 15 | 14 | Quote versionado/hash-bound; replay recalcula el hash de intención y revalida la evidencia original; conciliación de cuenta/reserva/binding/ledger; recuperación tras commit y dos reconciliaciones idempotentes; suite main exacta pasa 1604 pruebas | Los archivos SQLite independientes no forman una transacción global. Reparación parcial debe seguir en UNKNOWN/BLOCKED hasta conciliar |
| Operación sostenida, supervisión y captura prospectiva | 10 | 4 | PR #78 agrega captura incremental durable de BTC/ETH, backoff de reconexión, gestión de `serverShutdown`, manifest/hash y validador independiente, con cobertura sintética en CI | Aún no se ha ejecutado/aceptado una captura real continua de 24 h, ni demostrado alertas y supervisión operativa o aceptación del estudio nuevo completo |
| **TOTAL** | **100** | **73** | | |

### Cómo interpretar la cifra

- **73/100** es una puntuación de preparación demostrada bajo la matriz anterior, no una estimación del trabajo de programación restante.
- **1.604 pruebas aprobadas** en main `43d2cd70b2a350cf2987210166df7711d932e41b` (run [39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044)); CodeQL [39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033) y pip-audit [39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089) también pasan. Esta evidencia no demuestra que toda situación operativa esté cubierta ni que la estrategia sea rentable.
- El objetivo >90 debe tratarse como un *release gate*, no como una meta de apariencia. No se dan puntos por una función solo diseñada o por una prueba que no se ejecutó.
- `NO_TRADE` no es un defecto que deba quitarse para aumentar el porcentaje. Es el bloqueo correcto mientras falte evidencia económica.

## Hallazgos priorizados y soluciones

### P0 — Estrategia robusta, sin anticipación y en un periodo nuevo
**Condición:** el estado económico permanece `NO_TRADE`. No activar EMA/RSI ni BTC-gated solo porque el backtest sea reproducible.

**Criterios de salida:**
1. Congelar estrategia, parámetros, dataset, costes y benchmarks antes de mirar el holdout.
2. Evaluar frente a cash y buy-and-hold con costes idénticos; separar evaluación y walk-forward no solapado.
3. Cambiar sufijos futuros y demostrar que no cambian señales, indicadores ni fills anteriores al corte. **Parcialmente cubierto por PR #76:** backtest virtual con `evaluation_end` y gate BTC→ETH en un índice de decisión permanecen idénticos al perturbar solo el sufijo futuro. Falta extender la propiedad al reporte/artefactos de la ejecución anual completa.
4. Registrar resultados netos, drawdown, fees, spread/slippage modelados y muestras completas/incompletas.
5. Aceptar avance solo si los umbrales económicos definidos antes de la corrida se cumplen en el periodo independiente; si no, mantener `NO_TRADE`.

**Por qué:** Freqtrade documenta que cargar y calcular todo el dataframe de una vez puede filtrar velas futuras a la señal. El análisis anti-lookahead dedicado detecta cambios en indicadores, entradas y salidas entre backtests base y aislados: [Freqtrade — Lookahead analysis 2026.5](https://docs.freqtrade.io/en/2026.5/lookahead-analysis/).

### P0 — Composición de señal con el runtime financiero PAPER
**Estado:** completado en PR #74. `bot_obrero/market_signal_paper_runtime.py` conecta `generate_market_signal` con el `FinancialExecutionRuntime` existente solo ante `PROPOSAL_READY`; valida señal y propuesta enlazadas, análisis canónico, cuenta PAPER persistida, contexto completo, book ticker público Binance Spot y `AvailabilityBinding`. Requiere el adaptador local `PaperExecutionAdapter` y no tiene fallback a LIVE. El E2E sintetiza el recorrido señal→propuesta→RiskEngine→autorización/admisión/reserva→fill PAPER→reconciliación; el caso `NO_TRADE` no inicia runtime.

**Brecha residual:** el módulo es un punto de composición invocable, no un servicio de captura permanente. El caller aún debe aportar el context factory, book ticker público, política de riesgo y readiness evidence. Deben validarse en una fase separada los errores de disponibilidad/latencia y las condiciones operativas sostenidas. No ejecutar con dinero real ni habilitar LIVE.

### P0 — Ejecutar y aceptar la captura prospectiva real de 24 h antes de asignar T0 al estudio nuevo
**Infraestructura:** PR #78 integra `bot_obrero/prospective_capture.py`, con captura incremental JSONL, fsync, hash de configuración/registro/archivo, manifiesto, reconexión con backoff, clasificación de `serverShutdown`, checks temporales y validador que vuelve a comprobar el dataset. Fuente aplicada: [Binance Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) describe el límite de 24 h por conexión y el evento `serverShutdown`; [CCXT Manual](https://docs.ccxt.com/es/docs/manual) explica el manejo diferenciado de errores de transporte y rate limits.

**Estado:** código y pruebas CI integrados; **la captura real no se ha ejecutado ni aceptado todavía**. No marcar como hecho un dataset sintético ni usar una prueba de CI como evidencia de 24 h.

**Criterios de salida:** seguir [docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md](../PROSPECTIVE_BTC_ETH_CAPTURE_V1.md), ejecutar desde una máquina persistente durante al menos 86.400 segundos, requerir una jornada UTC con 24 velas finales de cada símbolo y ausencia de huecos, duplicados finales, desorden, regresiones de reloj y gaps de entrega por encima de política; después ejecutar el comando `validate` y registrar los hashes inmutables. No backdatear disponibilidad ni fijar T0 anual antes de que el manifest real sea `COMPLETED` y la validación independiente pase.

### P1 — Conservación de SQLite y recuperación de fallos
La integración PAPER usa rollback journal `DELETE` y testea el modo efectivo. SQLite describió una corrupción poco frecuente bajo condiciones de concurrencia de varias conexiones en WAL y corrigió el WAL-reset bug en SQLite 3.51.3; el riesgo descrito depende de ese patrón y no es prueba de que BOT-CRIPTO sufriera corrupción: [SQLite — WAL-reset bug](https://www.sqlite.org/wal.html#walresetbug), [SQLite 3.51.3](https://www.sqlite.org/releaselog/3_51_3.html). Mantener `DELETE` es el mitigador actual; si se vuelve a WAL, fijar primero la versión SQLite corregida y probar concurrencia y recuperación.

No prometer atomicidad entre bases SQLite separadas. Ante un estado parcial: `UNKNOWN/BLOCKED`, sin nuevo envío; reconciliar proyecciones idempotentemente.

### P1 — Mantener los workflows de GitHub Actions actualizados
GitHub retiró Node 20 de los runners el 23 de septiembre de 2026 y los JavaScript actions pasaron a Node 24: [GitHub Changelog](https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/). Main ya usa versiones first-party compatibles con Node 24; comprobar el CI del SHA exacto en cada cambio.

### P1 — Fills simulados frente al precio real
QuantConnect publicó en junio de 2026 una corrección a LEAN para impedir que órdenes sobre barras horarias/diarias se ejecutaran contra el cierre anterior ya obsoleto, porque podía inflar los resultados de backtest: [QuantConnect — Closing the stale-price loophole](https://www.quantconnect.com/announcements/20949/closing-the-stale-price-loophole-on-daily-and-hourly-data/p1). La nueva composición PAPER rechaza cotizaciones posteriores a la decisión, fuera de orden o caducadas; los costes siguen siendo hipótesis y no se extrapolan a rentabilidad real.

## Evidencia GitHub del estado que se puntúa

- Integración PAPER y reconciliación: [PR #71 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/71).
- Captura prospectiva durable (sin claim de captura real ya realizada): [PR #78 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/78); CI de main [39076567728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076567728), `success`, 1593 pruebas.
- Endurecimiento del validador y sincronización: [PR #79 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/79); CI de main [39076940903](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076940903), `success`, 1595 pruebas en 10.39 s.
- Composición canónica señal→riesgo→PAPER: [PR #74 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/74), CI de main [39073000856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073000856), `success`, 1578 pruebas en 10.30 s.
- Defensa anti-lookahead suffix invariance: [PR #76 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/76), CI de main [39073489775](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073489775), `success`, 1580 pruebas en 8.21 s; SMA E2E [39073419193](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073419193), indicadores E2E [39073419196](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073419196) y backtest público [39073419200](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073419200), todos `success` en la punta de PR #76.
- CI de main tras el merge: [run 39069869061](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069869061), `success`, 1561 pruebas.
- CI de punta de integración: [tests 39069780762](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780762), [SMA 39069780537](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780537), [indicadores 39069780522](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780522), [backtest/Product V2 con datos públicos 39069780401](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780401): todos `success` en `0d139afdf7719d86b7d938ef0cb2a2e8dac949dc`.
- CI de la punta PR #72: [tests 39071436733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436733) y [39071432824](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071432824), ambas `success`, 1574 pruebas; E2E SMA [39071436664](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436664), indicadores [39071436711](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436711) y backtest público [39071436661](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071436661), todos `success`.
- CI de la punta PR #73: [run 39071644011](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071644011), `success`, **1576 passed in 10.04s**. Main después del merge de PR #73: run [39071689923](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071689923), `success`, 1576 pruebas en 10.52s sobre `99424107ccaafe1a0985bb956523fa342e66285c`.
- CI de la sincronización documental con la puntuación 68/100: run [39071775954](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071775954), `success`, 1576 pruebas en 10.18s sobre `fd4677828854660e89b773fcc57594fd7e57af8e`.
- Estado de continuidad: [PROJECT_STATE.md](../../cerebro/PROJECT_STATE.md).
- Auditoría de riesgos y addendum B1-R: [MAIN_EXTERNAL_TECHNICAL_AUDIT_2026-10-17.md](./MAIN_EXTERNAL_TECHNICAL_AUDIT_2026-10-17.md).
- Casos externos y mitigaciones: [EXTERNAL_FAILURE_CASE_STUDIES.md](../research/EXTERNAL_FAILURE_CASE_STUDIES.md).

## Límites de seguridad

La puntuación no autoriza trading real. No habilitar LIVE, claves privadas, endpoints privados ni operaciones con dinero real. El próximo incremento de preparación debe venir de evidencia temporal/económica nueva, pruebas reproducibles y un ciclo PAPER seguro, nunca de relajar `NO_TRADE`.


## Actualización de revisión — 2026-10-17: productor de señal y procedencia

PR #72 integró `bot_obrero/market_signal_pipeline.py`, que produce una `Signal` canónica o una propuesta opcional desde velas 4h cerradas; no ejecuta órdenes y no prueba edge económico. PR #73 añadió rechazo explícito de fuentes ajenas a Binance Spot REST/WebSocket y registra cómo se obtuvo la finality de cada vela. En WebSocket, la base es el campo oficial `k.x`; en REST, la etiqueta identifica una inferencia local basada en `received_at >= candle_end`, no una bandera de Binance.

El puntaje se actualiza de 64 a 68 por estas capacidades implementadas y probadas. **La meta >90 no se alcanza** hasta validar una estrategia en holdout prospectivo, terminar 24 horas de captura continua verificable y conectar la señal a la admisión financiera/PAPER completa bajo riesgo. El estado global sigue en `NO_TRADE`.


## Actualización tras PR #74 — composición de señal con PAPER (2026-10-17)

PR #74 quedó fusionada en [`a00e78a634ea7bac4ad266a3ae7b3394c709b6e0`](https://github.com/eltiootaku01-hue/bot-cripto/commit/a00e78a634ea7bac4ad266a3ae7b3394c709b6e0). El CI post-merge de main [run 39073000856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073000856) terminó `success`, **1578 pruebas aprobadas en 10.30 s**. En la punta de PR #74 también terminaron `success` [SMA E2E 39072946681](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39072946681) y [indicadores E2E 39072946684](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39072946684).

La puntuación aumenta de 68 a **71/100**: +3 en el ciclo mercado→Signal→TradeProposal→riesgo→PAPER por una composición implementada y E2E sintético verificado. No aumentan los puntos de evidencia económica ni captura prospectiva. Siguen pendientes 24 horas de captura íntegra, prueba anti-lookahead/sufijo futuro del estudio BTC/ETH y validación neta independiente. `NO_TRADE` se mantiene.


## Actualización de preparación tras PR #76 — 17 de octubre de 2026

La PR #76 añade dos regresiones anti-lookahead: (1) el resultado de `run_virtual_backtest` permanece igual al modificar precios estrictamente posteriores al periodo definido por `evaluation_end`; (2) el gate histórico BTC→ETH conserva decisión y motivo cuando se cambia el sufijo posterior al índice de decisión, manteniendo igual el prefijo. Estas pruebas evidencian una propiedad causal para esas rutas; no son una verificación exhaustiva de cada salida/artefacto anual.

PR #76 mergeada en [`9e10c34566ed4e068c6360297d49ee6d4dc13e3f`](https://github.com/eltiootaku01-hue/bot-cripto/commit/9e10c34566ed4e068c6360297d49ee6d4dc13e3f); CI post-merge main [39073489775](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073489775): `success`, **1580 pruebas aprobadas en 8.21 s**. E2E SMA, indicadores y backtest público en la punta PR #76 también finalizaron `success`.

La cifra ponderada aumenta de **71 a 72/100** (+1 en análisis/defensa anti-lookahead). Siguen bloqueados: 24 horas de captura prospectiva con recuperación/integridad y procedencia temporal, validación neta de estrategia en holdout independiente y verificación de los resultados del estudio BTC→ETH completo. Decisión económica: `NO_TRADE`.


## Actualización tras PR #78 — infraestructura de captura prospectiva (17 de octubre de 2026)

PR #78 se fusionó en [`92b8b50a0722eea68c435717f870ef361a2927e8`](https://github.com/eltiootaku01-hue/bot-cripto/commit/92b8b50a0722eea68c435717f870ef361a2927e8). El CI post-merge de main [39076567728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076567728) terminó `success`, con **1593 pruebas aprobadas en 11.13 s**. En el head de PR #78 también pasaron [SMA E2E 39076551987](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076551987), [indicadores E2E 39076551978](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076551978) y [backtest público 39076552023](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076552023).

El score aumenta de **72 a 73/100** (+1 en operación/captura por infraestructura persistente y verificable). No se agregan puntos por haber capturado datos en tiempo real: una ejecución real de 24 h sigue pendiente. `NO_TRADE` sigue vigente; la rentabilidad neta independiente y los artefactos completos anti-lookahead anual continúan bloqueados.


## Última verificación después de PR #79 — 17 de octubre de 2026

Main está en [`a196651842a84cdfc31fde2f3230cd27ff61bf55`](https://github.com/eltiootaku01-hue/bot-cripto/commit/a196651842a84cdfc31fde2f3230cd27ff61bf55). El CI post-merge exacto [39076940903](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076940903) concluyó `success`, con **1595 pruebas en 10.39 s**. PR #79 endurece la validación de la captura prospectiva y sus pruebas negativas.

La matriz permanece en **73/100**. La infraestructura de captura está implementada y probada, pero no se ha ejecutado una captura real completa de 24 horas; la validación neta independiente de estrategia y la invariancia de todos los artefactos del estudio anual permanecen pendientes. Por estas razones no se declara >90 y `NO_TRADE` continúa.


## Actualización de evidencia — PR #82 y #83 (17 de octubre de 2026)

- [PR #82 — replay económico PAPER con evidencia hash-bound verificada](https://github.com/eltiootaku01-hue/bot-cripto/pull/82), merge SHA `6728f9097bb6093c980e758ee68f5222d1443f9b`. El CI post-merge [39079511218](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079511218) pasó **1598 pruebas en 11.85 s**. Regresiones nuevas rechazan un bid que difiere de la intención hasheada y un `fill_count=True`.
- [PR #83 — pinning inmutable de GitHub Actions](https://github.com/eltiootaku01-hue/bot-cripto/pull/83), merge SHA `a07b8aac15970f3349f9de54f021cfbeb71e4e20`. El CI de main exacto [39079694856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079694856) pasó **1598 pruebas en 11.22 s**; SMA, indicadores y backtest público de la punta de PR también pasaron.
- **Score verificado: 73/100, sin incremento por estas dos correcciones.** La matriz ya puntuaba CI e integridad/recovery; la brecha restante no se resuelve solo con pruebas del código.
- **Tope de puntuación actual con los pesos publicados:** el área económica sigue en 5/20, lo que limita matemáticamente el máximo a 85/100 aun si todas las demás áreas alcanzaran el total. Superar 90 exige primero mejorar la evidencia económica y revisar la rúbrica sin falsear resultados. La captura de 24 horas aceptada y un edge neto prospectivo independiente siguen ausentes.
- Se conserva `NO_TRADE`; sin LIVE, claves privadas ni dinero real.


## Confirmación tras sincronización documental PR #84

La documentación de continuidad distingue ahora el último SHA con cambios de código/workflows (`a07b8aac15970f3349f9de54f021cfbeb71e4e20`) de la sincronización documental posterior (`4a28961c0c0139e72455b626a8136d5381c155f0`). El CI de este segundo SHA [39079895257](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079895257) terminó `success`: 1598 pruebas en 10.94 s. La matriz sigue en 73/100 y el razonamiento del límite de 85/100 mientras el área económica continúe 5/20 no cambia.


## Evidencia actualizada — PR #86 y #87 (18 de octubre de 2026)

- **Anti-lookahead por ventana (PR #86):** los outputs de baseline y BTC-gated para la evaluación y las tres ventanas de validación permanecen invariantes al modificar solamente el sufijo posterior a cada ventana; incluye fills, métricas y liquidación final. Los tests usan datos sintéticos y no prueban edge. Merge SHA `96295e6c59286ded396789b615e4f14ed3300516`; suite [39080745206](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39080745206) pasó 1603 pruebas.
- **Seguridad automatizada (PR #87):** CodeQL Python/Actions y pip-audit en main con schedule/manual; Dependabot para pip y GitHub Actions. La primera auditoría encontró `PYSEC-2026-1845` en pytest 8.4.2; `pytest>=9.0.3,<9.1` corrige la versión afectada según el mantenedor. Tras la actualización, pip-audit devuelve `No known vulnerabilities found` en las dependencias auditables. El paquete propio `bot-obrero` no se publica en PyPI, así que pip-audit no puede analizar ese artefacto desde dicho índice; no se interpreta como un informe de seguridad completo.
- **CI de main exacto:** [tests 39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044), success, 1604 pruebas en 14.62 s; [CodeQL 39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033), success; [pip-audit 39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089), success. E2E SMA/indicadores pasaron en el SHA de PR #87.
- **Puntuación conservada: 73/100.** No se otorgan puntos por seguridad automatizada a lo ya cubierto por la categoría de CI, y el cambio anti-lookahead no sustituye el estudio de artefactos reales. La evidencia económica permanece 5/20; el máximo aritmético sigue 85/100 hasta que mejore esa categoría.
- **Bloqueos restantes:** captura prospectiva real de 24 h aceptada, edge neto independiente en holdout, alertas/supervisión operativa y resolver formalmente las limitaciones de atomicidad entre SQLite independientes. `NO_TRADE` continúa.


## Actualización de puntuación — PR #93 (18 de octubre de 2026)

PR [#93](https://github.com/eltiootaku01-hue/bot-cripto/pull/93) corrigió una brecha de integridad del validador de captura: el contrato exige un entero JSON exacto para `schema_version` del manifiesto, configuración y envelope, y para `sequence`. La suite incorpora cuatro regresiones y el CI post-merge exacto de main [39100954733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954733) pasó **1608 pruebas en 15.66 s**. [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954774) y [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954741) también pasaron; los E2E SMA/indicadores de la punta de PR finalizaron `success`.

**Puntuación conservada: 73/100.** Este arreglo refuerza el contrato de integridad, pero no demuestra adquisición de datos en tiempo real ni rentabilidad. La categoría económica sigue en 5/20 y, con la matriz actual, el máximo aritmético permanece en 85/100 hasta mejorar la evidencia económica. El >90 requiere, además, ejecutar/aceptar la captura prospectiva real de 24 h, validar el holdout independiente con costes iguales frente a cash y buy-and-hold, y demostrar supervisión/recuperación sostenida. `NO_TRADE` se mantiene.
