#!/usr/bin/env bash
set -euo pipefail
# Encrypted logical backup of the CipherContact database (plan 17).
# Run from the repo root with the stack up. Requires docker compose and gpg.

BACKUP_DIR="${BACKUP_DIR:-./backups}"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-deploy/secrets/backup_passphrase}"
DB_USER="${DB_USER:-ciphercontact}"
DB_NAME="${DB_NAME:-ciphercontact}"

if [ ! -f "$PASSPHRASE_FILE" ]; then
  echo "ERROR: backup passphrase file not found: $PASSPHRASE_FILE" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/ciphercontact-$ts.dump.gpg"

echo "Creating encrypted backup: $out"
docker compose exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc \
  | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "$PASSPHRASE_FILE" -o "$out"

sha="$(sha256sum "$out" | awk '{print $1}')"
size="$(wc -c < "$out")"
printf '{"file":"%s","sha256":"%s","bytes":%s,"created_at":"%s"}\n' \
  "$(basename "$out")" "$sha" "$size" "$ts" >> "$BACKUP_DIR/manifest.jsonl"

# Integrity check: ensure the archive decrypts.
gpg --batch --yes --quiet --decrypt --passphrase-file "$PASSPHRASE_FILE" "$out" > /dev/null
echo "Backup complete and decrypt-verified: $out ($size bytes)"
echo "A backup is only trusted after a full restore test (scripts/restore-test.sh)."
echo "Copy the backup and its passphrase to separate, off-device custody (plan 17.3)."
