# MODO 1 — TENDENCIA: protocolo de búsqueda walk-forward V2

**Decisión inicial:** `NO_TRADE`.  
**Tipo:** investigación histórica adaptativa / control de implementación; no es prueba prospectiva independiente.  
**Rango de evaluación pedido:** 2026-01-01 00:00 UTC a 2026-10-18 00:00 UTC (el archivo real solo cubre velas completas hasta el 17 de octubre).

## Procedencia y limitaciones

- Dataset lógico SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Configuración SHA-256: `2f6dc2e6021cd32fe5ff9b994f28d638d4f94ec2ce34b5c3873536e86ba0c50a`.
- Archivo fuente SHA-256: `3851a560aafbaa44f7d57081bd12f7dfdae070c2abff3d65e122ad8fcbd6df36`.
- 15.360 velas 1h para BTCUSDT y 15.360 para ETHUSDT desde el 15-01-2025 al 17-10-2026. Tras excluir grupos incompletos al agrupar por UTC, quedan 3.839 velas 4h completas por activo.
- Las 30.720 velas tienen `available_at` ausente y finality desconocida. La identidad de los bytes no demuestra que cada vela estuviera disponible a tiempo para una decisión real.
- **Exposición anterior:** el histórico ya se usó en otros ensayos. Este procedimiento oculta cada mes futuro a su selección mensual, pero no es un holdout globalmente intacto. La persistencia de este documento es posterior a la ejecución exploratoria local; no debe describirse como preregistro criptográficamente previo a esa ejecución.

## Grid fijo (2.880 configuraciones únicas)

- Canal de entrada Donchian: 20, 30, 40, 55, 80, 120 velas de 4h.
- Canal de salida Donchian: 10, 15, 20, 30, 40 velas.
- EMA: 100, 150, 200, 250 velas; pendiente comparada con 3, 5 o 10 velas atrás.
- Stop inicial: 2, 3, 4 o 5 × ATR14.
- Filtro ADX: apagado o ADX14 ≥ 20.
- Spot long-only BTCUSDT y ETHUSDT; una posición total a la vez; entrada en la apertura 4h siguiente al cierre que da la señal; 95% del efectivo por entrada; máximo cinco entradas en ventana móvil de 30 días; sin margen, short ni leverage.

## Cómo evita mirar el futuro de la evaluación

Para cada mes de enero a octubre 2026:

1. Solo se considera el tramo de 180 días inmediatamente anterior al inicio de ese mes.
2. Se calcula para las 2.880 reglas un cribado vectorizado de rendimientos a 24h no solapados con costes BASE y STRESS_1. Ese cribado es solo filtro computacional, no el resultado final.
3. Como máximo los 80 candidatos que superan ese cribado reciben replay completo con stop, gaps conservadores, canal de salida, límite de entradas y equity de liquidación.
4. Solo se puede escoger una regla si en ese tramo previo tiene al menos 3 trades realizados, retorno neto positivo en BASE y STRESS_1, y drawdown no peor que −25% en ambos. Se maximiza el mínimo entre esos dos retornos y se usan desempates deterministas. Si no queda candidata, el mes opera `NO_TRADE`.
5. El mes de evaluación se reproduce secuencialmente y solo puede influir en la selección del mes siguiente después de terminar. Las reglas de salida quedan congeladas para una posición ya abierta.
6. Se guardan los motivos de rechazo y las métricas de todas las 28.800 filas config-mes. No se borran del artefacto las reglas perdedoras aunque el informe principal no las promocione.

## Costes y referencias

- BASE: comisión 0,10% por lado, spread completo supuesto 10 bps, slippage 5 bps por lado.
- STRESS_1: comisión 0,10%, spread supuesto 25 bps, slippage 10 bps por lado.
- STRESS_2: comisión 0,20%, spread supuesto 50 bps, slippage 20 bps por lado.
- Estos costes son supuestos aplicados a OHLC, no spreads/fills históricos medidos.
- Comparar contra cash, buy-and-hold BTC y buy-and-hold ETH en todo el mismo periodo; guardar también cada mes completo frente a cash y buy-and-hold mensual bajo los mismos costes.

## Puerta de ganador (congelada)

No se autoriza promover una estrategia salvo que en todo el tramo OOS:
- retorno neto positivo en BASE, STRESS_1 y STRESS_2;
- drawdown BASE no peor que −20%;
- al menos 30 operaciones cerradas;
- retorno BASE mayor que cash y ambos buy-and-hold completos del mismo período.

Si falla cualquier condición, el resultado operativo es `NO_TRADE`. Estos mínimos no prueban por sí solos una ventaja; solo son barreras de rechazo.

## Artefactos e integridad

CI debe validar hashes fuente/lógicos/configuración, conteos, parámetros, fecha, las 2.880 configuraciones, 28.800 filas de cribado, número de replays completos y el resultado de la puerta de ganador. El manifiesto registra tamaño y SHA-256 de cada archivo de evidencia.

La prueba valida la lógica histórica y el control de información temporal sobre velas OHLC. **No** valida fills con bid/ask real, disponibilidad histórica, lot-size/min-notional históricos ni el pipeline de PAPER extremo a extremo. No envía órdenes.
