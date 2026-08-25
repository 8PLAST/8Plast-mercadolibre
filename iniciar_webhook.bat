@echo off
cd /d "%~dp0"
set "WEBHOOK_PYTHON="
where py >nul 2>nul && set "WEBHOOK_PYTHON=py"
if not defined WEBHOOK_PYTHON where python >nul 2>nul && set "WEBHOOK_PYTHON=python"
if not defined WEBHOOK_PYTHON if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "WEBHOOK_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined WEBHOOK_PYTHON (
  echo No se encontro Python 3. Instalarlo desde https://www.python.org/downloads/
  pause
  exit /b 1
)
%WEBHOOK_PYTHON% -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo Falta instalar Flask. Ejecuta una vez:
  echo %WEBHOOK_PYTHON% -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo Webhook disponible en http://localhost:8000
echo Para detenerlo, cerra esta ventana.
%WEBHOOK_PYTHON% webhook_server\app.py
if errorlevel 1 pause
