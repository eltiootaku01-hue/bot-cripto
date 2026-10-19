@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0BAJAR_Y_EXTRAER_BOTCRIPTO.ps1"
if errorlevel 1 (
  echo.
  echo La descarga/extraccion no se completo. Lee el mensaje de error anterior.
) else (
  echo.
  echo Proceso completado. Usa la carpeta BOT-CRIPTO-LOCAL del Escritorio con tu bot local.
)
echo.
pause
