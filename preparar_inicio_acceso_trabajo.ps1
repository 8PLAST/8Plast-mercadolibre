$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$policy = Join-Path $root 'ngrok_readonly_policy.yml'
if (-not (Test-Path -LiteralPath $policy)) {
    throw 'Primero ejecutá configurar_acceso_trabajo.ps1 para autorizar tu correo.'
}

$launcher = Join-Path $root 'tools\ngrok\ngrok.exe'
$action = New-ScheduledTaskAction -Execute $launcher -Argument ('http 8765 --traffic-policy-file "' + $policy + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 20 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName '8PLAST - Acceso remoto lectura' `
    -Action $action `
    -Trigger @($trigger, $watchdog) `
    -Settings $settings `
    -Description 'Túnel saliente seguro con autenticación Google hacia el portal de solo lectura.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName '8PLAST - Acceso remoto lectura'
Write-Host 'Acceso remoto registrado e iniciado.'
