# Jarvis Fleet install for Windows: make this PC reachable by your other Jarvis
# devices over Tailscale. Run from the repo root in PowerShell. Idempotent.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_fleet.ps1 [-DryRun]
#
# Firewall rule needs an elevated (Administrator) PowerShell; without it the
# script still does everything else and prints the command to run.
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Port = 4317
$RuleName = "Jarvis Fleet"
$TaskName = "JarvisFleetBridge"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$JarvisHome = if ($env:JARVIS_HOME) { $env:JARVIS_HOME } else { Join-Path $env:USERPROFILE ".jarvis" }
$TokenFile = Join-Path $JarvisHome "bridge_token"
$BridgeScript = Join-Path $Repo "src\bridge.py"
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

function Write-Step([string]$Message) { Write-Host "==> $Message" }

function Invoke-Change([string]$Description, [scriptblock]$Action) {
    if ($DryRun) { Write-Host "    [dry run] $Description"; return }
    Write-Host "    $Description"
    & $Action
}

# ---- 1. Python 3.11+ ------------------------------------------------------
function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Pre = @("-3") },
        @{ Exe = "python"; Pre = @() }
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        foreach ($c in $candidates) {
            $found = Get-Command $c.Exe -ErrorAction SilentlyContinue
            if (-not $found) { continue }
            $probe = @($c.Pre) + @("-c", "import sys; print(sys.version_info >= (3, 11))")
            $answer = & $found.Source @probe 2>$null
            if ($LASTEXITCODE -ne 0 -or ($answer -join "") -ne "True") { continue }
            $where = @($c.Pre) + @("-c", "import sys; print(sys.executable)")
            $exePath = (& $found.Source @where 2>$null) -join ""
            return @{ Exe = $exePath.Trim(); Pre = @($c.Pre) }
        }
    } finally {
        $ErrorActionPreference = $previous
    }
    return $null
}

Write-Step "Checking Python 3.11+"
$Python = Find-Python
if (-not $Python) { throw "Python 3.11 or newer not found. Install it from https://www.python.org/downloads/ and retry." }
Write-Host "    $($Python.Exe)"

# ---- 2. Tailscale installed and logged in ----------------------------------
Write-Step "Checking Tailscale"
$Tailscale = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $Tailscale) { $Tailscale = "C:\Program Files\Tailscale\tailscale.exe" }
if (-not (Test-Path -LiteralPath $Tailscale)) {
    throw "Tailscale not found. Install it from https://tailscale.com/download and sign in."
}
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$TailIp = (& $Tailscale ip -4 2>$null | Select-Object -First 1)
$TailExit = $LASTEXITCODE
$ErrorActionPreference = $previous
$TailIp = "$TailIp".Trim()
if ($TailExit -ne 0 -or $TailIp -notmatch '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}$') {
    throw "Tailscale is installed but not logged in (no 100.x address). Run: tailscale up"
}
Write-Host "    tailnet IPv4: $TailIp"

# ---- 3. Bridge token (32 random bytes, hex) --------------------------------
Write-Step "Bridge token"
if (Test-Path -LiteralPath $TokenFile) {
    Write-Host "    keeping existing $TokenFile"
} else {
    Invoke-Change "create $TokenFile" {
        New-Item -ItemType Directory -Force -Path $JarvisHome | Out-Null
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $hex = ([BitConverter]::ToString($bytes) -replace "-", "").ToLower()
        [System.IO.File]::WriteAllText($TokenFile, $hex)
    }
}
Invoke-Change "restrict $TokenFile to $User" {
    & icacls.exe $TokenFile /inheritance:r /grant:r "${User}:(R,W)" | Out-Null
}

# ---- 4. Bind address for the bridge (user environment) ---------------------
Write-Step "Bind address"
Invoke-Change "set user env JARVIS_BRIDGE_BIND=$TailIp" {
    [Environment]::SetEnvironmentVariable("JARVIS_BRIDGE_BIND", $TailIp, "User")
    $env:JARVIS_BRIDGE_BIND = $TailIp
}

# ---- 5. Firewall: TCP 4317 from the tailnet only ----------------------------
Write-Step "Firewall rule '$RuleName'"
if (-not $IsAdmin) {
    Write-Host "    not elevated: skipped. Run this in an Administrator PowerShell:"
    Write-Host "    New-NetFirewallRule -DisplayName '$RuleName' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress 100.64.0.0/10"
} else {
    Invoke-Change "replace rule: TCP $Port from 100.64.0.0/10 only" {
        Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort $Port -RemoteAddress 100.64.0.0/10 | Out-Null
    }
}

# ---- 6. Scheduled task: bridge at logon, hidden window ----------------------
Write-Step "Scheduled task '$TaskName'"
$Interpreter = $Python.Exe
$Windowless = Join-Path (Split-Path -Parent $Python.Exe) "pythonw.exe"
if (Test-Path -LiteralPath $Windowless) { $Interpreter = $Windowless }
Invoke-Change "register task (runs at logon as $User): $Interpreter `"$BridgeScript`"" {
    $action = New-ScheduledTaskAction -Execute $Interpreter -Argument "`"$BridgeScript`"" -WorkingDirectory $Repo
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
}

# ---- 7. Start now (the task starts at the next logon) -----------------------
Write-Step "Starting the bridge"
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "    already listening on port $Port: restart Jarvis to pick up the new bind address"
} else {
    Invoke-Change "start $Interpreter (hidden window)" {
        Start-Process -FilePath $Interpreter -ArgumentList "`"$BridgeScript`"" `
            -WorkingDirectory $Repo -WindowStyle Hidden | Out-Null
    }
}

# ---- 8. Pairing summary -----------------------------------------------------
$DeviceName = ($env:COMPUTERNAME.ToLower() -replace "[^a-z0-9]+", "-").Trim("-")
Write-Host ""
Write-Host "Done. Pair this device from the controller:"
Write-Host "  device name : $DeviceName"
Write-Host "  URL         : http://${TailIp}:$Port"
Write-Host "  token file  : $TokenFile (open it and copy the single line)"
Write-Host ""
Write-Host "On the controller, run:"
Write-Host "  python src\fleet.py add $DeviceName http://${TailIp}:$Port"
Write-Host "and paste the token when asked."
if ($DryRun) { Write-Host ""; Write-Host "Dry run: nothing was changed." }
