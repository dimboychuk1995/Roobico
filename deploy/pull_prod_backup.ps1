# Roobico: pull the latest PROD Mongo backup from the server to this PC.
#
# Purpose: offsite copy. Server keeps 7 daily dumps in /var/backups/mongo,
# but they live on the same droplet as the database - if the droplet dies,
# both die. This script copies the newest archive to the local disk daily
# (Windows Task Scheduler task "Roobico prod backup pull").
#
# Alerting: if the newest archive on the server is older than $MaxAgeHours,
# or the download fails, the script writes a marker file to the Desktop
# (ROOBICO-BACKUP-PROBLEM.txt), logs the reason and exits 1.
#
# NOTE: keep this file pure ASCII - PowerShell 5.1 misparses BOM-less
# unicode files.

$ErrorActionPreference = "Stop"

$SshKey      = "C:\Users\User\Desktop\roobico\id_ed25519"
$SshHost     = "root@198.199.122.49"
$RemoteDir   = "/var/backups/mongo"
$LocalDir    = "C:\Users\User\Backups\Roobico"
$LogFile     = Join-Path $LocalDir "pull_backup.log"
$AlertFile   = "C:\Users\User\Desktop\ROOBICO-BACKUP-PROBLEM.txt"
$KeepLocal   = 14
$MaxAgeHours = 30   # daily backup at 03:00 UTC -> anything older means cron is broken

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

function Alert($reason) {
    Log ("ALERT: " + $reason)
    $body = @(
        "Roobico PROD backup problem detected at $(Get-Date)."
        ""
        "Reason: $reason"
        ""
        "Check on the server: ssh -i $SshKey $SshHost"
        "  tail -50 /var/log/mongo_backup.log"
        "  ls -lh /var/backups/mongo/"
        ""
        "Local log: $LogFile"
        "Delete this file after resolving."
    ) -join "`r`n"
    Set-Content -Path $AlertFile -Value $body
    exit 1
}

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null

# --- Find the newest archive on the server and check freshness ------------
# Remote command deliberately has NO quotes: PowerShell 5.1 mangles embedded
# double quotes when passing arguments to native ssh. Backup filenames never
# contain spaces, whitespace is the field separator.
$remoteCmd = 'f=$(ls -1t {0}/prod_*.archive.gz 2>/dev/null | head -1); echo $f $(stat -c %Y $f 2>/dev/null) $(stat -c %s $f 2>/dev/null)' -f $RemoteDir
$remoteInfo = ssh -i $SshKey -o StrictHostKeyChecking=no -o ConnectTimeout=20 $SshHost $remoteCmd
if ($LASTEXITCODE -ne 0) { Alert "SSH to server failed (exit $LASTEXITCODE)." }

$parts = -split "$remoteInfo".Trim()
if ($parts.Count -lt 3) { Alert "No prod_*.archive.gz files found in $RemoteDir (got: '$remoteInfo')." }

$remotePath = $parts[0]
$remoteName = Split-Path $remotePath -Leaf
$mtimeUtc   = [DateTimeOffset]::FromUnixTimeSeconds([int64]$parts[1]).UtcDateTime
$remoteSize = [int64]$parts[2]
$ageHours   = ([DateTime]::UtcNow - $mtimeUtc).TotalHours

Log ("Newest on server: {0} ({1:N1} MB, {2:N1}h old)" -f $remoteName, ($remoteSize / 1MB), $ageHours)

if ($ageHours -gt $MaxAgeHours) {
    Alert ("Newest backup on server is {0:N1}h old (limit {1}h) - server cron is likely broken." -f $ageHours, $MaxAgeHours)
}
if ($remoteSize -lt 1MB) {
    Alert ("Newest backup is suspiciously small: {0} bytes." -f $remoteSize)
}

# --- Download if we don't have it yet --------------------------------------
$localPath = Join-Path $LocalDir $remoteName
if (Test-Path $localPath) {
    $localSize = (Get-Item $localPath).Length
    if ($localSize -eq $remoteSize) {
        Log "Already downloaded, nothing to do."
    } else {
        Log "Local copy size mismatch ($localSize vs $remoteSize) - re-downloading."
        Remove-Item $localPath -Force
    }
}
if (-not (Test-Path $localPath)) {
    scp -i $SshKey -o StrictHostKeyChecking=no "${SshHost}:$remotePath" $localPath
    if ($LASTEXITCODE -ne 0) { Alert "scp download failed (exit $LASTEXITCODE)." }
    $gotSize = (Get-Item $localPath).Length
    if ($gotSize -ne $remoteSize) { Alert "Downloaded size $gotSize does not match remote $remoteSize." }
    Log ("Downloaded {0} ({1:N1} MB)." -f $remoteName, ($gotSize / 1MB))
}

# --- Local rotation ---------------------------------------------------------
$old = Get-ChildItem $LocalDir -Filter "prod_*.archive.gz" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $KeepLocal
foreach ($f in $old) {
    Log ("Removing old local backup: " + $f.Name)
    Remove-Item $f.FullName -Force
}

# Clear a stale alert marker if everything is healthy now.
if (Test-Path $AlertFile) {
    Log "Backup healthy again - removing alert marker."
    Remove-Item $AlertFile -Force
}

Log "OK."
exit 0
