# Product V1 — sensibilidad del capital y protección del presupuesto

## Conversión ARS → USDT

La ruta completa de conversión, la cotización ejecutable, la fuente/fecha de una conversión ARS→USDT y sus costes no están verificados. Por eso, los escenarios A/B/C permanecen en `CONVERSION_UNKNOWN` y no se inventan saldos disponibles.

| Capital nominal ARS | Conversión verificada | USDT netos | Candidatas asequibles en ARS | Coste operación completa en ARS | Retorno neto atribuible al capital ARS | Drawdown atribuible | Decisión |
|---:|---|---|---|---|---|---|---|
| 10.000 | CONVERSION_UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_COMPUTABLE | NOT_COMPUTABLE | NO_TRADE |
| 25.000 | CONVERSION_UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_COMPUTABLE | NOT_COMPUTABLE | NO_TRADE |
| 50.000 | CONVERSION_UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | NOT_COMPUTABLE | NOT_COMPUTABLE | NO_TRADE |

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
