[CmdletBinding()]
param(
    [switch]$WithPostgres
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeArgs = @('-f', (Join-Path $root 'docker-compose.yml'))
if ($WithPostgres) {
    $composeArgs += @('-f', (Join-Path $root 'deploy\docker-compose.postgres.yml'))
}

Push-Location $root
try {
    # Intentionally use stop, never down -v: all databases and run state remain intact.
    & docker compose @composeArgs stop --timeout 120
    if ($LASTEXITCODE -ne 0) {
        throw "Stop failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
