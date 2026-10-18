# Automatización de auditoría estática y dependencias

**Fecha:** 17 de octubre de 2026  
**Base inspeccionada:** main con PR #86 fusionada  
**Rama de implementación:** `security/automated-codeql-dependency-audit-2026-10-17`  
**Estado al abrir la PR:** los checks de este workflow deben pasar en el SHA exacto antes del merge; no se presume que ya hayan pasado.

## Brecha

El repositorio contaba con regresiones, pruebas de backtesting y acciones de GitHub fijadas por SHA, pero no se había verificado un workflow propio de análisis CodeQL ni una auditoría recurrente de vulnerabilidades conocidas de las dependencias instaladas. Esto dejaba dos clases de defectos dependientes de revisiones ad hoc.

## Hallazgo accionable descubierto durante la primera auditoría

La primera ejecución real del workflow `pip-audit` detectó `PYSEC-2026-1845` en `pytest 8.4.2`. El aviso del ecosistema marca como afectadas las versiones anteriores a `9.0.3`; la release `pytest 9.0.3` documenta que corrige el uso inseguro del directorio temporal (`CVE-2025-71176`).

- Aviso: https://github.com/advisories/GHSA-6w46-j5rx-g56g
- Changelog de `pytest 9.0.3`: https://github.com/pytest-dev/pytest/releases/tag/9.0.3
- Registro OSV/PyPA: https://osv.dev/vulnerability/PYSEC-2026-1845

**Corrección preparada en esta PR:** la restricción de test pasa de `pytest>=8,<9` a `pytest>=9.0.3,<9.1`. La restricción mantiene la versión por debajo de la rama 9.1 mientras se valida la suite completa, pero permite recibir eventuales parches 9.0.x. Esta decisión se acepta solo si el CI completo pasa y el nuevo `pip-audit` no vuelve a reportar el aviso.

## Controles añadidos

1. **CodeQL:** análisis de Python y archivos de GitHub Actions con la suite `security-extended`, en pull requests a `main`, push a `main`, ejecución semanal y ejecución manual. La acción se fija al commit oficial `2892aa5e19bbd11bc0cff5427e3b750a04d9e3c2` de la release v4.38.2; checkout también usa un SHA completo.
2. **pip-audit:** instala el proyecto y sus dependencias de prueba, con el auditor fijado a `pip-audit==2.10.1`, y falla si encuentra vulnerabilidades conocidas en el entorno instalado. Se ejecuta en push/PR, semanalmente y bajo demanda.
3. **Dependabot:** revisiones semanales de versiones de dependencias Python y GitHub Actions; las actualizaciones se proponen en PR en lugar de introducirse silenciosamente.

## Fuentes oficiales contrastadas

- GitHub: code scanning/CodeQL analiza vulnerabilidades y errores y muestra alertas cuando el servicio está habilitado: https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-code-scanning
- GitHub: el SHA completo es el método recomendado para fijar una acción como release inmutable: https://docs.github.com/en/actions/reference/security/secure-use
- PyPA: `pip-audit` examina entornos Python frente a vulnerabilidades conocidas; su código de salida es 1 cuando encuentra al menos una: https://github.com/pypa/pip-audit
- PyPA publicó `pip-audit 2.10.1` para corregir un crash al procesar una entrada de vulnerabilidad OSV que omitía un campo opcional; el auditor está fijado a esa release para que un fallo del escáner no interrumpa silenciosamente la detección: https://github.com/pypa/pip-audit/releases/tag/v2.10.1
- CodeQL Action v4.38.2: https://github.com/github/codeql-action/releases/tag/v4.38.2

## Límites y política de mantenimiento

- El análisis estático detecta clases de vulnerabilidad cubiertas por sus reglas, no todos los defectos.
- `pip-audit` solo conoce las vulnerabilidades publicadas en las fuentes consultadas; no demuestra que una dependencia sea segura ni sustituye una lockfile/hash de reproducibilidad.
- Los findings de seguridad deben triagearse; no suprimir una alerta solo para que CI pase.
- Mantener Actions pinneadas por SHA. Dependabot puede proponer otro SHA/versión; la actualización requiere revisar upstream, la release, el diff y todos los checks pertinentes.
- Nada de esto prueba una ventaja económica, no habilita LIVE y no cambia `NO_TRADE`.
