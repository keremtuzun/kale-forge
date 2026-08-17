# Ship the backend to the cloud VM: code + model + config, then run bootstrap remotely.
#
#   .\push-from-laptop.ps1 -VmIp 203.0.113.7
#
# Re-run any time to update the VM (the model is only re-uploaded if missing remotely).
# Prereqs: the VM exists (Ubuntu, ARM or x86), its public key is ~/.ssh/kale-cloud.pub,
# and ports 80/443 are open in the cloud console's security list.
param(
    [Parameter(Mandatory = $true)][string]$VmIp,
    [string]$User = "ubuntu",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\kale-cloud",
    [switch]$SkipModel
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$modelLocal = Join-Path $root "build\gguf\kale-design-v18-q4_k_m.gguf"
$sshTarget = "$User@$VmIp"
$ssh = "ssh"; $scp = "scp"
$sshArgs = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=accept-new")

if (-not (Test-Path $KeyPath)) { throw "SSH key not found at $KeyPath - generate it first (see README.md)" }
if (-not $SkipModel -and -not (Test-Path $modelLocal)) { throw "GGUF not found at $modelLocal" }

# One durable token per install: reused on re-push so Vercel and the VM stay in agreement.
$tokenFile = Join-Path $PSScriptRoot ".token.local"
if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
} else {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = ([Convert]::ToBase64String($bytes) -replace '[/+=]', '').Substring(0, 40)
    [IO.File]::WriteAllText($tokenFile, $token)
    Write-Host "generated API token -> $tokenFile (flip-vercel.ps1 reads it from there)"
}

# Stage a trimmed tree. LF endings for everything bash will read.
$stage = Join-Path $env:TEMP "kale-cloud-stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path "$stage\src", "$stage\models" -Force | Out-Null
foreach ($f in @("Dockerfile.api", "docker-compose.yml", "Caddyfile", "bootstrap.sh", ".env.example")) {
    $text = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $f)) -replace "`r`n", "`n"
    [IO.File]::WriteAllText((Join-Path $stage $f), $text)
}
$envText = "KALE_HOST=kale-infer.$VmIp.nip.io`nKALE_API_TOKEN=$token`n" +
           "KALE_MODEL_VERSION=kale-design-qwen3-4b-v18-cad-repair-50`n" +
           "KALE_LLAMA_THREADS=4`nKALE_LLAMA_CTX=8192`n"
[IO.File]::WriteAllText((Join-Path $stage ".env"), $envText)
robocopy "$root\services\analysis" "$stage\src\services\analysis" /E /XD __pycache__ .pytest_cache /XF *.pyc | Out-Null
robocopy "$root\services\inference" "$stage\src\services\inference" /E /XD __pycache__ .pytest_cache logs /XF *.pyc | Out-Null
robocopy "$root\apps\site\app" "$stage\src\apps\site\app" /E /XD __pycache__ /XF *.pyc | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed" }

Write-Host "uploading code to ${sshTarget}:~/kale ..."
& $ssh @sshArgs $sshTarget "mkdir -p ~/kale/models"
& $scp @sshArgs -r "$stage\Dockerfile.api" "$stage\docker-compose.yml" "$stage\Caddyfile" `
    "$stage\bootstrap.sh" "$stage\.env.example" "$stage\.env" "$stage\src" "${sshTarget}:~/kale/"
if ($LASTEXITCODE -ne 0) { throw "scp code upload failed" }

if (-not $SkipModel) {
    & $ssh @sshArgs $sshTarget "test -f ~/kale/models/kale-design-v18-q4_k_m.gguf"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "uploading the 2.5 GB model (one-time; this is the slow part)..."
        & $scp @sshArgs $modelLocal "${sshTarget}:~/kale/models/"
        if ($LASTEXITCODE -ne 0) { throw "model upload failed" }
    } else {
        Write-Host "model already on the VM - skipping upload"
    }
}

Write-Host "running bootstrap on the VM (installs docker, opens ports, builds, starts)..."
& $ssh @sshArgs $sshTarget "bash ~/kale/bootstrap.sh"
if ($LASTEXITCODE -ne 0) { throw "bootstrap failed - ssh in and check: sudo docker compose logs" }

Write-Host ""
Write-Host "backend is up. Final step, run:" -ForegroundColor Green
Write-Host "  powershell -File `"$PSScriptRoot\flip-vercel.ps1`" -VmHost kale-infer.$VmIp.nip.io"
