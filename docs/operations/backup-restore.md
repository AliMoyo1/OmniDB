# Backup and restore runbook

Implements plan section 17. Encrypted logical backups, kept off-device, with a tested
restore. A backup is not trusted until a restore has been proven.

## Setup

Create the backup passphrase (kept in separate custody from the data and the server):

    openssl rand -base64 48 > deploy/secrets/backup_passphrase

## Take a backup

    bash scripts/backup.sh

Writes `backups/ciphercontact-<timestamp>.dump.gpg`, appends a line to
`backups/manifest.jsonl` (filename, sha256, size, time), and verifies the archive
decrypts. Then copy the backup and the passphrase to separate, off-device custody.

## Restore

    bash scripts/restore.sh backups/ciphercontact-<timestamp>.dump.gpg ciphercontact_restore

## Test a restore (monthly, and before high-risk releases)

    bash scripts/restore-test.sh

Restores the latest backup into a throwaway database, checks that tables exist, then
drops it. Record the result and the actual recovery time.

## Recovery objectives (candidate, confirm before pilot)

- Recovery point objective: at most one hour of committed work lost.
- Recovery time objective: core service restored within four hours.

## Custody and rotation

- Keep at least one recovery copy protected from ordinary production credentials.
- Store the passphrase separately from the backup and the server.
- Rotate backups per the approved retention schedule so expired data ages out.
- Alert on missed, incomplete, undersized, or unverified backups (added with monitoring).

## Scheduling

Run `scripts/backup.sh` from cron on the host, for example nightly. Backup verification
and freshness alerting are wired with the monitoring step. Do not keep raw campaign
uploads in backups longer than their approved retention (D-09, ADR-020).
