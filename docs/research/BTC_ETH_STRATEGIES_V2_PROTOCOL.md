# BTC_ETH_STRATEGIES_V2 — protocolo prospectivo preregistrado

**Versión del protocolo:** 1.0  
**Fecha de diseño:** 2026-10-17 UTC  
**Estado:** `DESIGNED / NOT RUN`  
**Decisión operativa:** `NO_TRADE`  
**Autoridad:** CEREBRO / IA-CHAN  
**Tipo de trabajo:** diseño de investigación; no modifica runtime ni habilita órdenes. Incorporado en `main` como protocolo de referencia; la ejecución económica V2 continúa sin iniciar.

## 1. Objetivo y regla principal

Evaluar si una familia pequeña de estrategias BTC/ETH conserva una ventaja económica neta en un **periodo futuro de 365 días que todavía no existe en los datos analizados**. No se aceptará como “nuevo” otro corte retrospectivo del histórico que ya se inspeccionó para seleccionar ideas.

V1 ya exploró partes del histórico disponible, comparó siete reglas y probó una partición walk-forward; aun así, parte de la evaluación V1 se solapa con la evaluación previa de EMA/RSI y BTC-gated. El dataset histórico termina con la vela horaria que comienza a las 2026-10-17 18:00 UTC; su identidad SHA-256 es `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`. En consecuencia, **V2 no puntuará como evaluación principal ninguna vela anterior al inicio prospectivo T0**.

Este documento solo pre-registra el protocolo. Todavía no existe resultado V2, no comenzó la captura prospectiva y no se ha demostrado que el año futuro tenga integridad suficiente. No trasladar estimaciones de V1 como si fueran resultados V2.

## 2. Cómo se fija el periodo nuevo sin elegirlo tras ver los precios

Se usa el ancla `T0`, definida por una regla operacional que no puede depender de retornos:

1. Revisar y aprobar este protocolo y congelar el código, reglas de señales, costos, métricas y pruebas mediante commit SHA en GitHub.
2. Preparar un colector **solo público y sin claves privadas** para velas horarias cerradas BTCUSDT/ETHUSDT y snapshots públicos de mejor bid/ask. Ejecutar una prueba de humo continua de 24 horas que no puntúa, validando timestamps, salud, reconexiones, persistencia y hashes.
3. Solo después de que la prueba de humo termine correctamente, registrar un commit de preregistro que incluya hashes de código/configuración y `T0`. `T0` será la primera frontera de hora UTC que ocurra después de ese commit y de la prueba de humo. El valor exacto se fija **antes** de abrir el segmento de evaluación; no puede moverse en respuesta a los precios.
4. Periodo primario: `[T0, T1)`, donde `T1 = T0 + 365 días = T0 + 8.760 horas`. Los 8.760 intervalos horarios deben estar completos y alineados en ambos activos.
5. Si la prueba de humo falla, no se inicia la evaluación. Si hay un hueco o pérdida de evidencia después de T0, no rellenar retroactivamente observaciones ni decisiones; clasificar el estudio `INCOMPLETE`. Reiniciar requiere una nueva versión de preregistro, un nuevo T0 futuro y un motivo documentado, sin consultar resultados de rendimiento del periodo fallido.

Esto permite elegir la fecha por disponibilidad técnica, no por comportamiento del mercado. La primera vela de T0 puede ser después del final del dataset V1; no se aceptan precios del periodo prospectivo para ajustar reglas.

## 3. Datos públicos y evidencia punto-en-tiempo

**Activos y granularidad:** Binance Spot `BTCUSDT` y `ETHUSDT`, velas `1h` UTC. Sin derivados, margen ni apalancamiento.

**Fuentes oficiales y brechas de adquisición:**
- [Binance Spot WebSocket streams — klines](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md): aceptar una vela como cerrada solamente cuando el payload tenga `k.x = true`; el stream incluye tiempos de apertura/cierre. El repositorio ya contiene `bot_obrero/prospective_capture.py` para persistencia de klines y su validador; esto prueba infraestructura implementada, no una captura real aceptada.
- [Binance Spot WebSocket streams — book ticker](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md): capturar mejor bid/ask para el precio de referencia disponible después de cada decisión. Aún falta demostrar un colector durable integrado para quotes (`bookTicker`) que registre el instante de primera recepción, bid/ask y hashes junto a las velas; no tratar klines como sustituto de evidencia de cotización.
- [Binance Spot REST API](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md): documentar endpoint, parámetros, zona horaria UTC y respuestas utilizadas en la reconciliación de integridad.
- [Freqtrade — lookahead analysis](https://docs.freqtrade.io/en/2026.5/lookahead-analysis/): referencia metodológica para detectar indicadores/señales que cambian al alterar datos posteriores y para advertir cobertura insuficiente de tipos de señales.

Por cada vela y quote se almacenarán de forma inmutable: símbolo, intervalo, OHLCV original como cadenas decimales, tiempo de apertura/cierre del exchange cuando esté presente, hora UTC local de primera recepción, reloj monotónico de recepción, estado de finalización, conexión/sesión de origen, hash de payload y número de versión del esquema. Registrar desfase estimado del reloj contra el endpoint público de hora de Binance, además del instante de muestreo; si el reloj no puede verificarse dentro del límite preregistrado de ±1 segundo, bloquear decisiones y marcar evidencia temporal como UNKNOWN.

No convertir una consulta histórica hecha después en prueba de disponibilidad histórica. No sobrescribir un payload que cambie en la reconciliación: guardar una nueva revisión y marcar el conflicto. No se admiten decisiones retroactivas sobre una vela que el colector no haya observado a tiempo.

### Criterios mínimos de integridad

- 8.760 marcas horarias esperadas por activo en `[T0,T1)`, estrictamente ordenadas, únicas y separadas exactamente por una hora.
- Timestamp de recepción registrado, payload de vela cerrado y reloj del colector aceptable antes de calcular una decisión.
- BTC y ETH temporalmente alineados. OHLC positivos y coherentes; duplicados, solapamientos, barras abiertas, precios no finitos o cambios sin versionado bloquean la aceptación.
- Quote de salida/entrada con bid/ask positivos, bid ≤ ask, recibido después de la creación de la decisión y con edad dentro del límite preregistrado (máximo 2 segundos en el momento de selección). Si no hay quote válido, no inventar precio de fill.
- 100% de las velas/decisiones requeridas y evidencia de quotes de ejecución presentes. Un hueco no se repara con backfill de la API para simular que ese dato se conocía entonces.
- Guardar fragmentos diarios inmutables en una rama de evidencia separada de `main`, con SHA-256 por archivo y hash encadenado al fragmento previo. Un manifiesto final conserva commits, hashes, límites T0/T1, versión del esquema y conteos de cada día. No depender de artefactos temporales de CI como única copia del año entero.

Si no puede implementarse un colector continuo y una persistencia duradera antes del inicio, V2 queda `BLOCKED_NOT_STARTED`; no sustituir esta captura por un backtest retrospectivo y cambiarle el nombre.

## 4. Estrategias congeladas para la evaluación

Se reutilizan las siete definiciones de V1, sin búsqueda de hiperparámetros, sin añadir indicadores, sin tuning de umbrales y sin promoción por ranking dentro del año nuevo. Los commits de código/configuración se congelan antes de T0.

1. `EMA12_26_RSI`: ETH largo-only; compra en cruce ascendente EMA12/EMA26 con RSI14 ≥ 50; venta en cruce bajista o RSI14 ≥ 70.
2. `EMA24_96_CROSS`: ETH largo-only; cruces EMA24/EMA96.
3. `DONCHIAN_24_12`: ETH largo-only; compra si el cierre excede el máximo de las 24 velas previas; venta si el cierre cae por debajo del mínimo de las 12 velas previas. El canal excluye la vela actual.
4. `RSI_MEANREV_EMA200`: compra cuando RSI14 cruza hacia abajo hasta ≤ 30 con cierre ETH > EMA200; vende cuando RSI14 ≥ 55 o cierre ETH < EMA200.
5. `BTC_GATED_ETH_TREND`: compra ETH si cierre ETH > EMA50 ETH, cierre BTC > EMA50 BTC y momentum ETH de 72 h > 0; salida cuando alguna de las condiciones deja de cumplirse.
6. `ETH_TSMOM_168H`: compra ETH si momentum de 168 h > 0 y cierre > EMA100; salida si cualquiera falla.
7. `BTC_ETH_RS_ROTATION_168H`: como máximo una posición Spot larga; al cierre, elige BTC o ETH con el momentum de 168 h positivo más alto; si ninguno es positivo, mantiene cash.

Se utilizarán 90 días de historia previa solo como warm-up de indicadores y no se incluirán en las métricas V2. El warm-up se congela y hashea antes de T0. Las estrategias comienzan la evaluación en cash, sin posiciones heredadas. Los cálculos deben usar exclusivamente observaciones recibidas hasta el momento de la decisión; parámetros e indicador no se recalculan con datos posteriores.

**Selección múltiple:** las reglas ya fueron examinadas en V1, aunque las fechas de V2 serán nuevas. Por tanto, V2 prueba prospectivamente esas reglas congeladas; no se afirmará que la familia de estrategias fue concebida sin mirar resultados anteriores. Se publicarán todos los resultados de todos los candidatos y no solo el ganador.

## 5. Contrato económico y ejecución virtual

- Capital inicial por cartera: 31 USDT hipotéticos, independiente de cualquier presupuesto en ARS; Spot, sin margin ni leverage; posición máxima por entrada 95% del saldo de cotización.
- Máximo de cinco nuevas entradas por ventana móvil de 30 días, aplicado de la misma forma a los candidatos y documentado para rotación.
- Señal tras recibir una vela finalizada y calcular indicadores causales. El fill virtual usa el primer `bookTicker` válido cuya recepción sea posterior al momento de creación de la decisión. Comprar usa ask; vender usa bid. No utilizar el open de una vela si ocurrió antes de que la señal estuviera disponible.
- Cantidades y contabilidad económica con `Decimal`; redondeo de cantidad hacia abajo según filtros Spot efectivos. Las cantidades por debajo de los filtros o de notional mínimo se rechazan, no se redondean al alza.
- Se cargan comisión y deslizamiento adverse al precio de bid/ask capturado. El spread del libro observado ya está expresado por ask frente a bid; no sumarlo otra vez como un spread ficticio. Guardar cada componente de costo por fill.
- Modelar tres escenarios fijos para todos los métodos: BASE comisión 0,10% y slippage adicional 5 bps por lado; STRESS_1 comisión 0,10% y slippage 10 bps por lado; STRESS_2 comisión 0,20% y slippage 20 bps por lado. El spread proviene de las quotes registradas en el momento de referencia y el mismo método de fill se aplica a las estrategias y benchmarks.
- Cash comienza en 31 USDT, mantiene el saldo sin operaciones y tiene retorno 0%; como no opera, no incurre en comisiones de transacción. Buy-and-hold BTC y buy-and-hold ETH compran en el primer quote elegible al inicio T0 y liquidan con el primer quote elegible en o después de T1; los dos fills, spread observado, comisión y slippage se incluyen en los retornos. Las estrategias se liquidan totalmente en quote elegible al cierre T1, con costos de salida.
- Los precios de referencia, fills simulados, balances, comisiones, slippage, redondeos, rechazo de órdenes, curva de equity y liquidación final deben poder recalcularse desde los registros inmutables.

No confundir spreads observados durante V2 con los spreads supuestos de V1; publicar los escenarios como diferentes si el modelo del spread cambia. La comparación dentro de V2 usa exactamente los mismos parámetros y reglas de costo por escenario.

## 6. Métricas preregistradas

Para cada estrategia, cash, buy-and-hold BTC y buy-and-hold ETH, dentro del mismo intervalo y cada uno de los tres escenarios:

- retorno final neto en % y USDT, PnL neto, equity final, fees, slippage modelado y número/volumen de fills;
- máximo drawdown sobre equity marcado a hora, volatilidad de retornos diarios, peor día y peor semana;
- turnover y tiempo en cash/mercado, número de trades ganadores/perdedores y rendimiento por trimestre como descriptivo, no como cortes para reajuste;
- exceso de retorno neto frente a cash, buy-and-hold BTC y buy-and-hold ETH, medidos en las mismas fechas T0/T1 y con los costos correspondientes;
- intervalos de confianza por bootstrap en bloques de retornos diarios (longitud de bloque fijada en siete días, 10.000 réplicas y semilla determinista registrada), conservando la dependencia serial mejor que un remuestreo de horas independientes;
- corrección Holm con error familiar 5% para las hipótesis de candidatos frente al benchmark pasivo de mejor retorno en BASE y STRESS_1. Publicar hipótesis probadas, p-valores ajustados e intervalos, aunque el resultado sea negativo o no evaluable.

Durante la ventana se permitirá consultar **solo integridad del colector y exposición de datos** para reparar fallos operativos; no se publicarán rendimientos acumulados, rankings de estrategia ni decisiones parciales. El informe económico completo se libera cuando se cierra `[T0,T1)` o cuando el estudio queda clasificado `INCOMPLETE`. Los trimestres no son oportunidades para modificar la estrategia.

## 7. Prueba anti-lookahead y cobertura de señales

Antes de iniciar T0, las pruebas deben verificar, como mínimo:

1. **Prefijo vs serie completa:** para los indicadores y señales de cada estrategia, la decisión correspondiente a cualquier corte se obtiene con el mismo resultado usando solo el prefijo disponible hasta ese corte. Ninguna decisión puede depender de filas posteriores.
2. **Perturbación de sufijo:** fijar una semilla y 24 timestamps de prueba preregistrados (incluidos cortes en límites de UTC y de folds sintéticos); cambiar OHLCV de las filas estrictamente posteriores al corte en BTC y ETH no debe cambiar indicadores, señales, decisiones, quotes elegibles ni fills anteriores al corte.
3. **Reproducción incremental:** ejecutar el mismo dataset evento por evento y después como replay offline de prefijos; deben coincidir señales, balances y orden de fills.
4. **Frontera de disponibilidad:** asserts que el quote elegido para cualquier fill tenga `received_at >= decision_created_at`; la vela de señal debe estar cerrada y recibida antes del cálculo. Prohibido rellenar con open/close anterior a la disponibilidad.
5. **Señales cubiertas:** registrar cuántas entradas/salidas y condiciones ocurrieron en la evaluación. Una estrategia sin cobertura suficiente se etiqueta `COVERAGE_INSUFFICIENT`, nunca `PASS` solo por no encontrar diferencias.
6. **Replay hash-equal:** los hashes de todos los fragmentos diarios, warm-up congelado, configuración, calendario T0/T1 y replay deben coincidir. Cualquier mutación del archivo debe fallar de forma cerrada.

Referencias metodológicas: la documentación de Freqtrade advierte que el análisis anti-lookahead solo evalúa los tipos de señales que realmente fueron activados; por eso V2 combina pruebas de invariancia con reporte obligatorio de cobertura.

## 8. Criterio de interpretación precomprometido

La clasificación inicial y por defecto es `NO_ROBUST_EDGE_FOUND`. Solo se podrá etiquetar `CANDIDATE_FOR_SECOND_INDEPENDENT_REVIEW` —nunca estrategia activa ni permiso de operación— si se cumplen **todas** estas condiciones:

1. integridad de las 8.760 horas y evidencia temporal suficientes; todas las verificaciones anti-lookahead pasan y la cobertura de señales es adecuada;
2. retorno neto del candidato positivo en BASE y STRESS_1; retorno incremental frente a cash positivo en los tres escenarios;
3. supera a **ambos** benchmarks buy-and-hold en retorno neto en BASE y no queda por debajo de cash en STRESS_2;
4. los intervalos de confianza precomprometidos, con corrección Holm, sostienen exceso de retorno positivo en la hipótesis primaria;
5. drawdown, volumen de costes y dispersión temporal se publican sin omitir periodos desfavorables.

Si ninguna estrategia cumple todo, el resultado es `NO_ROBUST_EDGE_FOUND / NO_TRADE`. Si la evidencia no cumple temporalidad, cobertura, hashes o integridad, la clasificación es `INCOMPLETE / NO_TRADE`, no negativa ni positiva económicamente. Aunque una candidata pase el filtro, se requiere una nueva revisión de CEREBRO y después PAPER prospectivo reconciliado; nunca pasa automáticamente a LIVE. No existe garantía de ganancia futura.

## 9. Entregables de V2

- Protocolo preregistrado con SHA de código/configuración y T0/T1.
- Manifiesto del warm-up y de cada archivo diario encadenado con hash.
- Informe de salud de datos sin retornos durante la prueba.
- Al cerrar, dataset exacto, replay, logs, trazabilidad de quotes/fills, pruebas anti-lookahead, JSON/CSV de métricas, informe económico y hashes SHA-256.
- Registro final en GitHub en una rama separada de `main`; sin credenciales privadas. Acciones con permisos mínimos y sin ejecutar código no confiable con permisos de escritura.
- Dictamen explícito `PASS / FAIL / UNKNOWN / INCOMPLETE` para cada vector y decisión final `NO_TRADE`.

## 10. Estado actual

`DESIGNED / NOT RUN`. No hay T0 fijado: la captura prospectiva de klines y su validador ya existen en `main`, pero todavía no se ha ejecutado/aceptado la prueba de humo real de 24 h y no se ha demostrado la persistencia conjunta de bookTicker requerida por este protocolo. No se adquirió ni se puntuó ningún dato V2, no se modificaron estrategias operativas y no se habilitaron órdenes.

La siguiente etapa técnica es cerrar y probar la persistencia de bookTicker ligada temporalmente a la misma decisión/fill, después hacer la prueba de humo real de 24 h y aceptar sus manifiestos. No comenzar el año de evaluación hasta que código, configuración y protocolo estén congelados, y el artefacto real de captura quede validado.

---

**Decisión operativa persistente: `NO_TRADE`.**

