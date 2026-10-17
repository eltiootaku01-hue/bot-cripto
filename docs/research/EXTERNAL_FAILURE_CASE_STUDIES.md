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

**Corrección en esta revisión:** se resuelven los límites efectivos de mercado campo a campo. Si un campo `MARKET_LOT_SIZE` está en cero, solo se usa el correspondiente `LOT_SIZE` normal si es positivo y finito. Si no hay un sustituto válido, la simulación queda bloqueada. Se agregan tests de ambos casos. Así se evita tanto la excepción por paso cero como asumir una cantidad ilimitada.

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

GitHub retiró Node 20 en los runners el 23 de septiembre de 2026. Los jobs de esta rama registraban warnings al usar `actions/checkout@v4`, `actions/setup-python@v5` y `actions/upload-artifact@v4`, que se forzaban a Node 24. Se actualizarán los workflows del proyecto a releases oficiales actuales compatibles con Node 24. La guía de GitHub sobre [checkout seguro de PR](https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/) además muestra por qué no se debe ejecutar código no confiable de una PR bajo permisos privilegiados. Los workflows auditados usan `pull_request`, `push` o `workflow_dispatch`; ninguno necesita cambiar a `pull_request_target`.

## 6. Resultados económicos disponibles y límites

La prueba independiente de 365 días EMA/RSI dio estos resultados netos modelados:

| Activo / costes | Retorno neto modelado | Drawdown máximo |
|---|---:|---:|
| BTC, base | -29.2995% | -30.0234% |
| ETH, base | -21.8140% | -24.5854% |
| BTC, estrés | -40.2913% | -40.7285% |
| ETH, estrés | -29.4779% | -30.9149% |

Esto demuestra que esa configuración histórica no acreditó edge. No es una predicción del futuro. El estudio pareado BTC→ETH aún debe superar de nuevo el workflow tras la corrección de filtros, además de las tres ventanas walk-forward, el escenario de costes alto y el replay offline con idénticos hashes.

## 7. Bloqueos que siguen separados

1. La estrategia EMA/RSI probada en modo independiente dio pérdidas; no debe activarse como estrategia ganadora.
2. El estudio BTC→ETH necesita un nuevo CI exitoso con dataset, evaluación, validación y replay.
3. El replay económico PAPER verifica el ledger local. No acredita recuperación integrada entre reserva, binding, orquestador y `SQLiteIdempotencyLedger` después de un fill confirmado.
4. Incluso un resultado histórico positivo y reproducible no garantiza ganancias futuras; la decisión requiere benchmark neto, walk-forward sin reoptimización, sensibilidad a costes y evidencia prospectiva PAPER.

**Decisión operativa: `NO_TRADE`. No hay autorización para órdenes LIVE ni promesa de rentabilidad.**
