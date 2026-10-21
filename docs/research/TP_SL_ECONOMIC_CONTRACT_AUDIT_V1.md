# Auditoría del contrato económico TP/SL V1

**Estado:** auditoría documental de la implementación observada; solo investigación.  
**Decisión operativa:** `NO_TRADE`.  
**Base auditada:** `research/tp-sl-wave-simulation-2026-01-to-10`, HEAD previo `c459e570f8601f7e4e12146559c42de59457d244`.  
**`main` observado al inicio:** `c523c47364b6fb4febd9c48385bc63bcde87ea61`.  
**Alcance de este cambio:** documentación únicamente. No cambia señales V1, TP/SL, costes, simulador, pruebas, `main` ni órdenes.

## 1. Dictamen ejecutivo

En el simulador actual, **TP +1,00 % y SL −0,50 % son movimientos brutos del precio de salida respecto de `entry_fill`**, no retornos netos de la cuenta. El neto se calcula por separado después de la ejecución modelada de salida y de las comisiones de entrada y salida.

La implementación sigue esta separación matemática, pero el ledger contiene un nombre que puede inducir a error: `gross_price_return_pct` **no** divide por `entry_fill`; divide el precio bruto de salida por `entry_open_raw`. Por lo tanto, no representa el retorno bruto desde el precio de fill y no debe compararse directamente con `net_return_on_entry_outlay_pct` como si ambos compartieran el mismo precio de referencia.

## 2. Contrato observado en el código

Fuente principal: [`research/tp_sl_barrier_backtest_v1.py`](../../research/tp_sl_barrier_backtest_v1.py). Constantes de TP/SL: `TAKE_PROFIT_PCT = 0.01`, `STOP_LOSS_PCT = 0.005`. Solo las señales congeladas `UP` abren posiciones largas Spot.

Sean:

- `P0 = entry_open_raw`: apertura bruta de la vela de entrada.
- `e = (spread_bps / 2 + slippage_bps) / 10_000`: penalización modelada de precio por lado.
- `f = fee_rate`: comisión aplicada al notional de entrada y al de salida.
- `P_entry = entry_fill = P0 × (1 + e)`.
- `P_TP = P_entry × 1.01`; `P_SL = P_entry × 0.995`.

En una salida normal exactamente por barrera, antes de costes de salida, el movimiento bruto calculado **desde el fill** es +1,00 % para TP o −0,50 % para SL. Hay excepciones de ejecución por el contrato OHLC: un gap de stop sale a la apertura bruta de esa vela y puede empeorar la pérdida; un gap de objetivo se acredita de forma conservadora al trigger de TP; una salida temporal se efectúa al cierre de la vela límite.

El fill de salida modelado es:

```text
P_exit = exit_trigger_raw × (1 − e)
entry_cash_outlay = quantity × P_entry × (1 + f)
net_proceeds = quantity × P_exit × (1 − f)
net_pnl_usdt = net_proceeds − entry_cash_outlay
net_return_on_entry_outlay_pct = 100 × net_pnl_usdt / entry_cash_outlay
```

Estas ecuaciones corresponden al cálculo de `_finish_trade`: el fill de salida recibe media spread más deslizamiento, se aplica una comisión sobre el notional de salida y el PnL neto resta el desembolso de entrada incluido su fee. El `entry_fill` ya incluye el ajuste de ejecución adverso de entrada; no se debe restar ese ajuste otra vez dentro del retorno calculado desde el propio fill. Su efecto frente a la apertura bruta y sobre las cantidades compradas sí está incorporado en el escenario costado.

## 3. Qué significa cada campo del ledger

| Campo actual | Semántica observada | Interpretación correcta |
|---|---|---|
| `entry_open_raw` | Apertura bruta de la vela de entrada | Referencia de mercado previa al ajuste de entrada |
| `entry_fill` | `entry_open_raw × (1 + e)` | Precio de compra modelado, antes de la comisión explícita |
| `take_profit_trigger` / `stop_loss_trigger` | Nivel calculado desde `entry_fill` | Barreras brutas +1,00 % / −0,50 % respecto del fill |
| `exit_trigger_raw` | Precio de salida decidido por la regla OHLC/gap/tiempo | Precio bruto usado antes del ajuste de salida |
| `exit_fill` | `exit_trigger_raw × (1 − e)` | Precio de venta modelado, antes de descontar la comisión de salida |
| `gross_price_return_pct` | `100 × (exit_trigger_raw / entry_open_raw − 1)` | Cambio bruto respecto de la apertura, **no** respecto de `entry_fill` |
| `total_fees_usdt` | Fee de entrada + fee de salida | Comisiones explícitas; no incluye spread/deslizamiento |
| `net_pnl_usdt` | Neto recibido menos desembolso de entrada | PnL monetario después de ejecución y fees modelados |
| `net_return_on_entry_outlay_pct` | `100 × net_pnl_usdt / entry_cash_outlay_usdt` | Retorno neto sobre el efectivo usado por esa entrada |

### Hallazgo de esquema

`gross_price_return_pct` es semánticamente ambiguo por su nombre. En la implementación auditada usa `entry_open_raw`, mientras que la barrera se define sobre `entry_fill`. No se cambió ni renombró aquí para preservar los artefactos y la trazabilidad históricos. Antes de consumir este ledger en informes económicos reutilizables, el contrato de datos debería exponer explícitamente los dos conceptos:

- `gross_return_from_entry_fill_pct = 100 × (exit_trigger_raw / entry_fill − 1)`;
- `raw_trigger_return_vs_entry_open_pct = 100 × (exit_trigger_raw / entry_open_raw − 1)`.

Si se mantiene `gross_price_return_pct` por compatibilidad, debe documentarse como alias legado del segundo campo y no etiquetarse como retorno desde fill.

## 4. Resultado matemático de un TP/SL normal tras costes

Para una salida normal exactamente en la barrera, sin gap ni salida temporal, el retorno neto del desembolso de entrada es:

```text
net_return = ((exit_trigger_raw / entry_fill)
              × (1 − e) × (1 − f) / (1 + f) − 1) × 100%
```

La siguiente tabla deriva de las tarifas y los ajustes por lado fijados en el protocolo; son valores **teóricos del contrato**, no una nueva simulación ni una estimación de rentabilidad futura.

| Escenario | Fee por lado | Ajuste de precio por lado, e | TP bruto desde fill | TP neto teórico | SL bruto desde fill | SL neto teórico | Movimiento bruto mínimo desde fill para equilibrio* |
|---|---:|---:|---:|---:|---:|---:|---:|
| BASE | 0,10 % | 0,100 % | +1,00 % | +0,6974 % | −0,50 % | −0,7981 % | +0,3005 % |
| STRESS_1 | 0,10 % | 0,225 % | +1,00 % | +0,5714 % | −0,50 % | −0,9222 % | +0,4262 % |
| STRESS_2 | 0,20 % | 0,450 % | +1,00 % | +0,1441 % | −0,50 % | −1,3432 % | +0,8546 % |

* El punto de equilibrio resuelve `(P_exit_trigger / P_entry) × (1 − e) × (1 − f) = (1 + f)`, con el mismo ajuste y tarifa a ambos lados. Los resultados suponen fill exacto al trigger, sin gap, sin redondeo de lote/notional y sin salida por tiempo.

**Lectura:** un TP alcanzado no equivale a ganar neto +1 %. Bajo los supuestos fijos, el TP normal deja aproximadamente +0,6974 % en BASE, +0,5714 % en STRESS_1 y +0,1441 % en STRESS_2 sobre el desembolso de entrada. Un SL normal termina peor que −0,50 % neto. Los gaps, costes reales variables, cambios de precio entre trigger y ejecución o filtros del exchange pueden alterar estos valores.

El “round trip” resumido en el protocolo (aproximadamente 0,40 %, 0,65 % y 1,30 %) es una aproximación de fees y ejecución a partir de la apertura de referencia. No debe restarse mecánicamente de +1 % desde fill: ese cálculo mezclaría bases de precio distintas y contaría mal el ajuste de entrada. Para un trade concreto deben usarse los fills, notionales, comisiones y PnL del ledger.

## 5. Costes fijados y límites del modelo

| Escenario | Comisión por lado | Spread completo asumido | Deslizamiento por lado | Ajuste aplicado a cada fill: media spread + slippage |
|---|---:|---:|---:|---:|
| BASE | 0,10 % | 10 bps | 5 bps | 10 bps = 0,100 % |
| STRESS_1 | 0,10 % | 25 bps | 10 bps | 22,5 bps = 0,225 % |
| STRESS_2 | 0,20 % | 50 bps | 20 bps | 45 bps = 0,450 % |

El spread y el deslizamiento son **supuestos**, no observaciones históricas verificadas de libro de órdenes. El replay usa cantidades divisibles y no simula filtros de cantidad/notional del exchange; las velas OHLC no identifican el orden intravela; la disponibilidad histórica `available_at`/finality sigue `UNKNOWN`. Por esto los números anteriores validan la aritmética especificada, no la exactitud de la ejecución real.

## 6. Cobertura revisada y acciones pendientes

**OBSERVED**
- El simulador fija TP/SL como multiplicadores de `entry_fill`.
- El coste de ejecución de entrada se incluye en `entry_fill`; el de salida se resta mediante `exit_fill`.
- Se contabilizan comisiones en ambas patas.
- `gross_price_return_pct` usa `entry_open_raw`; `net_return_on_entry_outlay_pct` usa el desembolso de entrada.

**OBSERVED — pruebas existentes revisadas**
- `research/test_tp_sl_barrier_backtest_v1.py` cubre el orden conservador cuando una vela toca ambas barreras, el stop con gap, el gap de objetivo, los valores fijos y el cálculo de drawdown.
- La función de test de contrato fija las constantes y algunos valores de costes, pero esa suite no demuestra por sí sola el cálculo numérico de retorno neto en cada escenario ni aclara el nombre de `gross_price_return_pct`.

**UNKNOWN**
- Spread/deslizamiento históricos realmente ejecutables, precio de fill real, orden intravela y aplicabilidad de filtros de mercado no están demostrados por esta simulación.

**Siguiente control antes de reutilizar el esquema**
1. Solicitar revisión de CEREBRO para cualquier cambio de contrato de datos.
2. En una tarea de implementación separada, agregar pruebas aritméticas golden para TP y SL netos en los tres escenarios, la identidad de `net_pnl_usdt` y el significado de las dos bases brutas.
3. Mantener los artefactos existentes inmutables; si se agregan columnas, versionar el esquema y no sobrescribir retrospectivamente el significado de campos antiguos.

## 7. Evidencia y decisión

- Protocolo congelado: [`TP_SL_BARRIER_BACKTEST_V1_PREREGISTRATION.md`](TP_SL_BARRIER_BACKTEST_V1_PREREGISTRATION.md).
- Código auditado en el HEAD previo: [`tp_sl_barrier_backtest_v1.py`](../../research/tp_sl_barrier_backtest_v1.py).
- Pruebas revisadas: [`test_tp_sl_barrier_backtest_v1.py`](../../research/test_tp_sl_barrier_backtest_v1.py).
- Ledger congelado V1 SHA-256: `b8f555028791d0a9d2f93dc428acf3882d5ec3488ac56221df3136e4841c3d00`.
- Dataset SHA-256: `13d90afa798c94d56c18331e676089bc4594266c09d71bdab74146df1829f5a8`.
- Diagnóstico de pérdida/señal vs costes: [Actions run #39622638043](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39622638043).
- Suite general previa: [Actions run #39622640580](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39622640580).

Esta auditoría fue documental: no se modificó ni volvió a ejecutar el backtest, no se recalibró nada sobre el período enero–octubre de 2026, no se cambiaron las señales V1 ni se ejecutaron órdenes. El gate continúa en **`NO_TRADE`**.
