# Jarvis Fleet uninstall for Windows: removes the listener task and the firewall
# rule (firewall removal needs Administrator; otherwise the bootstrap prints the
# command). Keeps the local loopback token. Thin wrapper around fleet_bootstrap.py.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_fleet.ps1 [-DryRun]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Bootstrap = Join-Path $Repo "scripts\fleet_bootstrap.py"

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

$Python = Find-Python
if (-not $Python) {
    throw "Python 3.11 or newer not found. Install it from https://www.python.org/downloads/ and retry."
}

$BootstrapArgs = @($Bootstrap, "--uninstall")
if ($DryRun) { $BootstrapArgs += "--dry-run" }
& $Python $BootstrapArgs
if ($LASTEXITCODE -ne 0) {
    throw "fleet_bootstrap.py --uninstall stopped (exit $LASTEXITCODE). See the FAIL line above."
}
