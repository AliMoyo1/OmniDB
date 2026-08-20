#!/usr/bin/env bash
set -euo pipefail
# Restore the latest backup into a throwaway database, sanity-check it, then drop it.
# Proves the backup is restorable (plan 17.4). Run at least monthly and before high-risk releases.

BACKUP_DIR="${BACKUP_DIR:-./backups}"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-deploy/secrets/backup_passphrase}"
DB_USER="${DB_USER:-ciphercontact}"
TEST_DB="ciphercontact_restoretest_$(date -u +%s)"

latest="$(ls -1t "$BACKUP_DIR"/ciphercontact-*.dump.gpg 2>/dev/null | head -n1 || true)"
if [ -z "$latest" ]; then
  echo "ERROR: no backups found in $BACKUP_DIR" >&2
  exit 1
fi
echo "Testing restore of: $latest"

docker compose exec -T postgres createdb -U "$DB_USER" "$TEST_DB"
trap 'docker compose exec -T postgres dropdb -U "$DB_USER" --if-exists "$TEST_DB" >/dev/null 2>&1 || true' EXIT

gpg --batch --yes --quiet --decrypt --passphrase-file "$PASSPHRASE_FILE" "$latest" \
  | docker compose exec -T postgres pg_restore -U "$DB_USER" -d "$TEST_DB" --no-owner --clean --if-exists

count="$(docker compose exec -T postgres psql -U "$DB_USER" -d "$TEST_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
count="$(echo "$count" | tr -d '[:space:]')"
echo "Restored table count (public schema): $count"
if [ "${count:-0}" -lt 1 ]; then
  echo "ERROR: restore produced no tables" >&2
  exit 1
fi
echo "Restore test PASSED for $latest"
