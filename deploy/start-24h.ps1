[CmdletBinding()]
param(
    [switch]$WithPostgres,
    [switch]$SkipBuild,
    [switch]$SkipBackup
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backendEnv = Join-Path $root 'backend\.env'
$backendEnvExample = Join-Path $root 'backend\.env.example'
$composeFile = Join-Path $root 'docker-compose.yml'
$postgresComposeFile = Join-Path $root 'deploy\docker-compose.postgres.yml'
$secretDirectory = Join-Path $root 'deploy\secrets'
$postgresSecret = Join-Path $secretDirectory 'postgres_password.txt'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker is not installed or is not available in PATH.'
}

& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose v2 is required.'
}

if (-not (Test-Path -LiteralPath $backendEnv)) {
    Copy-Item -LiteralPath $backendEnvExample -Destination $backendEnv
    Write-Warning "Created backend\.env from the example. Configure the Provider before writing."
}

Push-Location $root
try {
    $composeFiles = @('-f', $composeFile)

    if ($WithPostgres) {
        New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null
        if (-not (Test-Path -LiteralPath $postgresSecret)) {
            $existingPgVolumes = @(
                & docker volume ls --filter 'label=com.docker.compose.volume=pgdata' --quiet |
                    Where-Object { $_ }
            )
            if ($LASTEXITCODE -ne 0) {
                throw 'Unable to inspect existing PostgreSQL volumes.'
            }
            if ($existingPgVolumes.Count -gt 0) {
                throw @"
An existing pgdata volume was found, but deploy\secrets\postgres_password.txt is missing.
Create that file with the database's existing password before using -WithPostgres.
No volume or database has been changed.
"@
            }
            $bytes = [byte[]]::new(36)
            $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
            try {
                $rng.GetBytes($bytes)
            }
            finally {
                $rng.Dispose()
            }
            $password = [Convert]::ToBase64String($bytes).Replace('+', 'A').Replace('/', 'B').TrimEnd('=')
            [System.IO.File]::WriteAllText($postgresSecret, $password, [System.Text.UTF8Encoding]::new($false))
            Write-Host 'Generated a local PostgreSQL password secret.'
        }
        $composeFiles += @('-f', $postgresComposeFile)
    }

    if (-not $SkipBackup) {
        $sqliteFile = Join-Path $root 'backend\data\novel_os.db'
        if (Test-Path -LiteralPath $sqliteFile) {
            # Stop only the API so SQLite can be copied consistently. Volumes are never removed.
            & docker compose @composeFiles stop backend 2>$null
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backupDir = Join-Path $root "backups\pre-deploy-$stamp"
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            Copy-Item -LiteralPath $sqliteFile -Destination (Join-Path $backupDir 'novel_os.db')
            Write-Host "SQLite snapshot: $backupDir"
        }
    }

    $upArgs = @('compose') + $composeFiles + @('up', '-d', '--remove-orphans')
    if (-not $SkipBuild) {
        $upArgs += '--build'
    }
    & docker @upArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Deployment failed with exit code $LASTEXITCODE."
    }

    & docker compose @composeFiles ps
    Write-Host 'Novel Agent OS is running. Open http://127.0.0.1:5173'
}
finally {
    Pop-Location
}
