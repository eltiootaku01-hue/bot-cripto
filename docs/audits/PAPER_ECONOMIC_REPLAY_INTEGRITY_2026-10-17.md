# Auditoría de integridad del replay económico PAPER

**Fecha:** 17 de octubre de 2026  
**Base inspeccionada:** `main` en `19b2d4d43b6b7b301f9b2f6e8dfa19889273575f`  
**PR #82:** fusionada en main mediante `6728f9097bb6093c980e758ee68f5222d1443f9b`.  
**CI post-merge de PR #82:** [run 39079511218](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079511218), `success`, 1598 pruebas en 11.85 s.  
**HEAD actual de main tras PR #83:** `a07b8aac15970f3349f9de54f021cfbeb71e4e20`; [CI exacto 39079694856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079694856), `success`, 1598 pruebas en 11.22 s.

## Dictamen

Se identificaron dos defectos de validación en el camino de replay económico local. La brecha principal permitía que `replay_paper_economics()` aceptara un campo de cotización modificado aunque el hash guardado siguiera teniendo la forma de SHA-256: el replay comprobaba el formato de `quote_evidence_hash`, pero no recalculaba el hash a partir de la evidencia completa ni cotejaba los campos de cotización persistidos con el contenido de la intención de orden que originalmente estaba hasheada. Además, `snapshot()` omitía `intent_json`, así que el replay no recibía esa evidencia original.

Esto no demuestra que los archivos SQLite de una ejecución real se hayan alterado. Es una brecha reproducible en el contrato de validación: un snapshot sintético con `quote_bid` cambiado de `99.9` a `98`, manteniendo ask/fill y un hash de aspecto válido, podía dar una falsa impresión de integridad si se llamaba al replay aislado.

Se detectó también una ambigüedad de tipos: en Python, `True == 1`. Para una cuenta con exactamente un fill, `fill_count=True` pasaba la comparación por igualdad aunque no fuera un entero reportado válido.

## Correcciones preparadas

1. `PaperExecutionAdapter.snapshot()` decodifica el `intent_json` durable y expone su payload como `intent_payload`; no publica el JSON bruto duplicado.
2. `replay_paper_economics()` recalcula SHA-256 de la intención canónica y la coteja con `intent_hash`.
3. El replay vuelve a validar la evidencia completa con `validate_paper_quote_evidence`, recomprueba su hash criptográfico y exige que símbolo, decisión, contexto, bid/ask, timestamps, fuente, identidad de observación y hash coincidan con los campos de fill persistidos.
4. El replay rechaza `fill_count` si no es un entero real distinto de `bool`, además de exigir igualdad con la versión y el número de fills.
5. Se agregan regresiones para la manipulación de `quote_bid` y `fill_count=True`.

La conciliación global PAPER ya valida el vínculo entre fill, intención, autorización de riesgo, reserva, binding y ledger. Esta corrección cubre una frontera distinta: el replay económico autónomo no debe devolver `REPLAYED` si no puede demostrar por sí mismo la integridad de lo que verifica.

## Relación con fallos públicos y documentación de mantenedores

- **Freqtrade — lookahead analysis:** explica que un backtest puede producir resultados falsamente favorables cuando una señal ve datos futuros y recomienda contrastar los indicadores/entradas/salidas base con ejecuciones aisladas. BOT-CRIPTO mantiene las pruebas de invariancia de sufijo futuro; no se afirma que este defecto de replay sea el mismo incidente. https://docs.freqtrade.io/en/2026.5/lookahead-analysis/
- **Binance Spot WebSocket Streams:** documenta rotación de conexión de 24 horas, `serverShutdown` y requisitos de ping/pong. La captura BOT-CRIPTO ya modela reconexión y fail-closed; el protocolo real de 24 h aún debe ejecutarse y validarse. https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
- **SQLite WAL-reset bug:** los mantenedores de SQLite describieron un escenario raro de corrupción en modo WAL con varias conexiones intentando escribir/checkpoint simultáneamente y lo corrigieron en SQLite 3.51.3. BOT-CRIPTO mantiene rollback journal `DELETE`; no hay evidencia de que sufriera ese incidente. https://www.sqlite.org/wal.html#walresetbug
- **GitHub Actions — seguridad de checkout:** GitHub documentó el riesgo de ejecutar código no confiable de PR en workflows privilegiados y recomienda evitar el patrón vulnerable. Los workflows auditados de este proyecto usan eventos no privilegiados para probar PRs. https://github.blog/changelog/2026-06-18-safer-pull_request_target-defaults-for-github-actions-checkout/

Estas referencias fundamentan patrones de prevención y recuperación; no deben citarse como prueba de que BOT-CRIPTO haya sufrido incidentes externos específicos.

## Gates que siguen bloqueados

- El score de preparación permanece **73/100** hasta reevaluar sobre el CI del SHA exacto y evidencias operativas nuevas. Esta corrección por sí sola no acredita operación sostenida ni rentabilidad.
- La captura real continua BTC/ETH de 24 h no se ha registrado como aceptada.
- La estrategia no tiene una ventaja neta robusta demostrada en un holdout prospectivo independiente.
- La decisión global se mantiene `NO_TRADE`; sin LIVE, claves privadas, endpoints privados ni dinero real.

## Verificación completada

- PR #82 fusionada: https://github.com/eltiootaku01-hue/bot-cripto/pull/82
- CI de la punta corregida: [39079462980](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079462980), `success`; SMA e indicadores E2E también pasaron en esa misma punta.
- CI post-merge del merge SHA #82: [39079511218](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079511218), `success`, 1598 pruebas en 11.85 s.
- CI del HEAD actual `main` tras integrar PR #83: [39079694856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079694856), `success`, 1598 pruebas en 11.22 s.
- La nueva prueba de replay rechaza el cambio de bid inconsistente con la intención hasheada; otras dos regresiones rechazan `fill_count=True` en replay y en la proyección de la cuenta canónica.
- La puntuación permanece 73/100: el fix mejora la verificación económica de PAPER, pero la atomicidad global entre bases SQLite separadas, la captura prospectiva real de 24 h y el edge independiente continúan fuera de esta corrección.
