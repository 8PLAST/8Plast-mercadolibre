$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'No existe el Python de la instalación.' }
$worker = Join-Path $root 'mercadolibre_worker.py'
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $worker + '"') -WorkingDirectory $root
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# El segundo disparador funciona como supervisor: si el proceso fue cerrado o se
# cayó, el siguiente intervalo lo inicia. Si sigue vivo, IgnoreNew evita duplicarlo.
$watchdogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 20 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
Register-ScheduledTask `
    -TaskName '8PLAST - Ventas Mercado Libre' `
    -Action $action `
    -Trigger @($logonTrigger, $watchdogTrigger) `
    -Settings $settings `
    -User $env:USERNAME `
    -Description 'Recupera ventas pendientes, descuenta solo stock físico interno y se autosupervisa cada 5 minutos.' `
    -Force
Start-ScheduledTask -TaskName '8PLAST - Ventas Mercado Libre'
