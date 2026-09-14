#!/usr/bin/env bash
# Nightly PROD (27018) → DEVELOP (27017) sync on the Roobico server.
#
# Makes the develop instance an exact copy of production:
#   1. fresh mongodump of prod (directory dump, admin/config excluded — the
#      admin user on dev stays as is);
#   2. every app database on dev that does not exist on prod is dropped
#      (test shops created on dev disappear — dev == prod, by design);
#   3. mongorestore --drop into dev (collections replaced, indexes included);
#   4. sanity check: master_db.tenants/users/shops counts must match.
#
# Install: copy to /usr/local/bin/mongo_sync_dev.sh, chmod 700, root crontab:
#   0 4 * * * /usr/local/bin/mongo_sync_dev.sh >> /var/log/mongo_sync_dev.log 2>&1
# (03:00 UTC is the prod backup — see CLAUDE.md "Бэкапы прода".)
# Memory: the dev mongod runs with wiredTiger cacheSizeGB 0.25 (/etc/mongod.conf)
# and the box has a 1 GB /swapfile — both added 2026-09-14 after the restore
# OOM-killed the dev mongod. Takes ~1 minute for ~90 MB of data.
#
# Credentials are read from /usr/local/bin/mongo_backup.sh (single place for
# the admin password). Both instances use the same admin user.
set -euo pipefail

BACKUP_SCRIPT="/usr/local/bin/mongo_backup.sh"
PROD_PORT=27018
DEV_PORT=27017
AUTH_DB="admin"
WORK_ROOT="/var/backups/mongo/sync_tmp"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

if [[ ! -r "$BACKUP_SCRIPT" ]]; then
  log "ERROR: $BACKUP_SCRIPT not readable — cannot load Mongo credentials"
  exit 1
fi
USER=$(grep -oP '^USER="\K[^"]+' "$BACKUP_SCRIPT")
PASS=$(grep -oP "^PASS='\K[^']+" "$BACKUP_SCRIPT")
if [[ -z "$USER" || -z "$PASS" ]]; then
  log "ERROR: could not parse USER/PASS from $BACKUP_SCRIPT"
  exit 1
fi

# Refuse to run if either port is not what we expect (paranoia: never restore INTO prod).
if [[ "$DEV_PORT" == "$PROD_PORT" ]]; then
  log "ERROR: DEV_PORT equals PROD_PORT"
  exit 1
fi

TS=$(date -u +%Y%m%d_%H%M%S)
DUMP_DIR="$WORK_ROOT/$TS"
mkdir -p "$DUMP_DIR"
trap 'rm -rf "$DUMP_DIR"' EXIT

mongo_eval() {  # mongo_eval <port> <js>
  mongosh --quiet --port "$1" -u "$USER" -p "$PASS" --authenticationDatabase "$AUTH_DB" --eval "$2"
}

log "Sync start: prod:$PROD_PORT -> dev:$DEV_PORT"

# 1. Dump prod (read-only on prod).
mongodump --quiet --port "$PROD_PORT" -u "$USER" -p "$PASS" --authenticationDatabase "$AUTH_DB" \
  --gzip --out "$DUMP_DIR"
rm -rf "$DUMP_DIR/admin" "$DUMP_DIR/config" "$DUMP_DIR/local"
DUMPED_DBS=$(find "$DUMP_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
DB_COUNT=$(echo "$DUMPED_DBS" | grep -c . || true)
if [[ "$DB_COUNT" -lt 1 ]] || ! echo "$DUMPED_DBS" | grep -qx "master_db"; then
  log "ERROR: prod dump looks wrong ($DB_COUNT databases, master_db missing) — dev left untouched"
  exit 1
fi
log "Prod dump: $DB_COUNT databases, $(du -sh "$DUMP_DIR" | cut -f1)"

# 2. Drop dev databases that no longer exist on prod.
DEV_DBS=$(mongo_eval "$DEV_PORT" \
  'db.adminCommand({listDatabases:1}).databases.map(d=>d.name).filter(n=>!["admin","local","config"].includes(n)).forEach(n=>print(n))')
while IFS= read -r name; do
  [[ -z "$name" ]] && continue
  if ! echo "$DUMPED_DBS" | grep -qxF -- "$name"; then
    log "Dropping dev-only database: $name"
    mongo_eval "$DEV_PORT" "db.getSiblingDB($(printf '%s' "$name" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')).dropDatabase()" >/dev/null
  fi
done <<< "$DEV_DBS"

# 3. Restore into dev, replacing collections. Single-threaded on purpose: the
# box has 2 GB RAM for two mongods + gunicorn, and a parallel restore got the
# dev mongod OOM-killed (2026-09-14). Full mongorestore output goes to a file,
# only failures are echoed into the log.
RESTORE_LOG="$DUMP_DIR/restore.log"
if ! mongorestore --port "$DEV_PORT" -u "$USER" -p "$PASS" --authenticationDatabase "$AUTH_DB" \
     --gzip --drop --numParallelCollections 1 --numInsertionWorkersPerCollection 1 \
     --dir "$DUMP_DIR" > "$RESTORE_LOG" 2>&1; then
  log "ERROR: mongorestore failed — last lines:"
  grep -iE "failed|error" "$RESTORE_LOG" | tail -10 || tail -10 "$RESTORE_LOG"
  exit 1
fi

# 4. Sanity check.
COUNTS_JS='const m=db.getSiblingDB("master_db"); print([m.tenants.countDocuments(), m.users.countDocuments(), m.shops.countDocuments()].join("/"))'
PROD_COUNTS=$(mongo_eval "$PROD_PORT" "$COUNTS_JS")
DEV_COUNTS=$(mongo_eval "$DEV_PORT" "$COUNTS_JS")
if [[ "$PROD_COUNTS" != "$DEV_COUNTS" ]]; then
  log "ERROR: master_db counts differ after restore (prod $PROD_COUNTS, dev $DEV_COUNTS)"
  exit 1
fi
log "Done. master_db tenants/users/shops = $DEV_COUNTS on both instances"
