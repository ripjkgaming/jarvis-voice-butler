# Jarvis Fleet setup for Windows: make this PC reachable by your other Jarvis
# devices over Tailscale. Run once in PowerShell from the repo root.
$ErrorActionPreference = "Stop"
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $ts) { $ts = "C:\Program Files\Tailscale\tailscale.exe" }
if (-not (Test-Path $ts)) { throw "Install Tailscale first (https://tailscale.com/download) and sign in." }
$ip = (& $ts ip -4 | Select-Object -First 1).Trim()
if (-not $ip) { throw "Tailscale has no IPv4; sign in first." }
$home = if ($env:JARVIS_HOME) { $env:JARVIS_HOME } else { Join-Path $env:USERPROFILE ".jarvis" }
$tokenFile = Join-Path $home "bridge_token"
if (-not (Test-Path $tokenFile)) {
    New-Item -ItemType Directory -Force -Path $home | Out-Null
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    ([BitConverter]::ToString($bytes) -replace "-", "").ToLower() | Set-Content -NoNewline $tokenFile
}
[Environment]::SetEnvironmentVariable("JARVIS_BRIDGE_BIND", $ip, "User")
Write-Host "Bridge will listen on $ip (restart Jarvis to apply)."
Write-Host "Allow inbound TCP 4317 from the tailnet only if Windows Firewall prompts."
Write-Host ""
Write-Host "On the computer you control FROM, run:"
Write-Host "  python src/fleet.py add $($env:COMPUTERNAME.ToLower()) http://${ip}:4317"
Write-Host "and paste the token from: $tokenFile"
