# BOT OBRERO - FASE 1.3 - VALIDACION ADVERSARIAL

HEAD inicial: 6462a8148691a5bd9331c9bf2e3ae056272d9465

Auditoria previa:
- CRITICO: MurphyGuard.ready() era un setter publico.
- ALTO: RiskGuard no imponia ReadinessGate.
- ALTO: LifecycleOrder aceptaba cantidades inconsistentes.
- MEDIO: Order usaba float.
- MEDIO: retry de ORDER_RESULT_UNKNOWN no estaba prohibido por contrato.
- MEDIO: fills equivalentes dependian del orden para reconciliacion.

Correcciones:
- READY requiere evidencia determinista completa: reconciliacion, reloj, stream, recursos, permisos, configuracion y proteccion.
- RiskGuard usa ReadinessGate antes de autorizar una entrada.
- LifecycleOrder mantiene requested = filled + remaining.
- Order usa Decimal.
- UNKNOWN nunca habilita retry ciego.
- Fills se comparan como multiconjunto orden-independiente, sin eliminar duplicados.

Ataques cubiertos: A-R y las invariantes S dentro del nucleo disponible.
Integraciones reales no son testables porque no existe exchange adapter real.

Tests nuevos:
tests/test_phase13_adversarial.py
tests/test_murphy_001.py

Ejecucion:
NOT VERIFIED. No hay evidencia de pytest local ni de GitHub Actions para el SHA final.
Comandos:
python -m pip install -e ".[test]"
pytest -q

Gaps CORE:
adapter real, reconciliacion end-to-end, ownership/protection integrados y persistencia de restart.

Gaps INTEGRATION:
REST/WS, reconexion, metricas fisicas, persistencia transaccional, crash recovery y backtester completo.

Gaps VERIFICATION:
suite local y CI del SHA final no verificados; evidencia externa A/B inexistente.

ZERO REAL ORDERS. No se anadieron credenciales, exchange real ni dinero real.
