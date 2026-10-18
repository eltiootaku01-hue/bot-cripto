# BTC/ETH — Plan de prueba virtual de tácticas V1/V2

**Estado actual:** replay histórico V1 verificado; V2 `DESIGNED / NOT RUN`.  
**Decisión económica/operativa:** `NO_ROBUST_EDGE_FOUND / NO_TRADE`.  
**Capital de simulación:** 31 USDT hipotéticos; no son fondos disponibles ni conversión de ARS.  
**Alcance:** Binance Spot público, BTCUSDT y ETHUSDT, velas horarias, sin margen, apalancamiento, órdenes reales ni LIVE.

## 1. Decisión actual: no hay táctica ganadora probada

El replay de las siete reglas en el histórico anual ya examinado identifica a `ETH_TSMOM_168H` —momentum ETH a 168 horas con filtro EMA100— como líder del segmento EVALUATION, pero eso **no demuestra un edge robusto ni una estrategia ganadora**.

| Métrica de evaluación histórica (365 días) | Resultado de ETH_TSMOM_168H |
|---|---:|
| Retorno neto BASE | +15,5138 % |
| Retorno neto STRESS_1 | +1,9100 % |
| Retorno neto STRESS_2 | -26,3695 % |
| Drawdown máximo BASE | -11,7674 % |
| Drawdown máximo STRESS_1 | -18,2353 % |
| Drawdown máximo STRESS_2 | -35,0021 % |
| VALIDATION_1, retorno BASE | -10,6421 % |
| VALIDATION_2, retorno BASE | +4,6558 % |
| VALIDATION_3, retorno BASE | -14,7518 % |

El candidato lidera en esa ventana, pero en STRESS_2 pierde dinero y los tres folds de validación no son consistentemente positivos. Otros candidatos tampoco muestran retorno positivo en los tres folds y escenarios económicos. Elegir esta táctica como “ganadora”, ajustar parámetros sobre el mismo histórico o iniciar PAPER con ella sería contrario a los criterios registrados.

El estudio V1 advierte que el histórico conserva `available_at=UNKNOWN` y `finality=UNKNOWN` en las velas: el hash demuestra identidad/integridad de bytes, no que cada vela estuviera disponible al momento de cada decisión. La evaluación es exploratoria, no una confirmación punto-en-tiempo.

## 2. Qué se corrigió para que el riesgo del backtest sea comparable

Se corrigió la valoración de drawdown de V1 y del motor virtual canónico. Una posición abierta se marca al valor estimado neto de liquidación del cierre, descontando los tipos de coste de salida (half-spread, slippage y fee) que se asumen en un SELL. Esa marca no crea un fill de venta ni contabiliza su comisión hipotética como realizada. El PnL no realizado de la auditoría diaria se reconcilia con la equity neta.

Validaciones:
- Motor canónico en SHA `939a862e01de598da392d687b790d83017a9321c`: [1675 tests](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220900), [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220869), [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39113220777), todo exitoso en el mismo SHA.
- Replay histórico de las siete reglas con drawdown corregido en SHA `b1effde0e0f786bc7dde7dd2bb5ec974802c5929`: [replay V1](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39112722110) exitoso, seis perturbaciones anti-lookahead, siete estrategias y hashes originales verificados.

## 3. Protocolo para evaluar candidatas sin autoengaño

1. **No reajustar con V1.** La lista de siete reglas y parámetros está congelada en [BTC_ETH_STRATEGIES_V2_PROTOCOL.md](BTC_ETH_STRATEGIES_V2_PROTOCOL.md). V1 se conserva como investigación histórica; no se transforma un ranking retrospectivo en holdout.
2. **Aceptar datos reales primero.** Ejecutar una captura pública Kline + bookTicker durante al menos 24 horas reales solapadas, con un día UTC común completo, manifiestos/hashes reproducibles y sin gaps fuera de política. Instrucciones: [guía de captura](../PROSPECTIVE_BTC_ETH_CAPTURE_V1.md). El issue bloqueante es [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98).
3. **Preregistrar antes de T0.** Congelar SHA, dataset, configuración, reglas, costes y benchmarks en un commit antes de ver el periodo futuro. `T0` se fija después de aceptar el smoke de 24 h; la evaluación futura durará 365 días.
4. **Mismos costes y benchmarks.** En BASE, STRESS_1 y STRESS_2 comparar los siete candidatos con cash y buy-and-hold de BTC y ETH en el mismo intervalo y con convenciones económicas idénticas. Informar fees, spread/slippage, fills, filtros, entradas rechazadas, liquidación, retorno neto y drawdown neto de costes.
5. **Anti-lookahead con cobertura.** Alterar sufijos futuros de ambos activos y demostrar que ningún indicador, señal, decisión o fill anterior cambia. Registrar cobertura de las siete estrategias; ninguna regla puede aprobar solo por no haber generado operaciones.
6. **Regla de aceptación fija.** Una candidata solo pasa a revisión independiente si los datos tienen las 8.760 horas de evaluación, la evidencia punto-en-tiempo es admisible, su retorno neto es positivo en BASE y STRESS_1, vence a cash y ambos buy-and-hold en BASE, no cae por debajo de cash en STRESS_2, y el exceso sobre el benchmark primario sigue positivo con la corrección estadística preregistrada y drawdown aceptable. No ampliar umbrales después de ver resultados.
7. **Decisión fail-closed.** Datos incompletos significan `INCOMPLETE / NO_TRADE`. Si ninguna candidata pasa, se mantiene `NO_ROBUST_EDGE_FOUND / NO_TRADE`. Un resultado aprobado requiere revisión CEREBRO y PAPER prospectivo reconciliado; nunca se promueve automáticamente a LIVE.

## 4. Costes y límites de simulación fijados

- BASE: comisión 0,10 % por fill, spread completo supuesto 10 bps y slippage 5 bps por lado.
- STRESS_1: comisión 0,10 %, spread 25 bps y slippage 10 bps.
- STRESS_2: comisión 0,20 %, spread 50 bps y slippage 20 bps.
- Capital virtual inicial 31 USDT; Spot long-only; hasta 95 % del quote disponible por entrada; máximo cinco entradas nuevas en una ventana móvil de 30 días; sin apalancamiento ni posiciones cortas.
- Las cotizaciones observadas solo pueden usarse si estaban disponibles antes de la decisión y cumplen el contrato; si no hay evidencia de disponibilidad, no inventar fill.
- Cuando una estrategia y un benchmark usan una valoración de liquidación en cierres, ambos deben deducir los mismos costes de salida; la valoración abierta no es un fill contabilizado.

La especificación V2 completa, incluidos bootstrap por bloques, corrección Holm y criterios estadísticos, permanece en [BTC_ETH_STRATEGIES_V2_PROTOCOL.md](BTC_ETH_STRATEGIES_V2_PROTOCOL.md).

## 5. Avisos técnicos externos aplicados

- [QuantConnect, junio de 2026](https://www.quantconnect.com/announcements/20949/closing-the-stale-price-loophole-on-daily-and-hourly-data/p1) corrigió un comportamiento del simulador que podía hacer fills contra cierres horarios/diarios antiguos e inflar el backtest. La lección aplicada es no mezclar precios frescos, cierres conocidos y marcas económicas inconsistentes.
- [Freqtrade — lookahead analysis 2026.5](https://docs.freqtrade.io/en/2026.5/lookahead-analysis/) advierte que el análisis solo verifica tipos de señales que efectivamente se activaron. Por eso el protocolo requiere perturbación de sufijo y cobertura de señales.
- [Binance — Spot WebSocket streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md) documenta el límite de 24 h y `serverShutdown`; esa evidencia justifica la captura supervisada, pero únicamente un artefacto real puede cerrar el gate #98.

## Dictamen

**No hay una táctica ganadora demostrada con la evidencia actual.** El plan defendible es mantener `NO_TRADE` y ejecutar el protocolo prospectivo congelado, en lugar de elegir y optimizar otra vez con el mismo histórico. Ninguna simulación garantiza ganancias futuras.
