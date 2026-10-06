# BOT-CRIPTO — DEEP DIVE DE ARQUITECTURA DE REFERENCIA

Fecha: 2026-10-06
Base de BOT-CRIPTO auditada: main @ f804e52317b771430c999675329f681eea133b66
CI de esta base: run 37476184696 — tests — SUCCESS

## Objetivo

Esta segunda pasada no busca copiar código.

Busca extraer principios arquitectónicos reutilizables de los proyectos de referencia y contrastarlos contra los contratos propios de BOT-CRIPTO.

Regla:
OBSERVED = visto en fuente real.
TESTED = respaldado por ejecución.
INFERRED = conclusión arquitectónica.
UNKNOWN = evidencia insuficiente.
PROPOSED = diseño propio pendiente de autorización.

## 1. CONVERGENCIAS QUE DEBEMOS CONSERVAR

### 1.1 Fronteras explícitas

OBSERVED:
Nautilus separa DataEngine, RiskEngine, ExecutionEngine y Portfolio.
LEAN separa Portfolio Construction, Risk Management y Execution.
Hummingbot separa strategy, executor y connector.
Freqtrade separa Strategy, exchange y backtesting.
Passivbot separa planificación, ejecución, fills, riesgo y recuperación.

INFERRED:
Una clase monolítica que combine Strategy + Risk + Sizing + Execution sería una regresión arquitectónica.

DECISIÓN:
BOT-CRIPTO mantiene fronteras contractuales pequeñas.

### 1.2 Observación no equivale a autoridad

OBSERVED:
Los proyectos maduros distinguen datos observados, estado derivado, decisiones y acciones externas.

INFERRED:
BOT-CRIPTO debe mantener esta cadena de autoridad:
MarketData/Observation
→ Analysis/Signal
→ RiskDecision
→ RiskAuthorization
→ Financial Admission / Reservation
→ Execution

DECISIÓN:
Una señal, indicador o estrategia nunca autoriza por sí sola gastar dinero.

### 1.3 El tiempo es parte del contrato

OBSERVED:
Freqtrade usa startup_candle_count para controlar warmup.
Jesse usa warmup en backtest.
Nautilus y Passivbot incorporan clocks, timestamps y readiness.

INFERRED:
"No hay suficientes velas" y "los datos no estaban disponibles a tiempo" son problemas distintos.

DECISIÓN:
La semántica propia de availability/evidence timestamp de BOT-CRIPTO es una garantía arquitectónica y no debe sustituirse por un simple contador de velas.

### 1.4 Lifecycle de órdenes como estado

OBSERVED:
Hummingbot PositionExecutor mantiene explícitamente open/close/TP/failed orders, fills y estados de shutdown.
ClientOrderTracker mantiene órdenes activas, cacheadas y perdidas.
Nautilus ExecutionEngine administra lifecycle y reconciliation.

INFERRED:
submit_order() no puede ser el modelo completo de ejecución.

DECISIÓN:
La integración Reservation → Execution deberá mantener un lifecycle explícito, auditable e idempotente.

### 1.5 Risk autoriza; Execution ejecuta

OBSERVED:
LEAN modela Portfolio Construction → Risk Management → Execution.
Nautilus coloca RiskEngine antes de ExecutionEngine.

INFERRED:
Risk no debe llamar al exchange.

DECISIÓN:
Risk produce decisión/autoridad. Execution es el dueño de la frontera externa.

### 1.6 Backtest no es solamente velas + estrategia

OBSERVED:
Freqtrade trata fees, precision, wallet, timeframe, protections y otras condiciones como parte del backtest.
Jesse produce trades, métricas, equity curve y sesiones.
Nautilus comparte semántica de dominio entre backtest y live.
OctoBot trata backtesting como una capacidad distinta.

INFERRED:
El backtest mínimo correcto necesita:
dataset + reloj/replay + strategy + risk + sizing + simulated execution + accounting + metrics.

DECISIÓN:
No construiremos un backtest que simplemente cuente señales.

### 1.7 Reproducibilidad es un requisito funcional

OBSERVED:
Freqtrade documenta la dependencia entre configuración/parametrización y reproducibilidad.
Jesse separa entrenamiento y testing y conserva resultados de backtest.
Sus experimentos de performance también usan fingerprints de resultados.

INFERRED:
Una corrida de investigación debe poder explicar qué dataset, parámetros, warmup, fees, reglas y versión contractual produjo el resultado.

DECISIÓN:
La identidad de un backtest será evidencia, no decoración.

### 1.8 Recovery = reconciliación, no retry ciego

OBSERVED:
Nautilus reconcilia el estado interno con el estado real del venue.
Passivbot tiene settling de position/fill y recovery con preflight/fingerprints.
Hummingbot contempla órdenes perdidas y actualizaciones posteriores.

INFERRED:
UNKNOWN después de una operación externa no significa automáticamente FAILED.

DECISIÓN:
MurphyGuard/fail-closed + reconciliation siguen siendo la estrategia correcta.

### 1.9 Observabilidad causal pero acotada

OBSERVED:
Passivbot usa tipos de eventos, reason codes, IDs causales y límites de payload.
Freqtrade incorpora freshness y timeout para orderbook en su rama experimental ccxt.pro_orderbook.

INFERRED:
Debemos poder responder qué ciclo, snapshot, propuesta u orden originó un hecho sin introducir un sistema distribuido.

DECISIÓN:
Observabilidad acotada y causal. No Kafka/Redis por defecto.

## 2. EXTRACCIÓN POR PROYECTO

### NautilusTrader

Aporta:
- boundaries claros;
- reconciliation;
- shared semantics entre backtest/live;
- ports/adapters;
- recovery basado en evidencia;
- modelado explícito de execution/risk.

No copiar:
- kernel Rust completo;
- message bus/cache distribuido;
- multi-venue;
- framework completo.

### Hummingbot

Aporta:
- lifecycle explícito de una posición;
- tracking de órdenes activas/cacheadas/perdidas;
- fill accounting;
- collateral lock y cuantización.

No copiar:
- una segunda arquitectura de executor paralela a ExecutionOrchestrator.

### Freqtrade

Aporta:
- Strategy interface rica;
- warmup;
- protections separadas;
- backtest con supuestos explícitos;
- optimización reproducible.

No copiar:
- código GPL;
- arquitectura completa de aplicación.

### QuantConnect LEAN

Aporta:
- separación Portfolio Construction / Risk / Execution;
- target como output intermedio;
- drawdown como política separada;
- risk que transforma objetivos antes de ejecución.

No copiar:
- framework entero;
- tipos numéricos que contradigan nuestras garantías.

### Jesse

Aporta:
- catálogo de indicadores;
- backtest con métricas y sesiones;
- train/test en optimización;
- fingerprints de resultados como regresión determinista.

No copiar:
- Redis/DB/UI como requisitos del core financiero.

### OctoBot

Aporta:
- backtesting como capacidad separada;
- dependency graph y resolución transitiva en flows complejos.

No copiar:
- producto/UI;
- complejidad de DSL;
- carga tolerante para autoridad financiera;
- código GPL.

### Passivbot

Aporta:
- eventos tipados;
- causal IDs;
- fill settling;
- control de order churn;
- recovery con fingerprints;
- preocupación explícita por integridad del dataset de backtest.

No copiar:
- Rust como requisito;
- supervisor/tmux/Docker;
- estrategia completa.

## 3. VARIANTS DE LOS ZIP — VALOR REAL

### Freqtrade ccxt.pro_orderbook

Branch observado:
ccxt.pro_orderbook
Tip observado:
ea4f84ee41888266b80523c01f51c5d59405be2a

Comparación contra develop:
diverged; 37 commits ahead; 180 commits behind.

Lección:
un orderbook en tiempo real necesita freshness, timeout, lifecycle de suscripción y aislamiento del transporte.

No justifica añadir websocket ahora.

### Jesse add-backtest-checker

Branch observado:
add-backtest-checker
Tip:
a070636116d54e28cbf42364cc83f2552b8ade31

Comparación contra master:
diverged; 3 commits ahead; 1657 behind.

Lección:
hay valor en vigilar el estado del proceso de backtest.

No sustituye replay determinista, dataset integrity ni result fingerprint.

### OctoBot user_action_created_id

Branch observado:
user_action_created_id
Tip:
6b2685c62fedeffd281f5c59c035a60b07c78ecf

Comparación contra master:
diverged; 8 commits ahead.

Lección:
dependencias explícitas y transitivas son más auditables que secuencias implícitas.

Advertencia:
la carga tolerante de estado puede ser válida para configuración/product state, pero no para Reservation/Risk/Execution. En datos financieros autoritativos, corrupción o ausencia debe producir UNKNOWN/fail-closed.

### Passivbot codex/tm-hsl-lean-one-side

Tip:
b7065e765ee67e52b8b8df5a58e8616297689dd3

Comparación contra master actual:
ahead_by = 0
behind_by = 650

Conclusión:
es un snapshot histórico/ancestor, no una arquitectura adicional.

## 4. LO QUE BOT-CRIPTO YA RESUELVE FUERTEMENTE

OBSERVED en el código/documentación del proyecto:
- MarketData canonical;
- availability temporal;
- EvidenceTimestamp;
- AvailabilityBinding;
- diseño de RiskEvaluationContext inmutable;
- Reservation;
- ReservationReadSet;
- EffectiveCapacity;
- atomic admission;
- Financial Admission;
- ExecutionBoundary;
- ExecutionOrchestrator;
- durable idempotency;
- MurphyGuard/fail-closed;
- infraestructura de adquisición/evidencia.

INFERRED:
El núcleo de seguridad financiera de BOT-CRIPTO ya está deliberadamente orientado a evidence + authority, y no necesita ser reemplazado por un framework externo.

## 5. LO QUE REALMENTE FALTA

Prioridad de diseño:

1. Strategy Runtime.
2. Sizing / Portfolio Construction.
3. Risk Engine operativo + RiskAuthorization.
4. Integración Reservation → Execution.
5. Replay determinista.
6. Simulated Execution + accounting.
7. Backtest metrics + reproducibility fingerprint.
8. Drawdown/exposure policies.
9. Recovery/reconciliation runtime completo.
10. Parameter optimization / walk-forward.
11. Orderbook/websocket freshness, solo si una estrategia lo exige.
12. Observabilidad causal acotada.

## 6. LO QUE NO DEBEMOS HACER

- copiar NautilusTrader entero;
- duplicar ExecutionOrchestrator con un Executor paralelo;
- convertir Strategy en autoridad financiera;
- convertir Risk en transport;
- hacer un backtest que solo cuente señales;
- tolerar corrupción de estado financiero;
- reintentar ciegamente operaciones cuyo outcome sea UNKNOWN;
- introducir Redis/Kafka/Docker por prestigio;
- añadir multi-venue antes de necesidad real;
- añadir orderbook/websocket antes de un caso de uso real;
- introducir GPL en producción por comodidad.

## 7. CONCLUSIÓN

ALTA CONFIANZA:

Los siete proyectos principales y los variants relevantes ya permiten diseñar las próximas fronteras sin improvisar.

A partir de aquí la investigación deja de ser "buscar más bots".

El método correcto es:
1. elegir una frontera concreta de BOT-CRIPTO;
2. usar solo 2-3 referencias máximamente pertinentes;
3. diseñar el contrato propio;
4. contrastarlo con esta bitácora;
5. auditarlo;
6. implementar solo después de cerrar evidencia.

UNKNOWN:
La licencia de cada repositorio no implica automáticamente que una pieza concreta esté autorizada para reutilización en nuestro producto. Cualquier reutilización literal futura requiere revisión específica de licencia/copyright.

REGLA FINAL:
la referencia externa nunca gana al contrato de BOT-CRIPTO.
