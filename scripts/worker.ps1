$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location (Join-Path $taskRoot 'backend')
& (Join-Path $taskRoot '.venv\Scripts\python.exe') -m app.worker
