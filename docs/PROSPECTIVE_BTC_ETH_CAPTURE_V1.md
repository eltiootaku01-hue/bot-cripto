# BTC/ETH prospective public-data capture V1

**Implementation status:** capture code and regression tests are merged to `main`; strict JSON schema/sequence validation was hardened by PR #93. A successful real 24-hour capture has **not** yet been recorded or independently accepted.  
**Target dataset:** Binance Spot, public WebSocket Kline streams, `BTCUSDT` and `ETHUSDT`, 1-hour candles.  
**Safety boundary:** market data only; no API keys, private endpoints, orders, real money, or LIVE execution.

## Why this component exists

The existing `BinanceSpotWebSocketAdapter.listen` method is a bounded acquisition boundary. It was intentionally written without a durable streaming loop, reconnection policy, persistence, or 24-hour lifecycle manager. Binance's official Spot WebSocket documentation says one connection is valid for only 24 hours, describes the `serverShutdown` event, documents ping/pong requirements, and limits connection attempts and incoming control messages. A long-running capture must therefore record events incrementally and treat connection rotation, disconnects, missing candles, and persistence errors as evidence—not as success.

Sources rechecked on 2026-10-18:
- Binance Spot market-data streams: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
- CCXT manual, exception handling and rate limits: https://docs.ccxt.com/es/docs/manual
- Freqtrade lookahead analysis: https://docs.freqtrade.io/en/2026.5/lookahead-analysis/

These external reports establish known failure patterns; they do not establish that the same incident occurred in BOT-CRIPTO.

## Run on a persistent machine

### First-time setup on Windows (PowerShell)

The repository has no root-level README, so use these explicit setup steps before the first capture. Install Git for Windows and Python 3.11 or newer first. From the parent folder where you keep projects:

```powershell
git clone https://github.com/eltiootaku01-hue/bot-cripto.git
Set-Location bot-cripto
git fetch origin main
git switch main
git pull --ff-only origin main
py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e .
```

For later attempts, reuse the same checkout and environment; refresh `main` first, then update the editable install:

```powershell
git fetch origin main
git switch main
git pull --ff-only origin main
& .\.venv\Scripts\python.exe -m pip install -e .
git status --short
```

If Git refuses to switch branches or pull because of local changes, stop and inspect them instead of using `reset --hard` or deleting files. Before capture, the source gate must report a clean `bot_obrero/` and `pyproject.toml`. The checkout must stay on `main`, and the output datasets should not be committed to the source tree.

This command is designed for a persistent terminal such as a local machine or a configured Codespace. A GitHub-hosted CI run only tests the code; it is not evidence that 24 hours of market capture occurred.

From the repository root, the **recommended one-command runner** is:

```powershell
python -m bot_obrero.prospective_capture_pair capture --output-dir evidence/prospective-btc-eth-pair
```

Before starting any network capture, the runner verifies Git source provenance: it must run from the repository root on branch main; local HEAD must match the fetched refs/remotes/origin/main; and bot_obrero/ plus pyproject.toml must have no modified or untracked files. It records the source SHA, origin/main SHA, branch and source-status result in the configuration whose SHA-256 is stored in the pair manifest. Unknown Git state, a stale checkout, a non-main branch or modified capture source blocks before the collectors connect. Refresh the remote-tracking ref first with: git fetch origin main; then switch to main and update fast-forward only before running.

The paired runner waits until the next UTC midnight (unless started within five seconds of midnight), then launches Kline and bookTicker together. Because the processes start sequentially, it gives Kline a target of 86,460 seconds (24 hours plus a 60-second overlap guard) and bookTicker a target of 86,400 seconds; both have a 300-second grace. The 60-second guard exceeds the runner's maximum allowed UTC start lateness and prevents normal startup skew from making an otherwise complete 24-hour capture fail the strict overlap check. It watches both child processes, stops the remaining collector if either fails, independently validates both manifests and the common overlap, and writes a pair-manifest.json containing the result, configuration hash, source provenance, dataset hashes and log paths. Child manifest paths in the validation summary are relative to the pair run directory; the provenance record does not save the computer absolute working-directory path. The JSONL datasets and per-collector manifests remain in the same run directory. A successful run exits with code 0 and reports status VALID; a failed/interrupted run returns nonzero and must not be used for preregistration.

The runner expects the current working directory to be the repository root. Keep the PC powered on, prevent sleep/hibernation for the duration, maintain network connectivity, and leave the terminal running. For the paired runner, the Kline target is 86,460 seconds plus 300 seconds of grace; bookTicker targets 86,400 seconds plus 300 seconds of grace. The pair validator still requires at least 86,400 seconds of actual overlap. Do not use synthetic CI artifacts as market evidence, and do not edit a blocked/running manifest to make it pass.

The underlying Kline collector remains available independently:

```powershell
python -m bot_obrero.prospective_capture capture --output-dir evidence/prospective-btc-eth --symbols BTCUSDT ETHUSDT --interval 1h --duration-seconds 86400
```

It waits for the next UTC midnight to start and allows up to five additional minutes for the final hour to close and arrive. If the stream cannot demonstrate the acceptance criteria within that grace period, the run is `BLOCKED` and cannot be used to start the annual prospective holdout. To start immediately instead, pass `--no-align-next-utc-midnight`; an unaligned run may not contain a complete UTC day and can correctly end `BLOCKED`.

The default Kline stream chunk is 1,000 messages. The adapter rotates connections at bounded chunks and uses capped exponential backoff after transport errors. A `serverShutdown` control event is treated as reconnectable transport, not as a Kline payload. Events are fsynced by default. A persistence or malformed-payload failure blocks capture and stops the Kline stream workers.

## Output and provenance

Each run creates a unique directory containing:
- `BTCUSDT.jsonl` and `ETHUSDT.jsonl`: one canonical `MarketData` envelope per received event; records include sequence number, run identity and a per-record SHA-256.
- `manifest.json`: UTC schedule/start/end, requested duration, effective configuration and hash, per-symbol counts, final-candle chronology, inter-event gaps, transport errors/reconnects, and SHA-256 hashes for each dataset file.

Intermediate updates to the same candle are retained; the stream can emit multiple updates per candle. A duplicate is flagged, not silently removed. Candle finality is taken from the official Binance Kline `k.x` flag as represented by the existing canonical adapter. `available_at` is required and must be based on an explicit consumer-handoff clock; it is never fabricated from event time or candle close.

By default, a gap greater than 10 seconds between accepted stream messages is counted as a continuity break. This threshold is deliberately strict because the Spot Kline stream normally updates more frequently than once per hour. Reconnects are retained in the manifest; reconnecting does not erase a data gap.

## Acceptance gate

A run is accepted only when every condition holds:

1. `manifest.json` has status `COMPLETED`.
2. Actual elapsed capture time is at least 86,400 seconds.
3. Both symbols contain at least one entire UTC day of 24 unique, aligned final hourly candles, from 00:00 through 23:00 UTC.
4. No missing hourly bars, duplicate final bars, out-of-order final bars, unaligned final bars, inter-event gaps above the configured limit, or local clock regressions are recorded.
5. No fatal parsing, availability, or persistence error occurred; every accepted record has a valid open/partial/non-final or closed/complete/final Kline state contract.
6. Every record's receive timestamp is within the declared capture interval, and event/receive/availability timestamps are ordered.
7. Capture start/end wall-clock duration agrees with the monotonic duration within the defined tolerance; a record cannot be outside the run window even if a manifest is edited.
8. File sizes, record counts/sequences, per-record hashes, configuration hash, and whole-file SHA-256 values pass independent validation.

Validate a run:

```powershell
python -m bot_obrero.prospective_capture validate evidence/prospective-btc-eth/<run-id>/manifest.json
```

The validator must print `status: VALID` and returns exit code zero only for an accepted manifest with unchanged dataset files. It independently recomputes the file/record hashes, sequence numbers, source identity, candle-state contract, receive timestamps versus the run start/end, daily coverage and continuity gates; it does not simply trust the manifest's `accepted` flag. A directory left `RUNNING` after a crash is not a completed run. Do not manually change its status to `COMPLETED`.

## What this does not prove

A passing capture verifies a new period of acquisition integrity and delivery timing. It does not establish that a strategy is profitable, that past historical candles were available in real time, or that a market signal should be executed. The annual BTC→ETH strategy must stay `NO_TRADE` until the frozen strategy, costs and benchmarks pass the independent evaluation protocol and any held-out validation. No annual evaluation T0 should be assigned before the capture manifest has been accepted and the dataset/configuration hashes have been recorded in the study evidence.

Large JSONL datasets should not be committed as source code or used to inflate code-completion percentages. Preserve them as immutable GitHub Actions artifacts, a versioned release asset, or another durable evidence store, and record their hashes and location in the project state.


## Strict schema typing (PR #93)

The validator requires `schema_version` to be the exact JSON integer `1` in the manifest, configuration and each JSONL event envelope. Each envelope `sequence` must be an exact integer starting at 1; JSON booleans such as `true` are rejected even though Python compares `True == 1`. This check is covered by regression tests. Capture-validator fix SHA from PR #93: `8c325d9899ae4a15b06240012b05473f2cd34824`.

CI evidence for that change: pre-merge suite [39100827937](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100827937) passed 1608 tests, and post-merge main suite [39100954733](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39100954733) passed 1608 tests. A passing suite validates this software contract; it does not mean the real 24-hour capture has been completed.


## Current main continuity (18 October 2026)

The capture schema validation change from PR #93 remains in main. PR #96 separately hardens the PAPER economic replay's handling of malformed fill ordering; it does not change the capture collector or its acceptance criteria. Latest functional main SHA: `bb4cf093e4798a9ce80a770281a6003dfb4d2f9b`. Post-merge main suite [39101918101](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918101) passed 1610 tests; [CodeQL](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918074) and [pip-audit](https://github.com/eltiootaku01-hue/bot-cripto/actions/runs/39101918092) passed. These tests still do not substitute for a real accepted 24-hour capture.


## Companion capture: prospective bookTicker evidence

The Kline collector above proves candle closure and hourly continuity. It does **not** by itself provide an executable quote for every decision. BTC/ETH V2 also needs point-in-time best bid/ask observations, with receive/availability timestamps and immutable hashes.

Main now includes:
- `bot_obrero/binance_book_ticker.py`: bounded public Spot `bookTicker` WebSocket adapter, exact approved-symbol guard, decimal and crossed-market validation, update ID validation, separate local receive/handoff clocks, and a hash-bound quote contract.
- `bot_obrero/prospective_book_ticker_capture.py`: JSONL per-symbol persistence, fsync, run manifest, reconnect backoff, SHA-256 file/record/configuration checks, and a validator that recomputes acceptance from stored records.
- The quote adapter streams directly into the durable sink without keeping a second full in-memory copy of results. Update-ID integrity uses constant-memory counters rather than a set growing with every quote. The journal writes each quote incrementally, flushes buffered JSONL output and fsyncs at least every 1,000 records or one second, whichever comes first; finalization also flushes/fsyncs before hashing. In an abrupt host failure, the policy therefore bounds buffered (not yet fsynced) data to about one second under continuous events. Manifest summaries update at most once per second except when an anomaly or batch threshold triggers an earlier update. A single connection is kept open for the run and is re-opened after a real transport failure or provider shutdown rather than after every small message batch. The quote capture defaults to a five-minute grace beyond the requested 86,400-second target (86,700 elapsed seconds total). In the recommended paired runner, Kline receives the additional 60-second overlap guard; for manual paired runs, use the Kline command below with `--duration-seconds 86460`. The grace period alone does not guarantee 24 hours of overlap when two processes start at different times; `validate-pair` remains authoritative.
- Regression tests in `tests/test_binance_book_ticker.py` and `tests/test_prospective_book_ticker_capture.py`. The latter uses synthetic quote events to validate the 24-hour persistence contract; it is not evidence of a real capture.

**Important timestamp limit:** Binance Spot's documented raw bookTicker payload includes update ID, symbol, bid/ask prices and quantities, but does not normally include an exchange event timestamp. The adapter therefore records local receive time as the observation clock when no provider event timestamp exists, and separately records local consumer availability. It never fabricates a past exchange timestamp. Downstream execution/replay must still reject a quote that is not available after the decision or exceeds the preregistered freshness limit.

### Run both public-data captures together

Start both commands in separate terminals **before the next UTC midnight** on a persistent machine. Both default to aligning the run to the next UTC midnight; use the same target start for the Kline and bookTicker smoke captures.

Terminal A — hourly klines (use the 60-second overlap guard when pairing with bookTicker):

```powershell
python -m bot_obrero.prospective_capture capture --output-dir evidence/prospective-btc-eth --symbols BTCUSDT ETHUSDT --interval 1h --duration-seconds 86460
python -m bot_obrero.prospective_capture validate evidence/prospective-btc-eth/<run-id>/manifest.json
```

Terminal B — best bid/ask quotes:

```powershell
python -m bot_obrero.prospective_book_ticker_capture capture --output-dir evidence/prospective-btc-eth-bookticker --symbols BTCUSDT ETHUSDT --duration-seconds 86400 --grace-seconds 300
python -m bot_obrero.prospective_book_ticker_capture validate evidence/prospective-btc-eth-bookticker/<run-id>/manifest.json
```

After **both** independent validators print `status: VALID`, run the joint overlap validator:

```powershell
python -m bot_obrero.prospective_book_ticker_capture validate-pair --kline-manifest evidence/prospective-btc-eth/<kline-run-id>/manifest.json --bookticker-manifest evidence/prospective-btc-eth-bookticker/<bookticker-run-id>/manifest.json
```

The joint validator re-reads both manifests, independently re-validates each capture, requires at least 86,400 seconds of overlap, and requires one common complete UTC day of final hourly candles at the beginning of that common window. It prints `status: VALID`, start/end of the overlap, overlap duration, shared complete UTC day(s), both run IDs and all dataset hashes. A successful independent validation of each capture alone is not sufficient if the time windows do not actually overlap.

Replace each `<run-id>` with the directory created by that run. For the quote capture, the manifest must have `status: COMPLETED`, actual elapsed duration of at least 86,700 seconds when using the default 300-second grace, no timestamp regressions, no duplicate/out-of-order update IDs, no observed inter-event gap above policy, and receive coverage from the beginning to the end of the run. If either stream is blocked, invalid or does not satisfy `validate-pair`, the smoke test is not accepted and T0 must not be set.

Keep the two manifests, dataset SHA-256 values, configuration hashes, run identifiers and the joint-validation output as immutable evidence outside the source tree (for example, a versioned GitHub Actions artifact or release asset). Do not commit large raw JSONL datasets to `main`. A green synthetic test suite is necessary software evidence, not a substitute for the real overlapping capture.


## Git provenance gate — latest hardening (2026-10-18)

The paired capture runner records and hash-binds the code revision used for data acquisition. It fails closed unless the current directory is the Git repository root, the current branch is main, HEAD exactly equals the locally fetched origin/main ref, the source status for bot_obrero/ and pyproject.toml is empty, and all required Git metadata commands succeed. A blocked run persists the reason in pair-manifest.json and never starts the two collectors.

Run git fetch origin main before capture so the remote-tracking ref is refreshed; the runner deliberately does not fetch or alter branches itself. This does not prove market data was captured: it makes any future evidence traceable to an exact clean source revision.


## Revalidación de procedencia antes y después de la captura (18-10-2026)

El control Git no se ejecuta una sola vez: comprueba la fuente al preparar la corrida, vuelve a compararla justo después de la espera a medianoche y antes de abrir los WebSockets, y vuelve a comprobarla cuando ambos colectores terminan y antes de validar la pareja. Si cambia HEAD, origin/main, rama o estado del código durante la espera/captura, la corrida termina BLOCKED y no puede usarse como evidencia aceptada.


## Live remote-main verification — hardening 2026-10-18

The paired runner now verifies not only `HEAD` and the locally cached `refs/remotes/origin/main`, but also queries the live remote ref with:

```powershell
git ls-remote --exit-code origin refs/heads/main
```

It records `live_main_sha` in source provenance and requires the remote SHA, local tracking SHA and `HEAD` to agree at initial validation, after the UTC-midnight wait immediately before launch, and after both collectors finish before accepting the manifests. If `origin/main` is stale relative to the live remote, the run becomes `BLOCKED` with `CAPTURE_ORIGIN_MAIN_TRACKING_STALE`. If the remote cannot be queried or the ref cannot be parsed, the provenance gate fails closed; the runner does not accept the datasets. Refresh the local tracking ref before starting as instructed above, and do not edit a blocked manifest to make it pass.

Regression test: `tests/test_prospective_capture_pair.py::test_source_provenance_blocks_stale_local_origin_main_when_remote_advances`. CI for code/test SHA `2cd9709095167fb58aff27dfc6fc8eecedf8b05e`: 1,676 tests, CodeQL and pip-audit all `success`. This verifies the provenance guard, not a real market capture. Issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) remains open until the real paired manifests pass the independent validators.


## Empaquetar la evidencia aceptada para revisión independiente

Después de que `prospective_capture_pair capture` finalice con código 0 y `status: VALID`, se puede empaquetar esa corrida para entregarla a un revisor. La herramienta vuelve a comprobar la pareja con los validadores independientes y rechaza manifiestos no válidos, hashes incoherentes, rutas que salgan de la carpeta de la corrida, enlaces simbólicos, archivos temporales, cambios de procedencia y cualquier intento de sobrescribir un ZIP anterior. **No transforma una captura sintética en evidencia real**; solo empaqueta una captura real previamente aceptada.

Desde PowerShell, en la raíz de un clon limpio y actualizado de `main`:

```powershell
$pairRoot = ".\evidence\prospective-btc-eth-pair"
$run = Get-ChildItem $pairRoot -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($null -eq $run) { throw "BLOCKED: no existe carpeta de corrida" }

& .\.venv\Scripts\python.exe -m bot_obrero.prospective_capture_bundle create --run-dir $run.FullName
if ($LASTEXITCODE -ne 0) {
  throw "BLOCKED: la herramienta rechazó el empaquetado; conservar el directorio y diagnosticar"
}
```

El resultado devuelve la ruta del ZIP, la del archivo de suma `.sha256`, el SHA-256, el tamaño y el solapamiento recalculado. El ZIP y su archivo `.sha256` quedan junto a la carpeta de la corrida, no dentro de ella. Adjuntar ambos al informe de revisión y conservar también la carpeta original sin modificar. Se puede comparar el hash reportado con `Get-FileHash <ruta-del-ZIP> -Algorithm SHA256`.

La revisión debe comprobar que la corrida es el resultado real de este colector y que sus datos originales están incluidos. Un ZIP con estructura válida o una prueba unitaria de la herramienta no satisface la issue [#98](https://github.com/eltiootaku01-hue/bot-cripto/issues/98) sin datos reales Kline + bookTicker con ≥86.400 segundos de solapamiento y un día UTC común completo.


## Prueba de integración del verificador de recepción

La suite combina ahora dos clases de cobertura: fixtures unitarios con el validador aislado para probar errores de empaquetado/ZIP y una integración con diarios Kline/bookTicker sintéticos que ejecuta ambos validadores de producción sin sustituirlos. La prueba positiva confirma la ruta completa de empaquetado y recepción; una segunda modifica un JSONL dentro del ZIP, recalcula deliberadamente el SHA-256 externo y comprueba que el manifiesto interno sigue rechazando el contenido alterado. Estas pruebas fortalecen el contrato del verificador, pero **siguen siendo datos sintéticos y no cuentan como captura real para cerrar la issue #98**.

## Verificar un ZIP recibido (nueva comprobación independiente)

Después de recibir el ZIP y su sidecar `.sha256`, ejecutar desde la raíz del mismo clon actualizado de `main`:

```powershell
& .\.venv\Scripts\python.exe -m bot_obrero.prospective_capture_bundle verify --archive ".\evidence\<nombre>-evidence.zip"
if ($LASTEXITCODE -ne 0) {
  throw "BLOCKED: el paquete recibido no supera verificación; conservarlo sin editar"
}
```

`verify` exige el sidecar `<archivo>.zip.sha256`, recalcula el SHA-256 del ZIP, rechaza paths inseguros, miembros duplicados, enlaces simbólicos/entradas no regulares, archivos fuera de la allowlist y paquetes de tamaño descomprimido superior a 4 GiB. Extrae los nueve archivos permitidos de forma segura en un directorio temporal, reejecuta la validación independiente de la pareja y elimina la extracción al terminar. Solo devuelve `VALID` si coinciden los hashes, los manifiestos, los datasets y el día UTC común; los archivos fuente permanecen sin modificar. Esta verificación no convierte datos sintéticos en datos reales ni sustituye la revisión de procedencia.

## Aislamiento del contenido incluido en el ZIP

La versión funcional de empaquetado en main limita los archivos permitidos a:
- `pair-manifest.json`;
- el manifiesto Kline y los dos ficheros JSONL de Kline (`BTCUSDT.jsonl`, `ETHUSDT.jsonl`);
- el manifiesto bookTicker y los dos ficheros JSONL de quotes (`BTCUSDT-bookTicker.jsonl`, `ETHUSDT-bookTicker.jsonl`);
- `logs/kline.log` y `logs/bookticker.log`.

Si aparece un archivo distinto, incluso una nota agregada por una persona, el paquete se rechaza con `UNEXPECTED_CAPTURE_FILE`. Las rutas inseguras, symlinks y ficheros `.tmp` también bloquean la operación. Este control evita que un archivo ajeno termine compartido por accidente; no sustituye la validación independiente de origen ni convierte datos sintéticos en evidencia real.

En el SHA funcional [`281302d`](https://github.com/eltiootaku01-hue/bot-cripto/commit/281302deebdfcdf3b23734f651386fed35bf85bf), la suite pasó 1.704 pruebas en Python 3.11–3.14, CodeQL y `pip-audit` terminaron `success`. La regresión `test_bundle_refuses_unexpected_files_instead_of_packaging_them` prueba que un archivo `operator-notes.txt` en la carpeta de logs bloquea el empaquetado.
