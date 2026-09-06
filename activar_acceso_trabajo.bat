@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0preparar_inicio_acceso_trabajo.ps1"
if errorlevel 1 (
  echo.
  echo No se pudo activar el inicio automatico.
)
pause
