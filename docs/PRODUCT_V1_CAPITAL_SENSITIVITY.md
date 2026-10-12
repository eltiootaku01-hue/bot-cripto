# Product V2 — filtros BTC y sensibilidad del capital

## Conversión ARS → USDT

La ruta completa de conversión, la cotización ejecutable, la fuente/fecha de una conversión ARS→USDT y sus costes no están verificados. Por eso, los escenarios A/B/C permanecen en `CONVERSION_UNKNOWN` y no se inventan saldos disponibles.

| Capital ARS | Conversión verificada | USDT netos | Candidatas que pasan filtros de mercado | Candidatas asequibles en ARS | Coste operación completa ARS / % capital | Retorno mínimo de equilibrio | Retorno neto histórico atribuible al capital ARS | Drawdown atribuible | Decisión |
|---:|---|---|---|---|---|---|---|
| 10.000 | CONVERSION_UNKNOWN | UNKNOWN | UNKNOWN (depende del snapshot del informe) | UNKNOWN | UNKNOWN / UNKNOWN | UNKNOWN | NOT_COMPUTABLE | NOT_COMPUTABLE | NO_TRADE |
| 25.000 | CONVERSION_UNKNOWN | UNKNOWN | UNKNOWN (depende del snapshot del informe) | UNKNOWN | UNKNOWN / UNKNOWN | UNKNOWN | NOT_COMPUTABLE | NOT_COMPUTABLE | NO_TRADE |
| 50.000 | CONVERSION_UNKNOWN | UNKNOWN | UNKNOWN (depende del snapshot del informe) | UNKNOWN | UNKNOWN / UNKNOWN | UNKNOWN | NOT_COMPUTABLE | NOT_COMPUTABLE | NO_TRADE |

Los costes de conversión, reserva de comisiones, máximo utilizable y capital sin invertir también son `UNKNOWN`. El sistema no convierte ARS en USDT usando una tasa inventada.

## Sensibilidad normalizada separada

El programa evalúa escenarios computacionales independientes de **100, 250 y 500 USDT hipotéticos**. Estos importes no son equivalencias de 10.000, 25.000 o 50.000 ARS, ni estimaciones de una cotización real. Sirven únicamente para estudiar cómo los mínimos de cantidad/nocional y los costes modelados interactúan con diferentes saldos ficticios.

Todos los escenarios reutilizan las mismas velas históricas, período, estrategia, filtros del instrumento, comisión por fill, supuesto de spread, deslizamiento y límite de cinco entradas BUY en cualquier ventana móvil de 30 días. No se fuerzan entradas.

La asequibilidad normalizada se calcula contra el snapshot público actual del precio y los filtros del instrumento, redondeando la cantidad al step size y reservando los costes modelados de entrada. No demuestra que haya existido una señal histórica ni que el capital ARS se pueda convertir a ese saldo USDT. Cada backtest informa entradas BUY simuladas, retorno neto modelado, drawdown máximo y comisiones.

## Conclusiones que sí permite el estado actual

- **10.000 ARS:** el primer bloqueo demostrable es `CONVERSION_UNKNOWN`. Sin ruta y costes verificables no es posible afirmar si el obstáculo económico posterior son mínimos, comisiones, spread o falta de señales para ese saldo.
- **25.000 ARS:** no se puede demostrar que cambie la asequibilidad real; sigue faltando la conversión verificable.
- **50.000 ARS:** tampoco se puede afirmar que amplíe oportunidades reales. El bloque normalizado puede mostrar cambios de escala en USDT hipotéticos, pero no resuelve el bloqueo ARS.
- **Ventaja histórica neta:** los resultados de backtest son observaciones del período y de supuestos de coste declarados, no prueba de rentabilidad futura. Si no hay señales o la rentabilidad neta no supera los controles, la decisión es `NO_TRADE`.

## Seguridad y reproducibilidad

- Solo datos públicos; sin claves privadas, órdenes reales, apalancamiento ni runtime financiero.
- Spread histórico no disponible en klines: el spread utilizado es una hipótesis explícita.
- Las pruebas nuevas de escenarios son deterministas y offline.
- No se afirma que las pruebas se hayan ejecutado localmente. Consultar GitHub Actions sobre el SHA final para la evidencia remota.

Comando de investigación:

```bash
python -m bot_obrero.product_v2 --days 180 --universe-limit 5 --initial-ars 10000
pytest -q tests/test_product_v2.py
```


## Filtro explícito de relación con BTC

Product V2 usa ahora umbrales de investigación configurables para las correlaciones de retornos alineados temporalmente:

- --min-btc-corr-30d 0.50
- --min-btc-corr-90d 0.60

Son valores iniciales de producto, no umbrales científicamente validados ni garantías de calidad futura. Una candidata con métrica desconocida, varianza insuficiente o historia no alineada se rechaza de forma conservadora. Los rechazos se informan con BTC_CORRELATION_30D_BELOW_MINIMUM, BTC_CORRELATION_90D_BELOW_MINIMUM o BTC_METRICS_INSUFFICIENT, junto con las demás razones aplicables.

## Régimen BTC temporalmente coherente

BULLISH requiere que el cierre de la última vela 4h cerrada esté por encima de ambas EMA, que la EMA rápida supere la lenta y que el RSI cumpla el umbral configurado. BEARISH requiere cierre por debajo de ambas EMA, EMA rápida por debajo de lenta y RSI por debajo de 50. Las demás combinaciones son NEUTRAL. El precio spot actual se muestra como una observación separada y no clasifica el régimen.

Si BTC no es BULLISH, no se presentan candidatas como entradas aprobadas. Los resultados históricos pueden mostrarse para investigación.

## Escenarios de capital y límites de evidencia

Los escenarios nominales de 10.000 / 25.000 / 50.000 ARS permanecen en CONVERSION_UNKNOWN: no se ha verificado una ruta de conversión ejecutable, hora, costes, saldo neto, reserva de comisiones ni coste de operación expresable en ARS. Por tanto, las celdas ARS que dependen de conversión permanecen UNKNOWN o NOT_COMPUTABLE, y la decisión es NO_TRADE.

La comparación separada de 100 / 250 / 500 USDT es matemática y normalizada, no una equivalencia de ARS. Reutiliza el mismo histórico, estrategia y supuestos de costes para explorar mínimos de cantidad/nocional, fills, retorno neto y drawdown. La asequibilidad de ese bloque solo se refiere a un snapshot público actual del instrumento; no prueba conversión de ARS ni garantiza que haya señal histórica. No se escala automáticamente el tamaño para consumir todo el saldo y se conserva el máximo de cinco entradas en cualquier ventana móvil de 30 días.

**Conclusión actual:** el bloqueo principal verificable de los tres presupuestos ARS es la falta de conversión comprobada. No puede afirmarse que 25.000 o 50.000 ARS amplíen oportunidades reales. Solo el resultado remoto de la suite y del E2E sobre el SHA final podrá acreditar la validación de código; ningún backtest demuestra rentabilidad futura.


## Fuentes públicas investigadas para ARS → USDT

- Binance describe el flujo de compra P2P de USDT mediante moneda fiat, método de pago y anuncios con límites por vendedor: https://www.binance.com/es-AR/support/faq/detail/360043832851
- La página oficial de tarifas P2P indica que las tarifas aplicables varían por mercado y que las tasas personalizadas requieren iniciar sesión: https://www.binance.com/en/fee/p2pFeeRate
- El anuncio oficial de 2025 incluyó ARS entre los mercados fiat con tarifa taker de 0,06–0,08 USDT por orden en la Zona P2P; esto no verifica la tarifa actual de una orden concreta: https://www.binance.com/es-AR/support/announcement/detail/70c92ace9acb45529cd4541195248c91

Estas páginas demuestran que P2P es una vía documentada y que pueden existir comisiones, pero no constituyen una cotización ejecutable actual para una cuenta concreta. En esta revisión no se verificaron anuncios en vivo, método de pago disponible, límites para 10.000/25.000/50.000 ARS, precio efectivo, spread, comisión aplicable ni USDT netos. Por eso los tres escenarios siguen en CONVERSION_UNKNOWN y NO_TRADE. No se usa una tasa tomada de una fuente genérica ni se presenta una ruta P2P como disponible sin verificarla.


## Coste mínimo modelado de una operación completa

El informe calcula el retorno bruto aproximado de equilibrio de una ida y vuelta con comisión por entrada/salida, medio spread supuesto en cada lado y deslizamiento por fill. También muestra las comisiones totales y el coste modelado de spread/deslizamiento de los backtests. El punto de equilibrio es una aproximación del modelo, no una comisión real observada ni una predicción. Los costes expresados en ARS siguen siendo UNKNOWN mientras no se verifique una conversión ejecutable.


## Product V2 — baseline frente a estrategia integrada

La salida separa **BASELINE EMA/RSI** (referencia sin filtros BTC históricos) de **INTEGRATED BTC-GATED** (simulación donde cada nueva entrada exige régimen BTC BULLISH y correlaciones de retornos de 30/90 días calculadas sobre velas 4h alineadas hasta el cierre de la señal). Una entrada admitida se ejecuta como máximo en la apertura de la vela siguiente. El filtro no bloquea salidas válidas.

El período contiene 90 días de calentamiento, 180 días de evaluación y 60 días finales de validación independiente con parámetros fijos. No se ajustan parámetros con la ventana de validación. Si falta histórico o las velas BTC/candidata no se alinean, la candidata se excluye; el período no se reduce silenciosamente.

Los informes comparan retorno neto, drawdown, entradas/salidas, exposición y rechazos por filtros para baseline e integrada; los escenarios hipotéticos de 100/250/500 USDT se ejecutan en ambas variantes. Los importes de 10.000/25.000/50.000 ARS siguen en `CONVERSION_UNKNOWN` y `NO_TRADE` sin ruta de conversión ejecutable y costes verificados. Los resultados históricos no prueban rentabilidad futura.


## Verificación de admisión histórica por entrada

La variante integrada evalúa el régimen BTC y las correlaciones de 30/90 días en el índice histórico exacto de cada señal BUY. Solo utiliza prefijos de velas 4h alineadas hasta el cierre de esa señal; una modificación posterior del histórico no debe cambiar la decisión anterior. Las ventanas insuficientes, las métricas desconocidas o un régimen distinto de `BULLISH` rechazan la nueva entrada y dejan un motivo en el informe.

La admisión se aplica únicamente a nuevas entradas. Una posición ya abierta conserva las reglas EMA/RSI de salida aunque el régimen BTC deje de ser alcista. El baseline EMA/RSI sin filtros y el backtest integrado se calculan por separado y no se combinan sus fills ni sus métricas. Los tests deterministas comprueban también que una candidata bloqueada puede diferir del baseline; la diferencia medida no implica que el filtro mejore la rentabilidad.

La validación independiente permanece fija en 60 días después de los 180 días de evaluación y no se utiliza para ajustar parámetros. El presupuesto ARS permanece `CONVERSION_UNKNOWN`; las simulaciones de 100/250/500 USDT son escenarios ficticios separados.
