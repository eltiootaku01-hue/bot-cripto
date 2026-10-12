# BOT-CRIPTO — análisis y backtest virtual v1

## Requisitos

Python 3.11 o superior. Desde la raíz del repositorio, instala el proyecto y las pruebas con:

    python -m pip install -e ".[test]"

## Análisis de mercado

La CLI de solo lectura consume metadatos públicos de Binance Spot y calcula SMA, EMA y RSI mediante los algoritmos y contratos existentes:

    python -m bot_obrero.analysis_cli --symbol BTCUSDT --interval 1m

Opciones principales: --candles (100 por defecto), --sma-period (20), --ema-period (20) y --rsi-period (14). El análisis exige disponibilidad temporal explícita, calidad válida, completitud conocida y continuidad del histórico. Los estados UNKNOWN no se interpretan como prueba de vela abierta.

## Backtest histórico virtual

Ejemplo reproducible de configuración (los datos de mercado son descargados de Binance al ejecutar el comando):

    python -m bot_obrero.virtual_backtest --symbol BTCUSDT --interval 1m --candles 500

El backtest admite hasta 1000 velas cerradas y tiene un presupuesto acotado de solicitudes históricas. El ejemplo de estrategia es long-only: compra cuando la EMA rápida cruza por encima de la EMA lenta y el RSI supera el umbral configurado; vende cuando la EMA rápida cruza por debajo de la lenta o el RSI alcanza el umbral de salida. La señal se calcula al cierre de una vela y la ejecución simulada ocurre, como mínimo, en la apertura de la vela siguiente. Nunca se rellena una operación al precio de cierre que generó esa misma señal.

Parámetros configurables:

- --fast-ema-period (12), --slow-ema-period (26), --rsi-period (14)
- --rsi-buy-min (50), --rsi-sell-at (70)
- --initial-quote-balance (10000)
- --position-fraction (1, entre 0 y 1)
- --fee-rate (0.001 por fill)
- --slippage-bps (5 puntos básicos por fill)

El motor virtual usa Decimal, saldo ficticio en la moneda cotizada, posición spot larga sin apalancamiento, fills simulados, comisiones, deslizamiento, equity marcada al cierre, PnL realizado/no realizado y drawdown máximo. Una posición abierta al final se valora al último cierre y no se liquida artificialmente. El informe muestra el símbolo canónico, intervalo, período histórico, parámetros, indicadores, operaciones, saldo, posición, comisiones, PnL, retorno y drawdown.

## Política temporal y limitaciones

La adquisición histórica canónica puede entregar available_at=None y dejar UNKNOWN la completitud del sobre MarketData o la finality de la vela. El backtest no modifica esos campos ni afirma que sean evidencia histórica de disponibilidad. Su modelo de simulación declara explícitamente que el cierre de cada vela se considera observable en su límite de cierre y que cualquier señal solo se ejecuta en la apertura siguiente. Si available_at está presente y es posterior al cierre simulado, el histórico se rechaza. La calidad inválida, vela abierta/desconocida, completitud de vela UNKNOWN, huecos, duplicados, desorden, timeframe inesperado y datos insuficientes detienen el cálculo; una última vela explícitamente abierta/parcial se excluye.

Este modelo temporal es una hipótesis de backtest, no una prueba de cuándo estuvo realmente disponible cada dato para un consumidor en vivo. Las velas OHLC no permiten reconstruir el camino intravela, la latencia real ni la profundidad de mercado. El deslizamiento y las comisiones son supuestos configurables, no cotizaciones ejecutables. El resultado no es una garantía de rentabilidad futura.

## Seguridad

Solo se usan endpoints públicos de ExchangeInfo y klines Spot. El simulador mantiene su propia contabilidad ficticia y no importa ni invoca StrategyRuntime, FinancialExecutionRuntime, TradeProposal, Risk, reservas, reconciliación ni órdenes reales. No requiere claves API ni añade dependencias o servicios residentes.

## Pruebas

Las pruebas de tests/test_virtual_backtest.py son deterministas y simulan la adquisición; no llaman a Binance. El workflow existente de GitHub Actions permanece sin cambios y ejecuta la suite completa con pytest -q. En el entorno disponible, pytest local no se ejecutó porque el terminal no pudo resolver github.com por DNS; la validación remota debe atribuirse exclusivamente al SHA exacto del workflow.
