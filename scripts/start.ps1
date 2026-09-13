param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location $taskRoot
docker compose up -d --wait db
if ($LASTEXITCODE -ne 0) { throw 'Database failed to start.' }
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $taskPython)) { throw 'Create .venv and install uv first; see README.md.' }
Set-Location (Join-Path $taskRoot 'backend')
$env:UV_PROJECT_ENVIRONMENT = '..\.venv'
if (-not $SkipInstall) {
    & '..\.venv\Scripts\uv.exe' sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Dependency sync failed.' }
}
& $taskPython -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
Write-Host 'Start the worker in a separate terminal: .\scripts\worker.ps1'
Write-Host 'Open http://127.0.0.1:8765'
& $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port 8765
