# BOT OBRERO — FASE 1.1

## Estado de la base
La inspección de `main` mostró que el repositorio inicial sólo contenía `.gitignore`. No existía código de FASE 1, tests ni catálogo de fallos que conservar.

Por tanto, F-001..F-014 quedan **MISSING / NOT VERIFIED** en este repositorio; no se afirma que estén implementados.

## Implementado en FASE 1.1
- F-015: resultado de orden ambiguo + freeze.
- F-016: conflicto REST/WebSocket.
- F-017: protección de posición no confirmada.
- F-018: separación cuenta/exposición de estrategia.
- F-019: política contextual de ejecución.
- F-020: recuperación ante estado compuesto desconocido.
- Evidencia temporal para look-ahead.
- Idempotencia por client_order_id.
- Health model con liveness separado de readiness.
- Clock skew check.
- Intrabar ambiguity guard.
- TEST-MURPHY-001.

Todos los F-015..F-020 son **evidence_level C**: escenarios de ingeniería. No se presentan como incidentes históricos.

## Fuera de alcance
No hay ejecución real contra exchanges, trading con dinero real, estrategia rentable, apalancamiento, retiros, backtester completo ni adaptadores REST/WebSocket reales.

## Verificación
La implementación fue escrita en GitHub. La ejecución local de pytest desde este entorno **NO está verificada todavía**; por ello no se afirma PASS hasta ejecutar la suite.


## FASE 1.2 — Estado

Se añadieron defensas iniciales para O1, O2, O3, E3, N1, D3 y S1:
- AccountConfiguration / StrategyAssumptions.
- Decimal OrderConstraints.
- LifecycleOrder con CANCEL_UNKNOWN.
- clasificación de fallos de infraestructura, watchdog de WebSocket y retry limitado.
- Candle cerrada / incomplete candle.
- límite explícito de profundidad de order book cuando el modelo está incompleto.
- ResourceHealth fail-closed.
- API permissions sin retiros.
- circuit reasons.
- shutdown, log rotation y storage safety policies.

Estas defensas son todavía componentes de núcleo/ingeniería, no integraciones reales con exchanges.

### Verificación

Los tests fueron escritos en GitHub. No se dispone de una ejecución de GitHub Actions verificable para los commits de esta fase mediante las herramientas disponibles, y el entorno local no puede clonar GitHub por restricción de red. Por ello **no se afirma PASS de la suite remota**.

### Limitaciones pendientes

- Reconciliación completa de balances/posiciones/open orders/fills con fuentes reales.
- Cancelación y submit reales.
- WebSocket real, reconexión y snapshot.
- Rate limiter con backoff/jitter ejecutable.
- Medición real de CPU/RAM/DISK/DB/network.
- Log handler real con rotación.
- Persistencia transaccional real.
- Backtester completo con spread, fees, funding, slippage, latency y partial fills.
- Evidencia externa A/B para los escenarios C del catálogo.
