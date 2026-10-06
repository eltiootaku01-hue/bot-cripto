# OCTOBOT — NOTAS ARQUITECTÓNICAS

Fuente:
https://github.com/Drakkar-Software/OctoBot

Licencia observada: GPL-3.0

Referencia auditada: master

## OBSERVED

La documentación actual de OctoBot trata como capacidades separadas:
- backtesting;
- paper trading;
- TradingView strategy/indicator automation;
- strategy designer;
- resultados comparables;
- multi-exchange product flows.

El repositorio contiene mucha documentación y UI relacionada con backtesting y diseño de estrategias.

## LO MÁS VALIOSO

1. Pensar el backtest como una capacidad de producto independiente.
2. Comparar múltiples ejecuciones.
3. Hacer configurable la estrategia sin mezclarla con la UI.
4. Mantener una frontera clara entre research/paper/live.

## LO QUE NO DEBEMOS IMPORTAR AHORA

- UI completa;
- cloud product;
- estrategias completas;
- arquitectura GPL dentro del código productivo.

## DECISIÓN

Usar principalmente como referencia de:
- experiencia de backtesting;
- estrategia configurable;
- comparación de resultados.

Fuente:
https://github.com/Drakkar-Software/OctoBot/tree/master/docs/content
