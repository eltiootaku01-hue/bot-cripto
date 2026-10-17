# BOT-CRIPTO — Casos públicos de fallos, actualizaciones y mitigaciones

**Fecha de revisión:** 17 de octubre de 2026  
**Rama inspeccionada:** `feat/paper-operational-runtime-v1`  
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

**Fuentes:** [Freqtrade — lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/) y [Freqtrade — errores comunes de estrategia](https://docs.freqtrade.io/en/stable/strategy-customization/).

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
4. El replay económico PAPER verifica el ledger local. No acredita recuperación integrada entre reserva, binding, orquestador y `SQLiteIdempotencyLedger` después de un fill confirmado.
5. Incluso un resultado histórico positivo y reproducible no garantiza ganancias futuras. Se exige validación prospectiva PAPER reconciliada y repetible antes de plantear cualquier autorización posterior.

**Decisión operativa: `NO_TRADE`. No hay autorización para órdenes LIVE ni promesa de rentabilidad.**
