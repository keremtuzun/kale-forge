# Background watcher: retry SSH with the recovery key until the Run Command lands the key,
# then automatically pull the app source. Bounded so it can't run forever.
param(
    [string]$VmIp = "150.136.151.230",
    [string]$User = "ubuntu",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\kale-oracle-recovery",
    [int]$MaxMinutes = 30
)
$deadline = (Get-Date).AddMinutes($MaxMinutes)
$sshArgs = @("-i", $KeyPath, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no")
$target = "$User@$VmIp"
$n = 0
while ((Get-Date) -lt $deadline) {
    $n++
    $who = & ssh @sshArgs $target "whoami" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Output "SSH RESTORED after $n tries ($who). Pulling source..."
        & powershell -NoProfile -File (Join-Path $PSScriptRoot "pull-source.ps1") -VmIp $VmIp -User $User -KeyPath $KeyPath
        exit 0
    }
    Start-Sleep -Seconds 20
}
Write-Output "watcher timed out after $MaxMinutes min without SSH access - the Run Command never executed."
exit 1
