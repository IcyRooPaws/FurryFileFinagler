# FurryFileFinagler daily run: replaces fff_daily.ps1.
#
# Same order as the old script (see 'fff daily --help'):
#   fluffle search -> sort workflow -> e621 sync -> sort workflow -> fa sync-external
# Like the old script, a failed stage doesn't stop the run. Unlike it, the exit code is
# non-zero when any stage failed, so Task Scheduler's "Last Run Result" shows failures.
#
# Usage (Task Scheduler passes -scheduled, which is accepted and ignored like before):
#   powershell -NoProfile -ExecutionPolicy Bypass -File fff_daily.ps1 [-scheduled] [-Environment test] [fff daily options]
#   e.g.  .\fff_daily.ps1 -Environment test --dry-run
param(
    [switch]$scheduled,
    [ValidateSet("", "prod", "test")]
    [string]$Environment = "",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$DailyArgs = @()
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-Not (Test-Path $python)) {
    Write-Host "ERROR: virtual environment not found at $python" -ForegroundColor Red
    Write-Host "Create it: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt; .venv\Scripts\python -m pip install -e . --no-deps" -ForegroundColor Gray
    exit 2
}

$fffArgs = @("-m", "fff")
if ($Environment) { $fffArgs += @("--env", $Environment) }
$fffArgs += "daily"
$fffArgs += $DailyArgs

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "FurryFileFinagler daily run" -ForegroundColor Cyan
Write-Host "  $python $($fffArgs -join ' ')" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan

Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$ErrorActionPreference = "Continue"
& $python @fffArgs 2>&1 | ForEach-Object { Write-Host $_ }
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) {
    Write-Host "fff daily finished with failures (exit $code); see logs\daily\" -ForegroundColor Yellow
} else {
    Write-Host "fff daily completed successfully" -ForegroundColor Green
}
exit $code
