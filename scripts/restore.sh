#!/usr/bin/env bash
set -euo pipefail
# Restore an encrypted backup into a target database.
# Usage: bash scripts/restore.sh <backup-file.gpg> [target_db]

BACKUP_FILE="${1:?usage: restore.sh <backup-file.gpg> [target_db]}"
TARGET_DB="${2:-ciphercontact_restore}"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-deploy/secrets/backup_passphrase}"
DB_USER="${DB_USER:-ciphercontact}"

echo "Restoring $BACKUP_FILE into database $TARGET_DB"
docker compose exec -T postgres createdb -U "$DB_USER" "$TARGET_DB" 2>/dev/null || true
gpg --batch --yes --quiet --decrypt --passphrase-file "$PASSPHRASE_FILE" "$BACKUP_FILE" \
  | docker compose exec -T postgres pg_restore -U "$DB_USER" -d "$TARGET_DB" --no-owner --clean --if-exists
echo "Restore complete into $TARGET_DB"
