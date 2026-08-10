# Point the live site at the cloud backend, permanently, and retire the laptop's role.
#
#   .\flip-vercel.ps1 -VmHost kale-infer.203.0.113.7.nip.io
#
# Does four things, in order, verifying each: (1) the cloud API answers with the token,
# (2) Vercel's INFERENCE_URL / INFERENCE_TOKEN / INFERENCE_TIMEOUT_SECONDS are replaced,
# (3) the site is redeployed and kaleai.vercel.app re-aliased (the alias is pinned and
# never follows deploys on its own), (4) the laptop autostart is disabled so a reboot
# cannot re-point production at a fresh tunnel — serve-v18.py auto-publishes, and two
# writers fighting over INFERENCE_URL is exactly what this flip ends.
# Use -KeepLaptopAutostart to skip step 4.
param(
    [Parameter(Mandatory = $true)][string]$VmHost,
    [switch]$KeepLaptopAutostart
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$site = Join-Path $root "apps\site"
$vercelJs = Join-Path $root "build\vercel-cli\node_modules\vercel\dist\index.js"
$tokenFile = Join-Path $PSScriptRoot ".token.local"
if (-not (Test-Path $tokenFile)) { throw "no .token.local - run push-from-laptop.ps1 first" }
$token = (Get-Content $tokenFile -Raw).Trim()
$url = "https://$VmHost"

function Invoke-Vercel { param([string[]]$VercelArgs)
    Push-Location $site
    try { & node $vercelJs @VercelArgs; if ($LASTEXITCODE -ne 0) { throw "vercel $($VercelArgs -join ' ') failed" } }
    finally { Pop-Location }
}
function Set-VercelEnv { param([string]$Name, [string]$Value)
    Push-Location $site
    try {
        & node $vercelJs env rm $Name production --yes 2>$null | Out-Null
        # `type` streams the file byte-for-byte: no trailing newline sneaks into the value.
        $tmp = Join-Path $env:TEMP "kale-envval.txt"
        [IO.File]::WriteAllText($tmp, $Value)
        cmd /c "type `"$tmp`" | node `"$vercelJs`" env add $Name production"
        if ($LASTEXITCODE -ne 0) { throw "env add $Name failed" }
        Remove-Item $tmp -Force
    } finally { Pop-Location }
}

Write-Host "1/4 checking $url/health ..."
$health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 30
if ($health.status -ne "ok") { throw "cloud API not healthy: $($health | ConvertTo-Json -Compress)" }
Write-Host "    provider=$($health.provider) model=$($health.model_version)"
try {
    Invoke-RestMethod -Uri "$url/v1/stats" -TimeoutSec 15 | Out-Null
    throw "SECURITY: /v1/stats answered without the bearer token - is KALE_API_TOKEN set on the VM?"
} catch [System.Net.WebException] {
    Write-Host "    token required without bearer - good"
}

Write-Host "2/4 updating Vercel production env ..."
Set-VercelEnv "INFERENCE_URL" $url
Set-VercelEnv "INFERENCE_TOKEN" $token
Set-VercelEnv "INFERENCE_TIMEOUT_SECONDS" "120"

Write-Host "3/4 deploying the site and moving the pinned alias ..."
Push-Location $site
try {
    $deployOut = & node $vercelJs deploy --prod --yes 2>&1
    if ($LASTEXITCODE -ne 0) { throw "deploy failed: $($deployOut | Select-Object -Last 5)" }
    $deployUrl = ($deployOut | Select-String -Pattern "https://kale-[a-z0-9]+-keremtuzuns-projects\.vercel\.app" -AllMatches |
        ForEach-Object { $_.Matches } | Select-Object -Last 1).Value
    if (-not $deployUrl) { throw "could not find the deployment URL in vercel output" }
    Write-Host "    deployed $deployUrl - verifying ?fs=1 ..."
    $fs = & node $vercelJs curl "$deployUrl/studio?fs=1&prompt=27x27%20swerve%20robot%20with%20hooded%20shooter"
    if (-not ($fs -join "`n").Contains("FeatureScript")) { throw "deployment failed the fs=1 health check - NOT aliasing" }
    & node $vercelJs alias set ($deployUrl -replace "https://", "") kaleai.vercel.app
    if ($LASTEXITCODE -ne 0) { throw "alias set failed" }
} finally { Pop-Location }

if (-not $KeepLaptopAutostart) {
    Write-Host "4/4 retiring the laptop backend ..."
    $auto = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\kale-v18-autostart.cmd"
    if (Test-Path $auto) {
        $parked = Join-Path $PSScriptRoot "kale-v18-autostart.cmd.disabled"
        Move-Item $auto $parked -Force
        Write-Host "    autostart parked at $parked (move it back to undo)"
    }
    # If serve-v18.py is ever run by hand again, it must not hijack INFERENCE_URL.
    setx KALE_AUTO_PUBLISH 0 | Out-Null
    Write-Host "    KALE_AUTO_PUBLISH=0 set for this user"
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -match 'serve-v18\.py|llama-server|cloudflared|uvicorn app\.main:app'
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "    stopped PID $($_.ProcessId) ($($_.Name))"
    }
} else {
    Write-Host "4/4 skipped (laptop autostart kept; it WILL re-point INFERENCE_URL on next boot)"
}

Write-Host ""
Write-Host "done - kaleai.vercel.app now runs against $url" -ForegroundColor Green
Write-Host "smoke test: open the studio signed in and generate; STEP download should work with the laptop off."
