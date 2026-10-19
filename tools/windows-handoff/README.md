# Kit de extracción Windows — BOT-CRIPTO

Este script descarga el ZIP oficial de un commit fijado, valida que contenga pyproject.toml y cerebro/PROJECT_STATE.md, extrae el código y deja el ZIP original más un manifiesto SHA-256 para análisis local. No instala dependencias ni ejecuta el bot.

## Ejecutar

Desde Windows PowerShell 5.1 o PowerShell 7:

    .\BAJAR_Y_EXTRAER_BOTCRIPTO.ps1

Destino por defecto: Desktop\BOT-CRIPTO-LOCAL. Para elegir otro:

    .\BAJAR_Y_EXTRAER_BOTCRIPTO.ps1 -Destination 'C:\BOT-CRIPTO-LOCAL'

También se puede abrir EJECUTAR_DESCARGA.cmd.

## Snapshot fijado

- Repo: https://github.com/eltiootaku01-hue/bot-cripto
- SHA: 439d0b6cab598db683462ed891f05093b30d6575
- URL: https://github.com/eltiootaku01-hue/bot-cripto/archive/439d0b6cab598db683462ed891f05093b30d6575.zip

## Seguridad

Solo descargar y extraer. No ejecutar el bot ni pruebas; no hay claves, endpoints privados, órdenes reales ni dinero real. Mantener NO_TRADE.

La CI de aplicación disponible corresponde a GitHub Actions sobre Linux. El workflow windows-handoff-smoke.yml comprueba específicamente la descarga/extracción del snapshot sobre windows-latest; eso no equivale a pasar toda la suite del bot en Windows.
