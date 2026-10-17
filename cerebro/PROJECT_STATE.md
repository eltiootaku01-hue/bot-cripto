# BOT-CRIPTO — ESTADO VERIFICADO DE CONTINUIDAD

## Continuidad de GitHub

- main observado al inicio de PAPER-OPERATIONAL-RUNTIME-V1: 8bbd9fc9511690db171c163b4a978a07e8aeb222.
- La PR #63 está CLOSED / MERGED; merge commit: 8bbd9fc9511690db171c163b4a978a07e8aeb222. La descripción histórica de la PR no sustituye su estado vivo.
- Rama de trabajo inspeccionada: feat/paper-operational-runtime-v1; al inicio apuntaba al mismo SHA que main, con cero commits propios.
- PR #62 sigue abierta y sin merge; solo modifica documentación de continuidad. No se modificó en este incremento.
- La suite post-merge enlazada en el historial del proyecto es evidencia de GitHub Actions, no de ejecución local.
- Local pytest: NOT RUN — DNS failure in the available terminal, salvo que una ejecución local posterior produzca evidencia nueva.

## PRODUCT V2 — investigación y decisión

- Entry point existente: python -m bot_obrero.product_v2.
- La investigación usa datos públicos Spot, backtests virtuales y costes declarados; no usa claves privadas, endpoints privados ni órdenes reales.
- El presupuesto en ARS y el saldo hipotético en USDT permanecen separados. Sin ruta y costes de conversión verificables, la puerta de capital permanece CONVERSION_UNKNOWN.
- La revisión de estrategia no demuestra rentabilidad neta repetible. La decisión de producto sigue siendo NO_TRADE; no se modifican parámetros para forzar entradas.

## PAPER-OPERATIONAL-RUNTIME-V1 — incremento de adaptador

- Se añade bot_obrero/paper_adapter.py, un ExchangeAdapter SQLite estrictamente local que exige intención persistida, cotización pública con disponibilidad explícita, temporalidad válida, comisión/deslizamiento Decimal e idempotencia de fills.
- Se añade tests/test_paper_adapter.py con pruebas deterministas offline, incluida una prueba de composición del runtime existente con una TradeProposal sintética. La prueba no convierte señales reales de Product V2.
- Se añade docs/PAPER_ADAPTER_V1.md para documentar capacidades y límites.
- No se implementa LIVE, no se invocan endpoints privados y no se modifican contratos financieros canonizados.
- El resultado local SUBMITTED del orquestador no se presenta como aceptación de Binance; los fills del adaptador se etiquetan PAPER.

## BLOQUEO ARQUITECTÓNICO — REQUIRES CEREBRO REVIEW

- product_v2.run() termina en FINAL DECISION: NO_TRADE y no produce una Signal canónica consumida por el runtime.
- No existe un consumidor verificado que convierta esa señal en TradeProposal con tamaño, tipo/política de precio y max_quote_spend sin inventar condiciones económicas.
- El saldo inicial del adaptador PAPER aún no está derivado del estado canónico de riesgo ni sincronizado como una fuente de verdad compartida. No declarar completo el ciclo operativo hasta resolver esa frontera.
- La CLI de ciclo PAPER, consulta de estado, kill switch y recuperación orquestada no se consideran implementadas por este incremento.

## Evidencia pendiente

- Ejecutar y revisar la suite remota sobre el SHA final de la PR.
- No confundir GitHub Actions con ejecución local.
- No fusionar la PR hasta revisión de CEREBRO y del propietario.
