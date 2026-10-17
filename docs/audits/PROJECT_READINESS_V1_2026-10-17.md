# BOT-CRIPTO — Evaluación de preparación técnica y ruta hacia >90%

**Evaluación:** 2026-10-17  
**Base funcional integrada (PAPER + productor de señal + composición a riesgo/PAPER):** `a00e78a634ea7bac4ad266a3ae7b3394c709b6e0`  
**Actualización de puntuación:** 2026-10-17, después de integrar PR #74; criterios revisados por el CI de main y el E2E integrado.  
**Propósito del porcentaje:** preparación demostrada para un bot de investigación y PAPER; no es porcentaje de líneas de código, probabilidad de beneficio ni autorización para operar con dinero real.

## Dictamen

**Preparación operativa basada en evidencia: 71/100. El umbral >90 todavía no está alcanzado.**

El porcentaje usa pesos publicados y puntúa solo capacidades con evidencia observada. La decisión económica sigue siendo `NO_TRADE`. Un buen CI no acredita una estrategia rentable, y una integración PAPER recuperable no acredita un productor operativo de señales ni 24 horas de captura prospectiva.

## Matriz de aceptación ponderada

| Área | Peso | Puntaje | Evidencia para el puntaje | Brecha que impide puntaje completo |
|---|---:|---:|---|---|
| Integridad del repositorio y CI reproducible | 10 | 10 | PR #71–#74 fusionadas; CI de main posterior a PR #74 con 1578 pruebas y E2E SMA/indicadores de la punta PR #74 aprobados | Seguir exigiendo CI en cada SHA de main |
| Adquisición pública y contratos temporales | 15 | 12 | Adaptadores Binance REST/WebSocket, validación de vela cerrada y continuidad, finality diferenciada por fuente y procedencia conservada | Finality REST derivada del reloj local; `available_at`/`finality` históricos pueden seguir UNKNOWN; falta captura prospectiva validada de 24 h |
| Análisis, backtest y defensa anti-lookahead | 15 | 13 | SMA/EMA/RSI canónicos, cálculo incremental y decisión simulada en cierre con fill en apertura siguiente; suite general y E2E pasan | Sigue pendiente la prueba de invariancia frente a sufijo futuro para el estudio BTC→ETH completo y la integridad prospectiva |
| Evidencia económica de estrategia | 20 | 5 | Backtests, benchmarks cash/buy-and-hold y evaluación walk-forward están implementados; el resultado conserva honestamente `NO_TRADE` | Las estrategias evaluadas perdieron dinero neto o no demostraron robustez; falta una validación independiente que justifique avance |
| Ciclo mercado→Signal→TradeProposal→riesgo→PAPER | 15 | 14 | PR #74 compone la salida de la pipeline canónica con contexto de riesgo completo, snapshot PAPER persistido, admisión/reserva y runtime PAPER existente; E2E sintético cubre fill y conciliación. `NO_TRADE` no invoca el runtime | El caller aún debe proporcionar la observación pública de libro, context factory, política y evidencia de readiness; no hay captura sostenida de producción y la estrategia no está validada económicamente |
| Persistencia económica y recuperación PAPER | 15 | 14 | Quote versionado/hash-bound; replay; conciliación de cuenta/reserva/binding/ledger; prueba de fallo tras commit del fill, reinicio y dos reconciliaciones idempotentes; CI reciente 1576 tests pasa | Los archivos SQLite independientes no forman una transacción global. Reparación parcial debe seguir en UNKNOWN/BLOCKED hasta conciliar |
| Operación sostenida, supervisión y captura prospectiva | 10 | 3 | Endpoints públicos, workflows de CI y pruebas E2E existentes | No se ha demostrado captura continua de 24 h, reconexión/reparación de huecos con evidencia, alertas operativas y aceptación de un estudio nuevo completo |
| **TOTAL** | **100** | **71** | | |

### Cómo interpretar la cifra

- **71/100** es una puntuación de preparación demostrada bajo la matriz anterior, no una estimación del trabajo de programación restante.
- **1.578 pruebas aprobadas** en el SHA post-merge `a00e78a634ea7bac4ad266a3ae7b3394c709b6e0` (run [39073000856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073000856)) prueban la suite en ese commit concreto; no demuestran que toda situación operativa esté cubierta ni que la estrategia sea rentable.
- El objetivo >90 debe tratarse como un *release gate*, no como una meta de apariencia. No se dan puntos por una función solo diseñada o por una prueba que no se ejecutó.
- `NO_TRADE` no es un defecto que deba quitarse para aumentar el porcentaje. Es el bloqueo correcto mientras falte evidencia económica.

## Hallazgos priorizados y soluciones

### P0 — Estrategia robusta, sin anticipación y en un periodo nuevo
**Condición:** el estado económico permanece `NO_TRADE`. No activar EMA/RSI ni BTC-gated solo porque el backtest sea reproducible.

**Criterios de salida:**
1. Congelar estrategia, parámetros, dataset, costes y benchmarks antes de mirar el holdout.
2. Evaluar frente a cash y buy-and-hold con costes idénticos; separar evaluación y walk-forward no solapado.
3. Cambiar sufijos futuros y demostrar que no cambian señales, indicadores ni fills anteriores al corte.
4. Registrar resultados netos, drawdown, fees, spread/slippage modelados y muestras completas/incompletas.
5. Aceptar avance solo si los umbrales económicos definidos antes de la corrida se cumplen en el periodo independiente; si no, mantener `NO_TRADE`.

**Por qué:** Freqtrade documenta que cargar y calcular todo el dataframe de una vez puede filtrar velas futuras a la señal. El análisis anti-lookahead dedicado detecta cambios en indicadores, entradas y salidas entre backtests base y aislados: [Freqtrade — Lookahead analysis 2026.5](https://docs.freqtrade.io/en/2026.5/lookahead-analysis/).

### P0 — Composición de señal con el runtime financiero PAPER
**Estado:** completado en PR #74. `bot_obrero/market_signal_paper_runtime.py` conecta `generate_market_signal` con el `FinancialExecutionRuntime` existente solo ante `PROPOSAL_READY`; valida señal y propuesta enlazadas, análisis canónico, cuenta PAPER persistida, contexto completo, book ticker público Binance Spot y `AvailabilityBinding`. Requiere el adaptador local `PaperExecutionAdapter` y no tiene fallback a LIVE. El E2E sintetiza el recorrido señal→propuesta→RiskEngine→autorización/admisión/reserva→fill PAPER→reconciliación; el caso `NO_TRADE` no inicia runtime.

**Brecha residual:** el módulo es un punto de composición invocable, no un servicio de captura permanente. El caller aún debe aportar el context factory, book ticker público, política de riesgo y readiness evidence. Deben validarse en una fase separada los errores de disponibilidad/latencia y las condiciones operativas sostenidas. No ejecutar con dinero real ni habilitar LIVE.

### P0 — Captura prospectiva de 24 h antes de asignar T0 al estudio nuevo
**Condición:** no declarar validado el estudio prospectivo BTC/ETH hasta completar 24 horas de adquisición sin huecos inexplicados y con timestamps de recepción/disponibilidad, cierre de velas y reconexión verificables.

**Criterios de salida:** reportar los días UTC completos, velas esperadas/observadas/duplicadas/faltantes, cierres válidos, primer instante de recepción, huecos reparados y hash del dataset. No backdatear disponibilidad ni iniciar el periodo de evaluación anual antes de congelar esa evidencia. El protocolo de investigación prospectiva sigue `DESIGNED / NOT RUN`.

### P1 — Conservación de SQLite y recuperación de fallos
La integración PAPER usa rollback journal `DELETE` y testea el modo efectivo. SQLite describió una corrupción poco frecuente bajo condiciones de concurrencia de varias conexiones en WAL y corrigió el WAL-reset bug en SQLite 3.51.3; el riesgo descrito depende de ese patrón y no es prueba de que BOT-CRIPTO sufriera corrupción: [SQLite — WAL-reset bug](https://www.sqlite.org/wal.html#walresetbug), [SQLite 3.51.3](https://www.sqlite.org/releaselog/3_51_3.html). Mantener `DELETE` es el mitigador actual; si se vuelve a WAL, fijar primero la versión SQLite corregida y probar concurrencia y recuperación.

No prometer atomicidad entre bases SQLite separadas. Ante un estado parcial: `UNKNOWN/BLOCKED`, sin nuevo envío; reconciliar proyecciones idempotentemente.

### P1 — Mantener los workflows de GitHub Actions actualizados
GitHub retiró Node 20 de los runners el 23 de septiembre de 2026 y los JavaScript actions pasaron a Node 24: [GitHub Changelog](https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/). Main ya usa versiones first-party compatibles con Node 24; comprobar el CI del SHA exacto en cada cambio.

### P1 — Fills simulados frente al precio real
QuantConnect publicó en junio de 2026 una corrección a LEAN para impedir que órdenes sobre barras horarias/diarias se ejecutaran contra el cierre anterior ya obsoleto, porque podía inflar los resultados de backtest: [QuantConnect — Closing the stale-price loophole](https://www.quantconnect.com/announcements/20949/closing-the-stale-price-loophole-on-daily-and-hourly-data/p1). La nueva composición PAPER rechaza cotizaciones posteriores a la decisión, fuera de orden o caducadas; los costes siguen siendo hipótesis y no se extrapolan a rentabilidad real.

## Evidencia GitHub del estado que se puntúa

- Integración PAPER y reconciliación: [PR #71 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/71).
- Composición canónica señal→riesgo→PAPER: [PR #74 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/74), CI de main [39073000856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39073000856), `success`, 1578 pruebas en 10.30 s.
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
