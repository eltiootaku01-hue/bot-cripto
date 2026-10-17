# GitHub Actions: fijación inmutable de dependencias

**Fecha:** 17 de octubre de 2026  
**Base:** `main` posterior a PR #82, `6728f9097bb6093c980e758ee68f5222d1443f9b`  
**PR #83:** fusionada en main mediante `a07b8aac15970f3349f9de54f021cfbeb71e4e20`.

## Hallazgo

Los ocho workflows inspeccionados fijaban las acciones oficiales de GitHub mediante etiquetas móviles de versión mayor (`@v7`). Eso facilita las actualizaciones, pero no fija exactamente el código que ejecuta el runner: la etiqueta puede moverse si el repositorio de la acción la actualiza o se ve comprometido.

La guía oficial de seguridad de GitHub declara que fijar una acción a un SHA completo de commit es la única forma de referenciar una release como inmutable. Fuente: https://docs.github.com/en/actions/reference/security/secure-use

## Cambio preparado

Se sustituyeron los selectores móviles por SHA de 40 caracteres verificados directamente en el repositorio oficial de cada acción y publicados como releases con firma/verificación de GitHub:

| Acción | Release verificada | SHA completo |
|---|---|---|
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | `v7.0.0` | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/upload-artifact` | `v7.0.2` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |

Fuentes de las releases oficiales:
- https://github.com/actions/checkout/releases/tag/v7.0.1
- https://github.com/actions/setup-python/releases/tag/v7.0.0
- https://github.com/actions/upload-artifact/releases/tag/v7.0.2

Los comentarios de cada referencia guardan el nombre legible de la versión y permiten auditarla sin volver a depender de la etiqueta durante la ejecución. Esto no deshabilita futuras actualizaciones: obliga a revisar la release y cambiar explícitamente el SHA con sus pruebas.

## Alcance y límites

- El cambio es de supply chain y reproducibilidad de CI; no cambia la lógica de trading, los indicadores ni las reglas de riesgo.
- El repositorio ya usa `pull_request`, `push` y/o `workflow_dispatch`; no se transforma ningún workflow en `pull_request_target`.
- El cambio no prueba rentabilidad, ni convierte en aceptada la captura prospectiva de 24 horas.
- CI de PR #83 pasó en el SHA exacto `689ce106e03fac0fe00955da24490cc53c1f72b1`: suite completa [39079618993](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079618993), backtest público [39079618963](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079618963), SMA [39079619294](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079619294) e indicadores [39079619010](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079619010), todos `success`.
- CI de main posterior al merge, en el SHA exacto `a07b8aac15970f3349f9de54f021cfbeb71e4e20`: [run 39079694856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079694856), `success`, 1598 pruebas aprobadas en 11.22 s.
- La puntuación permanece en 73/100 hasta que haya nueva evidencia operativa y económica; `NO_TRADE` permanece.

## Mantenimiento

Cuando una acción publique una actualización, comprobar la release y el commit en el repositorio oficial, leer las notas de cambios, revisar los permisos/diff pertinentes y actualizar la referencia pinneada mediante PR con CI. No volver a `@v7` por comodidad sin decisión explícita y documentada.


## Resultado verificado en main

La PR #83 quedó fusionada: https://github.com/eltiootaku01-hue/bot-cripto/pull/83. Main actual `a07b8aac15970f3349f9de54f021cfbeb71e4e20`; el CI exacto [39079694856](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39079694856) terminó `success`, con 1598 pruebas en 11.22 s. Los E2E de SMA, indicadores y backtest público también terminaron `success` en la punta de PR #83.