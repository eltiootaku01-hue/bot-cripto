# PAPER Adapter v1 — componente aislado y frontera pendiente

## Estado

Este incremento añade un adaptador de ejecución exclusivamente local y SQLite para probar la ruta canónica existente:

TradeProposal → Risk → Financial Admission → Reservation → ReservationExecutionBridge → ExecutionOrchestrator → PaperExecutionAdapter.

No se presenta como ciclo de mercado completo. La prueba de composición usa una propuesta determinista sintética y no convierte una señal de Product V2 en una orden.

## Qué hace el adaptador

- Implementa el contrato ExchangeAdapter existente; no redefine submit() ni cancel().
- No importa ni delega a clientes de Binance, endpoints privados, claves, StrategyRuntime ni FinancialExecutionRuntime.
- Exige un OrderIntent derivado de una reserva persistida y verificada.
- Exige instrumento Spot y una cotización pública explícita con available_at, observed_at, fuente identificada como Binance pública y antigüedad limitada.
- Aplica deslizamiento adverso configurable y comisión por fill usando Decimal.
- Persiste balance ficticio y fills en SQLite; no reinicializa un archivo existente si cambian la cuenta, el activo cotizado o el saldo inicial configurado.
- Protege los identificadores de orden contra doble fill y conflictos de intención.
- Etiqueta el estado y cada fill como PAPER.

El proveedor de cotizaciones se inyecta. El adaptador no obtiene datos de red por sí mismo. El consumidor operativo todavía debe conectar una adquisición pública ya validada y el estado ficticio recuperado a los contratos de contexto de riesgo.

## Pruebas añadidas

tests/test_paper_adapter.py contiene pruebas deterministas offline de fills BUY/SELL, comisiones, balances, persistencia y recuperación, idempotencia, cotizaciones UNKNOWN/atrasadas/futuras, límites de gasto, acceso directo bloqueado y composición del runtime financiero existente con un adaptador PAPER.

La prueba de composición está diseñada para demostrar la ruta de ejecución a partir de una TradeProposal sintética. No demuestra que una señal real de Product V2 pueda generar una propuesta canónica. Su ejecución y el resultado de GitHub Actions siguen pendientes hasta revisar el SHA final.

## Frontera que requiere revisión de CEREBRO

La revisión del código encuentra dos representaciones que aún no están conectadas:

1. product_v2.run() hace adquisición, análisis/backtests y termina expresamente en FINAL DECISION: NO_TRADE; no emite una Signal canónica consumida por un runtime operativo.
2. El contrato Signal aporta dirección, evidencia y temporalidad, mientras que TradeProposal requiere, entre otros, cantidad, tipo de orden, política de precio y, para BUY MARKET, max_quote_spend. No existe un consumidor productor verificado que determine esos términos sin inventar tamaño, presupuesto o semántica de ejecución.

Por tanto, no se ha conectado una señal de Product V2 a una TradeProposal, ni se han derivado las entradas de riesgo de un estado PAPER recuperado. Resolver esa frontera exige una decisión de CEREBRO sobre cómo se construye y autoriza una propuesta PAPER desde una señal, y cómo se enlaza el ledger PAPER con el CanonicalAccountState de riesgo sin alterar los contratos canonizados.

## Estado de seguridad

- PAPER solamente; no hay órdenes reales ni endpoints privados.
- No se ha implementado LIVE.
- La etiqueta interna ExecutionDecision("SUBMITTED") significa que el orquestador local completó su paso de envío al adaptador; no significa aceptación de Binance. La evidencia de fill es el registro SQLite con mode=PAPER.
- La prueba E2E de esta fase es determinista y no accede a Internet.
- La suite local debe registrarse como NOT RUN si el terminal continúa sin resolver github.com; el resultado remoto de GitHub Actions se informará por separado.


## R4 account persistence boundary

The PAPER adapter records an explicit immutable asset universe and an explicit
initial balance (including zero) for each configured asset. A fill is blocked if
either instrument asset is outside that universe or if a known asset lacks a
balance row. A transactionally coherent SQLite snapshot exposes the account
state version and persisted update cutoff; the version increments in the same
transaction as each successful fill. Legacy databases with fills but no provable
asset universe are rejected rather than reconstructed from fills.

This is an adapter-local additive schema extension. It does not change canonical
risk, authorization, reservation, bridge, or intent contracts. The snapshot is
not automatically promoted to CanonicalAccountState; that conversion remains
blocked until its shared contract and provenance requirements are approved.

## R4 quote identity boundary — BLOCKED

The existing order-intent/reservation payload does not carry a verifiable
PaperQuoteEvidence value. The adapter still obtains a quote from its provider
during submission, after risk authorization. Therefore, the adapter cannot prove
that the fill quote is the same evidence evaluated by risk. Enforcing this safely
requires an approved shared-contract change; no quote identity guarantee is
claimed by this increment.


## PAPER Economic Replay Contract v1

The durable fill schema versions the economic interpretation on every fill:
`economic_contract_version=1`, `fill_sequence`, `decimal_precision=28`, and
`decimal_rounding=ROUND_HALF_EVEN`. A V1 fill also retains the canonical intent
and its hash, quote-evidence identity/hash, bid/ask, fee asset/rate, slippage, UTC
fill timestamp, and post-fill balances.

The immutable initial asset universe and initial balances remain in
`paper_assets`; replay starts from those balances rather than inferring a seed
from later fills. Each successful fill receives the next contiguous sequence
number inside the same `BEGIN IMMEDIATE` transaction that writes both affected
balances, the fill row, and increments `paper_account.state_version`. The
sequence number, not lexical order of client order IDs, defines commit order.
V1 replay requires sequence values exactly `1..N` and rejects timestamps that
regress in that order. A unique partial index prevents duplicate sequence numbers
among V1 records.

V1 arithmetic runs inside a private Decimal context with precision 28 and
`ROUND_HALF_EVEN`, independent of the caller's ambient Decimal settings. The
fill price is the ask for BUY or bid for SELL adjusted adversely by persisted
slippage basis points; fee is `quantity × fill_price × fee_rate` and is charged
in the quote asset. V1 stores canonical decimal strings and does not silently
apply venue tick-size, lot-size, or currency quantization.

Existing rows upgraded from a schema without an economic contract get version
`0` and sequence `0`. They are **not** retroactively labeled V1: replay marks
them incomplete, the account snapshot cannot be promoted as complete, and new
fills fail closed until legacy history receives an explicit review/migration.
An unsupported future contract version is `UNKNOWN`, not silently interpreted
using V1 rules.

These controls prove deterministic accounting and replay integrity for the
persisted PAPER contract. They do not prove exchange execution, a completed real
24-hour market capture, a profitable strategy, or readiness for LIVE. The project
remains `NO_TRADE`.
