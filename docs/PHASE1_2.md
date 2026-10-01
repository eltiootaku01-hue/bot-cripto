# BOT OBRERO — FASE 1.2

## Auditoría y estado verificable

Repositorio: `eltiootaku01-hue/bot-cripto`  
Branch: `main`  
HEAD auditado al inicio: `afe2b8ec4e01e449e214834477121c670d7f4b22`

### Paso 0 — hallazgos

La inspección del HEAD confirmó que `bot_obrero/`, el catálogo, la documentación de FASE 1.1 y los tests de FASE 1.1/1.2 ya existían. No se reemplazó silenciosamente ninguna defensa.

La documentación de FASE 1.1 indica que F-001..F-014 no estaban presentes en el repositorio al comenzar FASE 1.1. Por tanto, en este repositorio esos riesgos de FASE 1 siguen **NOT VERIFIED**; los tests existentes verifican principalmente F-015..F-020.

La suite de GitHub Actions existe y ejecuta `pytest -q` con Python 3.12. La ejecución local de pytest desde este entorno no es posible; por ello este documento no convierte la existencia de tests en un PASS.

## Matriz de auditoría

| Riesgo | Implementación existente antes de FASE 1.2 | Defensa existente | Test existente | Gap encontrado |
|---|---|---|---|---|
| O1 | PARCIAL: configuración básica | posición/margen/apalancamiento/flags | `test_configuration_mismatch_blocks` | faltaban side, permisos requeridos y símbolo como contrato explícito |
| O2 | PARCIAL: Decimal + límites | tick/step/notional | `test_decimal_constraints` | faltaba normalización explícita y validación más defensiva |
| O3 | PARCIAL: estados y CANCEL_UNKNOWN | ciclo de vida mínimo | `test_cancel_unknown` | faltaban transiciones, fills y cantidades consistentes |
| E3 | PARCIAL | comparación REST/WS | `test_rest_websocket_conflict` | faltaban balances, INCOMPLETE/MATCH explícitos y snapshots multi-fuente |
| N1 | PARCIAL | errores, stale WS, retry | `test_stale_websocket` | faltaban clasificación HTTP, backoff, reconnect gate, secuencias y clock service |
| D3 | PARCIAL | candle cerrada + temporal | `test_incomplete_candle` | faltaban disponibilidad temporal y contrato de backtest |
| S1 | PARCIAL | health fail-closed | `test_resource_health_fail_closed` | no existe integración real de métricas del host ni persistencia física |
| LLM | NO ERA AUTORIDAD | no había autoridad determinista | no había test | se añadió parser advisory-only con fallback conservador |

## Implementación FASE 1.2

### O1
Se amplió `AccountConfiguration` y `StrategyAssumptions` para representar:
- position mode
- margin mode
- leverage
- supported order flags
- side
- account permissions
- symbol rules

La incompatibilidad produce `CONFIGURATION_MISMATCH` y no autoriza una entrada.

### O2
Se mantiene `Decimal` para precios/cantidades, se añadió normalización descendente y se conservan restricciones por símbolo/adaptador: tick, step, min/max quantity, min/max notional y precision.

### O3
`LifecycleOrder` ahora mantiene requested/filled/remaining, precio medio y estados explícitos. Una cancelación perdida permanece `CANCEL_UNKNOWN`; no se transforma en `CANCELED` por inferencia local.

### E3 / multi-bot
La reconciliación distingue:
- MATCH
- MISMATCH
- INCOMPLETE
- UNKNOWN

Los snapshots incluyen balances, posiciones, órdenes y fills. `OwnershipBook` permite account/instance/owner y rechaza ownership conflictivo.

### N1
Se añadieron clasificación de errores HTTP, retries acotados con backoff/jitter, detección de stale WS, secuencia monotónica, gate de reconexión que exige snapshot, y `ClockService` con tolerancia configurable.

### D3
Se añadió disponibilidad temporal explícita para candles y un contrato de completitud para modelos de backtest. No se inventa profundidad histórica.

### S1
Se conserva el principio fail-closed y se añadieron umbrales deterministas para clasificar ratios de recursos. La medición real del host, la base de datos y la rotación física de logs siguen pendientes de integración.

### Ejecución y LLM
`ExchangeAdapter` es una frontera abstracta sin implementación de órdenes reales. `ReadinessGate` exige evidencia determinista antes de READY. El LLM sólo puede aportar datos advisory; JSON inválido o ambiguo es rechazado.

## TEST-MURPHY-001

El nuevo test combina:
1. fill parcial;
2. WebSocket stale;
3. timeout REST;
4. clock drift;
5. cancelación pendiente;
6. respuesta de cancel perdida;
7. reinicio representado por nuevo guard;
8. divergencia local/remota;
9. idempotencia por client order id;
10. protección desconocida.

El comportamiento esperado es FROZEN/FAIL CLOSED hasta que la reconciliación y las demás evidencias sean confirmadas.

## Límites de verificación

**NO VERIFICADO:** ejecución local de pytest desde este entorno.

**PENDIENTE:** adapters REST/WS reales, medición física CPU/RAM/DISK/DB/network, persistencia transaccional real, backtester completo y cualquier ejecución con dinero real.

FASE 1.2 mantiene **ZERO REAL ORDERS**.
