$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "[1/4] Backend unit, integration, and end-to-end tests"
Push-Location (Join-Path $projectRoot "backend")
try {
    conda run --name resolveai python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }

    Write-Host "[2/4] 60-case Agent evaluation"
    conda run --name resolveai python -m evaluation.run --fail-under 90
    if ($LASTEXITCODE -ne 0) { throw "Offline regression failed." }
}
finally {
    Pop-Location
}

Write-Host "[3/4] Frontend lint"
npm run lint --prefix (Join-Path $projectRoot "frontend")
if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }

Write-Host "[4/4] Frontend production build"
npm run build --prefix (Join-Path $projectRoot "frontend")
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

Write-Host "ResolveAI verification passed."
