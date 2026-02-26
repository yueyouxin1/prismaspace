param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$SkipMigrate,
    [switch]$NoWatch
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

if (-not (Get-Command poetry -ErrorAction SilentlyContinue)) {
    throw "poetry is not installed or not in PATH."
}

if (-not (Test-Path ".env")) {
    throw "Missing .env in project root. Copy .env.example to .env first."
}

if (-not $SkipMigrate) {
    Write-Host "[start-dev] Running migrations..."
    poetry run alembic upgrade head
}

$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$webOutLog = Join-Path $logDir "dev-web.out.log"
$webErrLog = Join-Path $logDir "dev-web.err.log"
$workerOutLog = Join-Path $logDir "dev-worker.out.log"
$workerErrLog = Join-Path $logDir "dev-worker.err.log"
$webPidFile = Join-Path $logDir "dev-web.pid"
$workerPidFile = Join-Path $logDir "dev-worker.pid"

$webArgs = @(
    "run", "uvicorn", "app.main:app",
    "--host", $Host,
    "--port", $Port.ToString(),
    "--reload"
)

$workerArgs = @("run", "arq", "app.worker.WorkerSettings")
if (-not $NoWatch) {
    $workerArgs += @("--watch", "src")
}

Write-Host "[start-dev] Starting web process..."
$webProc = Start-Process -FilePath "poetry" -ArgumentList $webArgs -PassThru -RedirectStandardOutput $webOutLog -RedirectStandardError $webErrLog

Write-Host "[start-dev] Starting worker process..."
$workerProc = Start-Process -FilePath "poetry" -ArgumentList $workerArgs -PassThru -RedirectStandardOutput $workerOutLog -RedirectStandardError $workerErrLog

Set-Content -Path $webPidFile -Value $webProc.Id
Set-Content -Path $workerPidFile -Value $workerProc.Id

Write-Host ""
Write-Host "Started."
Write-Host "  Web PID: $($webProc.Id)"
Write-Host "  Worker PID: $($workerProc.Id)"
Write-Host ""
Write-Host "Logs:"
Write-Host "  $webOutLog"
Write-Host "  $webErrLog"
Write-Host "  $workerOutLog"
Write-Host "  $workerErrLog"
Write-Host ""
Write-Host "Tail logs:"
Write-Host "  Get-Content -Path $webOutLog -Wait"
Write-Host "  Get-Content -Path $workerOutLog -Wait"
Write-Host ""
Write-Host "Stop:"
Write-Host "  .\\scripts\\stop-dev.ps1"
