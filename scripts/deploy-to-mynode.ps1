# Copies the XPUB Watch archive to a MyNode and runs the installer there.
# Runs on Windows 10/11 (built-in OpenSSH client) in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File deploy-to-mynode.ps1 [-Archive file.tar.gz] [-Target admin@mynode.local] [-NoBackup]
#
# Defaults: newest xpubwatch-mynode-community-app-*.tar.gz in the current
# folder or next to this script; node admin@mynode.local (or $env:MYNODE_HOST).
# With SHA256SUMS next to the archive the checksum is verified first.
# Windows OpenSSH cannot share connections: the node password is asked up to
# four times (once more by sudo). An SSH key avoids that.
param(
    [string]$Archive,
    [string]$Target = $(if ($env:MYNODE_HOST) { $env:MYNODE_HOST } else { "admin@mynode.local" }),
    [switch]$NoBackup
)
$ErrorActionPreference = "Stop"
$RemoteDir = "xpubwatch-install"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not $Archive) {
    $found = Get-ChildItem -Path (Get-Location), $PSScriptRoot -Filter "xpubwatch-mynode-community-app-*.tar.gz" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $found) { Fail "No xpubwatch-mynode-community-app-*.tar.gz found. Use -Archive <file>." }
    $Archive = $found.FullName
}
if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) { Fail "Archive not found: $Archive" }
$Archive = (Resolve-Path -LiteralPath $Archive).Path
$Name = Split-Path $Archive -Leaf
$Dir = Split-Path $Archive -Parent
if ($Name -notmatch '^[A-Za-z0-9._-]+$') { Fail "Unexpected characters in file name: $Name" }

$Sums = Join-Path $Dir "SHA256SUMS"
if (Test-Path -LiteralPath $Sums) {
    $expected = $null
    foreach ($line in Get-Content -LiteralPath $Sums) {
        $parts = $line.Trim() -split '\s+'
        if ($parts.Count -ge 2 -and $parts[1].TrimStart('*') -eq $Name) { $expected = $parts[0].ToLower() }
    }
    if ($expected) {
        $actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $expected) { Fail "Checksum mismatch for $Name (expected $expected, got $actual)" }
        Write-Host "==> Checksum OK"
    } else { Write-Host "==> SHA256SUMS has no entry for $Name, checksum not verified" }
} else { Write-Host "==> No SHA256SUMS next to the archive, checksum not verified" }

$InstallerArgs = if ($NoBackup) { " --no-backup" } else { "" }

Write-Host "==> Connecting to $Target"
ssh $Target "rm -rf ~/$RemoteDir && mkdir -p ~/$RemoteDir"
if ($LASTEXITCODE -ne 0) { Fail "SSH connection to $Target failed" }

Write-Host "==> Copying $Name"
Push-Location $Dir
try { scp $Name "${Target}:$RemoteDir/$Name" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Fail "Copying failed" }

Write-Host "==> Running the installer (sudo asks for the node password)"
ssh -t $Target "cd ~/$RemoteDir && tar xzf $Name && sudo bash xpubwatch/install-on-mynode.sh$InstallerArgs && rm -rf ~/$RemoteDir"
if ($LASTEXITCODE -ne 0) { Fail "Installer failed (exit $LASTEXITCODE). Files kept on the node in ~/$RemoteDir" }
Write-Host "==> Done"
