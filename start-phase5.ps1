$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"

$python = Join-Path $PSScriptRoot ".venv-research\Scripts\python.exe"
$logDir = Join-Path $PSScriptRoot "data\research\phase5-v1\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("phase5-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

function Invoke-Step {
    param([string]$Name, [string[]]$Arguments)

    "START $Name $(Get-Date)" | Tee-Object -FilePath $log -Append
    & $python @Arguments 2>&1 | Tee-Object -FilePath $log -Append

    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

try {
    # Wait up to 30 minutes for sufficient RAM
    $ready = $false

    for ($i = 0; $i -lt 60; $i++) {
        $os = Get-CimInstance Win32_OperatingSystem
        $available = $os.FreePhysicalMemory / 1048576

        Write-Host ("Available RAM: {0:N2} GiB" -f $available)

        if ($available -ge 2.0) {
            $ready = $true
            break
        }

        Start-Sleep -Seconds 30
    }

    if (-not $ready) {
        throw "RAM did not reach 2 GiB within 30 minutes"
    }

    Invoke-Step "Resource check" @(
        "-c",
        "import json; from pathlib import Path; from trading.research.safety import resources; resources(json.loads(Path('config/phase5-research.json').read_text()))"
    )

    foreach ($step in @("snapshot", "prepare", "run", "report")) {
        Invoke-Step "Phase 5 $step" @(
            "-m", "trading.research.runner", $step
        )
    }

    Write-Host "PHASE 5 COMPLETED SUCCESSFULLY"
}
catch {
    Write-Host "PHASE 5 STOPPED: $_"
    "ERROR: $_" | Out-File $log -Append
}

Write-Host "Log: $log"
