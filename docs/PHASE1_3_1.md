# BOT OBRERO - FASE 1.3.1 - CIERRE DE CI + LIFECYCLE

## Estado inicial

- HEAD inicial: d56280c1b20f98cdcfb20c24627e8683bc79eecc
- Branch: main
- Commit anterior: test: adversarial defensive core validation

## Problemas encontrados

### PACKAGING / CI

Run previo verificado:
- workflow: tests
- run_id: 36801367643
- SHA: d56280c1b20f98cdcfb20c24627e8683bc79eecc
- job: pytest
- install: FAIL
- pytest: SKIPPED
- conclusion: failure

Causa exacta:
setuptools detectó múltiples paquetes top-level en flat-layout:
research y bot_obrero.

### LIFECYCLE

La auditoría encontró que apply_fill(), request_cancel() y cancel_unknown() no imponían una máquina de estados explícita. Un estado terminal podía intentar mutarse mediante una operación posterior.

También se mantuvo y reforzó:
requested_qty = filled_qty + remaining_qty

### READINESS CONTRACT

ReadinessInputs es un contrato de evidencia. Que un consumidor entregue protection_safe=True, reconciliation_match=True, clock_valid=True, stream_ready=True, resources_healthy=True, permissions_safe=True y configuration_valid=True no demuestra por sí solo que una integración real haya producido esos hechos.

ReadinessGate y MurphyGuard sólo verifican el contrato determinista recibido.

## Correcciones

### Packaging

Se configuró setuptools de forma explícita:
- inclusión: bot_obrero*
- exclusión: research*, tests*, docs*

No se movió el proyecto a src/ y no se modificó el nombre del paquete.

### Lifecycle

Se añadió una máquina de estados explícita con:
- submit(): NEW -> PENDING_SUBMIT
- acknowledge(): PENDING_SUBMIT -> ACKNOWLEDGED
- compatibilidad controlada: NEW -> ACKNOWLEDGED para eventos de recuperación/compatibilidad existentes
- apply_fill(): ACKNOWLEDGED/PARTIALLY_FILLED/PENDING_CANCEL/CANCEL_UNKNOWN -> PARTIALLY_FILLED/FILLED
- request_cancel(): ACKNOWLEDGED/PARTIALLY_FILLED -> PENDING_CANCEL; repetir sobre PENDING_CANCEL es idempotente
- cancel_unknown(): PENDING_CANCEL -> CANCEL_UNKNOWN
- confirm_cancel(): PENDING_CANCEL/CANCEL_UNKNOWN -> CANCELED
- reject(): NEW/PENDING_SUBMIT -> REJECTED
- expire(): ACKNOWLEDGED/PARTIALLY_FILLED/PENDING_CANCEL/CANCEL_UNKNOWN -> EXPIRED

Los estados FILLED, CANCELED, REJECTED y EXPIRED rechazan fill/cancel/cancel_unknown posteriores sin mutación.

Los estados matemáticamente imposibles son rechazados.

### Murphy

TEST-MURPHY-001 fue restaurado como prueba compuesta; no fue sustituido por tests de readiness.

La recuperación sigue requiriendo evidencia antes de READY.

## Tests añadidos o ampliados

- tests/test_lifecycle_transitions.py
- tests/test_packaging.py
- tests/test_murphy_001.py
- tests/test_phase13_adversarial.py
- tests/test_phase11.py: expectativas actualizadas a Decimal después de la migración intencional del Order legacy

## Verificación local

No existe checkout local accesible en este entorno. La resolución de github.com desde el entorno local falló con:
fatal: unable to access 'https://github.com/eltiootaku01-hue/bot-cripto.git/': Could not resolve host: github.com

Por tanto:
- instalación local del repositorio: NOT EXECUTABLE en este entorno
- pytest local del repositorio: NOT EXECUTABLE en este entorno

La evidencia ejecutable de la fase se obtuvo en GitHub Actions.

## GitHub Actions - código corregido

- workflow: tests
- run_id: 36802393850
- SHA: ca2130eac49aa8275bd3c29a8aa22a73c3f61a98
- job: pytest
- install: EXECUTED AND PASS
- pytest: EXECUTED AND PASS
- conclusion: success

Este run corresponde al commit que corrigió las regresiones de Decimal de los tests antiguos y el caso NEW -> apply_fill del test adversarial.

## GitHub Actions - commit documental

- workflow: tests
- run_id: 36802452730
- SHA: 8d131c20fa556ebfac651a2c6aadcdf5a3eafd41
- job: pytest
- install: EXECUTED AND PASS
- pytest: EXECUTED AND PASS
- resultado: 66 passed in 0.11s
- conclusion: success

Este commit es documental sobre la base de código que ya había pasado el run de validación anterior.

## Murphy 001

EXECUTED AND PASS como parte de pytest -q en el run de validación exitoso.

El escenario mantiene:
PARTIAL FILL -> WS STALE -> REST TIMEOUT -> CLOCK INVALID -> PENDING_CANCEL -> CANCEL UNKNOWN -> RESTART -> STATE MISMATCH -> DUPLICATE ORDER ATTEMPT -> PROTECTION UNKNOWN

y no permite READY sin evidencia completa.

## FAIL CLOSED

Comprobado por tests:
- cada evidencia individual falsa produce FAIL_CLOSED
- READY sin evidencia lanza error y mantiene FROZEN
- RiskGuard no puede saltar ReadinessGate
- restart inicia FROZEN
- ORDER_RESULT_UNKNOWN no habilita retry ciego
- protección por defecto desconocida no habilita readiness

## Gaps

### CORE
- persistencia crash-safe del ledger
- recovery end-to-end todavía modelado en núcleo
- reconciliación, ownership y protection siguen sin un orquestador real único

### INTEGRATION
- REST/WebSocket reales
- reconexión y snapshot real
- persistencia transaccional real
- medición física de recursos
- backtester completo
- exchange adapter real

### VERIFICATION
- no hay ejecución local por restricción de red
- existe evidencia CI real en GitHub Actions
- no existe evidencia externa A/B; los escenarios siguen siendo engineering scenarios

## Trading real

ZERO REAL ORDERS
ZERO REAL MONEY
ZERO REAL EXCHANGE
ZERO REAL CREDENTIALS
