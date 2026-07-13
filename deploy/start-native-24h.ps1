[CmdletBinding()]
param(
    [switch]$SkipBackup,
    [switch]$SkipBuild,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$configPath = Join-Path $runtimeDirectory 'native-runtime.json'
$stopPath = Join-Path $runtimeDirectory 'stop.requested'
$supervisor = Join-Path $PSScriptRoot 'native-supervisor.ps1'
$taskName = 'NovelAgentOS-24H'

function Assert-PortFree {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        $owners = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $Port is already in use by PID(s): $owners. Stop the existing service first."
    }
}

$pythonCommand = Get-Command python -ErrorAction Stop
$nodeCommand = Get-Command node -ErrorAction Stop
$npmCommand = Get-Command npm -ErrorAction Stop
$backendEnv = Join-Path $root 'backend\.env'
$database = Join-Path $root 'backend\data\novel_os.db'

if (-not (Test-Path -LiteralPath $backendEnv -PathType Leaf)) {
    throw 'backend\.env is missing. Configure the Provider before starting native 24H mode.'
}
Assert-PortFree -Port $BackendPort
Assert-PortFree -Port $FrontendPort

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
if (-not $SkipBackup -and (Test-Path -LiteralPath $database -PathType Leaf)) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupDirectory = Join-Path $root "backups\pre-native-$stamp"
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    Copy-Item -LiteralPath $database -Destination (Join-Path $backupDirectory 'novel_os.db')
    Write-Host "SQLite snapshot: $backupDirectory"
}

Push-Location (Join-Path $root 'backend')
try {
    & $pythonCommand.Source -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migration failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

if (-not $SkipBuild) {
    Push-Location (Join-Path $root 'frontend')
    try {
        & $npmCommand.Source run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

$config = [ordered]@{
    python = $pythonCommand.Source
    node = $nodeCommand.Source
    backend_port = $BackendPort
    frontend_port = $FrontendPort
    health_interval_seconds = 10
    failure_threshold = 6
    root = $root
    configured_at = [DateTimeOffset]::Now.ToString('o')
}
$configJson = $config | ConvertTo-Json
[System.IO.File]::WriteAllText($configPath, $configJson, [System.Text.UTF8Encoding]::new($false))
if (Test-Path -LiteralPath $stopPath) {
    Remove-Item -LiteralPath $stopPath -Force
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$actionArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $supervisor
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArguments
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
# Windows records an externally terminated task as 0xFFFFFFFF and does not
# consistently apply RestartCount.  An idempotent minute trigger closes that
# platform gap; IgnoreNew prevents it from spawning a second live supervisor.
$watchdogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($logonTrigger, $watchdogTrigger) `
    -Principal $principal `
    -Settings $settings
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Enable-ScheduledTask -TaskName $taskName | Out-Null
Start-ScheduledTask -TaskName $taskName

$healthy = $false
for ($attempt = 0; $attempt -lt 20; $attempt += 1) {
    Start-Sleep -Seconds 2
    try {
        $backend = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$BackendPort/health" -TimeoutSec 3
        $frontend = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$FrontendPort/" -TimeoutSec 3
        if ($backend.StatusCode -eq 200 -and $frontend.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    }
    catch {
        # The supervisor is still inside its bounded startup window.
    }
}
if (-not $healthy) {
    throw "Native 24H task was registered but did not become healthy. Inspect deploy\runtime\logs."
}

Write-Host 'Novel Agent OS native 24H mode is healthy. Open http://127.0.0.1:5173'
