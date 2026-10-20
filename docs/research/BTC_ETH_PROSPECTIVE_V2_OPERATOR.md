# BOT-CRIPTO — operación del experimento prospectivo BTC/ETH V2

## Estado y objetivo

El ejecutor V2 está implementado en el repositorio, pero no ha demostrado ventaja económica. La decisión sigue siendo **NO_TRADE**. Este proceso no envía órdenes: captura datos públicos de Binance Spot y simula las siete reglas preregistradas contra cash, buy-and-hold de BTC y buy-and-hold de ETH.

**No se puede declarar ganador hoy.** Hace falta una captura real válida, un preregistro comprometido antes de T0 y 365 días futuros completos de datos punto-en-tiempo. Una interrupción, hueco, quote ausente/no fresco, hash distinto o fallo de procedencia debe producir **INCOMPLETE / NO_TRADE**, sin rellenar datos retroactivamente.

## Requisitos operativos

- Windows con Git y Python 3.11 o superior.
- Un clon de main instalado en editable; para los comandos de captura, el directorio actual debe seguir siendo la raíz del clon.
- Máquina persistente, Internet estable, suspensión/hibernación deshabilitadas y capacidad de almacenamiento suficiente para una captura continua de Kline + bookTicker durante más de un año. El registro de bookTicker puede ocupar muchos GB. Mantén una copia de seguridad independiente del directorio de evidencia.
- No ejecutes git pull, cambies de rama, edites los archivos de código fuente ni uses el mismo clon para desarrollar durante la captura. El ejecutor tolera que el remoto main avance, pero exige que el HEAD local y los archivos de código congelados no cambien.
- Nunca subas las claves privadas, y no guardes el archivo anual bruto en un commit de código. No se necesita ninguna API key para estos endpoints públicos.

Instala un clon limpio siguiendo el README y confirma que .venv\Scripts\bot-cripto.exe status informa NO_TRADE e IMPLEMENTED / NOT RUN.

## Paso 1 — captura de humo real de 24 horas

Usa el lanzador de Windows tools/windows-handoff/INICIAR_CAPTURA_PROSPECTIVA.ps1 desde un clon limpio de main. Debes escribir la confirmación CAPTURAR. El lanzador espera su ventana de inicio UTC, captura Kline y bookTicker públicos en paralelo, valida las dos corridas y el solapamiento, crea un ZIP y un archivo .sha256, y vuelve a verificar el ZIP.

Acepta el humo únicamente si el resultado es PROSPECTIVE_CAPTURE_STATUS=VALID y la verificación del bundle también da VALID. Conserva el ZIP y el sidecar .sha256, el hash SHA-256 completo, el directorio original con manifests y logs y el SHA de código al que quedó vinculada la captura.

La prueba de humo certifica integridad de adquisición durante esa ventana. **No** mide rentabilidad ni demuestra que una estrategia gane.

## Paso 2 — elegir T0 y congelar los insumos

Escoge un T0 UTC en una frontera exacta de hora, suficientemente futuro para completar el preregistro y fusionarlo en main antes de T0. No selecciones T0 a partir del comportamiento del precio. El formato esperado es AAAA-MM-DDTHH:00:00Z; usa una fecha futura real al ejecutar el procedimiento.

Desde PowerShell, en la raíz del clon:

~~~~powershell
$Bot = ".\.venv\Scripts\bot-cripto.exe"
$T0 = "AAAA-MM-DDTHH:00:00Z" # reemplazar por la fecha/hora UTC real
$Evidence = "evidence\prospective-v2"
$SmokeZip = "C:\ruta\a\la-captura-de-humo-evidence.zip"
New-Item -ItemType Directory -Path $Evidence -Force | Out-Null
& $Bot prospective-v2 capture-filters --output "$Evidence\filters.json"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED: no se capturó el snapshot de filtros" }
& $Bot prospective-v2 capture-warmup --t0 $T0 --output "$Evidence\warmup.json"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED: no se congeló el warm-up" }
~~~~

El snapshot de filtros contiene la respuesta pública de exchangeInfo para validar cantidad mínima, step size, cantidad máxima y notional. El warm-up utiliza velas cerradas de los 90 días anteriores únicamente para inicializar indicadores; esos 90 días están excluidos de toda métrica económica. El período V2 no se puntúa hasta T0.

Comprueba que filters.json y warmup.json están fuera de main como evidencia local y que no se han modificado después de registrarlos. El preregistro conserva sus SHA-256; cambiar un byte bloquea la evaluación.

## Paso 3 — generar y comprometer el preregistro

Crea un nombre único para el archivo de registro. El comando no sobrescribe un archivo existente:

~~~~powershell
$Tag = $T0.Replace("-", "").Replace(":", "")
$Registration = "docs\research\BTC_ETH_PROSPECTIVE_V2_PREREGISTRATION_$Tag.json"
& $Bot prospective-v2 preregister --t0 $T0 --filters-json "$Evidence\filters.json" --warmup-json "$Evidence\warmup.json" --smoke-archive $SmokeZip --output $Registration
if ($LASTEXITCODE -ne 0) { throw "BLOCKED: no se creó un preregistro válido" }
~~~~

El preregistro contiene T0/T1, hashes de filtros/warm-up/humo/código, las siete estrategias, tres escenarios de costes, bootstrap de 10.000 réplicas en bloques diarios de siete días, semilla fija, corrección de Holm y el criterio de avance. No edites ese JSON manualmente.

Integra el archivo con el flujo normal de GitHub (rama, commit, PR y checks verdes). No escribas directamente sobre main ni omitas los controles de protección:

~~~~powershell
git switch -c "research/v2-preregistration-$Tag"
git add $Registration
git commit -m "research: preregister BTC-ETH prospective V2"
git push -u origin "research/v2-preregistration-$Tag"
~~~~

Abre una PR, revisa la diff y espera los checks requeridos. Después de la fusión, vuelve a main y actualiza el clon con fast-forward. Usa exactamente el archivo JSON integrado y verifica que el commit que lo contiene es anterior a T0. No cambies reglas, costes, indicadores o la familia de estrategias después del registro. Si T0 vence antes de completar la fusión, no inicies la corrida: genera un preregistro nuevo con otro T0 futuro y deja documentado el anterior.

## Paso 4 — iniciar la captura continua antes de T0

El humo aceptado y el preregistro comprometido son requisitos obligatorios. En el clon limpio ya actualizado a main, empieza antes de T0:

~~~~powershell
& $Bot prospective-v2 capture-period --t0 $T0 --filters-json "$Evidence\filters.json" --warmup-json "$Evidence\warmup.json" --preregistration-json $Registration --smoke-archive $SmokeZip --output-dir "$Evidence\year-capture"
~~~~

El proceso inicia ambos colectores ahora (sin esperar a medianoche), registra el contexto del preregistro en el hash del manifiesto y continúa hasta cubrir el final exclusivo T1 = T0 + 365 días, con margen de captura para obtener el quote de liquidación. El comando **no muestra resultados acumulados ni rankings durante el período**. Debe terminar con status VALID y código de salida 0.

Mantén la máquina, la sesión y el almacenamiento disponibles. La política exige que no haya huecos sobre los límites de integridad acordados; reconexiones o interrupciones que incumplan los límites bloquean la evidencia. No edites manifests, timestamps, archivos JSONL ni hashes. No uses backfill posterior para aparentar que una cotización estuvo disponible en el momento de una decisión.

El directorio anual puede exceder los límites de un artifact ZIP genérico. Consérvalo en almacenamiento persistente y respaldo independiente; no intentes forzarlo dentro de un único bundle limitado a 4 GiB ni lo comitees en main.

## Paso 5 — ejecutar el replay solo después de T1

Ejecuta la evaluación desde el mismo clon/fuente congelada que generó la captura, con el preregistro exacto comprometido en su SHA de origen. Si el remoto main avanzó durante el año, no cambies este clon a otro commit: el manifest documenta el SHA original. Conserva ese clon o vuelve a hacer checkout del SHA de origen.

~~~~powershell
$Result = "$Evidence\results-$(Get-Date -Format yyyyMMdd-HHmmss)"
& $Bot prospective-v2 replay --capture-root "$Evidence\year-capture" --filters-json "$Evidence\filters.json" --warmup-json "$Evidence\warmup.json" --preregistration-json $Registration --smoke-archive $SmokeZip --t0 $T0 --output-dir $Result --initial-usdt "31"
~~~~

Se comprueban los manifests Kline/bookTicker, hashes de registros y archivos, solapamiento, SHA de origen, preregistro, continuidad horaria en ambos activos y presencia de quotes frescos (máximo dos segundos) en todas las marcas y fills requeridos. Un quote faltante o fuera de ventana no se reemplaza con OHLC. El sistema calcula con Decimal, aplica lot size y notional Spot congelados, comisiones y slippage sobre bid/ask observados, liquida la cartera al final y compara siete reglas contra cash y ambos benchmarks de buy-and-hold.

Salidas esperadas:
- prospective-v2-summary.json: todos los escenarios, resultados, gates estadísticos y dictamen;
- prospective-v2-trades.csv: ledger de fills con quote, timestamp, fee, slippage y secuencia;
- prospective-v2-manifest.json: hashes SHA-256 de las salidas y vínculos a evidencia.

Un resultado puede quedar NO_ROBUST_EDGE_FOUND o INCOMPLETE. Incluso si aparece CANDIDATE_FOR_SECOND_INDEPENDENT_REVIEW, eso solo autoriza una segunda revisión independiente: la decisión operativa permanece NO_TRADE. No se activa PAPER automático ni LIVE.

## Diagnóstico de fallos

Conserva todo archivo, manifest y log de una corrida bloqueada y registra el motivo exacto. Nunca edites el output para conseguir VALID. Si falta una hora, un quote fresco, el bundle de humo, el snapshot de filtros, el warm-up, el preregistro o un hash, no emitas una conclusión de rentabilidad. Corrige la causa operativa; si T0 o la integridad del intervalo se perdió, crea una nueva versión preregistrada con un T0 futuro en lugar de reparar la corrida anterior.

**Estado inicial persistente:** IMPLEMENTED / NOT RUN; decisión económica: NO_TRADE.
