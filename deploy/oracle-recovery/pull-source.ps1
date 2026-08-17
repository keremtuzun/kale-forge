# Run AFTER recover.sh has been executed on the VM (via OCI Console → Run Command).
# Verifies the restored SSH access and pulls the app-source snapshot back to the laptop.
#
#   .\pull-source.ps1 -VmIp 150.136.151.230
param(
    [string]$VmIp = "150.136.151.230",
    [string]$User = "ubuntu",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\kale-oracle-recovery"
)
$ErrorActionPreference = "Stop"
$sshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10")
$target = "$User@$VmIp"
$dest = Join-Path $PSScriptRoot "recovered"
New-Item -ItemType Directory -Path $dest -Force | Out-Null

Write-Host "1/3 verifying SSH access with the recovery key ..."
$who = & ssh @sshArgs $target "whoami; hostname; docker ps --format '{{.Image}}' 2>/dev/null | head" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "SSH still failing. Has recover.sh been run in the OCI console yet?" -ForegroundColor Yellow
    Write-Host $who
    exit 1
}
Write-Host "    connected: $($who -join ' | ')" -ForegroundColor Green

Write-Host "2/3 pulling the source snapshot + run metadata ..."
foreach ($f in @("kale-app-source.tar.gz", "kale-app-container-inspect.json",
                 "kale-compose-resolved.yml", "kale-docker-ps.txt",
                 "kale-services.txt", "kale-pm2.txt")) {
    & scp @sshArgs "${target}:~/$f" $dest 2>$null
}
$tar = Join-Path $dest "kale-app-source.tar.gz"
if (Test-Path $tar) {
    $mb = [math]::Round((Get-Item $tar).Length / 1MB, 1)
    Write-Host "    got kale-app-source.tar.gz ($mb MB)" -ForegroundColor Green
    Write-Host "3/3 extracting ..."
    $srcDir = Join-Path $dest "app-source"
    New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
    & tar -xzf $tar -C $srcDir
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    extracted to $srcDir" -ForegroundColor Green
        Get-ChildItem $srcDir | Select-Object Name | Format-Table -HideTableHeaders
    }
} else {
    Write-Host "    no tarball retrieved — check recover.sh output in the console" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Recovered files are in: $dest" -ForegroundColor Green
Write-Host "SSH from now on:  ssh -i `"$KeyPath`" $target"
