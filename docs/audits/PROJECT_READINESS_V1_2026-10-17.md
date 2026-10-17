# BOT-CRIPTO — Evaluación de preparación técnica y ruta hacia >90%

**Evaluación:** 2026-10-17  
**Base funcional integrada:** `fd51e0dd1ff3f710680a98b685a6b9369c74a600`  
**HEAD de documentación consultado antes de esta evaluación:** `1eba201b06f8e2dccc62e94e6d9ffeb42e57734e`  
**Propósito del porcentaje:** preparación demostrada para un bot de investigación y PAPER; no es porcentaje de líneas de código, probabilidad de beneficio ni autorización para operar con dinero real.

## Dictamen

**Preparación operativa basada en evidencia: 64/100. El umbral >90 todavía no está alcanzado.**

El porcentaje usa pesos publicados y puntúa solo capacidades con evidencia observada. La decisión económica sigue siendo `NO_TRADE`. Un buen CI no acredita una estrategia rentable, y una integración PAPER recuperable no acredita un productor operativo de señales ni 24 horas de captura prospectiva.

## Matriz de aceptación ponderada

| Área | Peso | Puntaje | Evidencia para el puntaje | Brecha que impide puntaje completo |
|---|---:|---:|---|---|
| Integridad del repositorio y CI reproducible | 10 | 10 | PR #71 fusionada; run de tests en la punta documental actual con 1561 pruebas aprobadas; cambios en main y checks por SHA | Seguir exigiendo CI en cada cambio |
| Adquisición pública y contratos temporales | 15 | 11 | Adaptadores Binance REST/WebSocket, contrato canónico de vela, límites de paginación, rechazo de duplicados/huecos y timestamps con zona válida | La disponibilidad/finality histórica puede ser UNKNOWN; no hay captura prospectiva validada de 24 h |
| Análisis, backtest y defensa anti-lookahead | 15 | 13 | SMA/EMA/RSI canónicos, cálculo incremental y decisión simulada en cierre con fill en apertura siguiente; suite general y E2E pasan | Sigue pendiente la prueba de invariancia frente a sufijo futuro para el estudio BTC→ETH completo y la integridad prospectiva |
| Evidencia económica de estrategia | 20 | 5 | Backtests, benchmarks cash/buy-and-hold y evaluación walk-forward están implementados; el resultado conserva honestamente `NO_TRADE` | Las estrategias evaluadas perdieron dinero neto o no demostraron robustez; falta una validación independiente que justifique avance |
| Ciclo mercado→Signal→TradeProposal→riesgo→PAPER | 15 | 8 | Contratos Analysis/Snapshot/Strategy/Signal, puente canónico a propuesta, admisión financiera y runtime PAPER; prueba sintética integrada | No existe todavía un productor activo que transforme velas públicas actuales, cerradas y temporalmente admisibles en una Signal autorizable según una estrategia validada |
| Persistencia económica y recuperación PAPER | 15 | 14 | Quote versionado/hash-bound; replay; conciliación de cuenta/reserva/binding/ledger; prueba de fallo tras commit del fill, reinicio y dos reconciliaciones idempotentes; 1561 tests pasan | Los archivos SQLite independientes no forman una transacción global. Reparación parcial debe seguir en UNKNOWN/BLOCKED hasta conciliar |
| Operación sostenida, supervisión y captura prospectiva | 10 | 3 | Endpoints públicos, workflows de CI y pruebas E2E existentes | No se ha demostrado captura continua de 24 h, reconexión/reparación de huecos con evidencia, alertas operativas y aceptación de un estudio nuevo completo |
| **TOTAL** | **100** | **64** | | |

### Cómo interpretar la cifra

- **64/100** es una puntuación de preparación demostrada bajo la matriz anterior, no una estimación del trabajo de programación restante.
- **1.561 pruebas aprobadas** indica que el conjunto de pruebas automatizadas pasó en el SHA concreto consultado; no demuestra que toda situación de producción haya sido probada.
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

**Por qué:** Freqtrade documenta que cargar y calcular todo el dataframe de una vez puede filtrar velas futuras a la señal. El análisis anti-lookahead dedicado detecta cambios en indicadores, entradas y salidas entre backtests base y aislados: [Freqtrade — Lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/).

### P0 — Terminar el productor de señal y demostrar el ciclo PAPER con datos admisibles
**Condición:** el puente `Signal → TradeProposal` no reemplaza el productor de Signal. Las señales deben salir solo de velas 4h explícitamente cerradas, con orden temporal correcto y disponibilidad admitida; datos UNKNOWN, huecos, stale quotes o una estrategia no aprobada deben producir bloqueo, no una propuesta.

**Criterios de salida:** test de extremo a extremo que use un dataset reproducible, confirme los IDs/horas de observación usados, calcule Analysis/Snapshot/Strategy/Signal, valide expiración, aplique política PAPER explícita, pase por riesgo/admisión y llegue a la cuenta PAPER; verificar también ramas sin señal, señal caducada, datos desconocidos y decisión `NO_TRADE`. Ninguna etapa debe convertir datos UNKNOWN en permiso de operación.

### P0 — Captura prospectiva de 24 h antes de asignar T0 al estudio nuevo
**Condición:** no declarar validado el estudio prospectivo BTC/ETH hasta completar 24 horas de adquisición sin huecos inexplicados y con timestamps de recepción/disponibilidad, cierre de velas y reconexión verificables.

**Criterios de salida:** reportar los días UTC completos, velas esperadas/observadas/duplicadas/faltantes, cierres válidos, primer instante de recepción, huecos reparados y hash del dataset. No backdatear disponibilidad ni iniciar el periodo de evaluación anual antes de congelar esa evidencia. El protocolo de investigación prospectiva sigue `DESIGNED / NOT RUN`.

### P1 — Conservación de SQLite y recuperación de fallos
La integración PAPER usa rollback journal `DELETE` y testea el modo efectivo. SQLite describió una corrupción poco frecuente bajo condiciones de concurrencia de varias conexiones en WAL y corrigió el WAL-reset bug en SQLite 3.51.3; el riesgo descrito depende de ese patrón y no es prueba de que BOT-CRIPTO sufriera corrupción: [SQLite — WAL-reset bug](https://www.sqlite.org/wal.html#walresetbug), [SQLite 3.51.3](https://www.sqlite.org/releaselog/3_51_3.html). Mantener `DELETE` es el mitigador actual; si se vuelve a WAL, fijar primero la versión SQLite corregida y probar concurrencia y recuperación.

No prometer atomicidad entre bases SQLite separadas. Ante un estado parcial: `UNKNOWN/BLOCKED`, sin nuevo envío; reconciliar proyecciones idempotentemente.

### P1 — Mantener los workflows de GitHub Actions actualizados
GitHub retiró Node 20 de los runners el 23 de septiembre de 2026 y los JavaScript actions pasaron a Node 24: [GitHub Changelog](https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/). Main ya usa versiones first-party compatibles con Node 24; comprobar el CI del SHA exacto en cada cambio.

### P1 — Fills simulados frente al precio real
La documentación de QuantConnect señala que fills de backtest pueden divergir por latencia, datos obsoletos, precios disponibles y modelos de slippage: [QuantConnect — reconciliation](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation). Por eso los costes/modelos PAPER se documentan como hipótesis y la reconciliación usa evidencia persistida; no se extrapolan los resultados a rentabilidad real.

## Evidencia GitHub del estado que se puntúa

- Integración PAPER: [PR #71 — mergeada](https://github.com/eltiootaku01-hue/bot-cripto/pull/71).
- CI de main tras el merge: [run 39069869061](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069869061), `success`, 1561 pruebas.
- CI de punta de integración: [tests 39069780762](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780762), [SMA 39069780537](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780537), [indicadores 39069780522](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780522), [backtest/Product V2 con datos públicos 39069780401](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39069780401): todos `success` en `0d139afdf7719d86b7d938ef0cb2a2e8dac949dc`.
- CI del HEAD documental observado al preparar este informe: [run 39070159226](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39070159226), `success`, 1561 pruebas. HEAD consultado: `1eba201b06f8e2dccc62e94e6d9ffeb42e57734e`.
- Estado de continuidad: [PROJECT_STATE.md](../../cerebro/PROJECT_STATE.md).
- Auditoría de riesgos y addendum B1-R: [MAIN_EXTERNAL_TECHNICAL_AUDIT_2026-10-17.md](./MAIN_EXTERNAL_TECHNICAL_AUDIT_2026-10-17.md).
- Casos externos y mitigaciones: [EXTERNAL_FAILURE_CASE_STUDIES.md](../research/EXTERNAL_FAILURE_CASE_STUDIES.md).

## Límites de seguridad

La puntuación no autoriza trading real. No habilitar LIVE, claves privadas, endpoints privados ni operaciones con dinero real. El próximo incremento de preparación debe venir de evidencia temporal/económica nueva, pruebas reproducibles y un ciclo PAPER seguro, nunca de relajar `NO_TRADE`.
