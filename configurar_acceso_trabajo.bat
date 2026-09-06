@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0configurar_acceso_trabajo.ps1"
if errorlevel 1 (
  echo.
  echo No se pudo completar la configuracion.
)
pause
