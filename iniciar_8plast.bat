@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py app.py
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python app.py
  ) else if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
  ) else (
    echo.
    echo No se encontro Python 3 en esta computadora.
    echo Instalalo desde https://www.python.org/downloads/ y marca "Add Python to PATH".
    echo Luego volve a abrir este archivo.
    echo.
    pause
  )
)
if errorlevel 1 pause
