$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ngrok = Join-Path $root 'tools\ngrok\ngrok.exe'
$template = Join-Path $root 'ngrok_readonly_policy.example.yml'
$policy = Join-Path $root 'ngrok_readonly_policy.yml'

if (-not (Test-Path -LiteralPath $ngrok)) {
    throw 'No se encontró ngrok.exe. La instalación de 8PLAST quedó incompleta.'
}

$email = (Read-Host 'Correo de Google autorizado para consultar desde el trabajo').Trim().ToLowerInvariant()
if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
    throw 'El correo no tiene un formato válido.'
}

$tokenSecure = Read-Host 'Pegá el authtoken de tu cuenta gratuita de ngrok' -AsSecureString
$token = [System.Net.NetworkCredential]::new('', $tokenSecure).Password
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'El authtoken no puede estar vacío.'
}

& $ngrok config add-authtoken $token
if ($LASTEXITCODE -ne 0) {
    throw 'ngrok rechazó el authtoken.'
}
$token = $null

$safeEmail = $email.Replace("'", "''")
$content = Get-Content -LiteralPath $template -Raw
$content = $content.Replace('REEMPLAZAR_CON_CORREO_AUTORIZADO', $safeEmail)
[System.IO.File]::WriteAllText($policy, $content, [System.Text.UTF8Encoding]::new($false))

Write-Host ''
Write-Host 'Configuración guardada. El correo permitido es:' $email
Write-Host 'Ahora ejecutá iniciar_acceso_trabajo.bat para abrir el acceso seguro.'
