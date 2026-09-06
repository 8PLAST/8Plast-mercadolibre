@echo off
cd /d "%~dp0"
if not exist "ngrok_readonly_policy.yml" (
  echo Primero ejecuta configurar_acceso_trabajo.ps1
  pause
  exit /b 1
)
"%~dp0tools\ngrok\ngrok.exe" http 8765 --traffic-policy-file "%~dp0ngrok_readonly_policy.yml"
