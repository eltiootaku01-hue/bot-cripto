# BOT-CRIPTO — dictamen de investigación: pronóstico de retorno con filtro de coste V1

**Fecha del dictamen:** 2026-10-21 UTC  
**Repositorio:** `eltiootaku01-hue/bot-cripto`  
**Rama de este informe:** `research/cost-aware-ml-return-filter-50-100-v1`  
**Commit base de `main` al crear la rama:** `c523c47364b6fb4febd9c48385bc63bcde87ea61`  
**Estado:** `HYPOTHESIS_SELECTED / NOT_IMPLEMENTED / NOT_RUN`  
**Dictamen económico:** `UNKNOWN`  
**Decisión operativa:** `NO_TRADE`

> Este commit registra únicamente documentación de investigación. No implementa ni habilita una estrategia; no ejecuta backtests ni workflows; no modifica `main`, señales existentes, tests ni los sistemas de órdenes. La rama parte del SHA identificado arriba.

## 1. Dictamen ejecutivo

La hipótesis nueva que merece una prueba prospectiva independiente es un **modelo supervisado de predicción del retorno bruto a 24 horas con ejecución selectiva sensible a los costes**, usando XGBoost y solo BTCUSDT Spot largo/cash. Es diferente de la búsqueda ya hecha de reglas EMA/RSI, momentum, Donchian, reversión a la media, rotación BTC/ETH y del contrato TP +1 % / SL −0,5 %: aquí la señal no es una combinación de umbrales técnicos elegida por rentabilidad histórica, sino una predicción de retorno continua, cuyo uso queda condicionado a que supere un umbral económico calculado a partir de los costes observables.

**No se declara ganador.** Esta elección es una hipótesis de investigación apoyada por literatura, no una medida de ventaja en este repositorio. No se ha entrenado el modelo de esta ficha, no se ha ejecutado backtest y no existe resultado neto atribuible a él. El diseño debe pasar a implementación aislada y congelarse antes de iniciar la evaluación futura. `NO_TRADE` continúa vigente.

## 2. Evidencia anterior revisada y qué significa

1. [Torneo BTC/ETH V2, resultado persistido](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/docs/research/BTC_ETH_STRATEGY_TOURNAMENT_V2_RESULTS_2026-10-19.md): exploró 6.232 configuraciones de cuatro familias y encontró **cero** candidatas que vencieran simultáneamente a efectivo y ambos buy-and-hold en todos los folds. Incluso su primer puesto descriptivo perdió en la reexaminación histórica larga. El capital ficticio de aquel experimento era 31 USDT.
2. [Protocolo prospectivo BTC/ETH V2](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/docs/research/BTC_ETH_STRATEGIES_V2_PROTOCOL.md) y [operador V2](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/docs/research/BTC_ETH_PROSPECTIVE_V2_OPERATOR.md): ya fijan siete reglas, benchmarks y un futuro periodo de 365 días. La captura real aceptada y un T0 preregistrado siguen pendientes según la [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98). Este nuevo candidato se mantiene separado; **no se agrega retroactivamente** a la lista de siete reglas ni se cambian sus criterios.
3. [TP/SL V1 y su descomposición](https://github.com/eltiootaku01-hue/bot-cripto/issues/134#issuecomment-6319393846): las señales congeladas pierden también en el contrafactual de coste cero y las fricciones empeoran sustancialmente el resultado. Por tanto no se reutilizan esas señales ni se reajustan sus barreras.
4. [Candidata ETH Bollinger + EMA180](https://github.com/eltiootaku01-hue/bot-cripto/blob/main/docs/research/ETH_BBMR_4H_RESEARCH_CANDIDATE.md): es otra hipótesis ya estudiada y no promovida; no se recicla como la estrategia nueva.

Las métricas de los experimentos anteriores son evidencia sobre esas reglas y ventanas, no evidencia sobre el candidato ML propuesto aquí. Los registros históricos usados hasta octubre de 2026 están expuestos a selección previa; ninguno de sus cortes se declarará holdout fresco para evaluar el candidato.

## 3. Por qué merece una prueba — y límites de la literatura

- Bysik y Ślepaczuk, *Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting* (arXiv, publicado en 2026): encuentran que las señales ingenuas por signo de la predicción fallan al añadir costes; un filtro de ejecución dependiente del coste reduce la rotación y algunas configuraciones XGBoost resultan positivas, pero los propios autores describen evidencia estadística frágil y sin dominancia robusta tras bootstrap. [Artículo y resultados](https://arxiv.org/abs/2606.00060).
- Gaona, Rangel-Madariaga y Uribe, *Intraday trading strategy selection under transaction costs with machine learning* (Frontiers in Applied Mathematics and Statistics, 1-oct-2026): en 40 activos y distintos horizontes, solo una parte limitada de estrategias sigue siendo rentable tras incorporar costes. Esto apoya medir rendimiento económico y sensibilidad al horizonte, no presumir que ML genera beneficios. [Artículo](https://doi.org/10.3389/fams.2026.1888767).
- Deprez y Frömmel, *Are simple technical trading rules profitable in bitcoin markets?* (International Review of Economics & Finance, 2024): estudia 75.360 reglas y aplica procedimientos de hipótesis múltiples y evaluación fuera de muestra. Sirve de advertencia metodológica: la selección de la mejor regla entre muchas puede sobrestimar la ventaja. [Artículo](https://doi.org/10.1016/j.iref.2024.05.003).

Estas publicaciones justifican probar una hipótesis cost-aware; **no demuestran que el modelo aquí propuesto sea rentable**, que sus cifras se repliquen en BTCUSDT actual ni que pueda producir 50–100 USDT.

## 4. Objetivo, capital y horizonte fijados para el diseño

| Parámetro | Regla de investigación |
|---|---|
| Capital inicial hipotético | **100 USDT**; sin depósitos ni retiradas durante la evaluación |
| Objetivo de beneficio | **+50 USDT netos** al cierre como umbral mínimo; +100 USDT como objetivo superior de referencia, no límite máximo |
| Rendimiento equivalente | +50 % a +100 % de capital inicial, después de costes |
| Activo y mercado | BTCUSDT Spot, largo o efectivo; sin cortos, margen, derivados ni apalancamiento |
| Resolución de señales | Velas cerradas de 1 hora, UTC |
| Horizonte de cada predicción / tenencia | Retorno a 24 horas; una posición como máximo, sin solapamiento |
| Periodo económico primario | 365 días futuros completos, `[T0, T1)), con `T1 = T0 + 8.760 horas` |
| T0 | **No fijado**. Solo después de aceptar captura real, congelar código, modelo, dependencias, configuración y hashes, y registrar un commit de preregistro antes de la primera hora puntuada |
| Presupuesto de riesgo | Drawdown máximo ≤20 % en BASE y ≤25 % en cada uno de STRESS_1 y STRESS_2 |
| Estado de esta ficha | Diseño de candidato; falta implementación, freeze de artefactos, captura válida y evaluación prospectiva |

La meta monetaria es una exigencia del experimento, no una expectativa garantizada. Con 100 USDT, ganar 50–100 requiere +50–100 % en un año; un objetivo elevado junto con un drawdown limitado puede no ser alcanzable. Si no se cumple la puerta, no se incrementa riesgo ni apalancamiento para forzarla.

## 5. Regla candidata congelada propuesta

### 5.1 Datos y features

Fuente para la evaluación: datos públicos BTCUSDT Spot de Binance; vela cerrada de 1 h y quotes públicos `bookTicker`. Para cada señal en la hora `t`, todas las features deben poder reconstruirse exclusivamente a partir de velas recibidas y finalizadas hasta `t`. No se usarán columnas con resultados futuros ni quotes retroactivas como prueba de disponibilidad.

Vector de features fijo, sin selección automática:

- Retornos logarítmicos rezagados: 1, 3, 6, 12, 24, 72 y 168 horas.
- Desviación estándar de retornos horarios en ventanas terminadas de 24, 72 y 168 horas.
- Rango normalizado `(high - low) / close` de la última vela cerrada y media del rango normalizado de 24 horas.
- Cuerpo de vela firmado `log(close / open)` de la última vela.
- Posición del cierre en el rango de la vela `(close - low) / (high - low)`; cuando el rango sea cero, usar un valor nulo explícito y una marca de rango cero.
- Log-volumen horario y cociente del volumen de la última vela frente a la mediana de las 168 horas previas, solo si la ventana está completa.

No se añadirán indicadores, señales BTC/ETH, variables de sentimiento, datos on-chain ni nuevas features después de mirar resultados. Si una feature no puede calcularse sin usar información futura, se elimina mediante una nueva versión del protocolo antes de T0, no durante la evaluación.

### 5.2 Modelo y etiqueta de entrenamiento

- Modelo único: regresor XGBoost para estimar retorno aritmético bruto de BTC en las siguientes 24 horas: `y_t = close[t+24h] / close[t] - 1`.
- Configuración inicialmente propuesta y no ajustable por rendimiento del histórico: `objective=reg:squarederror`, `n_estimators=300`, `max_depth=3`, `learning_rate=0.03`, `subsample=0.8`, `colsample_bytree=0.8`, `min_child_weight=10`, `reg_lambda=10`, `reg_alpha=0`, `gamma=0`, `tree_method=hist`, `random_state=20261021`, `n_jobs=1`. Se fijarán versión y hashes exactos de XGBoost, Python y lockfile antes de T0.
- Entrenamiento único antes de T0 con filas históricas anteriores a T0 únicamente. Excluir las últimas 24 horas de entrenamiento para que todas las etiquetas estén cerradas antes de T0. Congelar SHA-256 del conjunto de entrenamiento, esquema, código de features y artefacto entrenado. No reentrenar, hacer early stopping, escoger checkpoints ni ajustar hiperparámetros durante los 365 días.
- El histórico disponible puede usarse para ajustar los coeficientes del modelo, pero **no** como evidencia de rendimiento de la estrategia. Sus campos `available_at`/finality no son fiables como prueba de causalidad histórica; el único resultado que puntúa el objetivo será el futuro capturado punto-en-tiempo. La adecuación del tamaño de muestra y la distribución de clases/retornos se comprobarán antes de T0 sin seleccionar modelos por beneficio de backtest.
- Si la implementación no puede congelar el modelo reproduciblemente, producir features causales o conservar el manifiesto del entrenamiento, no comienza la evaluación.

### 5.3 Decisión y ejecución virtual

1. Después de cada vela horaria cerrada, generar un pronóstico único de retorno bruto a 24 h.
2. Calcular el coste estimado de una ida y vuelta con la comisión fijada, slippage fijado y spread completo observado en el quote de decisión. La fórmula del filtro queda fija: entrar solo cuando `predicted_return > 1.5 × estimated_round_trip_cost`. El factor 1,5 no se optimiza después. El spread usado como umbral es una estimación; el replay de una operación debe cargar los fills reales simulados de compra al ask y de venta al bid, sin sumar el spread por segunda vez.
3. Usar el primer quote válido recibido después de crear la decisión, con `bid > 0`, `ask >= bid` y edad máxima de 2 segundos. Si no existe quote elegible, no inventar un precio: bloquear la decisión o marcar el estudio incompleto según corresponda.
4. Si ya hay una posición, no abrir otra. Una entrada consume como máximo 95 % del efectivo disponible, incluidas las comisiones, redondeada hacia abajo con los filtros Spot verificados antes de T0. No hay órdenes reales; son fills virtuales auditables.
5. Mantener hasta el primer quote elegible recibido a partir de las 24 horas de la entrada; liquidar a bid y registrar comisión y slippage de salida. No agregar stop, take-profit ni salida discretionary a posteriori. Tras la salida, evaluar la siguiente decisión horaria que cumpla todos los criterios.
6. Si faltan datos/quotes requeridos para una decisión, una salida o una marca de equity, no se rellena usando retrospectivamente OHLC ni backfill. El periodo pasa a `INCOMPLETE / NO_TRADE` si no se pueden demostrar los requisitos de integridad.

## 6. Costes, benchmarks y medición

Los mismos tiempos de entrada/salida, reglas de costes y convenciones de valoración se aplican a la estrategia y a los tres benchmarks. El bid/ask proviene de la captura prospectiva; no se sustituye por spreads hipotéticos si el quote real está disponible.

| Escenario fijo | Comisión por fill | Slippage adverso adicional por lado | Spread |
|---|---:|---:|---|
| BASE | 0,10 % | 5 bps | Spread público observado en quote de referencia |
| STRESS_1 | 0,10 % | 10 bps | Mismo spread observado, aplicando el escenario de slippage correspondiente |
| STRESS_2 | 0,20 % | 20 bps | Mismo spread observado, aplicando el escenario de slippage correspondiente |

Aplicar spread mediante los fills al ask/bid y no volver a restarlo como una línea separada de PnL. Comisiones/slippage se informan por operación. Si la captura de quotes no es íntegra, la evidencia económica queda bloqueada.

Benchmarks en la misma ventana y con capital inicial de 100 USDT:

- **Cash:** 100 USDT sin operar, retorno 0 %.
- **Buy-and-hold BTCUSDT:** compra en el primer quote elegible de T0 y venta al primer quote elegible en o después de T1, neto de coste completo.
- **Buy-and-hold ETHUSDT:** misma regla temporal, capital y modelado de costes para medir el costo de oportunidad, aunque la estrategia candidata solo opere BTC.

Publicar sin omitir periodos: PnL neto USDT, retorno neto, equity final, máximo drawdown diario y horario marcado al valor de liquidación, costes totales, número de operaciones, porcentaje de tiempo en mercado, resultado trimestral, peor semana, distribución de trades ganadores/perdedores y comparación con cada benchmark. El PnL objetivo siempre será cartera final menos los 100 USDT iniciales, nunca el retorno de una operación dividido por su notional.

## 7. Puertas de aceptación congeladas antes de T0

Solo podrá etiquetarse `CANDIDATE_FOR_SECOND_INDEPENDENT_REVIEW` si **se cumplen todas** las condiciones:

1. Integridad temporal completa del periodo de 8.760 horas: captura aceptada, quotes con `received_at`, hashes/manifiestos válidos, reloj y finality de datos verificables, sin backfill retroactivo. Superar pruebas anti-lookahead por prefijo, perturbación de sufijos, reproducción incremental y cobertura real de señales.
2. Beneficio neto de la cartera ≥**+50 USDT** bajo BASE; +100 USDT o más se informa como superación del objetivo, nunca como límite de ganancia. Resultado neto positivo en STRESS_1 y no negativo en STRESS_2.
3. Máximo drawdown ≤20 % bajo BASE y ≤25 % en STRESS_1 y STRESS_2. No se acepta el beneficio bruto ni un retorno antes de comisiones.
4. Superar a cash, buy-and-hold BTC y buy-and-hold ETH en BASE; exceso de retorno positivo frente a cash en los tres escenarios de costes.
5. Mínimo 60 round-trips completados en el año y al menos 10 en cada trimestre; si no se alcanza, `COVERAGE_INSUFFICIENT`, no PASS por ausencia de operaciones/perdidas.
6. Inferencia precomprometida: bootstrap móvil por bloques de siete días, 10.000 réplicas y semilla documentada para intervalos de confianza; aplicar Holm con error familiar 5 % a las comparaciones predefinidas frente a cash, buy-and-hold BTC y buy-and-hold ETH. El límite inferior ajustado del exceso de retorno BASE debe ser positivo frente a cada benchmark. Publicar p-valores e intervalos incluso si son desfavorables.
7. Modelo, dependencias, features, entrenamiento, umbral, ejecución, costes, benchmarks, semilla, código y configuración se congelan por SHA antes de T0. No habrá ranking de variantes, selección de ganadores ni cambio de umbral durante la evaluación.

Si hay datos incompletos, clasificar `INCOMPLETE / NO_TRADE`. Si los datos son íntegros pero cualquier gate económico/estadístico falla, clasificar `NO_ROBUST_EDGE_FOUND / NO_TRADE`. No se permitirá retocar el modelo y llamar al mismo periodo una nueva prueba independiente. Pasar esta puerta solo permite una segunda revisión independiente de CEREBRO y después valorar PAPER reconciliado; nunca activa LIVE ni ofrece garantía de rentabilidad.

## 8. Sesgo de selección y controles de robustez

- La idea está motivada por literatura externa, por lo que no se afirmará que fue concebida antes de toda investigación previa. Se evalúa como **una única candidatura predefinida**, sin grid search ni comparación de hiperparámetros por resultado.
- Los resultados históricos de BOT-CRIPTO no son holdout válido para este candidato. Solo pueden servir para entrenamiento previo o depuración técnica, nunca para declarar un PASS económico.
- Publicar la política completa y los resultados negativos, incluyendo cash time y todos los costes. El ranking no puede omitir una estrategia o fold que pierda.
- El experimento prospectivo existente de siete reglas mantiene su propio protocolo. Este candidato requiere su propia ficha de hashes y T0, sin reordenar reglas ni alterar los resultados V1/V2.
- Revisar sensibilidad únicamente en las tres hipótesis de coste preregistradas y las métricas fijadas arriba. No realizar perturbaciones de umbral/arquitectura en el segmento puntuado.
- La incertidumbre del modelo, cambios de régimen, fees reales, spread, fills y capacidad predictiva siguen siendo riesgos. Un modelo que predice bien pero no supera costes, benchmarks y límites de drawdown falla.

## 9. Dependencias y siguiente transición autorizada

**Bloqueos actuales observados en los registros del repositorio:**

- La [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) registra que todavía falta aceptar una captura real Kline + bookTicker con al menos 24 horas de solapamiento y una jornada UTC completa válida; por eso no se debe fijar T0 aún.
- Este candidato no dispone todavía de implementación, artefacto de entrenamiento congelado, pruebas causales ni evaluación prospectiva. Estos puntos están `UNKNOWN / NOT RUN`, no aprobados.
- La rama de este documento parte de `main` en `c523c47364b6fb4febd9c48385bc63bcde87ea61`; su creación y este informe no cambian la punta de `main`.

Secuencia autorizada: revisar este dictamen → decidir si se acepta la hipótesis → implementar de forma aislada y reproducible en esta rama (sin activar órdenes) → añadir tests sintéticos/anti-lookahead y comprobar dependencias → congelar el modelo y el contrato económico por hashes → aceptar captura real y fijar T0 con un nuevo commit de preregistro → ejecutar únicamente el experimento prospectivo fijado → revisar el informe completo. Hasta entonces, `NO_TRADE`.

## 10. Registro exacto de actividad de este dictamen

- Lectura de informes históricos y protocolos ya persistidos; revisión de literatura pública enlazada en la sección 3.
- Creada la rama `research/cost-aware-ml-return-filter-50-100-v1` desde `main` SHA `c523c47364b6fb4febd9c48385bc63bcde87ea61`.
- Este documento es un cambio documental solamente.
- **No** se cambió `main`; **no** se modificó código de trading, señal, test, workflow, dependencia o configuración económica; **no** se ejecutó un backtest; **no** se inició workflow; **no** hubo órdenes PAPER/LIVE.
- Dictamen final de este paso: **hipótesis nueva identificada; rentabilidad desconocida; falta evidencia prospectiva independiente; `NO_TRADE`.**


## Addendum — corrección del runtime de investigación (2026-10-22)

La validación de entorno detectó que CPython 3.12.15 no está disponible en el runner Windows administrado: la distribución oficial es source-only y Python 3.12.10 fue la última versión 3.12 con instaladores de Windows. Antes de fijar T0, la implementación aislada y su gate de dependencias cambian a **CPython 3.13.16 x86-64**, con XGBoost 3.4.1, NumPy 2.3.5, pandas 2.2.3, SciPy 1.16.3 y pytest 9.1.1 fijados. pandas 2.2.3 declara compatibilidad con Python 3.13. El lock con hashes y la instalación real todavía deben verificarse; esto no constituye evidencia económica y no cambia la hipótesis, los costes, las métricas ni los gates. Estado: `NOT_RUN / NO_TRADE`.

Fuentes: [Python 3.12.15](https://www.python.org/downloads/release/python-31215/), [pandas 2.2.3](https://pandas.pydata.org/pandas-docs/version/2.2.3/whatsnew/v2.2.3.html).
