$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "[1/4] Creating or updating Conda environment: resolveai"
conda env update --name resolveai --file (Join-Path $projectRoot "environment.yml") --prune

Write-Host "[2/4] Installing Python dependencies"
conda run --name resolveai python -m pip install -r (Join-Path $projectRoot "backend\requirements.txt")

Write-Host "[3/4] Installing frontend dependencies"
npm install --prefix (Join-Path $projectRoot "frontend")

Write-Host "[4/4] Creating local environment files when missing"
$backendEnv = Join-Path $projectRoot "backend\.env"
if (-not (Test-Path -LiteralPath $backendEnv)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "backend\.env.example") -Destination $backendEnv
}
$frontendEnv = Join-Path $projectRoot "frontend\.env.local"
if (-not (Test-Path -LiteralPath $frontendEnv)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\.env.local.example") -Destination $frontendEnv
}

Write-Host "Setup complete. Open $projectRoot in PyCharm and select the resolveai Conda interpreter."
