# OmniDB Build Log

Product: CipherContact. Build repository: OmniDB (https://github.com/AliMoyo1/OmniDB).
Authoritative plan: `docs/architecture/CipherContact - Detailed Implementation Plan v0.3.md`.

This file tracks scaffolding and build steps. Update it as work proceeds.

## 2026-08-20: Repository scaffold (Phase 0 and Phase 1 step 1)

Created the repository shell and the Phase 0 governance pack. No application logic yet, in line with the plan gate that Phase 0 decisions D-01 through D-08 must be resolved before Phase 1 feature work begins.

Done:
- [x] Directory structure per plan section 14.2 (app, migrations, tests, deploy, docs, scripts).
- [x] README, pyproject tooling stub, .gitignore, .env.example.
- [x] docs/architecture/overview.md with topology and structure.
- [x] docs/decisions/decision-log.md tracking D-01 through D-23.
- [x] docs/decisions/adr with process, template, index, and seeded ADR-001 and ADR-013.
- [x] docs/PHASE-0-CHECKLIST.md derived from plan section 20.
- [x] Moved the authoritative plan and change log into docs.

Open:
- [x] D-01 product naming: DECIDED as CipherContact (product); build repo remains OmniDB.
- [x] Scaffold pushed to https://github.com/AliMoyo1/OmniDB (main).
- [x] Phase 0 decisions D-01 through D-08 resolved 2026-08-20 (see decision-log.md and ADRs).

## 2026-08-20: Phase 0 decisions resolved

All blocking decisions recorded in docs/decisions/decision-log.md, with ADRs for the significant ones (003 access, 004A auth, 009 DNC, 005C workforce ID, 017 notifications, 020 retention). Architecture changes from the plan: Tailscale removed (LAN-only, IP access, unmanaged laptops); TOTP 2FA local auth; DNC label-and-skip with Team Captain override; 60-day completion-triggered retention with Team Captain Excel export. Target policy, exemptions, and bulk import deferred from the first pilot. Two flags stand: DNC-override legal review (D-02) and the export DLP note (D-09). Phase 1 is now unblocked.

## 2026-08-20: Phase 1 steps 1 to 3 built

Foundation scaffold with real code (no auth or business logic yet). See PHASE-1-PLAN.md.
- Step 1: pyproject (hatchling, constrained deps), app/config.py (pydantic-settings, secret-file loading), .env.example, deploy/secrets templates, ruff/mypy/pytest config.
- Step 2: Dockerfile (non-root), compose.yaml (postgres, redis, web, caddy; data network internal; health checks; only Caddy publishes 80/443), Caddyfile (LAN HTTPS via internal CA).
- Step 3: app/db.py, nine ORM models (organization, team, user, membership, role/reporting/delegation, session, audit), Alembic baseline 0001, app/main.py health endpoints.
- Verified locally: py_compile PASS, docker compose config PASS. Runtime DB migration to be run in the build env. Lockfile (scripts/lock.sh) to be generated where PyPI is reachable.
- Run instructions: docs/operations/running.md.

## 2026-08-20: Phase 1 steps 4 to 8 built

- Step 4 auth: Argon2id, opaque Postgres-authoritative sessions, session-bound CSRF, TOTP 2FA, Redis login rate limit, auth API, Fernet field encryption.
- Step 5 authz: default-deny capability service, effective-dated roles, scope coverage, self-approval guard, session invalidation on privilege change; admin API incl. Super Admin reset (D-06) and audit search.
- Step 6: structured JSON logging + redaction, request-context and security-header middleware, token-gated /readyz.
- Step 7: encrypted gpg backup, restore, and restore-test (proves restorability); runbook.
- Step 8: GitHub Actions CI (ruff, mypy advisory, unit + integration tests with PG/Redis, migration reversibility, pip-audit, gitleaks, docker build), tests/, release-manifest script.
- Verified locally: py_compile, docker compose config, shell bash -n, CI YAML parse. CI green and host runtime (compose up + alembic upgrade) pending in the build env.
- Phase 1 is code-complete. Next: Phase 2 (canonical data model + safe import pipeline).

## 2026-08-20: Phase 2 built (all increments 2A-2F)

Scope: canonical data model + safe staged import pipeline. Targets/exemptions/bulk-
workforce-import stay deferred per D-19/D-20/D-21 (see PHASE-2-PLAN.md).

- 2A: migration 0002 - campaigns, campaign_team/user_assignments, disposition
  definitions, contacts, campaign_contacts, suppression_entries, batches, work_items,
  call_attempts, import_jobs/rows/decisions, with the full invariant-backing constraint
  set (unique fingerprint, unique campaign+contact, one active work item per contact,
  one active primary campaign assignment per agent, unique active suppression, attempt
  and import idempotency).
- 2B: app/security/phone.py - E.164 normalization, keyed-HMAC fingerprint (ADR-019),
  Fernet encryption, combined into protect().
- 2C: app/imports/ - quarantine storage, upload validators (extension/signature/XLSX
  container structure, macro and external-link rejection, zip-bomb size cap), bounded
  CSV/XLSX parser (formulas never evaluated), classification, and the orchestrating
  service (parse -> preview -> versioned decision -> atomic idempotent commit with
  commit-time DNC/duplicate revalidation -> cleanup). Migration 0003 adds
  import_jobs.committed_result for idempotent replay. Celery worker + tasks.
- 2D: app/campaigns/ + app/api/campaigns.py - campaign lifecycle (real preconditions
  on launch/pause/archive) and import API, capability-gated and campaign-scope-aware
  ahead of Phase 4's scoped-role-assignment UI. Disposition creation enforces invariant
  9 (only "explicit_dnc" may cause suppression).
- 2E: compose worker + beat services; shared quarantine volume between web and worker.
  Dockerfile fix: pre-create and chown the quarantine mount point before switching to
  the non-root user (caught during review - the app user had no write access to
  /var/lib without this).
- 2F: tests/ packaged with __init__.py; shared integration fixtures; phone unit tests;
  a full import-flow integration test plus DNC-after-preview, stale-decision-version,
  launch-precondition, malicious-file, and authorization-negative tests.

Verified locally: py_compile after every increment, docker compose config, and a
careful manual trace of the migration DDL's FK creation order and reverse-order
downgrade (could not execute against a live Postgres here). Not yet observed: a real
CI run and docker compose up on the Linux host. Phase 2 is code-complete; next is
Phase 3 (agent workflow: leasing, disposition completion, callbacks) or bringing the
stack up on the host to validate everything built so far.

Next (Phase 1, after Phase 0 sign-off):
- [ ] Locked dependency file with hashes.
- [ ] FastAPI app, opaque sessions, CSRF, default-deny authorization helpers.
- [ ] Initial PostgreSQL schema and Alembic baseline.
- [ ] Docker Compose, Caddy LAN HTTPS, Tailscale Serve, health checks.
- [ ] CI checks, and encrypted backup with a tested restore.

## 2026-08-21: Phase 3 built (agent workflow vertical slice, all increments 3A-3F)

Transactional leasing, idempotent completion, explicit DNC via disposition, callbacks,
skip handling, agent stats. API-only; desktop UI is a follow-up. See PHASE-3-PLAN.md.

- 3A: lease_duration_minutes/max_skips_before_review config, migration 0004
  (work_items.skip_count), and a fix for a gap from Phase 1/2: Cache-Control: no-store
  on every /api/ response - load-bearing now since leasing is the first place a raw
  phone number leaves the server.
- 3B: app/work/service.py leasing - SELECT...FOR UPDATE OF work_items SKIP LOCKED,
  due-callback priority, one contact decrypted per lease, renew_lease,
  reclaim_expired_leases.
- 3C: idempotent complete_work_item with disposition branching (DNC sweep across ALL
  campaigns for the contact, callback scheduling, requeue-with-attempt-limit, review),
  skip_work_item.
- 3D: app/api/work.py (work/agent routers), app/reporting/agent_stats.py, WORK_QUEUE
  capability.
- 3E: expired-lease reclaim on Celery Beat.
- 3F: tests/integration/test_work_flow.py and tests/concurrency/test_leasing_concurrency.py
  (real threads, real separate Postgres connections, proves no duplicate active leases).

Also: installed ruff locally and ran it for real for the first time this session (previously
only py_compile). Found and fixed real issues, including one predating this phase (an
import-order bug in Phase 2D's app/api/campaigns.py that would have failed CI). Full
list in PHASE-3-PLAN.md and the "Lint cleanup" commit. Discovered this local shell's
Python (3.14) breaks SQLAlchemy 2.0.x at import time and the local package index won't
resolve the full dev dependency set - neither reflects the real target (Dockerfile
pins python:3.12-slim), so no version changes were made to chase either mismatch; ruff
itself is unaffected and stays in use. CI green, docker compose up on the host, and
actual pytest execution remain to be observed on the real build host.

Phase 3 is code-complete. Next: either Phase 4 pieces (management dashboards, workforce
assignment, campaign transfer) or, preferably, bring the stack up on the Linux host to
finally validate Phases 1-3 end to end - this is now the highest-value next step given
how much has been built without a live runtime check.

## 2026-08-21: Security hardening pass (external, commit 7c2ae0f), reviewed

A single commit, "security: harden campaign workflow controls" (7c2ae0f, 44 files,
~2000 lines), landed directly on main outside a Claude session and was reviewed line
by line at the user's request. Verdict: exceptional, professional-grade work. No
regressions found. Verified by careful reading and tracing only, not execution - same
local environment limits as the rest of this build (see the Phase 3 entry above).

Serious bugs fixed (all pre-existing in Phases 1-3):
- list_campaigns had no scope filter on the query itself - any user with VIEW_CAMPAIGN
  anywhere could list every campaign in the org. Fixed with a real campaign_scope_filter.
- _scope_covers treated any organization-scoped role as universally covering without
  comparing scope_id - harmless only because this deployment is single-org (D-07);
  would have leaked across orgs the moment multi-org existed.
- Login rate limiter failed open unconditionally on any Redis error, in every
  environment including production. Now fails closed specifically in production.
- Activation/reset tokens had no replay protection (stateless signed tokens, no
  server-side single-use tracking) - the same link could be used more than once.
  Replaced with a DB-backed ActivationToken model, single-use under row locking.
- Sliding session idle-expiry was computed in memory but never persisted on read-only
  (GET) requests, since those routes never call db.commit() - active read-only users
  could get silently logged out. Fixed by committing in the shared session dependency.
- commit_job deleted the quarantine file before the caller's db.commit() confirmed the
  transaction durable - a failed commit after that point would strand the import with
  no recoverable source file. Cleanup reordered to after confirmed commit.
- database_url was built with a raw f-string - a strong password from openssl rand
  (recommended in this repo's own runbook) can contain '/' or '+', corrupting an
  unescaped connection string. Rebuilt via SQLAlchemy's URL.create().
- The Phase 2 suppression unique index didn't actually prevent duplicate active DNC
  entries in this single-org deployment, because Postgres doesn't treat NULLs as equal
  in a unique index by default and organization_scope is NULL here. Fixed with
  NULLS NOT DISTINCT (Postgres 15+, already on postgres:16), plus a migration that
  de-duplicates existing rows (marked corrected, not deleted) before adding it.
- Idempotent completion replay re-read the work item's current (possibly since-changed)
  state instead of the state the original completion actually produced. Fixed by
  persisting resulting_work_item_state on the immutable call_attempts row.

Hardening added:
- Step-up reauthentication is now enforced (the /reauthenticate endpoint existed but
  gated nothing): admin credential resets and TOTP enroll/verify require a recent
  session.reauthenticated_at.
- Self-approval guard added to admin reset-2fa/reset-password (previously unguarded).
- Phone-scoped PostgreSQL advisory locks (new app/db_locks.py) serialize leasing, DNC
  completion, and import commit for the same number across campaigns and code paths -
  stronger than row-level locking alone, which can't protect across different rows for
  the same underlying contact. Lock acquisition is consistently ordered to avoid
  deadlocks (including between activation-token issue and consume).
- Settings.validate_production_safety: refuses to boot with app_env=production if any
  secret is too short, low-entropy, duplicated across fields, or still contains
  placeholder text (literally catches this repo's own deploy/secrets/*.example
  content if someone forgets to replace it).
- Redis given AOF persistence (--appendonly yes --appendfsync everysec) - it's the
  Celery broker, so a crash before a worker picks up a queued task previously lost it
  silently. compose.yaml now also sets APP_ENV=production/COOKIE_SECURE=true on
  web/worker/beat (activating the safety check above) and wires a real health_token
  secret (the /readyz gate existed but had no secret behind it, so it was inert).
- Multi-signal login rate limiting (account/IP/global), atomic via a Redis Lua script
  (the prior two-step INCR+EXPIRE had its own race), keys hashed rather than storing
  raw emails/IPs in Redis.
- Log redaction now recurses into dict/list/tuple args and covers exception
  tracebacks, not just the top-level message.
- CI: mypy and pip-audit flipped from advisory to blocking.
- New tests/concurrency/test_suppression_concurrency.py is notably rigorous - it
  queries Postgres's own pg_stat_activity to confirm a competing transaction is
  actually blocked on the advisory lock before proceeding, not just asserting on
  final outcome. New tests/integration/test_campaign_scope.py and additions to
  test_auth_flow.py directly regression-test the fixes above.

Migrations 0005 (activation_tokens, sessions.reauthenticated_at), 0006 (suppression
NULLS NOT DISTINCT + data cleanup), 0007 (call_attempts.resulting_work_item_state) -
all additive/safe, reviewed for correct FK/constraint ordering.
