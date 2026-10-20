# MODO 1 — TENDENCIA: ensayo de práctica V1

**Estado:** replay histórico exploratorio ejecutado localmente; flujo CI/PR pendiente de confirmación final.  
**Decisión operativa:** `NO_TRADE`.  
**Alcance:** BTCUSDT y ETHUSDT Spot ficticio, long-only, sin órdenes de exchange, claves privadas, margen ni apalancamiento.

## Qué queremos verificar

Esta prueba comprueba que una regla de tendencia puede recorrer una secuencia histórica de forma determinista y con controles básicos de ingeniería: agregación correcta de velas de cuatro horas, señal causal, entrada al siguiente open, una posición global a la vez, sizing por efectivo, comisiones y costes adversos, stop conservador con gap, salida de canal al siguiente open, valoración neta de liquidación, drawdown y registro verificable de operaciones.

No es prueba de que la integración operativa PAPER completa esté lista, y tampoco prueba que la táctica vaya a generar beneficios.

## Histórico y procedencia

- Dataset lógico SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Archivo fuente SHA-256: `3851a560aafbaa44f7d57081bd12f7dfdae070c2abff3d65e122ad8fcbd6df36`.
- Configuración SHA-256: `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`.
- 15.360 velas horarias por activo desde 2025-01-15 19:00 UTC hasta 2026-10-17 18:00 UTC.
- Se agregaron 3.839 velas de 4h completas por símbolo; se descartan grupos de borde incompletos.
- Las 30.720 velas no tienen `available_at` verificado y su finality histórica es UNKNOWN. Por eso el replay puede probar lógica retrospectiva, pero no demostrar qué cotizaciones estaban disponibles en tiempo real.

## Regla congelada

1. Evaluar solo al cierre de velas completas 4h.
2. Señal de entrada: close rompe el máximo de las 40 velas anteriores (la actual queda excluida); close por encima de EMA200; EMA200 mayor que su valor de cinco velas atrás.
3. Si BTC y ETH producen una señal a la vez, elegir la mayor ruptura normalizada por ATR14; empate por orden alfabético de símbolo.
4. Entrada al open de la siguiente vela 4h. Una sola posición total entre ambos símbolos.
5. Stop inicial de 3×ATR14 del candle de señal. Si el mínimo del candle cruza el stop, usar el peor de open o stop y aplicar costes; si existe gap por debajo del stop, la referencia de fill es el open, no el stop ideal.
6. Salida alternativa: close por debajo del mínimo de las 20 velas previas, ejecución al siguiente open.
7. Capital ficticio de 31 USDT, máximo 95% del quote disponible, máximo cinco entradas en cualquier ventana móvil de 30 días, sin short ni leverage.

## Resultado exploratorio local

| Costes | Capital final | Retorno neto | Drawdown máximo | Operaciones cerradas | Ganadoras |
|---|---:|---:|---:|---:|---:|
| BASE | 32,3359 USDT | +4,3095% | −34,3036% | 28 | 9 |
| STRESS_1 | 30,2558 USDT | −2,4007% | −36,5994% | 28 | 9 |
| STRESS_2 | 25,4534 USDT | −17,8924% | −42,1988% | 28 | 8 |

Los escenarios son BASE: comisión 0,10% por lado, spread supuesto 10 bps y slippage 5 bps por lado; STRESS_1: 0,10%, 25 bps y 10 bps; STRESS_2: 0,20%, 50 bps y 20 bps. Son hipótesis de coste sobre OHLC; no son spreads ni fills históricos observados.

En los 20 meses calendario completos, el modo tuvo cuatro meses positivos en cada escenario. El retorno mensual medio fue +0,796% BASE, +0,481% STRESS_1 y −0,331% STRESS_2; la mediana fue negativa en los tres casos (−0,626%, −0,979%, −1,889% respectivamente). Superó al benchmark mensual de cash en solo 4 de 20 meses y no mantuvo una ventaja consistente en todos los benchmarks o escenarios.

El buy-and-hold continuo del mismo histórico rindió −18,363% para BTC y −28,647% para ETH con los costes BASE asumidos. Que la regla de tendencia supere esos dos resultados negativos en BASE no demuestra por sí solo una ventaja: el drawdown llegó a −34% a −42%, la mayoría de las operaciones perdieron y el resultado se vuelve negativo al elevar los costes.

## Cobertura de ingeniería

El conjunto de pruebas cubre:
- velas 4h completas y continuidad temporal;
- exclusión de la vela actual de los canales Donchian;
- invariancia de prefijos ante sufijos futuros;
- cálculo conservador de stops/gaps;
- costes más adversos;
- entrada en el siguiente open y duración de posiciones no solapadas;
- tope de entradas por ventana;
- hash del histórico fijado y tratamiento de `available_at` / finality desconocidos.

El workflow dedicado descarga el artefacto histórico inmutable de GitHub Actions, ejecuta las pruebas, reproduce la simulación en las tres curvas de costes, contrasta los valores de resultado esperados y valida los tamaños y SHA-256 de cada salida. La evidencia queda como artefacto CI.

## Dictamen y próximos bloqueos

Este ensayo **prueba el motor histórico de práctica**, no todo el camino operativo market-data → señal → riesgo → PAPER ledger. El histórico no trae quotes bid/ask pasados verificables y no se reconstruyeron filtros históricos de lot-size, step-size o min-notional. El adaptador de señal operativo de BOT-CRIPTO también debe recibir datos admisibles con disponibilidad/finality verificadas; no se deben forzar velas históricas con finality UNKNOWN dentro del runtime.

Por tanto:
- el replay de ingeniería puede considerarse útil si CI termina en éxito;
- la economía de esta regla no supera el criterio robusto: `NO_TRADE`;
- antes de una práctica PAPER prospectiva se necesita el gate de captura real Kline + bookTicker de 24 horas solapadas de [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98);
- no modificar parámetros tras mirar este informe y no promover a PAPER automático o LIVE con este replay.

