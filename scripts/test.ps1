$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location $taskRoot
docker compose up -d --wait db
if ($LASTEXITCODE -ne 0) { throw 'Database unavailable.' }
$taskExists = docker compose exec -T db psql -U weijin -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='weijin_test'"
if (([string]($taskExists -join '')).Trim() -ne '1') {
    docker compose exec -T db createdb -U weijin weijin_test
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create test database.' }
}
$taskPreviousDb = $env:DATABASE_URL
$taskPreviousProvider = $env:MODEL_PROVIDER
try {
    $env:MODEL_PROVIDER = 'mock'
    $env:DATABASE_URL = 'postgresql+psycopg://weijin:local-development-only@127.0.0.1:55438/weijin_test'
    Set-Location (Join-Path $taskRoot 'backend')
    & '..\.venv\Scripts\python.exe' -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Test migration failed.' }
    & '..\.venv\Scripts\ruff.exe' check app tests migrations
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed.' }
    & '..\.venv\Scripts\python.exe' -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
} finally {
    $env:DATABASE_URL = $taskPreviousDb
    $env:MODEL_PROVIDER = $taskPreviousProvider
    Set-Location $taskRoot
}
