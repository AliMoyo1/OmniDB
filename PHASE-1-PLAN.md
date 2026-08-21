# Phase 1 plan: secure platform foundation

All eight steps are BUILT (2026-08-20). Runtime validation (docker compose up, alembic
upgrade, CI) runs in the build/CI environment; locally verified with py_compile, docker
compose config, and shell syntax checks.

## Step 1: tooling, dependencies, config  [done]
## Step 2: container stack  [done]
## Step 3: schema + Alembic baseline  [done]

## Step 4: authentication  [done]
Argon2id passwords, opaque Postgres-authoritative sessions (sliding idle + absolute),
signed session-bound CSRF, TOTP 2FA (encrypted secret), Redis login rate limit, and the
auth API (login, logout, me, reauthenticate, session list/revoke, activation). Fernet
field encryption.

## Step 5: authorization  [done]
Default-deny capability service, effective-dated role resolution, scope coverage,
self-approval guard, session invalidation on privilege change. Admin API: whoami, Super
Admin reset-2fa and reset-password (D-06), audit-events search.

## Step 6: logging + health  [done]
Structured JSON logging with a redaction backstop, request-context and security-header
middleware (CSP, frame-ancestors, Referrer-Policy, Permissions-Policy, nosniff), token-gated
/readyz, public minimal /healthz.

## Step 7: backup + restore  [done]
Encrypted pg_dump backups (gpg AES256), restore, and a restore-test that proves
restorability into a throwaway database. Runbook in docs/operations/backup-restore.md.

## Step 8: CI + tests + release manifest  [done]
GitHub Actions: ruff, mypy (advisory), unit + authorization tests, integration tests with
Postgres and Redis (alembic up plus down/up reversibility), pip-audit (advisory), gitleaks
secret scan, docker build. Tests under tests/. scripts/release-manifest.sh.

## Verification status
- [x] py_compile (app + migrations + tests)
- [x] docker compose config
- [x] shell syntax (bash -n) on all scripts
- [x] CI YAML parses
- [ ] CI green on GitHub Actions specifically (still not observed; local verification below is a strong proxy but not the same thing)
- [x] docker compose up + alembic upgrade head - done 2026-08-21 via Docker Desktop (Linux containers; the physical Linux production host itself is a separate later step). Found and fixed 3 real bugs only visible under live execution: configparser %-escaping of the DB URL, migration revision ids exceeding Alembic's VARCHAR(32) version column, and Celery Beat unable to write its schedule file because /app wasn't chowned to the app user. All fixed and pushed; see BUILD-LOG.md.

## Notes and follow-ups
- Generate the hash-pinned lockfile in the build env (scripts/lock.sh) and switch the
  Dockerfile to install from it.
- mypy and pip-audit are advisory in CI; flip to blocking once green.
- Phase 1 is code-complete. Phase 2 (canonical data model + safe import pipeline) is next.

## Log
- 2026-08-20: completed steps 1 to 8.
