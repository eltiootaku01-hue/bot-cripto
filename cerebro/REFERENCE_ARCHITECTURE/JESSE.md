# JESSE — NOTAS ARQUITECTÓNICAS

Fuente:
https://github.com/jesse-ai/jesse

Licencia observada: MIT

Referencia auditada: master

## OBSERVED

Jesse posee un catálogo amplio de indicadores, incluyendo:
EMA, RSI, SMA, MACD, Bollinger, ATR y muchos otros.

Su implementación de EMA usa un kernel Rust y permite cálculo secuencial o de serie completa.

Su modo de backtest:
- inicializa rutas y exchanges;
- carga velas históricas;
- ejecuta una simulación;
- genera metrics/trades;
- puede generar equity/drawdown/charts;
- mantiene una sesión de backtest para la UI.

## LO MÁS VALIOSO

1. Catálogo y cobertura de indicadores.
2. Tests y casos de borde de indicadores.
3. Separar indicador puro de orchestration/backtest.
4. Métricas estandarizadas.
5. Warmup de candles antes del período evaluado.

## BOT-CRIPTO

Nuestros EMA/RSI/SMA no deben reemplazarse.
Lo que sí debemos estudiar:
- validación de inputs;
- warmup;
- precisión;
- secuencial vs vectorizado;
- métricas;
- backtest determinista.

## DECISIÓN

ADOPTAR PATRONES E IDEAS.
NO importar el runtime de Jesse.

Fuente ejemplo:
https://github.com/jesse-ai/jesse/blob/master/jesse/indicators/ema.py
