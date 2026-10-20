# MODO 1 — TENDENCIA: grid walk-forward V2, enero–octubre de 2026

**Resultado: NO HAY GANADOR QUE PASE LA PUERTA — `NO_TRADE`.**

- Rama/PR: `research/trend-walkforward-grid-v2` / PR #125 (la URL se añadirá cuando se abra).
- Período OOS simulado: 2026-01-01 00:00 UTC a 2026-10-17 16:00 UTC, con el final exclusivo técnico fijado en 2026-10-18 00:00 UTC.
- Dataset lógico SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Archivo fuente SHA-256: `3851a560aafbaa44f7d57081bd12f7dfdae070c2abff3d65e122ad8fcbd6df36`.
- Configuración SHA-256: `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`.

## Qué se probó

Se definieron **2.880 configuraciones únicas** para Modo Tendencia:
- Donchian de entrada: 20/30/40/55/80/120 velas 4h.
- Donchian de salida: 10/15/20/30/40.
- EMA: 100/150/200/250; pendiente a 3/5/10 velas.
- Stop: 2/3/4/5 × ATR14.
- Filtro ADX: apagado o ADX14 ≥ 20.

Cada uno de los 10 meses de enero a octubre seleccionó candidatas solo con los 180 días anteriores. Eso genera **28.800 evaluaciones configuración-mes**. Un cribado vectorizado de retorno a 24h descartó candidatos débiles; 80 como máximo por mes fueron contrastados después con el replay completo de stop/canal. Se ejecutaron **680 replays completos de candidatas** en los plazos de entrenamiento. De todas las candidatas examinadas, se guardan en el CSV de auditoría sus parámetros, retornos, razones de rechazo y estado; no se borraron las pérdidas.

Ocho meses seleccionaron una regla histórica del período previo; julio y agosto quedaron en `NO_TRADE` porque ninguna de las candidatas re-reproducidas superó las barreras de entrenamiento.

## Resultado secuencial en el tramo ocultado a su selección mensual

Capital ficticio inicial: 31 USDT. Una sola posición Spot larga entre BTCUSDT y ETHUSDT; sin leverage, short ni órdenes reales.

| Escenario de costes | Capital final | Retorno neto | Drawdown máximo | Operaciones cerradas | Ganadoras |
|---|---:|---:|---:|---:|---:|
| BASE | 27,0544 USDT | −12,7278 % | −23,8767 % | 11 | 4 |
| STRESS_1 | 26,3572 USDT | −14,9766 % | −25,1291 % | 11 | 4 |
| STRESS_2 | 24,6280 USDT | −20,5548 % | −28,6664 % | 11 | 3 |

BASE usa comisión 0,10% por lado, spread completo supuesto 10 bps y slippage 5 bps por lado. STRESS_1 usa 0,10%, spread 25 bps y slippage 10 bps. STRESS_2 usa 0,20%, spread 50 bps y slippage 20 bps. Son supuestos para replay OHLC, no costes observados en libros históricos.

## Comparación en el mismo período

| Benchmark con costes BASE | Retorno |
|---|---:|
| Estrategia walk-forward | −12,7278 % |
| Cash | 0,0000 % |
| Buy-and-hold BTCUSDT | −7,1729 % |
| Buy-and-hold ETHUSDT | −18,2447 % |

La estrategia pierde frente a cash y BTC buy-and-hold en el mismo intervalo. No alcanza la regla de ganador.

## Comparación mensual (9 meses completos; octubre parcial)

| Escenario | Meses positivos | Supera cash | Supera BTC mensual | Supera ETH mensual | Retorno medio mensual | Peor mes |
|---|---:|---:|---:|---:|---:|---:|
| BASE | 2/9 | 2/9 | 3/9 | 4/9 | −1,0297 % | −9,8336 % |
| STRESS_1 | 2/9 | 2/9 | 3/9 | 4/9 | −1,2829 % | −10,4725 % |
| STRESS_2 | 1/9 | 1/9 | 3/9 | 4/9 | −1,9346 % | −12,1122 % |

## Puerta de ganador

La puerta congelada requería: retornos positivos en los tres escenarios; drawdown BASE no peor que −20%; al menos 30 operaciones cerradas; y retorno BASE superior a cash y ambos benchmarks de mantener. **Fallan los seis controles agregados**: el retorno es negativo en cada coste, el drawdown BASE supera el límite, solo hay 11 operaciones y el retorno no supera cash/BTC.

## Qué prueba, y qué no

Esta prueba demuestra que pudimos ejecutar un proceso de selección mensual sin dejar que la vela del mes objetivo participe en el ajuste de sus propios parámetros; además, el reporte incluye las decisiones de no operar, las operaciones y las curvas de equity. El resultado es negativo: el bot no tiene permiso para promover la configuración.

No es un holdout globalmente intacto: el archivo histórico ya se utilizó en investigaciones previas. Todas las 30.720 velas siguen con `available_at` ausente y finality desconocida; no hay bid/ask histórico verificable ni lot-size/min-notional históricos por fill. La prueba verifica una regla de investigación sobre OHLC, **no** la ejecución PAPER con datos de mercado realmente disponibles a cada decisión.

**Decisión de BOT-CRIPTO: mantener `NO_TRADE`.** No reemplazar controles operativos por la configuración que haya obtenido el mejor entrenamiento. La siguiente prueba económica requiere datos prospectivos independientes y un modelo PAPER con quotes verificables.
