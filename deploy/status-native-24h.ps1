[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$statePath = Join-Path $runtimeDirectory 'native-state.json'
$configPath = Join-Path $runtimeDirectory 'native-runtime.json'
$taskName = 'NovelAgentOS-24H'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$state = $null
$config = $null
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
}
if (Test-Path -LiteralPath $configPath) {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

$backendPort = if ($null -ne $config) { [int]$config.backend_port } else { 8000 }
$frontendPort = if ($null -ne $config) { [int]$config.frontend_port } else { 5173 }
function Test-Endpoint {
    param([string]$Uri)
    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3).StatusCode -eq 200
    }
    catch {
        return $false
    }
}

[ordered]@{
    task_state = if ($null -ne $task) { [string]$task.State } else { 'NotInstalled' }
    task_enabled = if ($null -ne $task) { [bool]$task.Settings.Enabled } else { $false }
    backend_healthy = Test-Endpoint -Uri "http://127.0.0.1:$backendPort/health"
    frontend_healthy = Test-Endpoint -Uri "http://127.0.0.1:$frontendPort/"
    runtime_state = $state
} | ConvertTo-Json -Depth 5
