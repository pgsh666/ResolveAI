$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $projectRoot "backend")
conda run --name resolveai python -m mcp_server.server
