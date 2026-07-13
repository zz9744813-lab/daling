[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$statePath = Join-Path $runtimeDirectory 'native-state.json'
$stopPath = Join-Path $runtimeDirectory 'stop.requested'
$taskName = 'NovelAgentOS-24H'

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
New-Item -ItemType File -Path $stopPath -Force | Out-Null

for ($attempt = 0; $attempt -lt 15; $attempt += 1) {
    Start-Sleep -Seconds 2
    if (-not (Test-Path -LiteralPath $statePath)) {
        continue
    }
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($state.status -eq 'stopped') {
            break
        }
    }
    catch {
        # A concurrent state refresh is retried on the next pass.
    }
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName $taskName | Out-Null
}

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in @(
        @{ Name = 'backend'; Pid = $state.backend_pid; Pattern = 'uvicorn*app.main:app' },
        @{ Name = 'frontend'; Pid = $state.frontend_pid; Pattern = 'vite.js*preview' }
    )) {
        if ($null -eq $entry.Pid) {
            continue
        }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.Pid)" -ErrorAction SilentlyContinue
        if ($null -ne $process -and [string]$process.CommandLine -like "*$($entry.Pattern)*") {
            Stop-Process -Id ([int]$entry.Pid) -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host 'Novel Agent OS native 24H mode is stopped and its scheduled task is disabled.'
