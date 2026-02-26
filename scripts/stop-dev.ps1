$ErrorActionPreference = "Continue"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$logDir = Join-Path $projectRoot "logs"

$pidFiles = @(
    (Join-Path $logDir "dev-web.pid"),
    (Join-Path $logDir "dev-worker.pid")
)

foreach ($pidFile in $pidFiles) {
    if (-not (Test-Path $pidFile)) {
        continue
    }

    try {
        $targetPid = Get-Content $pidFile | Select-Object -First 1
        if ($targetPid) {
            Stop-Process -Id $targetPid -Force -ErrorAction Stop
            Write-Host "[stop-dev] Stopped PID $targetPid"
        }
    } catch {
        Write-Host "[stop-dev] PID from $pidFile is not running."
    } finally {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[stop-dev] Done."
