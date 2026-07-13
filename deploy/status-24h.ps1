[CmdletBinding()]
param(
    [switch]$WithPostgres,
    [int]$Tail = 80
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeArgs = @('-f', (Join-Path $root 'docker-compose.yml'))
if ($WithPostgres) {
    $composeArgs += @('-f', (Join-Path $root 'deploy\docker-compose.postgres.yml'))
}

Push-Location $root
try {
    & docker compose @composeArgs ps
    & docker compose @composeArgs logs --tail $Tail backend frontend redis
}
finally {
    Pop-Location
}
