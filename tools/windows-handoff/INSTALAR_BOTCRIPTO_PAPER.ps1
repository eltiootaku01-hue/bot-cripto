[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "BOT-CRIPTO")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $gitCommand) {
    throw "Git is required. Install Git for Windows, reopen PowerShell, and run this script again."
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

Write-Host "Cloning the official main branch into $installPath"
& $gitCommand.Source clone --depth 1 --single-branch --branch main https://github.com/eltiootaku01-hue/bot-cripto.git $installPath
Assert-NativeSuccess "Git clone"

$branch = (& $gitCommand.Source -C $installPath branch --show-current).Trim()
Assert-NativeSuccess "Branch verification"
$headSha = (& $gitCommand.Source -C $installPath rev-parse HEAD).Trim()
Assert-NativeSuccess "HEAD verification"
$originMainSha = (& $gitCommand.Source -C $installPath rev-parse refs/remotes/origin/main).Trim()
Assert-NativeSuccess "origin/main verification"
if ($branch -ne "main" -or $headSha -ne $originMainSha) {
    throw "Clone provenance mismatch: branch=$branch HEAD=$headSha origin/main=$originMainSha. Do not capture data from this checkout."
}

$venvPath = Join-Path $installPath ".venv"
& $pythonCommand.Source -m venv $venvPath
Assert-NativeSuccess "Virtual environment creation"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check -e $installPath
Assert-NativeSuccess "BOT-CRIPTO installation"

Write-Host ""
Write-Host "Installed successfully. Current policy and gates:"
& $venvPython -m bot_obrero.operator_cli status
Assert-NativeSuccess "Operator status smoke check"

Write-Host ""
Write-Host "No capture or trading was started by this installer."
Write-Host "Read: $installPath\docs\OPERATOR_RUNBOOK.md"
Write-Host "To start the intentional real-data smoke capture, run this in a persistent PowerShell window:"
Write-Host ('& "{0}" -m bot_obrero.operator_cli capture-24h --output-dir "{1}"' -f $venvPython, (Join-Path $installPath "evidence\prospective-btc-eth-pair"))
Write-Host "That command waits for the next UTC midnight and captures for at least 24 hours."
