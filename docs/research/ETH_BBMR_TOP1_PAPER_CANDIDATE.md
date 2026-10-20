# ETH Bollinger Mean Reversion + EMA720 — TOP1 de investigación periódica

**Identidad:** `ETH_BBMR_168_EMA720`  
**CLI profile:** `eth-bbmr-top1`  
**Estado de aceptación:** `RESEARCH_ONLY / NOT PROMOTED`  
**Decisión operativa del proyecto:** `NO_TRADE`  
**Frecuencia:** guiada por señales; puede ser ocasional/mensual. No se deben forzar entradas diarias o mensuales.

## Programa listo para replay virtual

Desde un clon limpio y actualizado de `main`, instalar el paquete en el entorno virtual como indica el README. El replay anual, con capital ficticio de 60 USDT y liquidación final, se ejecuta así:

```powershell
.\.venv\Scripts\bot-cripto.exe backtest --strategy eth-bbmr-top1 --symbol ETHUSDT --interval 1h --candles 8760 --initial-quote-balance 60 --position-fraction 0.95 --liquidate-at-end --dataset-output evidence/eth-bbmr-top1-60usdt.json
```

El comando captura/consulta velas públicas históricas para un replay local. No envía órdenes. El bundle exportado identifica los datos adquiridos y la configuración mediante SHA-256; estos hashes no demuestran disponibilidad histórica point-in-time ni rentabilidad futura.

### Costes configurados

BASE, correspondientes a los valores por defecto del comando:
- Comisión: 0,10 % por fill.
- Spread completo supuesto: 10 puntos básicos, imputando la mitad en cada fill.
- Deslizamiento supuesto: 5 puntos básicos por fill.

Repetir exactamente el periodo/capital con los escenarios más exigentes:

```powershell
.\.venv\Scripts\bot-cripto.exe backtest --strategy eth-bbmr-top1 --symbol ETHUSDT --interval 1h --candles 8760 --initial-quote-balance 60 --position-fraction 0.95 --fee-rate 0.001 --spread-bps 25 --slippage-bps 10 --liquidate-at-end
.\.venv\Scripts\bot-cripto.exe backtest --strategy eth-bbmr-top1 --symbol ETHUSDT --interval 1h --candles 8760 --initial-quote-balance 60 --position-fraction 0.95 --fee-rate 0.002 --spread-bps 50 --slippage-bps 20 --liquidate-at-end
```

Los spreads y el deslizamiento anteriores son supuestos, no cotizaciones históricas observadas de libro.

## Regla congelada

1. Mercado `ETHUSDT Spot`, velas de una hora, long-only, sin margen ni apalancamiento.
2. Calcular en el cierre completado el promedio simple y la desviación estándar poblacional de los últimos 168 cierres; `z = (close - mean168) / stddev168`.
3. Entrar cuando `z < -2.5` y `close > EMA720`.
4. Salir cuando `z > +1.0` o `close <= EMA720`.
5. Señales calculadas solo sobre barras cerradas; fill virtual en la apertura de la siguiente vela.
6. Usar como máximo el 95 % del saldo USDT por entrada, aplicar los filtros de cantidad que aporte el adaptador de metadatos y mantener el límite de cinco nuevas entradas por ventana móvil de 30 días.
7. Si la desviación estándar es cero o faltan indicadores, no producir una señal.

Los umbrales son constantes del perfil; el perfil exige exactamente `ETHUSDT` en `1h` y el filtro EMA720 para impedir cambios silenciosos. El perfil está implementado en el motor de backtest virtual; no crea un bot de ejecución ni conecta con órdenes.

## Evidencia histórica conocida (no es evidencia prospectiva)

La regla se seleccionó después de examinar el histórico de BTC/ETH. En una simulación anual anterior con capital ficticio de 31 USDT, el resultado modelado fue +12,31 % BASE, +11,25 % STRESS_1 y +8,56 % STRESS_2, con solo cuatro round-trips, principalmente en septiembre/octubre. Durante una ventana intermedia de seis meses no hubo operaciones. El histórico ya fue usado para selección y no es un holdout global limpio.

El replay aislado de octubre de 2026, comenzando con 40 USDT en efectivo, registró una sola operación completada y perdedora:
- BASE: −0,3764 USDT (−0,94 %), saldo final 39,6236 USDT.
- STRESS_1: −0,4676 USDT (−1,17 %), saldo final 39,5324 USDT.
- STRESS_2: −0,7114 USDT (−1,78 %), saldo final 39,2886 USDT.

Los datos históricos disponibles finalizan el 17 de octubre de 2026. El histórico de origen tiene `available_at` no demostrado y finality `UNKNOWN`; las entradas/salidas son aproximaciones con OHLC, no fills históricos bid/ask verificables.

La búsqueda acumulada sobre el repositorio ya evaluó 6.232 candidatas con 56.166 escenarios de replay, una cuadrícula walk-forward de 2.880 configuraciones/28.800 pantallas mensuales y otras búsquedas especializadas. Ninguna pasó todos los criterios registrados de robustez. Los números de búsquedas distintas no se deben sumar como si fueran un único experimento independiente. Esta candidata se retiene como TOP1 de investigación ocasional para facilitar tests comparables, **no como estrategia ganadora**.

## Límites que no cambian

- `NO_TRADE` sigue vigente: no hay promoción económica a PAPER operativo ni LIVE.
- No se integran claves privadas, órdenes reales, margen ni apalancamiento.
- Un resultado positivo en el propio histórico de selección no reemplaza un periodo nuevo fuera de muestra ni la captura prospectiva preregistrada de Kline + bookTicker.
- Cambiar parámetros exige nueva versión/preregistro y reporte de todas las variantes, no solo la ganadora retrospectiva.
