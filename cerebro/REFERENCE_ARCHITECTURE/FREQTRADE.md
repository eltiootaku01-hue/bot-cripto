# FREQTRADE — NOTAS ARQUITECTÓNICAS

Fuente:
https://github.com/freqtrade/freqtrade

Licencia observada: GPL-3.0

Referencia auditada: develop

## OBSERVED

Freqtrade posee una Strategy interface rica con:
- timeframe;
- startup candle count;
- indicadores;
- entry/exit signals;
- stoploss;
- trailing stop;
- protections;
- position adjustment;
- informative data;
- hyperparameter support.

Su backtester:
- trabaja en modo dry-run;
- desactiva el uso normal de base de datos durante backtest;
- prepara startup candles;
- controla fees/precision;
- genera estadísticas y resultados.

## LO MÁS VALIOSO

1. Strategy necesita una frontera clara.
2. El startup/warmup de datos debe ser explícito.
3. El backtest debe controlar sus supuestos (fees, precision, timeframe).
4. Hyperparameters necesitan identidad/configuración reproducible.
5. Protections deben estar separadas de la strategy.

## RIESGO DE COPIA

La licencia es GPL-3.0.

No copiar código del repositorio directamente a BOT-CRIPTO sin una revisión explícita de licenciamiento.

## DECISIÓN

ADOPTAR IDEAS:
- Strategy interface;
- warmup contract;
- parameter identity;
- protections;
- backtest configuration.

REIMPLEMENTAR DE FORMA PROPIA Y COMPATIBLE CON NUESTROS CONTRATOS.
