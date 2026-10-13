$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $projectRoot "backend")
conda run --name resolveai python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
