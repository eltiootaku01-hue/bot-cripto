# BOT OBRERO - FASE 1.3.1

## Estado inicial
HEAD: d56280c1b20f98cdcfb20c24627e8683bc79eecc
Commit anterior: test: adversarial defensive core validation

## Problemas encontrados

### PACKAGING / CI
GitHub Actions run 36801367643 sobre el SHA inicial falló en el paso de instalación editable.
El log real mostró:
Multiple top-level packages discovered in a flat-layout: ['research', 'bot_obrero'].
El job fue failure; el paso pytest fue skipped.

### LIFECYCLE
Se verificó que apply_fill() podía mutar un estado terminal y que request_cancel()/cancel_unknown() no tenían una máquina de estados explícita.
También se mantuvo la exigencia de requested_qty = filled_qty + remaining_qty.

### READINESS CONTRACT
ReadinessInputs representa evidencia recibida por contrato; no demuestra por sí mismo que PositionProtection, Reconciliation, ClockService, ResourceHealth, ApiPermissions o AccountConfiguration hayan producido esa evidencia en una integración real.

## Correcciones
- Configuración explícita de setuptools para incluir sólo bot_obrero y excluir research/tests/docs.
- Máquina de estados explícita para submit, acknowledge, fill, cancel, reject y expire.
- Estados terminales rechazan mutaciones posteriores.
- TEST-MURPHY-001 compuesto restaurado y ampliado sin sustituirlo por una prueba de readiness.
- Test de metadata de distribución agregado.

## Tests añadidos
- tests/test_lifecycle_transitions.py
- tests/test_packaging.py
- restauración/ampliación de tests/test_murphy_001.py

## Evidencia previa a cierre CI
Workflow anterior:
tests
run_id=36801367643
SHA=d56280c1b20f98cdcfb20c24627e8683bc79eecc
job=pytest
install=FAIL
pytest=SKIPPED
conclusion=failure

La evidencia del nuevo SHA se añadirá sólo después de observar el run real.
