# Roobico production deploy script
# Usage: powershell -ExecutionPolicy Bypass -File .\deploy\deploy_prod.ps1

$ErrorActionPreference = "Stop"

$SshKey  = "C:\Users\User\Desktop\roobico\id_ed25519"
$SshHost = "root@198.199.122.49"
$AppDir  = "/home/deploy/Roobico"
$Branch  = "production"
$Service = "roobico.service"

function Invoke-Ssh($cmd) {
    ssh -i $SshKey -o StrictHostKeyChecking=no $SshHost $cmd
    if ($LASTEXITCODE -ne 0) { throw "SSH command failed: $cmd" }
}

Write-Host ">>> Pulling $Branch on server..." -ForegroundColor Cyan
Invoke-Ssh "cd $AppDir && sudo -u deploy git fetch --all --prune && sudo -u deploy git checkout $Branch && sudo -u deploy git reset --hard origin/$Branch"

Write-Host ">>> Installing requirements..." -ForegroundColor Cyan
Invoke-Ssh "cd $AppDir && sudo -u deploy ./venv/bin/pip install --upgrade pip >/dev/null && sudo -u deploy ./venv/bin/pip install -r requirements.txt"

Write-Host ">>> Clearing Python bytecode cache..." -ForegroundColor Cyan
Invoke-Ssh "cd $AppDir && sudo -u deploy find . -path ./venv -prune -o -type d -name __pycache__ -exec rm -rf {} + ; true"

Write-Host ">>> Restarting $Service..." -ForegroundColor Cyan
Invoke-Ssh "systemctl restart $Service && sleep 1 && systemctl is-active $Service"

Write-Host ">>> Tail of service log:" -ForegroundColor Cyan
Invoke-Ssh "journalctl -u $Service -n 20 --no-pager"

Write-Host ">>> Deploy done." -ForegroundColor Green
