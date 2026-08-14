# Point the live site's app pages at the NEW box, then deploy and move the pinned alias.
#
#   .\cutover-kaleai.ps1                 # cut over to the new VM
#   .\cutover-kaleai.ps1 -Rollback       # put everything back on the old VM
#
# This is kept as a script rather than an edit sitting in the repo so that an ordinary
# `vercel deploy` can never switch live traffic before the user database has been migrated.
# It rewrites the host in apps/site/vercel.json (4 rewrites) and api/oracle.py (ORIGIN),
# verifies the target answers first, then deploys and re-aliases (the alias is pinned and
# does not follow deploys on its own).
param(
    [string]$NewHost = "kaleai-app.150.136.212.31.nip.io",
    [string]$OldHost = "kale.150.136.151.230.nip.io",
    [switch]$Rollback
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$site = Join-Path $root "apps\site"
$vercelJs = Join-Path $root "build\vercel-cli\node_modules\vercel\dist\index.js"

$from = if ($Rollback) { $NewHost } else { $OldHost }
$to   = if ($Rollback) { $OldHost } else { $NewHost }
Write-Host "cutover: $from  ->  $to" -ForegroundColor Cyan

Write-Host "1/4 checking the target is serving the app ..."
$page = Invoke-WebRequest -UseBasicParsing "https://$to/" -TimeoutSec 30
if ($page.StatusCode -ne 200 -or $page.Content -notmatch "Kale\.ai") {
    throw "https://$to/ did not return the Kale.ai app - refusing to cut over"
}
$auth = try { (Invoke-WebRequest -UseBasicParsing "https://$to/api/auth/me" -TimeoutSec 20).StatusCode }
        catch { $_.Exception.Response.StatusCode.value__ }
if ($auth -ne 401) { throw "auth gate on https://$to returned $auth (expected 401) - refusing to cut over" }
Write-Host "    app serving, auth enforced" -ForegroundColor Green

Write-Host "2/4 rewriting host references ..."
foreach ($rel in @("vercel.json", "api\oracle.py")) {
    $p = Join-Path $site $rel
    $text = [IO.File]::ReadAllText($p)
    if ($text -notmatch [regex]::Escape($from)) { Write-Host "    $rel already on $to"; continue }
    [IO.File]::WriteAllText($p, $text.Replace($from, $to))
    Write-Host "    $rel -> $to"
}

Write-Host "3/4 deploying ..."
Push-Location $site
try {
    $out = cmd /c "node ""$vercelJs"" deploy --prod --yes"
    $url = ($out | Select-String -Pattern "https://kale-[a-z0-9]+-keremtuzuns-projects\.vercel\.app" -AllMatches |
            ForEach-Object { $_.Matches } | Select-Object -Last 1).Value
    if (-not $url) { throw "could not find the deployment URL" }
    Write-Host "    $url"
    $fs = cmd /c "node ""$vercelJs"" curl ""$url/studio?fs=1&prompt=27x27%20swerve%20robot%20with%20hooded%20shooter"""
    if (-not (($fs -join "`n").Contains("FeatureScript"))) { throw "deployment failed the studio health check - NOT aliasing" }
    Write-Host "4/4 moving the pinned alias ..."
    cmd /c "node ""$vercelJs"" alias set $($url -replace 'https://','') kaleai.vercel.app"
} finally { Pop-Location }

Write-Host ""
Write-Host "done. verify: https://kaleai.vercel.app/login and /app" -ForegroundColor Green
Write-Host "rollback any time with:  .\cutover-kaleai.ps1 -Rollback"
