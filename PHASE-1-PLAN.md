# Phase 1 plan: secure platform foundation

Steps 1 to 3 are BUILT (2026-08-20). Steps 4 to 8 follow.

## Step 1: tooling, dependencies, config  [done]
- [x] pyproject with constrained deps + hatchling build backend
- [ ] hashed lockfile (run scripts/lock.sh in an env with PyPI access, then commit)
- [x] app/config.py: pydantic-settings, secret-file loading from /run/secrets, computed DATABASE_URL
- [x] .env.example (non-secret config) and deploy/secrets/*.example (+ README)
- [x] ruff + mypy + pytest config

## Step 2: container stack  [done]
- [x] Dockerfile (non-root, slim) and .dockerignore
- [x] compose.yaml: postgres, redis, web, caddy; networks edge/data (data internal); health checks; no published DB or app ports
- [x] deploy/caddy/Caddyfile: LAN HTTPS via Caddy internal CA (SERVER_HOST), HSTS and security headers, reverse_proxy to web
- [ ] worker + scheduler services (added with the background-jobs step)

## Step 3: schema + Alembic baseline  [done]
- [x] app/db.py (sync engine, sessionmaker, get_session)
- [x] app/models: organization, team, user (workforce_id = email username), team_membership, role_assignment, reporting_assignment, delegation, session, audit_event
- [x] alembic.ini + migrations/env.py + 0001_baseline migration
- [x] app/main.py: /healthz (liveness), /readyz (db and redis)

## Verification (this session)
- [x] py_compile of app + migrations: PASS
- [x] docker compose config validate: PASS
- [ ] runtime model/migration load (deps not installed here; run in the build env: docker compose run --rm web alembic upgrade head)
- [ ] docker compose up, port scan, TLS and cookie inspection (build env)

## Next: Step 4 (authentication)
Opaque server-side sessions (PostgreSQL authoritative), CSRF, login/logout, Argon2id,
TOTP 2FA enroll and verify, Super Admin reset. Then Step 5 authorization (default-deny,
scoped helpers, session rotation, self-approval prevention), Step 6 redacted logging and
health hardening, Step 7 encrypted backup and tested restore, Step 8 CI and release manifest.

## Log
- 2026-08-20: completed steps 1 to 3. Syntax and compose validated locally; runtime DB
  checks deferred to the build env. Run instructions in docs/operations/running.md.
