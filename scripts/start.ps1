[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$NoBrowser,
    [switch]$CheckOnly,
    [ValidateRange(30, 1800)]
    [int]$StartupTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose.yml"
$exitCode = 0
$step = "initialization"

function Find-Docker {
    $command = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\resources\bin\docker.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "Docker CLI was not found. Install Docker Desktop, then run this script again."
}

function Find-DockerDesktop {
    param([string]$DockerPath)

    $installRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $DockerPath))
    $candidates = @(
        (Join-Path $installRoot "Docker Desktop.exe"),
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "Docker Desktop was not found. Start your Docker engine manually and run this script again."
}

function Test-DockerReady {
    param([string]$DockerPath)

    # A bounded probe avoids hanging while Docker Desktop is starting.
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $DockerPath
    $process.StartInfo.Arguments = 'info --format "{{.ServerVersion}}"'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        $null = $process.Start()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(5000)) {
            $process.Kill()
            return $false
        }
        $output = $outputTask.GetAwaiter().GetResult()
        $null = $errorTask.GetAwaiter().GetResult()
        return ($process.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($output))
    }
    finally {
        $process.Dispose()
    }
}

function Invoke-Docker {
    param([string[]]$DockerArguments)

    # Windows PowerShell 5.1 can treat native stderr as a terminating error.
    # Docker prints normal build progress there, so inspect its exit code instead.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $script:dockerExe @DockerArguments
        $nativeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "Docker command failed (exit $nativeExitCode): docker $($DockerArguments -join ' ')"
    }
}

function Assert-ServicePorts {
    $command = Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue
    if (-not $command) {
        return
    }
    $allowedOwners = @("com.docker.backend", "wslrelay", "wslhost", "docker-proxy", "vpnkit", "System")
    $connections = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)
    $conflicts = @()
    foreach ($connection in $connections) {
        if ($connection.LocalPort -notin @(3000, 8000, 8001, 16686, 4317, 4318)) {
            continue
        }
        $owner = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
        if (-not $owner -or $owner.ProcessName -notin $allowedOwners) {
            $ownerName = if ($owner) { $owner.ProcessName } else { "unknown process" }
            $conflicts += "$($connection.LocalAddress):$($connection.LocalPort) ($ownerName, PID $($connection.OwningProcess))"
        }
    }
    if ($conflicts.Count -gt 0) {
        throw ("Project ports are in use by another application: " + ($conflicts -join "; ") +
            ". Stop the existing FastAPI/MCP/frontend run configurations in PyCharm, or the other application, then retry.")
    }
}

function Wait-ForApplication {
    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $lastProblem = "The application is still starting."
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 5
            if ($health.app -ne "ResolveAI" -or -not $health.database_ready) {
                throw "The backend has not reported a ready ResolveAI database."
            }
            $queue = [string]$health.checks.queue
            if ($queue -ne "disabled" -and $queue -notmatch '^ready:[1-9][0-9]*-worker$') {
                throw "Redis/Worker is not ready: $queue"
            }
            $frontend = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:3000" -TimeoutSec 5
            if ($frontend.StatusCode -eq 200) {
                return $health
            }
        }
        catch {
            $lastProblem = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "Application readiness timed out after $StartupTimeoutSeconds seconds. $lastProblem"
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
        throw "docker-compose.yml was not found in $projectRoot"
    }

    $step = "Docker detection"
    Write-Host "[1/4] Checking Docker Desktop and Docker Compose..."
    $script:dockerExe = Find-Docker
    Invoke-Docker -DockerArguments @("compose", "version")

    if ($CheckOnly) {
        if (-not (Test-DockerReady -DockerPath $script:dockerExe)) {
            throw "Docker engine is not running. Start Docker Desktop or run without -CheckOnly."
        }
        # Quiet validation avoids displaying credentials from the resolved Compose file.
        Invoke-Docker -DockerArguments @("compose", "-f", $composeFile, "config", "--quiet")
        Write-Host "Docker engine and Compose configuration are ready. No services were changed." -ForegroundColor Green
    }
    else {
        if (-not (Test-DockerReady -DockerPath $script:dockerExe)) {
            Write-Host "Starting Docker Desktop in the background..."
            $desktopExe = Find-DockerDesktop -DockerPath $script:dockerExe
            Start-Process -FilePath $desktopExe -WindowStyle Hidden
            $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
            $ready = $false
            while ([DateTime]::UtcNow -lt $deadline) {
                if (Test-DockerReady -DockerPath $script:dockerExe) {
                    $ready = $true
                    break
                }
                Start-Sleep -Seconds 2
            }
            if (-not $ready) {
                throw "Docker engine did not become ready. Check the Docker Desktop status."
            }
        }

        $step = "service port checks"
        Assert-ServicePorts
        $step = "Compose startup"
        & (Join-Path $PSScriptRoot "bootstrap-auth.ps1")
        Write-Host "[2/4] Starting PostgreSQL, Redis, MCP, Worker, backend, frontend and Jaeger..."
        if (Test-Path -LiteralPath (Join-Path $projectRoot ".env")) {
            Write-Host "Using the existing project-root .env."
        }
        else {
            Write-Host "No project-root .env found; using Compose defaults (Demo unless overridden by your environment)."
        }
        Invoke-Docker -DockerArguments @("compose", "-f", $composeFile, "config", "--quiet")
        $step = "pre-migration database backup"
        & (Join-Path $PSScriptRoot "backup-database.ps1") -DockerExecutable $script:dockerExe
        $step = "Compose startup"
        $embeddingHostPath = Join-Path $projectRoot ".build\embedding-models"
        $embeddingManifest = Join-Path $embeddingHostPath "resolveai-model-manifest.json"
        if (-not (Test-Path -LiteralPath $embeddingManifest -PathType Leaf)) {
            Write-Host "Preparing the CPU Chinese embedding model (one-time download)..."
            New-Item -ItemType Directory -Path $embeddingHostPath -Force | Out-Null
            Invoke-Docker -DockerArguments @("compose", "-f", $composeFile, "build", "backend")
            $embeddingMount = $embeddingHostPath + ":/app/embedding-models:rw"
            Invoke-Docker -DockerArguments @("compose", "-f", $composeFile, "run", "--rm", "--no-deps", "-T", "-v", $embeddingMount, "backend", "python", "-m", "scripts.prepare_embeddings", "--cache-dir", "/app/embedding-models")
        }
        $composeArguments = @("compose", "-f", $composeFile, "up", "-d")
        if (-not $SkipBuild) {
            $composeArguments += "--build"
        }
        Invoke-Docker -DockerArguments $composeArguments

        $step = "application readiness"
        Write-Host "[3/4] Waiting for the database, Worker, backend and frontend..."
        $health = Wait-ForApplication
        Invoke-Docker -DockerArguments @("compose", "-f", $composeFile, "ps")
        Write-Host "Model mode: $($health.agent_mode) | Database: $($health.database) | Queue: $($health.checks.queue)"

        $step = "browser launch"
        Write-Host "[4/4] ResolveAI is ready." -ForegroundColor Green
        Write-Host "Support console: http://localhost:3000"
        Write-Host "Customer portal: http://localhost:3000/customer"
        Write-Host "Login credentials: private project-root .env.auth; customers can register on the login page."
        Write-Host "Swagger:         http://localhost:8000/docs"
        Write-Host "Jaeger:          http://localhost:16686"
        Write-Host "To stop services and keep your data: docker compose stop"
        if (-not $NoBrowser) {
            try {
                Start-Process "http://localhost:3000"
                Start-Process "http://localhost:3000/customer"
            }
            catch {
                Write-Warning "Services are ready, but the browser could not open automatically. Open the URLs above."
            }
        }
    }
}
catch {
    $exitCode = 1
    Write-Host "Startup failed during ${step}: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "For container errors, run these commands from $projectRoot :"
    Write-Host "  docker compose ps -a"
    Write-Host "  docker compose logs --tail 80 backend worker mcp init-db"
    Write-Host "If a port is already in use, stop your existing local dev server before retrying."
}
finally {
    Pop-Location
}
exit $exitCode
