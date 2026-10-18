# BOT-CRIPTO — Evaluación de preparación técnica y ruta hacia >90%

**Evaluación:** 2026-10-18  
**Estado exacto de main comprobado el 2026-10-18:** `5b67db204569cdba9cfa96ad81b06c2eade15a1c` (cambio anterior documental: endurecimiento del manifiesto bookTicker PR #107). En ese SHA: 1.694 tests aprobados ([39142481052](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39142481052)), CodeQL success ([39142481038](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39142481038)) y pip-audit success ([39142481017](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39142481017)). La puntuación permanece **77/100**. Issue #98 abierta; captura real no aceptada y holdout prospectivo no ejecutado.
**Últimos cambios de construcción previos:** se corrigió la valoración del equity de posiciones virtuales abiertas para descontar los costes modelados de salida y alinear drawdown/PNL diario con el benchmark. SHA funcional `939a862e01de598da392d687b790d83017a9321c`: 1675 tests ([39113220900](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220900)), CodeQL ([39113220869](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220869)) y pip-audit ([39113220777](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220777)), todos success sobre el mismo SHA. El replay V1 con la valoración de drawdown corregida pasó en `b1effde0e0f786bc7dde7dd2bb5ec974802c5929` ([run 39112722110](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39112722110)).
**Actualización de puntuación:** 2026-10-18. La matriz pasa de **76 a 77/100** por el orquestador `bot_obrero/prospective_capture_pair.py`, que coordina las dos capturas, cancela el proceso compañero si una falla, conserva logs y manifiesto de pareja y ejecuta la validación conjunta automáticamente. La suite añade cinco regresiones de coordinación y pasó 1655 pruebas. Es una mejora verificable de supervisión local, no evidencia de captura real ni de edge económico; los dos siguen pendientes.
**Propósito del porcentaje:** preparación demostrada para un bot de investigación y PAPER; no es porcentaje de líneas de código, probabilidad de beneficio ni autorización para operar con dinero real.

## Dictamen

**Preparación operativa basada en evidencia: 77/100. El umbral >90 todavía no está alcanzado.**

El porcentaje usa pesos publicados y puntúa solo capacidades con evidencia observada. La decisión económica sigue siendo `NO_TRADE`. Un buen CI no acredita una estrategia rentable; y tener un colector probado no equivale a disponer de una captura prospectiva real, aceptada y continua de 24 horas.

## Matriz de aceptación ponderada

| Área | Peso | Puntaje | Evidencia para el puntaje | Brecha que impide puntaje completo |
|---|---:|---:|---|---|
| Integridad del repositorio y CI reproducible | 10 | 10 | PR #71–#87 integradas; CI post-merge exacto en `43d2cd70b2a350cf2987210166df7711d932e41b`: 1604 pruebas; E2E SMA/indicadores y CodeQL/pip-audit pasaron; Actions fijadas por SHA y Dependabot semanal | Seguir exigiendo CI por SHA y revisar/triagear alertas de CodeQL/dependencias |
| Adquisición pública y contratos temporales | 15 | 14 | Adaptadores Binance REST/Kline y bookTicker; parser valida bid/ask/Decimal/update ID y relaciona observación/recepción/disponibilidad; cada manifest se valida de forma independiente y `validate-pair` comprueba símbolos iguales, ventana real ≥24 h y día UTC común, con pruebas unitarias e integración | No hay captura real aceptada; el bookTicker Spot normalmente no lleva event time de exchange, por lo que la recepción local no prueba disponibilidad histórica. Finality REST y observaciones históricas pueden seguir UNKNOWN |
| Análisis, backtest y defensa anti-lookahead | 15 | 14 | SMA/EMA/RSI canónicos y cálculo incremental; PR #76 cubre invariancia de backtest/gate; PR #86 añade métricas y fills invariantes por ventana para evaluación + 3 validaciones en baseline/BTC-gated al perturbar solo el sufijo posterior; suite exacta pasa 1604 pruebas | Falta validar sobre el dataset/artefactos reales completos del estudio anual y con evidencia temporal prospectiva; los tests nuevos usan datos sintéticos |
| Evidencia económica de estrategia | 20 | 5 | Backtests, benchmarks cash/buy-and-hold y evaluación walk-forward están implementados; el resultado conserva honestamente `NO_TRADE` | Las estrategias evaluadas perdieron dinero neto o no demostraron robustez; falta una validación independiente que justifique avance |
| Ciclo mercado→Signal→TradeProposal→riesgo→PAPER | 15 | 14 | PR #74 compone la salida de la pipeline canónica con contexto de riesgo completo, snapshot PAPER persistido, admisión/reserva y runtime PAPER existente; E2E sintético cubre fill y conciliación. `NO_TRADE` no invoca el runtime | El caller aún debe proporcionar la observación pública de libro, context factory, política y evidencia de readiness; no hay captura sostenida de producción y la estrategia no está validada económicamente |
| Persistencia económica y recuperación PAPER | 15 | 14 | Quote versionado/hash-bound; replay recalcula el hash de intención y revalida la evidencia original; conciliación de cuenta/reserva/binding/ledger; recuperación tras commit y dos reconciliaciones idempotentes; suite main exacta pasa 1604 pruebas | Los archivos SQLite independientes no forman una transacción global. Reparación parcial debe seguir en UNKNOWN/BLOCKED hasta conciliar |
| Operación sostenida, supervisión y captura prospectiva | 10 | 6 | Colectores Kline y bookTicker con JSONL/fsync, manifiestos, hashes y validadores; `prospective_capture_pair.py` supervisa ambos procesos, detiene el compañero si hay fallo, persiste logs y ejecuta la validación conjunta; cinco regresiones del orquestador | Captura real Kline+bookTicker aún no aceptada en un intervalo común de ≥24 h; faltan alertas/monitorización continua y holdout prospectivo anual completo |
| **TOTAL** | **100** | **77** | | |

### Cómo interpretar la cifra

- **77/100** es una puntuación de preparación demostrada bajo la matriz actualizada, no un porcentaje de líneas de código ni una estimación de beneficio.
- La revisión tras endurecer el parser pasó 1645 tests [39105436364](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436364), CodeQL [39105436371](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436371) y `pip-audit` [39105436379](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436379), todos `success` en `0913bbee0c6090bc182d9955bd711e4a5130ffca`. La revisión más nueva, con gracia de captura, pasó 1650 tests [39105870878](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870878), CodeQL [39105870780](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870780) y `pip-audit` [39105870782](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870782), todos `success` en `375009b96278b6ad44240ae7359f3c483958895b`. Los commits documentales posteriores se verifican por su propio SHA.
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


## Actualización tras PR #96 — 18 de octubre de 2026

La PR [#96](https://github.com/eltiootaku01-hue/bot-cripto/pull/96) endurece `replay_paper_economics()`: valida las claves de orden antes de ordenar los fills y usa timestamps normalizados a UTC, para evitar excepciones con snapshots malformados y ordenar instantes válidos que usan offsets distintos. Añade dos regresiones parametrizadas.

- PR #96: [1610 pruebas antes del merge](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821969), [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821961), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821964), [SMA E2E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821960) e [indicadores E2E](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101821955): todos `success`.
- Main post-merge exacto: [1610 pruebas en 11.67 s](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918101), [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918092) y [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918074): todos `success`.

**Preparación conservada: 73/100.** No se otorgan puntos de evidencia económica por mejorar la validación del replay. El área económica sigue en 5/20 y el tope aritmético es 85/100 hasta mejorarla. Faltan la captura prospectiva real, validada e íntegra durante al menos 24 h; un holdout económico independiente con costes iguales frente a cash y buy-and-hold; y evidencia de supervisión/recuperación sostenida. `NO_TRADE` continúa.


## Actualización — colector de cotizaciones y V1/V2 en main (18-10-2026)

- Se integró el colector público de best bid/ask (Spot `bookTicker`) con validación de contratos y persistencia JSONL/hash-bound; pruebas de parser, timestamps, shutdown y validador. La suite exacta de main tras corregir un caso de prueba mal formulado pasó 1628 tests en [run 39104242145](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39104242145) (15.53 s). `pip-audit` [39104242200](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39104242200) pasó. CodeQL [39104242189](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39104242189) debe confirmarse sobre ese SHA antes de considerarlo cerrado.
- Investigación V1 se integró como código/documentación en `research/btc_eth_strategies_v1.py`; el workflow de replay en main pasó [39103827728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103827728). Resultado preservado: `NO_ROBUST_EDGE_FOUND / NO_TRADE`; el candidato histórico ETH TSMOM 168h es negativo bajo STRESS_2 y no se acepta como edge prospectivo.
- Se preservaron en main los informes históricos del universo de altcoins R1/R2; la lista canónica sigue `BLOCKED_UNIVERSE_DEFINITION`. V2 está registrado en main, pero `DESIGNED / NOT RUN` hasta aceptar las dos capturas reales de 24 h en una ventana común.
- **Total revisado: 76/100.** El área económica sigue en 5/20; por aritmética, el máximo de esta rúbrica es 85/100 mientras no exista evidencia económica independiente nueva (las áreas no económicas suman 80 puntos posibles). No se conceden puntos por los colectores sintéticos ni por el replay retrospectivo.


## Actualización — validador conjunto de captura (18-10-2026)

El software ahora tiene un comando `validate-pair` que revalida por separado el manifiesto de klines y el de bookTicker, y luego comprueba identidad del universo, duración de solapamiento ≥86.400 s y una jornada UTC completa común al principio de la ventana. Las pruebas abarcan solapamientos cortos, límites temporales inválidos, ausencia de día común y desajuste de símbolos. El diario de quotes reduce presión de I/O: el adapter no retiene listas de eventos, el ID de actualización se coteja con memoria constante, y los writes se vacían/fsync cada 1.000 registros o 1 segundo, con manifiesto limitado a una actualización por segundo salvo anomalías.

La puntuación sube **75→76/100** (+1 adquisición de datos). La suite sobre SHA funcional `723d44b7c68d84fe905d2878203092ba5db02928` pasó [1641 pruebas](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105159335), `pip-audit` pasó [39105159320](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105159320); CodeQL [39105159249](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105159249) deberá comprobarse por SHA. El runbook está en [PROSPECTIVE_BTC_ETH_CAPTURE_V1.md](../docs/PROSPECTIVE_BTC_ETH_CAPTURE_V1.md). Ninguno de estos tests demuestra una captura real de 24 h ni edge independiente; `NO_TRADE` se mantiene.


## Último endurecimiento de parser — 18-10-2026

Un payload de bookTicker con un campo `e` no textual (lista, diccionario, booleano o entero) podía causar un `TypeError` al hacer membership contra un `set`. El parser ahora rechaza explícitamente cualquier tipo de evento inesperado como `BinanceWebSocketProtocolError`; cuatro regresiones cubren esos valores. El SHA funcional `0913bbee0c6090bc182d9955bd711e4a5130ffca` pasó 1645 tests, CodeQL y `pip-audit`. No añade puntos a la rúbrica porque es un endurecimiento dentro de la cobertura de integridad existente; total permanece 76/100.

El bloqueo para seguir a V2 quedó rastreado en [issue #98 — captura real Kline + bookTicker de 24 h](https://github.com/eltiootaku01-hue/bot-cripto/issues/98); el software y los validadores están listos, falta evidencia real aceptada.


## Ventana de gracia para capturas paralelas — 18-10-2026

Se añadió `DEFAULT_CAPTURE_GRACE_SECONDS = 300` al colector bookTicker. Con los valores predeterminados, mantiene la captura durante **86.700 segundos** (objetivo 86.400 + gracia 300) y el validador independiente comprueba que manifiesto/configuración coinciden y que elapsed no sea inferior a target+grace. Esto evita que un desfase de arranque/parada de unos segundos entre las dos terminales eche a perder el solapamiento. No rebaja el gate: el validador conjunto exige todavía ≥86.400 segundos de solapamiento efectivo y un día UTC completo compartido. Las regresiones cubren que 86.400 s no aprueben cuando se ha registrado una gracia de 300 s, y que 86.700 s sí puedan hacerlo bajo datos sintéticos.

En el SHA `375009b96278b6ad44240ae7359f3c483958895b`, tests [39105870878](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870878) aprobaron 1650 pruebas y `pip-audit` [39105870782](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870782) pasó. CodeQL [39105870780](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105870780) terminó `success` en el mismo SHA. La puntuación se mantiene en 76/100: el ajuste mejora la probabilidad de aceptar una captura válida, no representa captura real ni evidencia económica.


## Actualización — orquestador de captura conjunta (18-10-2026)

- Se añadió `bot_obrero/prospective_capture_pair.py`: un comando espera la próxima medianoche UTC apropiada, lanza los colectores Kline y bookTicker con los mismos símbolos, duración de 86.400 s y gracia de 300 s, supervisa ambos procesos, detiene el compañero si uno falla y revalida los dos manifiestos con `validate_paired_manifests`. Conserva logs por colector y `pair-manifest.json` con config hash, estado, rutas de los manifiestos y hashes del validador.
- Entrada única: `python -m bot_obrero.prospective_capture_pair capture --output-dir evidence/prospective-btc-eth-pair`. El runbook y la issue #98 ya recomiendan esta ruta. El comando debe ejecutarse desde la raíz del repositorio en un equipo que permanezca despierto y conectado. Una ejecución bloqueada no puede usarse para preregistro.
- Regresiones añadidas: elección del inicio UTC, rechazo de reloj sin zona horaria, comandos sin credenciales privadas/flags LIVE, duración y gracia canónicas, cancelación del otro proceso ante fallo y rechazo de intervalos inválidos. En el SHA `b1a21381a54e64db87f89265bd351d9db0370a65`, tests [39108357014](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39108357014) pasaron **1655 pruebas**, pip-audit [39108357017](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39108357017) y CodeQL [39108356962](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39108356962) terminaron `success`.
- **Puntuación actualizada: 77/100** (+1 en operación/supervisión local por orquestación y aborto coordinado probados). La captura real sigue sin ejecutarse/aceptarse y el holdout prospectivo con edge neto robusto sigue ausente. El área económica continúa en 5/20, por lo que el máximo aritmético de la matriz no cambia: **85/100** hasta nueva evidencia económica. `NO_TRADE` continúa.


## Actualización — procedencia de la captura (18-10-2026)

La nueva capa de procedencia rechaza capturas desde fuera de la raíz Git, ramas distintas de main, refs locales que no coincidan con origin/main, cambios/untracked en la fuente del colector o comandos de procedencia que fallan. La configuración hash-bound conserva los SHAs de código y la rama, sin almacenar una ruta absoluta de la máquina. Se añadieron regresiones de estado limpio, fuente modificada, checkout incorrecto, rama ajena a main y head atrasado/desfasado.

En SHA cec4e8e6dd58871a04dd1f1171eb985a6926ea37, tests (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109569250) pasaron 1661 pruebas, CodeQL (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109569231) y pip-audit (https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109569376) terminaron success.

**Puntuación se mantiene 77/100.** Este endurecimiento reduce la ambigüedad de procedencia; no equivale a captura real de mercado ni a edge económico. La captura prospectiva y el holdout independiente siguen pendientes; NO_TRADE se mantiene.


## Revalidación de procedencia en los límites de captura — 18-10-2026

Se añadió una segunda y tercera comprobación de Git: después de la espera UTC justo antes de abrir las conexiones y al concluir los dos colectores antes de aceptar los manifiestos. Una alteración de HEAD, origin/main, rama o fuente limpia bloquea la pareja. Las regresiones incluyen la fuente alterada entre el inicio y el lanzamiento. En SHA 76e770cf86f02359a7bbd1353d1c5aea0094eeec, tests [39109859114](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109859114) pasaron 1663 casos; CodeQL [39109859051](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109859051) y pip-audit [39109859053](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39109859053) deben pasar sobre ese mismo SHA.

**La puntuación permanece 77/100.** La nueva defensa vincula la evidencia a una revisión estable, pero no demuestra que haya ocurrido la captura real ni que exista ventaja económica.

## Nota de cobertura adicional — 18-10-2026

Se añadió una regresión para el cambio de SHA después de que ambos procesos de captura terminen y antes de validar/aceptar sus manifiestos. En el SHA funcional `33979f0f96afdd54189834cc64996fd2c220fb9a`, la suite pasó **1664 pruebas** [39111442312](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39111442312), CodeQL pasó [39111442302](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39111442302) y pip-audit pasó [39111442300](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39111442300), todo en el mismo SHA. Si la fuente cambia durante la corrida, la pareja queda `BLOCKED` antes de invocar el validador de aceptación.

**La puntuación permanece en 77/100.** Es más cobertura de una garantía ya implementada, no un aumento de evidencia de mercado. Para superar el 90 bajo esta rúbrica siguen siendo necesarios nuevos resultados económicos independientes; la captura real de 24 h y el holdout no pueden reemplazarse con tests sintéticos. `NO_TRADE` se mantiene.


## Equidad de valoración entre táctica y benchmark — 18-10-2026

El motor de backtest y el replay V1 ahora calculan drawdown sobre una marca neta de liquidación para una posición abierta, deduciendo spread/slippage modelados y comisión hipotética de salida. El replay V1 recalculó sus métricas de drawdown; los retornos no cambiaron. La táctica `ETH_TSMOM_168H` sigue sin ser ganadora validada: +15,5138 % BASE, +1,9100 % STRESS_1, -26,3695 % STRESS_2; sus retornos BASE por validación son -10,6421 %, +4,6558 % y -14,7518 %. El protocolo no permite promoción automática por encabezar un solo segmento.

La puntuación continúa en **77/100**. La evidencia económica sigue en 5/20 y el techo aritmético bajo la rúbrica actual permanece en 85/100 hasta que una prueba independiente cambie legítimamente esa categoría. Los resultados históricos tienen `available_at/finality` UNKNOWN y no sustituyen la captura prospectiva real ni un holdout anual futuro.


## Replay anual histórico en main — 18-10-2026

El workflow de regresión anual pasó sobre main SHA `977977f8ab5170cd8d17a44bc82586217c325e14`, igual que los 1675 tests, CodeQL y pip-audit. Se registró un dataset de 8.760 velas entre 2025-04-16 y 2026-04-16 UTC, con warm-up y tres folds, además de un replay offline hash-equal.

En la evaluación BASE, `BASELINE` perdió 26,8599 %, `BTC_GATED` perdió 10,0802 %, cash quedó en 0 % y buy-and-hold ETH subió 48,5559 %. La versión BTC_GATED no repite ganancias entre folds y pierde en STRESS_2; la clasificación es `NO_ROBUST_EDGE_FOUND / NO_TRADE`. El artefacto se marcó `HISTORICAL_EXPLORATORY`, pues `available_at` histórico es UNKNOWN y el periodo ya es retrospectivo. No suma evidencia para el holdout prospectivo.

**Puntuación sin cambios: 77/100.** La matriz sigue teniendo 5/20 en evidencia económica; los resultados recientes son negativos para el candidato y no justifican elevar la cifra. La captura real de 24 horas y la evaluación futura independiente siguen siendo las brechas obligatorias.


## Actualización de CI posterior a PR #99 — 2026-10-18

La sincronización post-merge confirma `main` SHA `15d4f5ba3e1169ce35cfb08c0f79355ada316e4a`: 1.676 tests aprobados en 14.86 s y CodeQL/pip-audit exitosos sobre ese SHA. La mejora evita que el test de fallo de un colector consulte Git remoto real mientras simula procesos; no cambia la puntuación económica ni acredita una captura de mercado. Preparación basada en evidencia: **77/100**; `NO_TRADE` hasta superar captura #98 y holdout prospectivo.


## Update after PR #101 — 2026-10-18

A paired-capture overlap edge case was identified and corrected in `main`: Kline target is now 86,460 seconds, bookTicker remains 86,400 seconds, both retain 300-second grace, and the pair manifest's hash-bound configuration records the 60-second guard. Full overlap acceptance remains ≥86,400 seconds. Exact post-merge SHA `3590947d6fceaebfc437cbc4d784d548693dff8e`: tests 1,676 passed, CodeQL and pip-audit success. No score increase is assigned: readiness remains **77/100**, evidence economics remains 5/20, real capture #98 and 365-day prospective holdout remain uncompleted, `NO_TRADE`.


## Validación estricta del manifiesto Kline — 18-10-2026

PR #103 corrigió una conversión permisiva del validador independiente: `target_duration_seconds` podía llegar como cadena numérica y `actual_elapsed_seconds` podía convertirse con `float()` antes de validar el tipo JSON. El contrato exige ahora entero exacto para duración objetivo en manifest/configuración, valor numérico finito y no booleano para tiempo transcurrido, y coherencia entre campos. Dos regresiones parametrizadas cubren el objetivo y el tiempo transcurrido, con hash de configuración recalcado en el caso manipulado. Evidencia del SHA exacto de main `6f2512f9dc6d0e00b202291606258c6894e84420`: tests 1.678/1.678, CodeQL y pip-audit success. Score sin cambios: **77/100**; sigue faltando la captura real #98 y la evaluación económica prospectiva independiente.


## Auditoría estructural Kline — PR #105 (18-10-2026)

Se corrigieron cuatro brechas del validador: conversión de `configuration.symbols` sin validar lista/tipos; tratamiento truthy/falsy de `fatal_errors` que aceptaba campo ausente o `null`; igualdad numérica que permitía floats en metadatos `bytes`/`records`; y llamada a `.replace()` sobre un `instrument.symbol` mal tipado. La PR añade nueve regresiones. La punta de PR pasó 1.687 tests y los checks de seguridad/dependencias/E2E; el SHA de main `2bf52675d7a8c26943a469f13dc2adbc7947bb77` pasó tests, CodeQL y pip-audit post-merge. Preparación se mantiene en **77/100**, sin puntuación económica adicional. No existe una captura real aceptada ni holdout prospectivo ejecutado; `NO_TRADE` permanece.


## Validación estricta de manifest bookTicker — PR #107 (18-10-2026)

La configuración ahora exige enteros JSON exactos para `target_duration_seconds` y `capture_grace_seconds`, además de coherencia con los valores top-level. El resumen por símbolo también exige enteros exactos para registros, IDs duplicados/desordenados, gaps y regresiones de reloj; un float equivalente como `0.0` ya no puede satisfacer accidentalmente la igualdad con `0`. Siete regresiones cubren la brecha. PR head y main post-merge pasaron 1.694 pruebas, CodeQL, pip-audit y E2E SMA/indicadores. Preparación se mantiene **77/100**; captura real y holdout independiente pendientes; `NO_TRADE` se mantiene.


## Instantánea viva de aceptación — 2026-10-18

- Revisión de GitHub: `main` en `5b67db204569cdba9cfa96ad81b06c2eade15a1c` antes del presente commit documental.
- Los checks de tests, CodeQL y pip-audit terminaron `success` en ese SHA exacto; la suite registró 1.694 pruebas aprobadas.
- No se observó un manifiesto real aceptado ni un artefacto de captura asociado a esos checks. La issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) continúa bloqueante.
- La rúbrica permanece en **77/100**. No asignar puntos por tests sintéticos como si fueran adquisición real o rentabilidad; no iniciar T0, no declarar ganador y mantener `NO_TRADE`.


## Revalidación documental contra main posterior a 947eefc — 2026-10-18

En la revisión de GitHub inmediatamente anterior a esta actualización documental, main apuntaba a 947eefcdb35da19d7a990b7b69fc98088c42cfb1. Los tres checks de ese SHA terminaron success: [pytest, 1.694 tests](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39144538447), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39144538441) y [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39144538444). La rama no tenía PR abiertas en la inspección correspondiente. El cambio actual es documental: no modifica código de ejecución ni suma puntos por pruebas sintéticas.

La evaluación permanece en **77/100**; la evidencia económica sigue en 5/20, las estrategias evaluadas no han demostrado edge neto robusto, y el estudio V2 continúa **DESIGNED / NOT RUN**, sin T0. La issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) sigue BLOCKED porque no consta una pareja de manifiestos de mercado real aceptada con ≥86.400 s de solapamiento, validación independiente y un día UTC completo común. El próximo trabajo no puede suplirse con más tests sintéticos: ejecutar la captura emparejada en una máquina persistente, revisar el resultado y registrar sus hashes inmutables.

Como restricción de planificación, GitHub documenta un máximo de 6 horas por job en ejecutores hospedados ([límites](https://docs.github.com/en/actions/reference/limits)) y retención predeterminada de 90 días para artefactos ([retención de Actions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)). Por ello, la captura de 24 h y la custodia de los datos de un holdout de 365 días necesitan una máquina persistente y almacenamiento durable; el artefacto temporal de un job hospedado no es sustituto de esa evidencia.

**Dictamen intacto:** no declarar 100 %, no nombrar una estrategia ganadora sin resultados independientes, no iniciar T0 ni habilitar LIVE. Mantener **NO_TRADE**.


## Actualización tras el hardening de bookTicker y matriz Python 3.11–3.14 — 2026-10-18

La auditoría del validador halló que `symbols_summary[*].maximum_inter_event_gap_seconds` se comparaba con el número recalculado sin exigir primero un tipo numérico canónico. La corrección en SHA funcional `85e0aaf8080345630b35d7c6e1d4db0ed85291b0` exige un `float` finito no negativo y rechaza valores booleanos; su nueva regresión fuerza `true` en una captura sintética con gap calculado de 1.0 segundo y verifica que no se acepte el manifiesto.

También se amplió `.github/workflows/tests.yml` para ejecutar la suite completa en Python 3.11, 3.12, 3.13 y 3.14, alineando las pruebas con `requires-python >=3.11` y las versiones soportadas por las dependencias de test y WebSocket. En el SHA funcional `85e0aaf8080345630b35d7c6e1d4db0ed85291b0`, todos los cuatro jobs de pytest terminaron `success` y `pip-audit` terminó `success`; CodeQL estaba todavía en curso al tomar la instantánea. Las pruebas cruzadas mejoran confianza técnica, pero no aumentan la puntuación económica.

**Estado sin cambios:** preparación **77/100**; evidencia económica 5/20; captura prospectiva real de la issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) sin aceptar; BTC_ETH_STRATEGIES_V2 `DESIGNED / NOT RUN`, sin T0 ni holdout anual; decisión `NO_TRADE`. No se declara edge ganador ni se habilita LIVE.


## Actualización — herramienta de empaquetado de evidencia prospectiva — 2026-10-18

El commit funcional [`ba9641c463d9699015deab85c14781031f388575`](https://github.com/eltiootaku01-hue/bot-cripto/commit/ba9641c463d9699015deab85c14781031f388575) incorporó `bot_obrero/prospective_capture_bundle.py` y siete pruebas nuevas. En ese SHA, la suite completa terminó con **1.703 pruebas aprobadas en cada versión Python 3.11, 3.12, 3.13 y 3.14**, y `pip-audit` finalizó `success`; el registro de CodeQL aún estaba en curso al tomar esta instantánea, por lo que no se afirma el resultado final de ese control en esta nota.

La herramienta solo empaqueta si el manifiesto de pareja ya es `VALID`, verifica la coherencia de configuración/procedencia, vuelve a ejecutar el validador independiente, rechaza rutas inseguras/archivos temporales y genera un ZIP más un SHA-256 sidecar. Las pruebas usan fixtures sintéticos; no aportan puntuación de adquisición ni rendimiento.

La preparación permanece en **77/100** y la evidencia económica en 5/20. La captura real de la issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) sigue pendiente; V2 permanece `DESIGNED / NOT RUN`, sin T0 ni evaluación independiente de 365 días. No promover táctica y mantener `NO_TRADE`.


## Hardening del paquete de captura: allowlist y CI en cuatro Python — 2026-10-18

Se endureció `bot_obrero/prospective_capture_bundle.py` tras una segunda revisión: ahora cualquier fichero no reconocido dentro de la corrida bloquea el ZIP. La allowlist exige el manifiesto de pareja, manifiestos individuales, dos JSONL Kline, dos JSONL bookTicker y los dos logs; la prueba `test_bundle_refuses_unexpected_files_instead_of_packaging_them` agrega deliberadamente `logs/operator-notes.txt` y comprueba rechazo sin crear archivo. Se corrigió además el fixture positivo para incluir ambos símbolos BTC y ETH, sin relajar el contrato de producción.

El SHA funcional exacto [`281302deebdfcdf3b23734f651386fed35bf85bf`](https://github.com/eltiootaku01-hue/bot-cripto/commit/281302deebdfcdf3b23734f651386fed35bf85bf) aprobó [pytest](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158157860) con **1.704 pruebas por versión** en Python 3.11, 3.12, 3.13 y 3.14, [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158157935) y [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39158157840), todos `success`.

No se cambia la puntuación de preparación por hardening o tests sintéticos. La matriz se mantiene en **77/100**, evidencia económica 5/20; captura #98 y estudio independiente de 365 días siguen pendientes. `NO_TRADE` permanece.
