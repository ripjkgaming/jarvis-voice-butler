# Jarvis Fleet install for Windows: makes this PC linkable by your other Jarvis
# devices over Tailscale. Thin wrapper around scripts\fleet_bootstrap.py.
# Run from the repo root (or anywhere; paths are resolved from this file).
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_fleet.ps1 [-DryRun]
#
# Everything runs as your user. Only the firewall rule needs Administrator: if
# this PowerShell is not elevated, one UAC prompt asks for that step. Declining
# it prints the command to run later. -DryRun changes nothing and never prompts.
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Bootstrap = Join-Path $Repo "scripts\fleet_bootstrap.py"
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

function Write-Step([string]$Message) { Write-Host "==> $Message" }

# Python 3.11+: `py -3` first, then `python`. Returns the full interpreter path or $null.
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
            $answer = (& $found.Source @probe 2>$null) -join ""
            if ($LASTEXITCODE -ne 0 -or $answer.Trim() -ne "True") { continue }
            $where = @($c.Pre) + @("-c", "import sys; print(sys.executable)")
            $exePath = ((& $found.Source @where 2>$null) -join "").Trim()
            if ($exePath) { return $exePath }
        }
    } finally {
        $ErrorActionPreference = $previous
    }
    return $null
}

Write-Step "Checking Python 3.11+"
$Python = Find-Python
if (-not $Python) {
    throw "Python 3.11 or newer not found. Install it from https://www.python.org/downloads/ and retry."
}
Write-Host "    $Python"

# Non-elevated run does everything except the firewall. Elevated runs do it all.
$SkipFirewall = (-not $IsAdmin) -and (-not $DryRun)
$BootstrapArgs = @($Bootstrap)
if ($DryRun) { $BootstrapArgs += "--dry-run" }
if ($SkipFirewall) { $BootstrapArgs += "--skip-firewall" }

Write-Step "Jarvis fleet bootstrap"
& $Python $BootstrapArgs
if ($LASTEXITCODE -ne 0) {
    throw "fleet_bootstrap.py stopped (exit $LASTEXITCODE). Fix the FAIL line above and run this again."
}

if ($SkipFirewall) {
    Write-Step "Firewall rule (Administrator: one UAC prompt)"
    $ElevatedArgs = '"' + $Python + '" "' + $Bootstrap + '" --firewall-only'
    try {
        $proc = Start-Process -FilePath $Python -ArgumentList $ElevatedArgs -Verb RunAs -Wait -PassThru
        if ($proc.ExitCode -eq 0) {
            Write-Host "    firewall rule applied"
        } else {
            Write-Host "    WARN firewall step exited $($proc.ExitCode); see the lines it printed"
        }
    } catch {
        Write-Host "    WARN elevation declined or failed. Later, in an Administrator PowerShell, run:"
        Write-Host "    `"$Python`" `"$Bootstrap`" --firewall-only"
    }
}

Write-Host ""
if ($DryRun) { Write-Host "Dry run: nothing was changed." } else { Write-Host "Install finished. Next steps are printed above." }
