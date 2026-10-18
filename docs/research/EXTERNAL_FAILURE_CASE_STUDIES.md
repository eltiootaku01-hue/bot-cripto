# BOT-CRIPTO — Casos públicos de fallos, actualizaciones y mitigaciones

**Fecha de revisión:** 18 de octubre de 2026  
**Base de código revisada:** `main` incluye PR #96 en `bb4cf093e4798a9ce80a770281a6003dfb4d2f9b` (validación fail-closed de claves de orden del replay PAPER), PR #93 (tipos estrictos del manifest de captura) y las correcciones de PR #86/#87. Suite post-merge exacta: [39101918101](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918101), 1610 tests; CodeQL [39101918074](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918074) y pip-audit [39101918092](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918092) `success`.
**Rama de origen técnica auditada:** `feat/paper-operational-runtime-v1`; la prueba de recuperación se añadió en `integrate/paper-runtime-main-sync-2026-10-17`.
**Regla de evidencia:** un caso de otro proyecto demuestra que una clase de fallo existe; solo una prueba local demuestra si BOT-CRIPTO tiene ese fallo.

## 1. SQLite: corrupción poco frecuente en modo WAL

**Fuente primaria:** [SQLite — Write-Ahead Logging / WAL-reset bug](https://www.sqlite.org/wal.html) y [SQLite 3.51.3 release notes](https://www.sqlite.org/releaselog/3_51_3.html).

SQLite publicó que el 3 de marzo de 2026 se encontró un bug que podía corromper una base en modo WAL cuando dos o más conexiones al mismo archivo intentaban escribir o hacer checkpoint casi simultáneamente. La corrección está en SQLite 3.51.3 (13 de marzo de 2026) y posteriores. El propio mantenedor lo describe como un escenario raro con requisitos de concurrencia específicos.

**Comparación local:** el adaptador PAPER anterior habilitaba WAL y el diseño usa más de una conexión SQLite entre componentes. No hay evidencia de que el bug hubiera corrompido el archivo del proyecto; sí existía un patrón de concurrencia parecido.

**Medida aplicada en la rama:** `PaperExecutionAdapter` usa el journal rollback `DELETE` en vez de depender de la versión SQLite incluida en el runtime. Mantiene transacciones explícitas y serializa el escritor. Un `SQLITE_BUSY` debe quedar como fallo/bloqueo, nunca convertirse en un fill supuesto. Si se reintroduce WAL, exigir primero SQLite corregido y una prueba de estrés/recuperación de varias conexiones.

La [documentación de Python `sqlite3`](https://docs.python.org/3/library/sqlite3.html) también explica el bloqueo de escrituras, el parámetro `timeout` y la necesidad de serializar escrituras cuando las conexiones se comparten.

## 2. Binance: `LOT_SIZE` frente a `MARKET_LOT_SIZE`

**Fuentes:** [documentación oficial de filtros de Binance Spot](https://github.com/binance/binance-spot-api-docs/blob/master/filters.md), [CCXT issue #663 — LOT_SIZE](https://github.com/ccxt/ccxt/issues/663) y [CCXT issue #6460 — MARKET_LOT_SIZE](https://github.com/ccxt/ccxt/issues/6460).

Los reportes de CCXT documentan rechazos `Filter failure: LOT_SIZE` cuando la cantidad no cumple el paso permitido y la necesidad de distinguir los límites para órdenes de mercado. Binance documenta filtros distintos para lote normal y lote de mercado. No debe usarse `float` ni asumirse que las reglas son idénticas.

**Fallo local observado en CI:** en el commit `000dbb77ae770a9c9e9ac9eceec2ef624033fae0`, la prueba anual pareada terminó con `MARKET_FILTER_INVALID_OR_INCOMPLETE:market_step_size`; el E2E de Product V2 terminó con `quantity_step must be a positive finite Decimal when supplied`. Evidencia: [job BTC→ETH anual](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39059081714) y [E2E público](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39059085206).

**Corrección en esta revisión:** se resuelven los límites efectivos de mercado campo a campo. Si un campo `MARKET_LOT_SIZE` está en cero, solo se usa el correspondiente `LOT_SIZE` normal si es positivo y finito. Si no hay un sustituto válido, la simulación queda bloqueada. Se agregaron tests de ambos casos.

**Regresión posterior de tests:** el commit `992ce76977f4e9f502ff856a83231412beaad105` reveló una diferencia solo textual en la prueba de rechazo seguro: la implementación devolvía `MARKET_LOT_SIZE_UNKNOWN_OR_INVALID`, pero el test esperaba `MARKET_LOT_SIZE_UNKNOWN`. Se actualizó la expectativa para coincidir con el contrato explícito de rechazo en `72423805c16966b07c01d8682fc66d5fda0c169f`. En el commit `f8b9c36768f68ee754f1cb217f920dea25ef445e`, la suite completa y los E2E generales están en `success`.

**Límite:** ExchangeInfo actual no demuestra que esos filtros tuvieran el mismo valor hace un año. Los filtros actuales son un guardarraíl para la simulación; no constituyen historia temporal del filtro por vela.

## 3. Backtesting: sesgo de anticipación (`lookahead bias`)

**Fuentes:** [Freqtrade — lookahead analysis](https://docs.freqtrade.io/en/2026.5/lookahead-analysis/) y [Freqtrade — errores comunes de estrategia](https://docs.freqtrade.io/en/stable/strategy-customization/).

Freqtrade explica que un backtest puede aparentar ganancias exageradas si una señal usa velas futuras, agregaciones sobre todo el dataframe, desplazamientos negativos o alineaciones incorrectas de marcos temporales. Su método compara una corrida base con corridas que aíslan señales para detectar cambios en indicadores y entradas/salidas.

**Comparación local:** el backtester de BOT-CRIPTO calcula indicadores sobre prefijos históricos; la serie incremental se compara por tests con los algoritmos canónicos. El filtro BTC de Product V2 tiene una prueba que modifica datos futuros y comprueba que no alteren una decisión anterior.

**Siguiente defensa:** añadir también una prueba de invariancia de sufijo futuro para el estudio BTC→ETH completo. Cambiar velas posteriores a un timestamp no debe cambiar señales, fills ni correlaciones disponibles hasta ese timestamp. No se debe aceptar rentabilidad si falla esa propiedad.

## 4. Simulación de fills, spread, deslizamiento y datos obsoletos

**Fuentes:** [QuantConnect — modelos de fills al último precio](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/supported-models/latest-price-model) y [QuantConnect — deslizamiento](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts).

La documentación de estos desarrolladores explica que el precio efectivo depende del tipo de dato, lado de cotización, instante de orden y modelo de deslizamiento; datos obsoletos o temporalmente incompatibles no deberían producir fills.

**Comparación local:** el backtester declara señal al cierre y fill en la siguiente apertura, aplica comisiones y supuestos de spread/slippage y ofrece liquidación terminal. Los informes marcan `available_at` y finality desconocidos cuando la fuente no aporta evidencia suficiente. Los hashes fijan identidad de datos/configuración, pero no convierten esos campos desconocidos en evidencia temporal.

**Condición:** los escenarios de costes base y estrés deben usar exactamente el mismo dataset normalizado; esta simulación no equivale a una ejecución real en libro de órdenes.

## 5. GitHub Actions: retirada de Node 20 y checkout seguro

**Fuentes oficiales:** [GitHub Changelog — Node 20 ya no está disponible](https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/), [checkout releases](https://github.com/actions/checkout/releases), [setup-python releases](https://github.com/actions/setup-python/releases) y [upload-artifact releases](https://github.com/actions/upload-artifact/releases).

GitHub retiró Node 20 en los runners el 23 de septiembre de 2026. Los workflows inspeccionados en esta rama ya se actualizaron a `actions/checkout@v7`, `actions/setup-python@v7` y `actions/upload-artifact@v7`, compatibles con Node 24. La evidencia del commit `f8b9c36768f68ee754f1cb217f920dea25ef445e` muestra que la suite completa, los E2E de SMA/indicadores y el E2E público de Product V2/backtest terminaron con `success`; el estudio anual pareado también terminó con `success`. La guía de GitHub sobre [checkout seguro de PR](https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/) explica por qué no se debe ejecutar código no confiable de una PR bajo permisos privilegiados. Los workflows auditados usan `pull_request`, `push` o `workflow_dispatch`; ninguno necesita cambiar a `pull_request_target`.

## 6. Resultados económicos disponibles y límites

La prueba independiente de 365 días EMA/RSI dio estos resultados netos modelados:

| Activo / costes | Retorno neto modelado | Drawdown máximo |
|---|---:|---:|
| BTC, base | -29.2995% | -30.0234% |
| ETH, base | -21.8140% | -24.5854% |
| BTC, estrés | -40.2913% | -40.7285% |
| ETH, estrés | -29.4779% | -30.9149% |

Esto demuestra que esa configuración histórica no acreditó edge. No es una predicción del futuro.

### BTC→ETH emparejado — evaluación anual completada

**Ejecución reproducible:** [GitHub Actions run 39062915950](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39062915950) · [workflow y artefactos](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39062915950).  
**Commit:** `f8b9c36768f68ee754f1cb217f920dea25ef445e` · **estado del job:** `success`.  
**Artefacto:** `btc-eth-follow-365d-f8b9c36768f68ee754f1cb217f920dea25ef445e` (incluye informe, replay offline y dataset normalizado).

| Escenario / estrategia | Capital virtual inicial | Equity final | Retorno neto | Drawdown máximo |
|---|---:|---:|---:|---:|
| Evaluación, ETH EMA/RSI, costes base | 31 USDT | 22.8529 USDT | -26.2809% | -27.6154% |
| Evaluación, BTC-gated ETH, costes base | 31 USDT | 27.8752 USDT | -10.0802% | -22.4288% |
| Evaluación, BTC-gated ETH, estrés 1 | 31 USDT | 24.6012 USDT | -20.6412% | -27.0041% |
| Evaluación, BTC-gated ETH, estrés 2 | 31 USDT | 17.7478 USDT | -42.7490% | -43.2858% |

Las tres ventanas walk-forward de 60 días no confirman robustez para la estrategia BTC-gated: con costes base dieron +3.1286%, -4.4565% y -2.4657%; en el escenario de estrés 2 dieron -4.4780%, -12.2652% y -10.4140%. La clasificación del estudio fue `NO_ROBUST_EDGE_FOUND` y la decisión explícita `NO_TRADE`.

El análisis encontró alta correlación histórica en el año evaluado (mediana aproximada de 0.8429 a 30 días y 0.8100 a 90 días; 365 muestras superaron 0.5 en ambas ventanas). **La correlación no se convirtió en rentabilidad de la estrategia:** el backtest perdió dinero en la evaluación completa y en escenarios de costes mayores.

El replay offline volvió a cargar el dataset guardado y el workflow comprobó la igualdad de los hashes del dataset y de la configuración. Identidad reproducible no significa verdad temporal: tanto BTC como ETH registraron `available_at=UNKNOWN` y `finality=UNKNOWN` en 15.360 observaciones cada uno (historia total adquirida, incluido warm-up/validación). El informe lo etiqueta explícitamente como limitación temporal.

Por tanto, el estudio pareado ya no está pendiente de ejecución: terminó correctamente como prueba de investigación, pero **no acredita ventaja económica robusta ni habilita operaciones**.

## 7. Bloqueos que siguen separados

1. Las estrategias independientes EMA/RSI y la variante BTC-gated evaluadas en este estudio dieron pérdidas netas durante la evaluación anual. No deben activarse como estrategias ganadoras.
2. **Disponibilidad temporal del histórico:** las observaciones reportan `available_at=UNKNOWN` y `finality=UNKNOWN`. El estudio no puede afirmar que cada precio fuera observable exactamente en el momento que simula la señal. Se requiere una política punto-en-tiempo verificable o una clasificación explícitamente limitada a histórico OHLC retrospectivo.
3. **Integridad de datos y robustez:** la prueba completa de invariancia frente a modificación del sufijo futuro para el estudio BTC→ETH sigue siendo una defensa adicional. Los tres folds de 60 días y dos escenarios de estrés no deben alterarse después de ver sus resultados.
4. **Actualización B1-R:** tras PR #71, la recuperación de un fill PAPER después de fallo y reinicio sí cuenta con una prueba integrada. Sigue sin existir atomicidad global entre los archivos SQLite de cuenta, reservas/binding y ledger; un fallo parcial debe seguir devolviendo `UNKNOWN/BLOCKED` y recuperarse sin un segundo envío.
5. Incluso un resultado histórico positivo y reproducible no garantiza ganancias futuras. Se exige validación prospectiva PAPER reconciliada y repetible antes de plantear cualquier autorización posterior.

**Decisión operativa: `NO_TRADE`. No hay autorización para órdenes LIVE ni promesa de rentabilidad.**


## 8. Seguimiento tras integrar PAPER en main — 17 de octubre de 2026

**Cambio verificado:** [PR #71](https://github.com/eltiootaku01-hue/bot-cripto/pull/71) fusionada. La antigua [PR #64](https://github.com/eltiootaku01-hue/bot-cripto/pull/64) se cerró como supersedida para evitar mantener dos rutas de integración.

### Error de ciclo de vida PAPER: fill persistido pero finalización interrumpida

El caso se convirtió en una prueba de regresión concreta: `test_paper_fill_recovers_after_interrupted_finalization_and_restart`. La prueba fuerza una excepción después de que el adaptador haya persistido el fill y antes de confirmar `SUBMITTED`; después cierra y reconstruye PAPER, reservas/binding y ledger desde disco. La primera conciliación debe reparar el estado; la segunda debe ser un no-op económico, sin fills, débitos, versiones o transiciones adicionales.

**Evidencia:** la suite del commit de main `fd51e0dd1ff3f710680a98b685a6b9369c74a600` terminó en [GitHub Actions run 39069869061](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069869061), `completed/success`, con **1561 pruebas aprobadas en 10.11 s**. En la punta de integración antes de fusionar, la suite y los E2E públicos relacionados también finalizaron con `success`: [tests](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780762), [SMA](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780537), [indicadores](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780522), [backtest/Producto V2 con datos públicos](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780401).

### Estado que no debe inferirse de esa prueba

- La reparación entre bases de datos separadas no es una transacción ACID única; por eso cualquier estado parcial se bloquea hasta una conciliación verificable.
- El puente canónico `Signal → TradeProposal` ya está en main, pero el productor operativo de señal a partir de velas públicas actuales/cerradas todavía no está completo.
- Product V2 mantiene `NO_TRADE`: los resultados económicos actuales no justifican habilitar operaciones. Esta corrección no habilita LIVE, no requiere claves privadas y no ejecuta órdenes reales.

Este seguimiento supersede el punto 7.4 anterior en su afirmación de que aún no había una prueba de recuperación integrada; el resto de los límites de esta sección se mantienen.


## 9. Binance finality y productor de señales — actualización posterior a PR #72/#73 (17 de octubre de 2026)

### Hallazgo: un indicador de cierre no debe perder su procedencia

El adaptador WebSocket ya parseaba el campo oficial Binance Kline `k.x` (vela cerrada) pero la capa normalizadora no conservaba la distinción y establecía `finality=UNKNOWN`. La traducción se corrigió: `k.x=true` produce `CandleFinality.FINAL`; `false` produce `NOT_FINAL`. Fuente oficial: [Binance Spot WebSocket Streams — Kline](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md).

En REST de klines no se usa una bandera de cierre del servidor en cada fila. La finality de la vela que ya alcanzó su tiempo de cierre se deriva de `received_at >= candle_end` usando el reloj local. La pipeline expone eso como `REST_RECEIVE_TIME_AFTER_CANDLE_CLOSE`, mientras WebSocket queda como `BINANCE_WEBSOCKET_KLINE_X_FLAG`. Son tipos de evidencia distintos y no deben describirse ambos como una bandera explícita de exchange. Véase [Binance Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api).

### Cambio fail-closed post-merge

Una revisión de la pipeline añadió un segundo guardarraíl: rechaza fuente/provider/venue salvo Binance Spot REST (`binance-spot-rest`) o WebSocket (`binance-spot-websocket`). La base de finality se conserva en la observación y en la evidencia de la señal. CI de la punta PR #73 [39071644011](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39071644011) terminó `success` con **1576 pruebas aprobadas en 10.04s**. La integración se fusionó vía [PR #73](https://github.com/eltiootaku01-hue/bot-cripto/pull/73) en commit `99424107ccaafe1a0985bb956523fa342e66285c`.

### Riesgo económico que permanece

El productor de señal se limita a análisis y a construir una propuesta opcional; no envía órdenes. Los resultados económicos no acreditan ventaja robusta; `NO_TRADE` global se mantiene. Sigue pendiente la prueba completa de invariancia frente a sufijo futuro para el estudio BTC→ETH: Freqtrade advierte que un backtest puede quedar sesgado cuando indicadores/decisiones miran velas futuras y propone contrastar las salidas base con backtests aislados ([Lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)).


## 10. QuantConnect LEAN: fills contra un precio ya obsoleto en barras horarias/diarias

**Fuente primaria / actualización publicada:** [QuantConnect — Closing the Stale-Price Loophole on Daily and Hourly Data (junio de 2026)](https://www.quantconnect.com/announcements/20949/closing-the-stale-price-loophole-on-daily-and-hourly-data/p1).

QuantConnect explicó que una orden de mercado intradía podía ejecutarse contra el cierre anterior al usar un activo suscrito solo a barras horarias o diarias. Ese precio no era fresco al momento de ordenar y podía inflar silenciosamente el backtest. LEAN modificó el modelo de fills para esperar datos frescos o convertir la orden a un tipo compatible con la siguiente apertura/cierre real.

**Medida equivalente en BOT-CRIPTO:** la pipeline de señales exige vela cerrada, `available_at <= decision_timestamp`, secuencia continua y antigüedad máxima. La integración PR #74 valida además que la cotización pública usada en contexto de riesgo sea de la misma identidad/símbolo, observada y disponible antes de la decisión, enlazada por `AvailabilityBinding` y fresca al intentar completar el fill PAPER. La prueba descubrió que la fixture había fechado el fill antes de la disponibilidad de la quote; el error resultante `PAPER_QUOTE_TEMPORAL_ORDER_INVALID` se resolvió avanzando el reloj de la fixture, sin cambiar el guardarraíl.

**Límite:** esto hace auditable el contrato temporal de la simulación. No vuelve un quote sintético en una cotización real ni prueba ventaja económica.

## 11. CCXT: errores transitorios no son equivalentes a errores de orden

**Fuente primaria:** [CCXT Manual — rate limit](https://docs.ccxt.com/docs/manual#rate-limit) y [CCXT Manual español — manejo de errores](https://docs.ccxt.com/es/docs/manual#manejo-de-errores).

CCXT documenta distintos tipos de fallos: límites de tasa, timeout/red, exchange no disponible, autenticación, orden inválida y fondos insuficientes. Sus rutas de retry se destinan a ciertas fallas recuperables de red/servidor; una orden inválida o insuficiencia de fondos no debe tratarse como un error de transporte que siempre se resuelve reintentando. La guía de rate limit también recomienda reutilizar instancias y respetar el peso de cada endpoint para evitar bloqueos.

**Aplicación a BOT-CRIPTO:** la integración de señal con PAPER invoca una sola vez el runtime financiero y no reintenta automáticamente cuando ocurre una excepción después de efectos durables; devuelve `UNKNOWN` para exigir reconciliación. La futura captura debe reutilizar/adaptar una sola política de limitación/reintento y registrar retry-after, timeout, última secuencia, huecos y recuperación. Ningún timeout del stream debe inventar `available_at`, convertir datos desconocidos en válidos o aprobar una orden.

## 12. Actualización de main: integración señal→riesgo→PAPER

**Cambio aplicado:** [PR #74](https://github.com/eltiootaku01-hue/bot-cripto/pull/74) fusionada en commit [`a00e78a634ea7bac4ad266a3ae7b3394c709b6e0`](https://github.com/eltiootaku01-hue/bot-cripto/commit/a00e78a634ea7bac4ad266a3ae7b3394c709b6e0).

El nuevo módulo `bot_obrero/market_signal_paper_runtime.py` exige propuesta canónica, señal todavía válida, contexto completo y enlazado al mismo análisis/señal/propuesta/cuenta, observación pública Binance Spot con binding de disponibilidad, evidencia explícita de readiness y adaptador PAPER local. Si la estrategia devuelve `NO_TRADE`, no se invoca context factory ni runtime. Ante outcome incierto, no hay retry automático.

**CI verificado:** [run de main 39073000856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073000856), `success`, **1578 pruebas aprobadas en 10.30 s** en el SHA de merge. [SMA E2E 39072946681](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39072946681) e [indicadores E2E 39072946684](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39072946684) también pasaron en el SHA de PR #74.

**Dictamen revisado tras PR #74:** preparación técnica ponderada 71/100, no >90. La actualización posterior a PR #76 sube el score actual a 72/100 solo por añadir evidencia de invariancia causal. No se ha probado una captura prospectiva continua de 24 horas ni edge robusto fuera de muestra; el proyecto continúa en `NO_TRADE`. No LIVE, dinero real ni claves privadas.


## 13. Freqtrade: defensa contra lookahead y prueba de sufijo futuro (PR #76)

**Fuente primaria:** [Freqtrade — Lookahead analysis, documentación 2026.5](https://docs.freqtrade.io/en/2026.5/lookahead-analysis/).

Freqtrade advierte que un backtest puede introducir sesgo si el cálculo de indicadores o señales usa valores futuros del dataframe. Su análisis dedicado compara la corrida base con corridas que aíslan señales, pero también señala que una señal que nunca se activa en el dataset analizado no queda verificada automáticamente; ninguna herramienta anti-lookahead elimina todas las formas posibles de sesgo por sí sola.

**Medida añadida en BOT-CRIPTO:** PR #76 incorpora dos regresiones sintéticas. La primera cambia drásticamente únicamente las barras posteriores al `evaluation_end` exclusivo y requiere que el resultado completo de `run_virtual_backtest` no cambie. La segunda genera historiales BTC/ETH horarios alineados con más de 90 días de warm-up, modifica solo el sufijo posterior al índice de decisión y exige que el gate histórico conserve la misma decisión/motivo.

**Evidencia:** PR #76 [mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/76); CI exacto de main [39073489775](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073489775), `success`, 1580 pruebas en 8.21 s. E2E de SMA, indicadores y backtest público de la punta de la PR también pasó.

**Límite:** estos tests verifican invariancia local al sufijo para dos rutas, no el output/artefactos completos de un backtest anual ni validación de disponibilidad/finality a nivel de datos prospectivos. La estrategia no está aprobada por rentabilidad: `NO_TRADE` se mantiene.


## 14. Binance Spot WebSocket: rotación de 24 h y evento `serverShutdown`

**Fuente primaria / documentación del mantenedor:** [Binance Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md).

Binance documenta que una conexión WebSocket de mercado permanece válida hasta 24 horas; el servidor puede emitir `serverShutdown` antes de desconectarla. La misma guía especifica el ping/pong para mantener la conexión y límites para mensajes de control/intentos de conexión. Por eso una conexión que funciona durante minutos no prueba un proceso prospectivo íntegro de 24 horas. El consumidor debe rotar/reconectar sin suponer que no hubo un hueco y debe persistir los eventos antes de depender de ellos.

**Medida local:** PR #78 extiende el adaptador de WebSocket con entrega incremental y handoff temporal individual, reconoce `serverShutdown` como evento de transporte y añade `bot_obrero/prospective_capture.py` para dos streams públicos (BTCUSDT/ETHUSDT, 1h). La captura registra JSONL con hashes, manifiesto, reconexiones y gaps; cualquier evento no válido o fallo de persistencia deja el resultado bloqueado. La validación vuelve a comprobar hashes, orden de secuencia, procedencia, timestamps, duración y criterios del día UTC.

**CI:** [main run 39076567728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076567728), `success`, 1593 pruebas. PR #78 [mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/78).

**Límite:** no se ha ejecutado una captura real aceptada de 24 horas. Un test sintético ni la prueba de E2E del backtest público son evidencia prospectiva; no iniciar el holdout BTC/ETH hasta que el comando `validate` acepte el manifiesto real y se conserven los hashes inmutables.

## 15. CCXT: clasificar los fallos antes de reintentar

**Fuente primaria:** [CCXT Manual (español)](https://docs.ccxt.com/es/docs/manual).

La documentación de CCXT distingue errores de red/timeouts y límites de tasa de errores de exchange que no se arreglan repitiendo ciegamente una llamada (por ejemplo, una orden inválida o autenticación). Un retry genérico puede duplicar efectos cuando el resultado de la primera operación es incierto; los límites de rate-limit deben tratarse según la clase de solicitud y el servidor.

**Medida local:** el colector reinicia la conexión solo ante fallos de transporte, con backoff exponencial limitado. Errores de payload, coherencia temporal, callback, escritura JSONL/fsync o manifiesto detienen la corrida en `BLOCKED`; no se pretende recuperar un fallo de persistencia como si fuera un timeout del exchange. La capa PAPER ya evita reintentos automáticos cuando los efectos económicos podrían haberse confirmado parcialmente.

**Límite:** la política actual es un punto de partida conservador, no un servicio de observabilidad gestionado. Sigue haciendo falta operación real, recuperación controlada y manifest de 24h aceptado antes del holdout. `NO_TRADE` continúa.

## 16. Dictamen actualizado tras PR #78

La infraestructura de captura, reconexión, fsync, hashes y validación independiente está integrada en main. El CI post-merge [39076567728](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076567728) terminó `success`, 1593 pruebas en 11.13 s. El puntaje ponderado sube de 72 a **73/100**, solo por el componente de infraestructura probado. No existe aún captura real aceptada ni edge neto independiente; no hay base para un score >90 ni para cambiar `NO_TRADE`.


## 17. Revisión final: no confiar solo en el manifiesto de captura

El patrón aplicado sigue una recomendación de defensa en profundidad: un resultado escrito como “COMPLETED” no debe bastar para aceptar datos. También deben verificarse los registros que sostienen el resultado, su configuración y sus límites temporales. Esta es una medida de ingeniería preventiva; no presupone que BOT-CRIPTO haya sufrido un ataque de manipulación.

**Correcciones en PR #79:** el colector rechaza Kline con estado/completitud/finality incoherentes; el validador vuelve a calcular integridad desde JSONL y comprueba que el timestamp de recepción de cada evento quede entre el inicio y el fin de la corrida, que se cumpla la hora programada, que la duración de reloj monotónico coincida con la duración de pared y que configuración/manifiesto coincidan. Se agregan tests negativos que modifican el intervalo declarado mientras conservan hashes externos coherentes.

**CI de main exacto:** [run 39076940903](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39076940903), success, 1595 pruebas aprobadas en 10.39 s, SHA a196651842a84cdfc31fde2f3230cd27ff61bf55.

**Estado operativo:** preparación ponderada 73/100. El código para captura prospectiva está en main; no existe todavía una captura real de 24 horas aceptada ni un edge neto independiente. No elevar el score por código que aún no produjo evidencia real. Mantener NO_TRADE, sin LIVE, claves privadas o dinero real.


## 11. Replay económico PAPER: presencia de hash no equivale a verificar el contenido

**Fuente técnica interna:** inspección del replay independiente en `bot_obrero/paper_economic_replay.py` y del snapshot durable en `bot_obrero/paper_adapter.py`. La clase de riesgo es una divergencia entre una evidencia hash-bound y sus campos proyectados: validar que un SHA-256 tenga 64 caracteres hexadecimales no demuestra que los valores de quote guardados sean los que contiene la evidencia hasheada.

**Brecha local observada:** el replay independiente comprobaba el formato de `quote_evidence_hash`, pero no recalculaba la evidencia original ni la cotejaba con los campos persistidos del fill. El snapshot además ocultaba `intent_json`, por lo que el replay no podía reconstruir la intención hasheada. Una modificación de `quote_bid` a un valor menor que `quote_ask`, manteniendo el hash con aspecto correcto, podía evadir ese chequeo del replay aislado. No se afirma que la base de datos haya sido alterada en uso real; el caso es una posibilidad demostrable en el contrato de validación.

**Corrección aplicada en main via [PR #82](https://github.com/eltiootaku01-hue/bot-cripto/pull/82), merge SHA `6728f9097bb6093c980e758ee68f5222d1443f9b`:** `snapshot` expone el payload de intención durable decodificado; el replay recalcula su hash canónico; valida de nuevo el objeto completo de quote con `validate_paper_quote_evidence`; coteja bid/ask, fuente, timestamps, ID/tipo de observación, símbolo, contexto e hash con el fill; rechaza `fill_count` booleano en el replay y en la proyección canónica. [CI post-merge 39079511218](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079511218) pasó 1598 pruebas en 11.85 s.

**Referencias de patrones de ingeniería:**
- Freqtrade documenta el riesgo de que un backtest consuma datos futuros y la comparación de corridas base/aisladas para encontrar divergencias: https://docs.freqtrade.io/en/2026.5/lookahead-analysis/
- SQLite detalla un bug de corrupción WAL y la corrección publicada en 3.51.3; BOT-CRIPTO usa rollback journal `DELETE`, sin evidencia de que el incidente ocurriera aquí: https://www.sqlite.org/wal.html#walresetbug
- Binance registra requisitos de ciclo de vida/reconexión del stream de mercado: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md

**Importante:** estas fuentes explican clases de fallos y mitigaciones; no son evidencia de que BOT-CRIPTO haya sufrido esos mismos incidentes. La evidencia de la corrección local será el diff y el CI del SHA exacto.


## 12. Supply chain de GitHub Actions: etiqueta mayor frente a SHA inmutable

**Fuente primaria:** [GitHub Docs — Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use). GitHub señala que fijar acciones a un SHA de commit completo es la forma de referenciarlas como releases inmutables y mitigar que una etiqueta referenciada cambie de código.

**Hallazgo local observado:** los ocho workflows usaban `actions/checkout@v7`, `actions/setup-python@v7` y/o `actions/upload-artifact@v7`. Son acciones first-party y versiones modernas, pero el tag mayor no identifica un único commit inmutable.

**Mitigación aplicada en main via [PR #83](https://github.com/eltiootaku01-hue/bot-cripto/pull/83), merge SHA `a07b8aac15970f3349f9de54f021cfbeb71e4e20`:** los ocho workflows están fijados a releases oficiales verificadas por SHA completo:
- `actions/checkout v7.0.1`: `3d3c42e5aac5ba805825da76410c181273ba90b1`. Release: https://github.com/actions/checkout/releases/tag/v7.0.1
- `actions/setup-python v7.0.0`: `5fda3b95a4ea91299a34e894583c3862153e4b97`. Release: https://github.com/actions/setup-python/releases/tag/v7.0.0
- `actions/upload-artifact v7.0.2`: `cf430e030ddbb5b0abf93d22962f4752f3646cd9`. Release: https://github.com/actions/upload-artifact/releases/tag/v7.0.2

Las etiquetas se resolvieron contra `refs/tags` en los repositorios oficiales y sus releases publicadas; no se copiaron SHA de forks. Los cambios son de seguridad/reproducibilidad, no de comportamiento financiero. CI completo de PR [39079618993](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079618993) y CI exacto de main tras merge [39079694856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079694856) pasaron con 1598 pruebas en main. Cualquier actualización posterior exige revisar release/diff y cambiar el SHA intencionalmente.

**Límite:** pinnear una dependencia no demuestra por sí solo que su código esté libre de errores, ni prueba la rentabilidad de BOT-CRIPTO.


## 14. Mantenedores de GitHub/PyPA: análisis estático y fallos del propio auditor

**Fuentes oficiales:**
- GitHub mantiene CodeQL para encontrar vulnerabilidades y errores de código; las reglas estándar y extendidas se actualizan con el proyecto: https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-code-scanning
- PyPA `pip-audit` compara los paquetes instalados con bases publicadas de vulnerabilidades: https://github.com/pypa/pip-audit
- El mantenedor de `pip-audit` publicó la versión 2.10.1 después de corregir un `KeyError` al interpretar un registro OSV que omitía el campo opcional `ranges`: https://github.com/pypa/pip-audit/releases/tag/v2.10.1
- CodeQL Action v4.38.2 se publicó el 24 de septiembre de 2026; el proyecto usa el commit upstream completo `2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2` y no una etiqueta móvil: https://github.com/github/codeql-action/releases/tag/v4.38.2

**Brecha local observada:** el repositorio ya tenía CI unitario/E2E y pinning inmutable para Actions, pero no se había comprobado un workflow CodeQL recurrente ni una auditoría automática de vulnerabilidades Python.

**Mitigación aplicada en main via [PR #87](https://github.com/eltiootaku01-hue/bot-cripto/pull/87), merge SHA `43d2cd70b2a350cf2987210166df7711d932e41b`:** CodeQL `security-extended` para Python y Actions; `pip-audit==2.10.1` sobre el entorno instalado; Dependabot semanal para ecosistemas `pip` y `github-actions`. El análisis y la auditoría corren en PRs a main, pushes a main, y de forma semanal/manual. Las Actions quedan fijadas por SHA. CI post-merge [39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044), CodeQL [39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033) y pip-audit [39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089) pasaron.

**Hallazgo corregible obtenido de la primera ejecución de este mismo auditor:** se detectó `PYSEC-2026-1845` en `pytest 8.4.2`, corregido desde la release `9.0.3`. La vulnerabilidad trata el uso de directorios temporales inseguros en Unix; la release del mantenedor documenta la corrección de `CVE-2025-71176`. Fuentes: https://github.com/advisories/GHSA-6w46-j5rx-g56g y https://github.com/pytest-dev/pytest/releases/tag/9.0.3. La PR cambia el extra de test a `pytest>=9.0.3,<9.1` y exige revalidar todas las pruebas más `pip-audit` antes de merge.

**Límites:** los scanners encuentran solo los problemas cubiertos por sus reglas y fuentes; no demuestran ausencia de vulnerabilidades. Si el auditor detecta una vulnerabilidad, la respuesta correcta es revisar y actualizar el paquete (o documentar una mitigación revisada), no ignorar el código de salida. Estas medidas no aportan evidencia de edge económico ni habilitan operaciones LIVE.


## 15. pytest: directorio temporal inseguro — CVE-2025-71176 / PYSEC-2026-1845

**Fuente del mantenedor:** pytest 9.0.3, publicado el 7 de abril de 2026, documenta la corrección del uso de un directorio temporal inseguro en Unix (`CVE-2025-71176`): https://docs.pytest.org/en/latest/changelog.html y https://github.com/pytest-dev/pytest/releases/tag/9.0.3. El aviso OSV/PYSEC marca `PYSEC-2026-1845` como corregido a partir de 9.0.3: https://github.com/advisories/GHSA-6w46-j5rx-g56g.

**Detección local:** la primera ejecución de `pip-audit` en la PR #87 detectó el aviso en `pytest 8.4.2` e informó como versión corregida `9.0.3`. Esto es evidencia de que el nuevo control encontró una dependencia vulnerable, no una suposición sacada del changelog.

**Mitigación aplicada:** el extra de test cambió de `pytest>=8,<9` a `pytest>=9.0.3,<9.1`, manteniendo el minor de pytest acotado para esta validación. La suite de main pasó 1604 pruebas en run [39081474044](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474044), y la auditoría en [39081474089](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474089) devolvió `No known vulnerabilities found`.

**Limitación:** pip-audit indica que el proyecto editable `bot-obrero 0.1.0` no puede encontrarse en PyPI y no se audita por ese índice. CodeQL finalizó con éxito [39081474033](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081474033), pero esto no equivale a una declaración de cero alertas o ausencia total de vulnerabilidades.


## 16. Python: confusión entre booleanos e enteros en el contrato JSON (PR #93)

**Fuente oficial del lenguaje:** el modelo de datos Python define `bool` como subtipo de `int`; `True` se comporta como 1 en contextos numéricos: https://docs.python.org/3/reference/datamodel.html#the-standard-type-hierarchy. Es una característica del lenguaje, no un defecto de Python.

**Hallazgo local reproducible:** el validador comparaba `schema_version` y `sequence` por igualdad simple. Por eso JSON `true` podía pasar como versión `1` o como secuencia de evento 1. La misma regla se aplicó al manifiesto, su configuración y cada envelope de registro. El hash de un registro no cubre los campos externos del envelope, por lo que los tests de regresión reconstruyen los hashes exteriores para probar directamente el contrato.

**Mitigación aplicada en main:** [PR #93](https://github.com/eltiootaku01-hue/bot-cripto/pull/93), merge `8c325d9899ae4a15b06240012b05473f2cd34824`, introduce comprobación de tipo entero exacto y cuatro regresiones: manifiesto, configuración, versión de envelope y secuencia booleana. La suite de la punta [39100827937](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100827937) pasó 1608 pruebas; post-merge main [39100954733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954733) pasó 1608 en 15.66 s. CodeQL [39100954774](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954774) y pip-audit [39100954741](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954741) finalizaron `success`.

**Límite:** esto demuestra rechazo de este defecto de tipado y su regresión; no demuestra que la captura real de 24 h haya ocurrido ni que una estrategia tenga edge económico.

## 17. Binance Spot: el estado de símbolo `CANCEL_ONLY` (anuncio de julio de 2026)

**Fuente primaria del mantenedor:** el changelog oficial de Binance Spot anunció que `CANCEL_ONLY` podía aparecer en `symbolStatus` en Exchange Information desde el despliegue de julio de 2026: https://github.com/binance/binance-spot-api-docs/blob/master/CHANGELOG.md.

**Revisión local:** `BinanceSpotInstrumentMetadata` mantiene el estado como texto, sin enum cerrado que provoque un error de parsing por un valor nuevo. `resolve_active` exige que el estado sea exactamente `TRADING` y que `isSpotTradingAllowed` sea verdadero, por lo que `CANCEL_ONLY` se rechaza como inactivo. Además, `tests/test_phase1_16_binance_instruments.py` ya incluye `CANCEL_ONLY` en el test parametrizado de estados inactivos. La revisión no halló una corrección de código adicional necesaria para ese cambio de API.

**Límite:** el bloqueo sirve para la resolución de instrumento que usa la integración de metadatos. La captura prospectiva usa el WebSocket público exclusivamente para recopilar datos y no coloca órdenes. No se habilita ejecución real.


## 18. websocket-client 1.9.2: carrera en el cierre de WebSocketApp

**Fuente primaria del mantenedor:** el changelog oficial de `websocket-client` para la versión 1.9.2 registra una corrección de carrera en `WebSocketApp.close()` cuando el hilo `run_forever()` borra el socket durante el handshake de cierre; el arreglo se vincula a #1057 y resuelve #1055/#1056: https://github.com/websocket-client/websocket-client/blob/master/ChangeLog. PyPI registra la publicación de la versión 1.9.2 el 31 de agosto de 2026: https://pypi.org/project/websocket-client/.

**Estado de la dependencia local:** `pyproject.toml` fija `websocket-client==1.9.2`, así que el proyecto no queda flotando en una versión anterior sin revisión. La integración de BOT-CRIPTO actual usa `websocket.create_connection()` de forma síncrona en `bot_obrero/binance_websocket.py`; no utiliza `WebSocketApp` ni `run_forever()`. Por lo tanto, el bug puntual publicado por el mantenedor no coincide directamente con la ruta activa auditada y no justifica un cambio de código preventivo sin un caso reproducible local.

**Medida local vigente:** las pruebas del adaptador verifican límites por conexión, errores de transporte, clasificación de `serverShutdown`, y cierre/reconexión coordinados por el colector prospectivo. Mantener la dependencia fijada y reevaluar sus changelogs antes de actualizarla o migrar a `WebSocketApp`/ejecución en segundo plano.

**Límite:** la versión corregida y una suite verde reducen un riesgo concreto, pero no reemplazan la captura real de 24 h, el seguimiento de errores durante la operación sostenida ni la evaluación económica independiente.


## 19. Replay económico PAPER: validar claves malformadas antes de ordenar

**Hallazgo local (PR #96):** la función de replay intentaba ordenar el historial por `(filled_at, client_order_id)` antes de verificar los tipos. Una lista JSON en una de esas claves, combinada con otro valor de tipo string, causaba `TypeError` durante la comparación y escapaba del resultado del auditor. El orden textual de timestamps ISO-8601 tampoco respeta necesariamente el orden temporal cuando coexisten offsets distintos.

**Mitigación en main:** [PR #96](https://github.com/eltiootaku01-hue/bot-cripto/pull/96) valida las estructuras, exige claves strings no vacías y fechas aware válidas, normaliza a UTC y ordena usando el instante más el ID. Dos regresiones verifican que claves malformadas devuelvan `UNKNOWN`, y no una excepción. Suite final de main: [1610 tests, success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918101); CodeQL [success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918074) y pip-audit [success](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918092).

**Límite:** no demuestra que una captura de 24 h se haya ejecutado ni aporta por sí solo evidencia de edge económico. `NO_TRADE` permanece.


## 10. GitHub CodeQL: no mezclar versiones de init y analyze en un mismo workflow

**Tipo de hallazgo:** fallo de configuración local reproducido en GitHub Actions; no es evidencia de un defecto interno en el analizador CodeQL.

**Fuentes upstream:** [release oficial CodeQL Action v4.39.0](https://github.com/github/codeql-action/releases/tag/v4.39.0), [documentación del mantenedor](https://github.com/github/codeql-action) y [opciones de agrupación de Dependabot](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file).

**Observación local del 18-10-2026:**
- PR #90 actualizaba el paso `init` por separado. El log [run 39081560283](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081560283) informa que se cargó configuración de `4.39.0` mientras se ejecutaba `4.38.2`.
- PR #91 actualizaba `analyze` por separado. El log [run 39081616377](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39081616377) informa el caso inverso: configuración de `4.38.2` con ejecución `4.39.0`.
- Tests, E2E y pip-audit de ambas ramas habían pasado; el bloqueo era el desajuste de versión entre pasos que colaboran en el mismo análisis.

**Corrección aplicada en main:**
1. Actualizar juntos `github/codeql-action/init` y `github/codeql-action/analyze` al SHA inmutable `6f2ac81f5cd81fe351a197ba0b00d91d03cd2aee`, verificado contra el tag oficial `v4.39.0`.
2. Añadir el grupo `codeql-action` a la configuración del ecosistema `github-actions` de Dependabot para evitar propuestas separadas de esos pasos.
3. Aplicar y validar el conjunto de cambios con el commit `fdb90440a89e4284ba425689c1c2ec177902a0cf`.

**Evidencia de validación sobre el mismo SHA:** tests [39103016285](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016285), CodeQL [39103016289](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016289) y pip-audit [39103016266](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39103016266) terminaron `success`. Los PR #90 y #91 se cerraron como supersedidos porque el cambio verificado ya está en `main`.

**Límite:** este arreglo demuestra que el workflow volvió a terminar correctamente en el SHA citado. No equivale a afirmar que CodeQL detecta toda vulnerabilidad ni sustituye la inspección futura de las alertas.


## 11. pytest 9.1.1: corregir regresiones de parametrización y fixtures

**Fuente primaria:** [pytest releases — 9.1.1 (19-06-2026)](https://github.com/pytest-dev/pytest/releases/tag/9.1.1).

El mantenedor registró varias correcciones en la versión 9.1.1, entre ellas:
- una regresión de pytest 9.1.0 que hacía fallar la sobrescritura de un fixture parametrizado mediante `@pytest.mark.parametrize(..., indirect=True)`, con el error de parametrización duplicada;
- un defecto lógico en el mensaje de `pytest.RaisesGroup`;
- un problema al cargar `conftest.py` como conftest inicial cuando no se pasaban argumentos y el archivo estaba en una ruta que empezaba por `test*`.

**Aplicación local:** BOT-CRIPTO actualizó en `main` el rango de desarrollo a `pytest>=9.1.1,<9.2` y validó ese cambio en la suite principal. La ejecución aprobada verifica que la actualización no rompe la suite actualmente cubierta; no afirma que el proyecto activara todos los casos upstream ni que todos los fallos de pytest estén presentes en BOT-CRIPTO.

**Control de versión:** Dependabot puede proponer revisiones futuras, pero el rango no se amplía automáticamente más allá de `<9.2` sin revisar resultados del mantenedor y ejecutar la CI sobre el SHA exacto.


## 12. Captura de bookTicker: limitar memoria, reconexiones y fsync

**Fuente primaria para el formato del proveedor:** [Binance Spot WebSocket Streams — Individual Symbol Book Ticker](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md). El payload documentado contiene `u,s,b,B,a,A` (update ID, símbolo, bid/ask y cantidades), pero no incluye normalmente un timestamp de evento de exchange.

**Hallazgo local en revisión de diseño (sin captura real de 24 h):** el primer borrador del colector durable de quotes retenía una copia en memoria de todos los resultados de `listen()`, guardaba un conjunto de todos los IDs observados y hacía `fsync` por registro y reescritura del manifiesto cada 25 updates. Con una fuente de mercado de alta frecuencia, esos patrones podían aumentar memoria de forma proporcional a los eventos, provocar reconexiones frecuentes y añadir I/O innecesario; esto podía convertir una captura prevista de 24 h en una corrida con gaps. No significa que se haya perdido un dataset real: aún no se ha ejecutado la captura prospectiva aceptada.

**Corrección aplicada en main:**
- `BinanceSpotBookTickerWebSocketAdapter.listen(..., retain_results=False)` transmite al sink durable sin conservar un segundo lote de eventos en RAM.
- El colector deja abierta la conexión durante la captura, con un límite defensivo de mensajes muy alto; las desconexiones reales, `serverShutdown`, errores y el límite de tiempo disparan la reconexión/terminación, no cada 1.000 mensajes.
- La detección de update ID usa el máximo/último ID y contadores de duplicados/desorden en memoria constante, en vez de una colección creciente que guarda cada ID único.
- El diario hace flush de cada línea al sistema operativo, `fsync` cada 1.000 registros o al cumplir 1 segundo, y actualiza el manifiesto como máximo una vez por segundo (además de anomalías o umbrales de lote). El manifiesto declara la política de fsync aplicada.
- Se corrigió también el contador de reconexiones: cuenta nuevos intentos de conexión después del primero; el evento de error se mide aparte, sin sumar dos veces la misma reconexión.

**Regresiones asociadas:** `tests/test_binance_book_ticker.py` verifica que el sink streaming no retenga la lista de resultados; `tests/test_prospective_book_ticker_capture.py` verifica que no exista un set acumulativo de IDs, y ejercita duplicados, gaps, hashes y validación sintética de 24 h.

**Validación:** el SHA `723d44b7c68d84fe905d2878203092ba5db02928` pasó [1.641 pruebas](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105159335) y `pip-audit` [39105159320](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105159320); CodeQL [39105159249](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105159249) terminó `success` para el mismo SHA. El SHA contiene el adaptador streaming sin retención en memoria, detección de update IDs de memoria constante, fsync por lote/tiempo y validador conjunto Kline/bookTicker. No declarar CodeQL aprobado hasta que su run termine. Para aceptar la captura real se exige aún ejecutar y validar los manifiestos Kline+bookTicker en una misma ventana de al menos 24 h, preservando `NO_TRADE`.


## 13. bookTicker: rechazar tipos JSON de evento inesperados sin propagar TypeError

**Fuente primaria del contrato:** [Binance Spot WebSocket Streams — Individual Symbol Book Ticker](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md). Un payload bookTicker válido define campos de tipos concretos: símbolo, ID de actualización, precios y cantidades. El parser debe rechazar cualquier tipo inesperado como error de protocolo, no permitir que un detalle de implementación filtre una excepción Python.

**Hallazgo local:** el parser inicial comparaba `event["e"]` con un conjunto de valores permitidos usando membresía en `set`. Si una entrada JSON malformada proporcionaba una lista o diccionario como tipo de evento, el valor podía ser no-hashable y Python lanzar `TypeError` antes de devolver el error protocolario acordado.

**Corrección aplicada:** el parser ahora realiza comparaciones explícitas contra los valores de evento válidos y convierte lista, diccionario, booleano o entero inesperado en `BinanceWebSocketProtocolError`. No se aceptan esos mensajes como quotes ni se inventa evidencia.

**Regresión:** `tests/test_binance_book_ticker.py` añade casos para los cuatro tipos inválidos.

**Validación de la revisión:** en SHA `0913bbee0c6090bc182d9955bd711e4a5130ffca`, la suite pasó **1645 pruebas** ([run 39105436364](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436364)) y `pip-audit` pasó ([run 39105436379](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436379)). CodeQL [39105436371](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39105436371) terminó `success`; los tres checks están en verde sobre el mismo SHA. La captura real combinada Kline+bookTicker de 24 h sigue pendiente y la decisión es `NO_TRADE`.
