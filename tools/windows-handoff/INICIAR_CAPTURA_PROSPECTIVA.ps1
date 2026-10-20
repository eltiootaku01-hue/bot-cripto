[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "BOT-CRIPTO-PROSPECTIVE-CAPTURE")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Convert-OutputToText($Output) {
    return (@($Output) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
}

$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $gitCommand) {
    throw "Git is required. Install Git for Windows and reopen PowerShell."
}
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    throw "Python 3.11+ is required. Install Python and ensure 'python' is on PATH."
}

$pythonVersionText = & $pythonCommand.Source --version
Assert-NativeSuccess "Python version check"
if ($pythonVersionText -notmatch "Python\s+(\d+)\.(\d+)\.(\d+)") {
    throw "Unable to parse Python version: $pythonVersionText"
}
$pythonMajor = [int]$Matches[1]
$pythonMinor = [int]$Matches[2]
if ($pythonMajor -ne 3 -or $pythonMinor -lt 11) {
    throw "Python 3.11 or newer is required; found $pythonVersionText."
}

$installPath = [System.IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $installPath) {
    throw "Destination already exists: $installPath. Nothing was overwritten. Choose a new -Destination or inspect the existing folder manually."
}
$parent = Split-Path -Parent $installPath
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}

Write-Host "Cloning the latest official main branch into $installPath"
& $gitCommand.Source clone --depth 1 --single-branch --branch main https://github.com/eltiootaku01-hue/bot-cripto.git $installPath
Assert-NativeSuccess "Git clone"

$branch = (& $gitCommand.Source -C $installPath branch --show-current).Trim()
Assert-NativeSuccess "Branch verification"
$headSha = (& $gitCommand.Source -C $installPath rev-parse HEAD).Trim().ToLowerInvariant()
Assert-NativeSuccess "HEAD verification"
$originMainSha = (& $gitCommand.Source -C $installPath rev-parse refs/remotes/origin/main).Trim().ToLowerInvariant()
Assert-NativeSuccess "origin/main verification"
$liveMainOutput = & $gitCommand.Source -C $installPath ls-remote --exit-code origin refs/heads/main
Assert-NativeSuccess "Live origin/main verification"
$liveMainLines = @($liveMainOutput | ForEach-Object { [string]$_ })
if ($liveMainLines.Count -ne 1 -or $liveMainLines[0] -notmatch '^([0-9a-fA-F]{40})\s+refs/heads/main$') {
    throw "Could not verify the live origin/main SHA. No capture was started."
}
$liveMainSha = $Matches[1].ToLowerInvariant()
if ($branch -ne "main" -or $headSha -ne $originMainSha -or $headSha -ne $liveMainSha) {
    throw "Clone provenance mismatch: branch=$branch HEAD=$headSha origin/main=$originMainSha live_main=$liveMainSha. Do not capture data from this checkout."
}

$venvPath = Join-Path $installPath ".venv"
& $pythonCommand.Source -m venv $venvPath
Assert-NativeSuccess "Virtual environment creation"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check -e $installPath
Assert-NativeSuccess "BOT-CRIPTO installation"

Write-Host ""
Write-Host "Checking the installed operator policy..."
$statusOutput = & $venvPython -m bot_obrero.operator_cli status
Assert-NativeSuccess "Operator status check"
$statusText = Convert-OutputToText $statusOutput
$status = $statusText | ConvertFrom-Json
if ($status.strategy_decision -ne "NO_TRADE" -or
    $status.live_execution -ne "DISABLED_BY_PROJECT_POLICY") {
    throw "Unexpected operator safety policy. No capture was started."
}
Write-Output $statusText

Write-Host ""
Write-Host "The next step captures REAL PUBLIC Binance market data for BTCUSDT and ETHUSDT."
Write-Host "It waits for the next UTC midnight and then runs for more than 24 hours."
Write-Host "Keep this PC awake, keep PowerShell open, and maintain internet connectivity."
Write-Host "No private API keys are used; no exchange orders or trades are submitted."
Write-Warning "This capture collects evidence only. It does NOT turn the strategy into TRADE."
$confirmation = Read-Host "Type CAPTURAR to start the supervised public-data capture"
if ($confirmation -cne "CAPTURAR") {
    throw "OPERATOR_DID_NOT_CONFIRM; no market capture was started."
}

$pairRoot = Join-Path $installPath "evidence\prospective-btc-eth-pair"
New-Item -ItemType Directory -Path $pairRoot -Force | Out-Null
Write-Host ""
Write-Host "Starting paired Kline + bookTicker capture. Leave this window open until it finishes."
$captureOutput = & $venvPython -m bot_obrero.operator_cli capture-24h --output-dir $pairRoot 2>&1
$captureExitCode = $LASTEXITCODE
$captureText = Convert-OutputToText $captureOutput
Write-Output $captureText
if ($captureExitCode -ne 0) {
    throw "CAPTURE_BLOCKED_OR_FAILED (exit $captureExitCode). Preserve the evidence and logs in $pairRoot; do not edit manifests."
}
try {
    $capture = $captureText | ConvertFrom-Json
} catch {
    throw "CAPTURE_RESULT_JSON_INVALID. Preserve the run directory in $pairRoot."
}
if ($capture.status -ne "VALID" -or $capture.exit_code -ne 0) {
    throw "CAPTURE_ACCEPTANCE_NOT_VALID. Preserve the run directory in $pairRoot; do not package it or change manifests."
}

$pairManifestPath = [string]$capture.pair_manifest_path
if ([string]::IsNullOrWhiteSpace($pairManifestPath)) {
    throw "PAIR_MANIFEST_PATH_MISSING. Preserve the run directory in $pairRoot."
}
$runDirectory = Split-Path -Parent $pairManifestPath
if (-not (Test-Path -LiteralPath (Join-Path $runDirectory "pair-manifest.json") -PathType Leaf)) {
    throw "PAIR_MANIFEST_NOT_FOUND at $runDirectory. Preserve the run directory."
}

Write-Host ""
Write-Host "Capture is VALID. Creating the evidence bundle and SHA-256 sidecar..."
$bundleOutput = & $venvPython -m bot_obrero.prospective_capture_bundle create --run-dir $runDirectory 2>&1
$bundleExitCode = $LASTEXITCODE
$bundleText = Convert-OutputToText $bundleOutput
Write-Output $bundleText
if ($bundleExitCode -ne 0) {
    throw "EVIDENCE_BUNDLE_CREATION_FAILED. Keep the original capture in $runDirectory."
}
try {
    $bundle = $bundleText | ConvertFrom-Json
} catch {
    throw "EVIDENCE_BUNDLE_RESULT_JSON_INVALID. Keep the original capture in $runDirectory."
}
if ($bundle.status -ne "VALID" -or $bundle.trading_enabled -ne $false) {
    throw "EVIDENCE_BUNDLE_NOT_VALID. Keep the original capture in $runDirectory."
}

Write-Host ""
Write-Host "Independently verifying the newly created bundle..."
$verifyOutput = & $venvPython -m bot_obrero.prospective_capture_bundle verify --archive ([string]$bundle.archive) 2>&1
$verifyExitCode = $LASTEXITCODE
$verifyText = Convert-OutputToText $verifyOutput
Write-Output $verifyText
if ($verifyExitCode -ne 0) {
    throw "RECEIVED_BUNDLE_VERIFICATION_FAILED. Keep the original capture and ZIP unchanged."
}
try {
    $verified = $verifyText | ConvertFrom-Json
} catch {
    throw "BUNDLE_VERIFICATION_RESULT_JSON_INVALID."
}
if ($verified.status -ne "VALID" -or $verified.sha256 -ne $bundle.sha256 -or
    $verified.trading_enabled -ne $false) {
    throw "BUNDLE_VERIFICATION_MISMATCH. Do not accept or alter the archive."
}

Write-Host ""
Write-Host "PROSPECTIVE_CAPTURE_STATUS=VALID"
Write-Host ("EVIDENCE_ARCHIVE=" + [string]$bundle.archive)
Write-Host ("EVIDENCE_SHA256=" + [string]$bundle.sha256)
Write-Host ("EVIDENCE_SIDECAR=" + [string]$bundle.sha256_file)
Write-Host "NEXT STEP: upload the ZIP and its .sha256 sidecar for independent review."
Write-Host "The economic decision remains NO_TRADE until a separate preregistered 365-day holdout passes."
Write-Host "No exchange orders were submitted."
