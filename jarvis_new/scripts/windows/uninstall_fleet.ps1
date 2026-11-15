# Jarvis Fleet uninstall for Windows: reverses install_fleet.ps1.
# Keeps the bridge token unless -RemoveToken is given. Run in PowerShell.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_fleet.ps1 [-DryRun] [-RemoveToken]
param(
    [switch]$DryRun,
    [switch]$RemoveToken
)

$ErrorActionPreference = "Stop"
$RuleName = "Jarvis Fleet"
$TaskName = "JarvisFleetBridge"
$JarvisHome = if ($env:JARVIS_HOME) { $env:JARVIS_HOME } else { Join-Path $env:USERPROFILE ".jarvis" }
$TokenFile = Join-Path $JarvisHome "bridge_token"

function Write-Step([string]$Message) { Write-Host "==> $Message" }

function Invoke-Change([string]$Description, [scriptblock]$Action) {
    if ($DryRun) { Write-Host "    [dry run] $Description"; return }
    Write-Host "    $Description"
    & $Action
}

Write-Step "Scheduled task '$TaskName'"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Invoke-Change "stop and unregister the task" {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
} else {
    Write-Host "    not registered"
}

Write-Step "Bridge process"
$bridges = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*bridge.py*" -and $_.Name -like "python*" }
if ($bridges) {
    foreach ($proc in $bridges) {
        Invoke-Change "stop process $($proc.ProcessId) ($($proc.Name))" {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Write-Host "    none running"
}

Write-Step "Firewall rule '$RuleName'"
if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Invoke-Change "remove rule" {
        Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "    not present (or needs an elevated PowerShell to remove)"
}

Write-Step "Bind address"
Invoke-Change "clear user env JARVIS_BRIDGE_BIND" {
    [Environment]::SetEnvironmentVariable("JARVIS_BRIDGE_BIND", $null, "User")
    Remove-Item Env:JARVIS_BRIDGE_BIND -ErrorAction SilentlyContinue
}

Write-Step "Bridge token"
if ($RemoveToken -and (Test-Path -LiteralPath $TokenFile)) {
    Invoke-Change "delete $TokenFile" {
        Remove-Item -LiteralPath $TokenFile -Force
    }
} elseif (Test-Path -LiteralPath $TokenFile) {
    Write-Host "    kept $TokenFile (pass -RemoveToken to delete it)"
} else {
    Write-Host "    no token file"
}

Write-Host ""
if ($DryRun) { Write-Host "Dry run: nothing was changed." } else { Write-Host "Jarvis Fleet removed from this PC." }
Write-Host "Peers still holding this device's token should run: python src\fleet.py remove <name>"
