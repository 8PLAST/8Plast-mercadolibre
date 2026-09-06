$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$action = New-ScheduledTaskAction -Execute $python -Argument '-m waitress --listen=127.0.0.1:8765 readonly_portal.app:app' -WorkingDirectory $root
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
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
    -TaskName '8PLAST - Portal solo lectura' `
    -Action $action `
    -Trigger @($logonTrigger, $watchdogTrigger) `
    -Settings $settings `
    -Description 'Portal web local de consulta, solo lectura, con autosupervisión cada 5 minutos.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName '8PLAST - Portal solo lectura'
Write-Host 'Portal local registrado e iniciado en http://127.0.0.1:8765'
