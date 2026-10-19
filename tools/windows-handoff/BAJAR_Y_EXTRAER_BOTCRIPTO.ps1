[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:USERPROFILE 'Desktop\BOT-CRIPTO-LOCAL')
)

$ErrorActionPreference = 'Stop'
$Repo = 'eltiootaku01-hue/bot-cripto'
$Commit = '439d0b6cab598db683462ed891f05093b30d6575'
$ArchiveUrl = "https://github.com/$Repo/archive/$Commit.zip"
$Work = $null

try {
    # Windows PowerShell 5.1 compatibility: explicitly enable TLS 1.2.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $Destination = [IO.Path]::GetFullPath($Destination)
    $Parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $Parent)) {
        New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $Destination) {
        throw ("El destino ya existe; no lo sobrescribo: " + $Destination + [Environment]::NewLine + "Elige otra ruta con el parámetro -Destination.")
    }

    $Work = Join-Path $Parent ('.bot-cripto-download-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Work | Out-Null
    $ZipPath = Join-Path $Work 'bot-cripto-source.zip'
    $ExtractPath = Join-Path $Work 'extracted'
    New-Item -ItemType Directory -Path $ExtractPath | Out-Null

    Write-Host "Repositorio: https://github.com/$Repo" -ForegroundColor Cyan
    Write-Host "SHA fijado:  $Commit" -ForegroundColor Cyan
    Write-Host 'Descargando el archivo oficial fijado a ese commit...'
    Invoke-WebRequest -Uri $ArchiveUrl -OutFile $ZipPath -UseBasicParsing

    if (-not (Test-Path -LiteralPath $ZipPath) -or (Get-Item -LiteralPath $ZipPath).Length -lt 1024) {
        throw 'La descarga no parece un ZIP de código fuente válido (archivo ausente o demasiado pequeño).'
    }

    # Validate ZIP structure before extraction.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $Names = @($Archive.Entries | ForEach-Object { $_.FullName })
        if (-not ($Names | Where-Object { $_ -match '^[^/]+/pyproject\.toml$' })) {
            throw 'El ZIP no contiene pyproject.toml en la raíz esperada; cancelo la extracción.'
        }
        if (-not ($Names | Where-Object { $_ -match '^[^/]+/cerebro/PROJECT_STATE\.md$' })) {
            throw 'El ZIP no contiene cerebro/PROJECT_STATE.md; cancelo la extracción.'
        }
    }
    finally {
        $Archive.Dispose()
    }

    Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractPath
    $Roots = @(Get-ChildItem -LiteralPath $ExtractPath -Directory | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName 'pyproject.toml')) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'cerebro\PROJECT_STATE.md'))
    })
    if ($Roots.Count -ne 1) {
        throw "Se esperaba una raíz de repositorio y se encontraron $($Roots.Count); cancelo para no elegir una carpeta incorrecta."
    }

    Move-Item -LiteralPath $Roots[0].FullName -Destination $Destination
    $ZipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $Manifest = @"
BOT-CRIPTO — PAQUETE DE FUENTE PARA ANÁLISIS LOCAL
Repositorio: https://github.com/$Repo
Commit solicitado: $Commit
URL de archivo: $ArchiveUrl
SHA-256 del ZIP descargado en esta máquina: $ZipHash
Ruta extraída: $Destination
Fecha local de descarga: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')

EVIDENCIA CI del commit indicado (no implica ejecución nativa de Windows):
- pytest 3.11: success, 1.713 pruebas; run https://github.com/$Repo/actions/runs/39235484116
- pytest 3.12: success, 1.713 pruebas; run https://github.com/$Repo/actions/runs/39235484116
- pytest 3.13: success, 1.713 pruebas; run https://github.com/$Repo/actions/runs/39235484116
- pytest 3.14: success, 1.713 pruebas; run https://github.com/$Repo/actions/runs/39235484116
- CodeQL: success; run https://github.com/$Repo/actions/runs/39235484241
- pip-audit: success; run https://github.com/$Repo/actions/runs/39235484147

LIMITES DE SEGURIDAD:
- Este script descarga y extrae; no instala dependencias ni ejecuta código del repositorio.
- El estado estratégico del proyecto sigue NO_TRADE.
- Sin LIVE, claves privadas, endpoints privados, órdenes reales ni dinero real.
- La CI citada se ejecutó en GitHub Actions sobre Linux; no es una prueba nativa de Windows.
"@
    $ManifestPath = Join-Path $Destination 'WINDOWS_LOCAL_HANDOFF_MANIFEST.txt'
    Set-Content -LiteralPath $ManifestPath -Value $Manifest -Encoding UTF8
    Move-Item -LiteralPath $ZipPath -Destination (Join-Path $Destination 'bot-cripto-source.zip')
    Remove-Item -LiteralPath $Work -Recurse -Force

    Write-Host ''
    Write-Host 'Listo: fuente descargada y extraída.' -ForegroundColor Green
    Write-Host "Carpeta para tu bot local: $Destination"
    Write-Host "Manifiesto: $ManifestPath"
    Write-Host 'No se instalaron dependencias ni se ejecutó el bot.' -ForegroundColor Yellow
}
catch {
    Write-Error $_
    if ($Work -and (Test-Path -LiteralPath $Work)) {
        Write-Host "Los archivos temporales se conservaron para inspección: $Work" -ForegroundColor Yellow
    }
    exit 1
}
