[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$logDirectory = Join-Path $runtimeDirectory 'logs'
$configPath = Join-Path $runtimeDirectory 'native-runtime.json'
$statePath = Join-Path $runtimeDirectory 'native-state.json'
$stopPath = Join-Path $runtimeDirectory 'stop.requested'
$supervisorLog = Join-Path $logDirectory 'supervisor.log'

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-SupervisorLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = '{0:o} {1}' -f [DateTimeOffset]::Now, $Message
    Add-Content -LiteralPath $supervisorLog -Value $line -Encoding UTF8
}

function Test-ManagedProcess {
    param([AllowNull()][System.Diagnostics.Process]$Process)
    if ($null -eq $Process) {
        return $false
    }
    try {
        return -not $Process.HasExited
    }
    catch {
        return $false
    }
}

function Get-ManagedListener {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$CommandPattern
    )
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) {
        return $null
    }
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) {
        throw "Cannot adopt ${Name}: port $Port has multiple listener owners."
    }
    $processId = [int]$owners[0]
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction Stop
    if ([string]$processInfo.CommandLine -notlike $CommandPattern) {
        throw "Cannot adopt ${Name} PID ${processId}: the command line is not managed by Novel Agent OS."
    }
    $process = [System.Diagnostics.Process]::GetProcessById($processId)
    Write-SupervisorLog "$Name PID $processId adopted after supervisor recovery."
    return $process
}

function Remove-OldProcessLogs {
    param([Parameter(Mandatory = $true)][string]$Name)
    Get-ChildItem -LiteralPath $logDirectory -Filter "$Name-*.log" -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -Skip 20 |
        Remove-Item -Force
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    Remove-OldProcessLogs -Name $Name
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdout = Join-Path $logDirectory "$Name-$stamp.out.log"
    $stderr = Join-Path $logDirectory "$Name-$stamp.err.log"
    $process = Start-Process `
        -FilePath $Executable `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Write-SupervisorLog "$Name started with PID $($process.Id)."
    return $process
}

function Stop-ManagedProcess {
    param(
        [AllowNull()][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not (Test-ManagedProcess -Process $Process)) {
        return
    }
    try {
        Stop-Process -Id $Process.Id -Force -ErrorAction Stop
        [void]$Process.WaitForExit(10000)
        Write-SupervisorLog "$Name PID $($Process.Id) stopped."
    }
    catch {
        Write-SupervisorLog "Unable to stop $Name PID $($Process.Id): $($_.Exception.Message)"
    }
}

function Test-HttpEndpoint {
    param([Parameter(Mandatory = $true)][string]$Uri)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Write-RuntimeState {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [AllowNull()][System.Diagnostics.Process]$Backend,
        [AllowNull()][System.Diagnostics.Process]$Frontend,
        [bool]$BackendHealthy,
        [bool]$FrontendHealthy,
        [Parameter(Mandatory = $true)][DateTimeOffset]$StartedAt
    )
    $backendPid = $null
    $frontendPid = $null
    if (Test-ManagedProcess -Process $Backend) {
        $backendPid = $Backend.Id
    }
    if (Test-ManagedProcess -Process $Frontend) {
        $frontendPid = $Frontend.Id
    }
    $state = [ordered]@{
        status = $Status
        supervisor_pid = $PID
        backend_pid = $backendPid
        frontend_pid = $frontendPid
        backend_healthy = $BackendHealthy
        frontend_healthy = $FrontendHealthy
        started_at = $StartedAt.ToString('o')
        updated_at = [DateTimeOffset]::Now.ToString('o')
    }
    $json = $state | ConvertTo-Json
    [System.IO.File]::WriteAllText($statePath, $json, [System.Text.UTF8Encoding]::new($false))
}

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Native runtime config not found: $configPath. Run start-native-24h.ps1 first."
}

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$python = [string]$config.python
$node = [string]$config.node
$backendPort = [int]$config.backend_port
$frontendPort = [int]$config.frontend_port
$healthInterval = [Math]::Max(5, [int]$config.health_interval_seconds)
$failureThreshold = [Math]::Max(2, [int]$config.failure_threshold)
$viteScript = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'

foreach ($requiredFile in @($python, $node, $viteScript)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required runtime file is missing: $requiredFile"
    }
}

$mutex = [System.Threading.Mutex]::new($false, 'Local\NovelAgentOS-24H-Supervisor')
$ownsMutex = $false
try {
    try {
        $ownsMutex = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }
    if (-not $ownsMutex) {
        Write-SupervisorLog 'A native supervisor is already running; duplicate launch ignored.'
        exit 0
    }

    if (Test-Path -LiteralPath $stopPath) {
        Remove-Item -LiteralPath $stopPath -Force
    }

    $env:PYTHONUTF8 = '1'
    # If only the supervisor died, its children can still be healthy.  Adopt
    # verified listeners so Task Scheduler recovery never creates duplicates.
    $backend = Get-ManagedListener `
        -Port $backendPort `
        -Name 'backend' `
        -CommandPattern '*uvicorn app.main:app*'
    $frontend = Get-ManagedListener `
        -Port $frontendPort `
        -Name 'frontend' `
        -CommandPattern '*vite.js*preview*'
    $backendStartedAt = [DateTimeOffset]::Now
    $frontendStartedAt = [DateTimeOffset]::Now
    $backendFailures = 0
    $frontendFailures = 0
    $startedAt = [DateTimeOffset]::Now
    $exitCode = 0

    Write-SupervisorLog 'Native 24H supervisor started.'
    try {
        while (-not (Test-Path -LiteralPath $stopPath)) {
            if (-not (Test-ManagedProcess -Process $backend)) {
                $backend = Start-ManagedProcess `
                    -Name 'backend' `
                    -Executable $python `
                    -Arguments "-m uvicorn app.main:app --host 127.0.0.1 --port $backendPort" `
                    -WorkingDirectory (Join-Path $root 'backend')
                $backendStartedAt = [DateTimeOffset]::Now
                $backendFailures = 0
            }
            if (-not (Test-ManagedProcess -Process $frontend)) {
                $quotedViteScript = '"{0}"' -f $viteScript
                $frontend = Start-ManagedProcess `
                    -Name 'frontend' `
                    -Executable $node `
                    -Arguments "$quotedViteScript preview --host 127.0.0.1 --port $frontendPort --strictPort" `
                    -WorkingDirectory (Join-Path $root 'frontend')
                $frontendStartedAt = [DateTimeOffset]::Now
                $frontendFailures = 0
            }

            $backendHealthy = Test-HttpEndpoint -Uri "http://127.0.0.1:$backendPort/health"
            $frontendHealthy = Test-HttpEndpoint -Uri "http://127.0.0.1:$frontendPort/"

            if ($backendHealthy) {
                $backendFailures = 0
            }
            elseif (([DateTimeOffset]::Now - $backendStartedAt).TotalSeconds -ge 45) {
                $backendFailures += 1
            }
            if ($frontendHealthy) {
                $frontendFailures = 0
            }
            elseif (([DateTimeOffset]::Now - $frontendStartedAt).TotalSeconds -ge 20) {
                $frontendFailures += 1
            }

            if ($backendFailures -ge $failureThreshold) {
                Write-SupervisorLog "Backend failed $backendFailures consecutive health probes; restarting."
                Stop-ManagedProcess -Process $backend -Name 'backend'
                $backend = $null
                $backendFailures = 0
            }
            if ($frontendFailures -ge $failureThreshold) {
                Write-SupervisorLog "Frontend failed $frontendFailures consecutive health probes; restarting."
                Stop-ManagedProcess -Process $frontend -Name 'frontend'
                $frontend = $null
                $frontendFailures = 0
            }

            Write-RuntimeState `
                -Status 'running' `
                -Backend $backend `
                -Frontend $frontend `
                -BackendHealthy $backendHealthy `
                -FrontendHealthy $frontendHealthy `
                -StartedAt $startedAt
            Start-Sleep -Seconds $healthInterval
        }
    }
    catch {
        $exitCode = 1
        Write-SupervisorLog "Supervisor failure: $($_.Exception.ToString())"
    }
    finally {
        Stop-ManagedProcess -Process $frontend -Name 'frontend'
        Stop-ManagedProcess -Process $backend -Name 'backend'
        Write-RuntimeState `
            -Status 'stopped' `
            -Backend $null `
            -Frontend $null `
            -BackendHealthy $false `
            -FrontendHealthy $false `
            -StartedAt $startedAt
        Write-SupervisorLog "Native 24H supervisor stopped with exit code $exitCode."
    }
    exit $exitCode
}
finally {
    if ($ownsMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
