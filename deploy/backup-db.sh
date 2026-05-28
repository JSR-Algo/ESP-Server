#!/usr/bin/env bash
set -euo pipefail

# TeeBot Database Backup Script
# Usage: ./backup-db.sh [--dry-run]
# Rollback: gunzip < backup.sql.gz | docker exec -i tbot-esp32-server-db mysql -uroot -p$MYSQL_ROOT_PASSWORD

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "[DRY RUN] No actual backup will be created"
fi

BACKUP_DIR="${TBOT_BACKUP_DIR:-./backups}"
DB_CONTAINER="${TBOT_DB_CONTAINER:-tbot-esp32-server-db}"
RETENTION_DAYS=7

echo "Starting database backup..."

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/tbot_backup_$TIMESTAMP.sql.gz"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would create: $BACKUP_FILE"
    echo "[DRY RUN] Would run: docker exec $DB_CONTAINER mysqldump --single-transaction --quick -uroot -p\$MYSQL_ROOT_PASSWORD tbot | gzip > $BACKUP_FILE"
else
    docker exec "$DB_CONTAINER" mysqldump --single-transaction --quick -uroot -p"${MYSQL_ROOT_PASSWORD}" tbot | gzip > "$BACKUP_FILE"
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
