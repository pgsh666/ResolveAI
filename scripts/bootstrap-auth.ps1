[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$taskRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $taskRoot ".env.auth"
if (Test-Path -LiteralPath $target) { Write-Host "Private staff credentials already exist; no passwords changed."; exit 0 }
function New-PrivateSecret {
    $buffer = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    return [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+','-').Replace('/','_')
}
$accounts = @(
    @{username='admin'; display_name='管理员'; role='admin'; password=(New-PrivateSecret)}
)
$accountJson = ConvertTo-Json -InputObject $accounts -Compress
$content = "# PRIVATE: local initial credentials; never upload or paste this file.`nAUTH_BOOTSTRAP_USERS='$accountJson'`nTOOL_APPROVAL_SECRET=$(New-PrivateSecret)`n"
# CreateNew prevents overwriting another startup's credentials.
$stream = [System.IO.File]::Open($target, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try { $data = (New-Object System.Text.UTF8Encoding($false)).GetBytes($content); $stream.Write($data,0,$data.Length) } finally { $stream.Dispose() }
if ($env:OS -eq 'Windows_NT') {
    $taskSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $target /inheritance:r /grant:r "*${taskSid}:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict the private credentials file ACL." }
}
Write-Host "Created private administrator credentials in .env.auth. Open that local file to see the password; do not share it."
