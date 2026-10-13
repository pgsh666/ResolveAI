[CmdletBinding()]
param([string]$DockerExecutable = 'docker', [ValidatePattern('^[a-z0-9_-]+$')][string]$ProjectName = '', [switch]$Acceptance)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$composeArgs = @('compose', '-f', (Join-Path $taskRoot 'docker-compose.yml'))
if ($ProjectName) { $composeArgs += @('-p', $ProjectName) }
if ($Acceptance) { $composeArgs += @('-f', (Join-Path $taskRoot 'docker-compose.acceptance.yml')) }
function Invoke-BackupDocker([string[]]$Arguments) {
    & $DockerExecutable @composeArgs @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Database backup failed. Application startup is stopped; existing data is retained.' }
}
Invoke-BackupDocker @('up', '-d', '--wait', '--wait-timeout', '120', 'db')
$count = & $DockerExecutable @composeArgs exec -T db psql -U resolveai -d resolveai -Atc "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect database safely; startup stopped.' }
if ([int]$count -eq 0) { Write-Host 'Fresh database: no existing tables to back up.'; exit 0 }
$directory = Join-Path $taskRoot '.backups'
New-Item -ItemType Directory -Path $directory -Force | Out-Null
if ($env:OS -eq 'Windows_NT') {
    $taskSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $directory /inheritance:r /grant:r "*${taskSid}:(OI)(CI)(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot protect database backup directory.' }
}
$identifier = [Guid]::NewGuid().ToString('N')
$filename = 'resolveai-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + $identifier + '.dump'
$temporary = '/tmp/' + $filename
$destination = Join-Path $directory $filename
if (Test-Path -LiteralPath $destination) { throw 'Backup destination exists; refusing to overwrite it.' }
Invoke-BackupDocker @('exec', '-T', 'db', 'pg_dump', '-U', 'resolveai', '-d', 'resolveai', '-Fc', '-f', $temporary)
Invoke-BackupDocker @('cp', ('db:' + $temporary), $destination)
Invoke-BackupDocker @('exec', '-T', 'db', 'pg_restore', '--list', $temporary) | Out-Null
if (-not (Test-Path -LiteralPath $destination -PathType Leaf) -or (Get-Item -LiteralPath $destination).Length -lt 20) { throw 'Backup file is empty; startup stopped.' }
Write-Host "Private binary database backup created before migration: $destination"
