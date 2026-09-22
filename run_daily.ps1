# One-click daily NSE EOD scan (Windows PowerShell).
#
# Usage:
#   .\run_daily.ps1
#   .\run_daily.ps1 -Date 2026-09-10
#   .\run_daily.ps1 -OfflineFixture
#
# First-time setup (run once):
#   python -m venv .venv
#   .venv\Scripts\Activate.ps1
#   pip install -e .

param(
    [string]$Date,
    [switch]$OfflineFixture,
    [string]$Universe
)

Set-Location -Path $PSScriptRoot

$PythonExe = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
Write-Host "Using $PythonExe"

$scriptArgs = @()
if ($Date) { $scriptArgs += @("--date", $Date) }
if ($OfflineFixture) { $scriptArgs += "--offline-fixture" }
if ($Universe) { $scriptArgs += @("--universe", $Universe) }

& $PythonExe "scripts\run_daily.py" @scriptArgs
$exitCode = $LASTEXITCODE

Write-Host ""
Write-Host "Run finished with exit code $exitCode."
exit $exitCode
