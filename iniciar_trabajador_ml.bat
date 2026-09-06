@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py mercadolibre_worker.py
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python mercadolibre_worker.py
  ) else if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" mercadolibre_worker.py
  )
)
