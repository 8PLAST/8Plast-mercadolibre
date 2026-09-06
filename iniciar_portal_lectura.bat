@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -Command "try { $r=Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2; if($r.status -eq 'ok'){exit 0} }; exit 1" >nul 2>nul
if %errorlevel%==0 exit /b 0
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -m waitress --listen=127.0.0.1:8765 readonly_portal.app:app
