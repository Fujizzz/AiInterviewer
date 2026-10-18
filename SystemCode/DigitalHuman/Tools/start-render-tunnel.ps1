param(
    [string]$Server = "47.239.50.129",
    [string]$User = "root",
    [int]$LocalPort = 8888
)
$ErrorActionPreference = "Stop"
if ($Server -notmatch '^[a-zA-Z0-9.-]+$' -or $User -notmatch '^[a-zA-Z0-9_-]+$' -or
    $LocalPort -lt 1 -or $LocalPort -gt 65535) {
    throw "Supply an explicit SSH hostname, username and valid local port."
}
Write-Host "Keep this terminal open. The UE connection stays on 127.0.0.1:$LocalPort and crosses an encrypted SSH tunnel."
Write-Host "Use the server account's normal SSH authentication; no password is saved by this script."
& ssh -N -T -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L "127.0.0.1:${LocalPort}:127.0.0.1:8888" "$User@$Server"
if ($LASTEXITCODE -ne 0) { throw "Rendering tunnel stopped with SSH exit code $LASTEXITCODE." }
