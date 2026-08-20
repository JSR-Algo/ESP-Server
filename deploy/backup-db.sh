#!/usr/bin/env bash
set -euo pipefail

# TeeBot Database Backup Script
# Usage: ./backup-db.sh [--dry-run]
# Rollback uses the database container's own credentials; never place a password on the host command line.

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "[DRY RUN] No actual backup will be created"
fi

BACKUP_DIR="${TBOT_BACKUP_DIR:-./backups}"
DB_CONTAINER="${TBOT_DB_CONTAINER:-tbot-esp32-server-db}"
# The real schema is tbot_esp32_server (NOT 'tbot'); keep in sync with MYSQL_DATABASE.
DB_NAME="${MYSQL_DATABASE:-tbot_esp32_server}"
DB_USER="${MYSQL_USER:-root}"
RETENTION_DAYS=7

echo "Starting database backup..."

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/tbot_backup_$TIMESTAMP.sql.gz"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would create: $BACKUP_FILE"
    echo "[DRY RUN] Would stream a transaction-safe dump from $DB_CONTAINER to $BACKUP_FILE"
else
    docker exec "$DB_CONTAINER" sh -c '
        password=${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}
        [ -n "$password" ] || { echo "database password is unavailable inside container" >&2; exit 64; }
        exec mysqldump --single-transaction --quick -u"$1" -p"$password" "$2"
    ' sh "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
    echo "Backup created: $BACKUP_FILE"
fi

# Cleanup old backups
if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would delete backups older than $RETENTION_DAYS days"
else
    find "$BACKUP_DIR" -name "tbot_backup_*.sql.gz" -mtime +$RETENTION_DAYS -delete
    echo "Cleaned up backups older than $RETENTION_DAYS days"
fi

echo "Backup complete"
