# BOT-CRIPTO — Revisión viva de gates, seguridad y evidencia

**Fecha:** 2026-10-18  
**Rama/HEAD observado:** main @ c3ef40918d0efd89dc01454ca7903b371fc4c9e8  
**Resultado general:** TECHNICAL CI GREEN / REAL CAPTURE BLOCKED / NO_TRADE

## 1. Evidencia observada por SHA

- Tests: [workflow run 39163429797](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39163429797) — success, 1.710 pruebas en cada Python 3.11, 3.12, 3.13 y 3.14.
- Dependencias: [pip-audit 39163429816](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39163429816) — success.
- Código: [CodeQL 39163429790](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39163429790) — success.
- Issue P0: [#98 — captura real Kline + bookTicker durante 24 h](https://github.com/eltiootaku01-hue/bot-cripto/issues/98), sigue open.
- Acción de gobernanza: [#109 — proteger main con PR y checks obligatorios](https://github.com/eltiootaku01-hue/bot-cripto/issues/109), open.

Los checks de ese SHA demuestran que CI pasó para esa instantánea. No validan automáticamente commits futuros y no demuestran edge financiero ni datos capturados durante 24 horas.

## 2. Hallazgo de gobernanza: main no protegida

**OBSERVED:** la [API de ramas de GitHub](https://api.github.com/repos/eltiootaku01-hue/bot-cripto/branches/main) indica protección desactivada, sin contextos de checks requeridos. La lista de [rulesets](https://api.github.com/repos/eltiootaku01-hue/bot-cripto/rulesets) está vacía. La documentación oficial permite exigir PR, checks, historial lineal y bloquear borrado/force-push ([protección de ramas](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)).

**Impacto:** no existe una barrera de repositorio que impida incorporar un cambio sin pasar los controles. Los workflows actuales están verdes, pero no hay una regla que bloquee merge cuando los checks fallan.

**Remediación recomendada:** en Settings → Branches, crear una regla para main que requiera PR; seleccionar los contextos reales de la matriz de pytest Python 3.11–3.14, CodeQL y pip-audit; bloquear force-push y borrado. Verificar la regla desde la API después de guardarla. No adivinar contextos de checks. Si solo hay un mantenedor, decidir conscientemente el requisito de aprobación para no bloquear merges.

**BLOCKED:** la sesión de navegador inspeccionada no estaba autenticada en GitHub y no mostró controles de administrador; no se cambió ninguna configuración. Registro [#109](https://github.com/eltiootaku01-hue/bot-cripto/issues/109).

## 3. Gate P0 de mercado: captura real ausente

**OBSERVED:** la consulta del árbol público de main no encontró evidence/prospective-btc-eth-pair. La [issue #98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) sigue abierta porque no hay dos manifiestos reales VALID ni dataset original aceptado.

**No se puede completar desde esta inspección remota:** el runner requiere una máquina persistente con checkout limpio de main, red estable, suspensión desactivada y duración superior a 24 h. GitHub Actions limita un job hospedado a seis horas ([límites](https://docs.github.com/en/actions/reference/limits)). No fabriqué manifiestos ni reutilicé fixtures sintéticos.

El siguiente procedimiento debe ejecutarse en PowerShell en el PC, desde un clon existente de main:

    git fetch origin main
    git switch main
    git pull --ff-only origin main
    .\.venv\Scripts\python.exe -m pip install -e .
    git status --short
    .\.venv\Scripts\python.exe -m bot_obrero.prospective_capture_pair capture --output-dir evidence/prospective-btc-eth-pair

Primero dejar que el runner haga los gates de procedencia. Si termina BLOCKED, conservar logs/artefactos, no editar manifiestos. Si genera VALID, empaquetar y después ejecutar el verificador de recepción con el sidecar SHA-256. Conservar los ficheros originales intactos.

## 4. Casos públicos de fallos y mitigaciones

- **Binance WebSocket:** la documentación oficial establece que cada conexión vive como máximo 24 h, envía ping cada 20 s y puede emitir serverShutdown; la captura necesita rotación/reconexión y registrar huecos en vez de tratarlos como cobertura continua ([docs](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)).
- **SQLite WAL-reset:** SQLite informa de un caso raro de corrupción en WAL con varias conexiones y escrituras/checkpoint concurrentes, corregido en 3.51.3. BOT-CRIPTO usa journaling DELETE en la ruta PAPER tras el hardening anterior. No hay evidencia de corrupción local ([fuente](https://www.sqlite.org/wal.html)).
- **Lookahead bias:** Freqtrade detalla que un backtest puede usar futuras velas y que su análisis solo verifica señales efectivamente disparadas; puede dejar señales sin comprobar ([docs](https://www.freqtrade.io/en/stable/lookahead-analysis/)). Hay pruebas de invariancia a sufijo futuro, pero sigue pendiente validar como conjunto todos los artefactos del estudio anual.
- **Acciones CI:** GitHub publicó workflow dependency lockfiles en vista previa el 14-oct-2026 para fijar acciones y dependencias transitivas. Se mantiene el uso de SHA completos para acciones first-party; evaluar el lockfile solo con la herramienta oficial mientras siga en preview ([changelog](https://github.blog/changelog/2026-10-14-github-actions-workflow-lockfiles-in-public-preview/)).
- **Privilegios CI:** los workflows de pruebas/E2E relevantes usan permissions: contents: read. GitHub ha publicado protecciones de ejecución para controlar actores y eventos, pero no se afirma que el ajuste de cuenta/repositorio esté activado aquí ([changelog](https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/)).

Los incidentes upstream explican clases de riesgo; no prueban que BOT-CRIPTO sufriera esos incidentes, salvo donde se indica una regresión local.

## 5. Criterio de aceptación que no se relaja

- Preparación ponderada: **77/100**; evidencia económica: **5/20**.
- BTC_ETH_STRATEGIES_V2: **DESIGNED / NOT RUN**; sin T0.
- No se ha ejecutado ni evaluado el holdout prospectivo independiente de 365 días.
- Los resultados históricos existentes no prueban una ventaja neta robusta.
- Decisión: **NO_TRADE**. Sin LIVE, dinero real, API keys ni endpoints privados.
- No afirmar 100 % ni declarar una táctica ganadora antes de aceptar la captura real, fijar T0 con protocolo preregistrado y superar la evaluación independiente con los mismos costes que cash y buy-and-hold, integridad temporal y controles anti-lookahead.

## 6. Próximas acciones en orden

1. El propietario activa la protección de main y verifica los contextos de checks en una PR real — [#109](https://github.com/eltiootaku01-hue/bot-cripto/issues/109).
2. Ejecutar la captura real desde el PC persistente — [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98).
3. Verificar ZIP, SHA-256 sidecar y validación interna; aceptar únicamente VALID.
4. Solo después, preregistrar el estudio y fijar T0.
5. Completar el holdout prospectivo de 365 días y reportar el resultado incluso si pierde.
