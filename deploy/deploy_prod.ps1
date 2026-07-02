# Roobico production deploy script
# Usage: powershell -ExecutionPolicy Bypass -File .\deploy\deploy_prod.ps1
#
# Flow: fetch/reset -> pip install -> restart -> healthcheck (/healthz via
# gunicorn unix socket). If the healthcheck fails, automatically roll back
# to the previous commit, restart and re-check.
#
# NOTE: keep this file pure ASCII. PowerShell 5.1 reads BOM-less files as
# ANSI, and unicode punctuation (em-dash etc.) breaks string parsing.

$ErrorActionPreference = "Stop"

$SshKey  = "C:\Users\User\Desktop\roobico\id_ed25519"
$SshHost = "root@198.199.122.49"
$AppDir  = "/home/deploy/Roobico"
$Branch  = "production"
$Service = "roobico.service"
$Socket  = "$AppDir/roobico.sock"

function Invoke-Ssh($cmd) {
    ssh -i $SshKey -o StrictHostKeyChecking=no $SshHost $cmd
    if ($LASTEXITCODE -ne 0) { throw "SSH command failed: $cmd" }
}

function Get-Ssh($cmd) {
    $out = ssh -i $SshKey -o StrictHostKeyChecking=no $SshHost $cmd
    if ($LASTEXITCODE -ne 0) { throw "SSH command failed: $cmd" }
    return ($out | Out-String).Trim()
}

function Test-Health {
    # Up to 10 attempts, 2s apart: gunicorn workers need time to boot.
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 2
        $code = ssh -i $SshKey -o StrictHostKeyChecking=no $SshHost `
            "curl -s -o /dev/null -w '%{http_code}' --unix-socket $Socket http://localhost/healthz || true"
        if ("$code".Trim() -eq "200") { return $true }
    }
    return $false
}

function Invoke-DeployAt($ref) {
    Invoke-Ssh "cd $AppDir && sudo -u deploy git fetch --all --prune && sudo -u deploy git checkout $Branch && sudo -u deploy git reset --hard $ref"
    Invoke-Ssh "cd $AppDir && sudo -u deploy ./venv/bin/pip install -r requirements.txt --quiet"
    Invoke-Ssh "cd $AppDir && sudo -u deploy find . -path ./venv -prune -o -type d -name __pycache__ -exec rm -rf {} + ; true"
    Invoke-Ssh "systemctl restart $Service && sleep 1 && systemctl is-active $Service"
}

# --- Remember current commit for rollback ---------------------------------
$PrevSha = Get-Ssh "cd $AppDir && sudo -u deploy git rev-parse HEAD"
Write-Host ">>> Current commit (rollback target): $PrevSha" -ForegroundColor Cyan

# --- Deploy ----------------------------------------------------------------
Write-Host ">>> Deploying origin/$Branch..." -ForegroundColor Cyan
Invoke-DeployAt "origin/$Branch"

# --- Healthcheck -------------------------------------------------------------
Write-Host ">>> Healthcheck /healthz..." -ForegroundColor Cyan
if (Test-Health) {
    $NewSha = Get-Ssh "cd $AppDir && sudo -u deploy git rev-parse --short HEAD"
    Write-Host ">>> Deploy OK ($NewSha)." -ForegroundColor Green
    Invoke-Ssh "journalctl -u $Service -n 10 --no-pager"
    exit 0
}

# --- Rollback ----------------------------------------------------------------
Write-Host ">>> HEALTHCHECK FAILED - rolling back to $PrevSha..." -ForegroundColor Red
Invoke-Ssh "journalctl -u $Service -n 40 --no-pager"
Invoke-DeployAt $PrevSha

if (Test-Health) {
    Write-Host ">>> Rollback OK - previous version is serving." -ForegroundColor Yellow
} else {
    Write-Host ">>> ROLLBACK HEALTHCHECK ALSO FAILED - manual intervention required!" -ForegroundColor Red
    Invoke-Ssh "journalctl -u $Service -n 60 --no-pager"
}
exit 1
