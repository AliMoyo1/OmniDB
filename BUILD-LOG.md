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

## 2026-08-21: First live bring-up of the full stack (Docker Desktop, local host)

Brought the entire stack up for real for the first time: docker compose build, all
migrations against genuine Postgres 16, all six services, and a full live HTTP/HTTPS
verification pass through Caddy. This is the point where every "verified by reading,
not execution" caveat from Phases 1-3 finally got resolved one way or the other.
Found and fixed four real, previously-unverifiable bugs; everything else checked out.

### Environment problem (not a code bug)

The first build attempt hung indefinitely - a single isolated `docker pull` ran 47
minutes with zero output before being killed. Root cause: Docker Desktop's WSL2
network bridge was in a bad state (the daemon couldn't reach the internet even though
Windows itself could - confirmed via prior successful pip/git operations). An earlier
`TaskStop` on the stuck build had also only killed the harness-tracked wrapper, not
the detached docker-buildx process tree, leaving three concurrent build attempts
silently competing for the same builder. Fixed by killing all stray docker.exe/
docker-compose.exe/docker-buildx.exe processes and restarting Docker Desktop, which
reset the network bridge - confirmed via a 10.7s `hello-world` pull immediately after.

### Bug 1: migrations/env.py + configparser interpolation

`config.set_main_option("sqlalchemy.url", ...)` stores the URL through Python's
configparser, which treats a bare `%` as its own interpolation syntax. The
security-hardening commit's database_url fix (percent-encoding the password via
URL.create()) is correct, but a percent-encoded password routinely contains `%2F`/
`%3D`-style sequences, which configparser then rejected as "invalid interpolation
syntax" before a single migration could run. Fixed by escaping `%` to `%%` before
storing it; configparser un-escapes it back to a single `%` on every read, so both
online and offline migration modes see the correct URL. Commit 7eb6c7c.

### Bug 2: migration revision ids longer than Alembic's own VARCHAR(32)

Migrations 0001-0004 applied cleanly, but 0005 failed with StringDataRightTruncation
- after successfully running its own DDL, while trying to record itself as the
current revision in Alembic's own alembic_version.version_num column (default
VARCHAR(32)). The security-hardening commit's migrations 0005-0007 used descriptive
revision ids 34-36 characters long; 0001-0004 happened to fit under 32 by chance.
Shortened all three ids and their down_revision chain (0005_activation_step_up,
0006_suppress_nulls_distinct, 0007_completion_idempotent) - safe to rename since no
real deployment had ever run them. Commit 7eb6c7c. Verified with a full round trip:
upgrade to head, downgrade to base, upgrade back to head, all clean, 24 tables
present throughout.

### Bug 3: Celery Beat could not write its schedule file

beat crashed on startup: `[Errno 13] Permission denied: 'celerybeat-schedule'`. The
Dockerfile's COPY instructions run as root, so /app is root-owned when USER app takes
effect; web and worker only ever read from /app so this was invisible, but beat
writes its persistent schedule state there. Fixed with an explicit
`chown -R app:app /app` alongside the existing quarantine-directory chown. Also set
broker_connection_retry_on_startup=True to silence a Celery deprecation warning seen
in the live worker logs. Commit 66190a4.

### Full live verification (all passed after the fixes above)

- All six containers (postgres, redis, web, worker, beat, caddy) healthy/running
  simultaneously; no host ports published for postgres/redis, confirmed via
  `docker compose ps`.
- HTTPS via Caddy with its internal CA (ADR-003): valid cert issued for "localhost",
  HTTP-to-HTTPS 301 redirect works, full security header set present (CSP, HSTS,
  X-Frame-Options, X-Content-Type-Options, Permissions-Policy, Referrer-Policy).
- Cache-Control: no-store confirmed present on /api/ responses and absent on
  /healthz - the Phase 3A fix, load-bearing since Phase 3's lease endpoint is the
  first place a raw phone number leaves the server.
- /readyz correctly returns 404 without the health_token header (hiding its
  existence) and 200 with real database+redis status with it.
- Full login flow through real HTTPS: Argon2id password verification, session and
  CSRF cookies issued, session-authenticated GET /auth/me works.
- CSRF genuinely enforced: a state-changing POST without the CSRF header is rejected
  with 403 before any real logout logic runs.
- Role-to-capability resolution verified live: a super_admin session's /whoami
  returns exactly the capability set defined in app/authz/capabilities.py.
- Wrong password rejected with a generic 401 (no user enumeration).
- Self-approval guard verified live: a Super Admin cannot reset their own password
  via the admin endpoint (403), but can reset a different user's (200, with a real
  activation token) - the security-hardening commit's fix, confirmed working exactly
  as designed.
- Activation-token single-use enforcement verified live: first activation with a
  token succeeds, replaying the same token is rejected (400), and the new password
  genuinely works for a subsequent login - the other major security-hardening fix
  (replacing the old stateless signed tokens, which had no replay protection),
  confirmed working exactly as designed.
- Celery end-to-end: Beat scheduled reclaim-expired-leases on its 2-minute cadence,
  the worker received and executed it successfully (0.065s, returned 0 - correct,
  no leases existed yet in this fresh database).

### What's still not covered by this pass

No live test yet of the campaign/import/lease/complete workflow (would need a real
CSV upload through the API) or of backup/restore. CI (GitHub Actions) has still not
been observed running green - only local Docker verification. Local test users
created for this verification (admin@ciphercontact.local as super_admin,
target@ciphercontact.local as a plain user with a reset password) were left in the
running local database rather than deleted, since the stack is still up locally for
further exploration - not real/production data, this is the first-ever bring-up on a
throwaway local Postgres volume.

Every code fix in this entry is pushed to main. The stack itself (containers,
volumes, local .env and deploy/secrets/* with real generated values) is NOT
committed - .env and deploy/secrets/* stay gitignored as designed, and only exist on
this machine's Docker Desktop right now.

## 2026-08-21: Live campaign -> import -> work-item -> completion flow, end to end

Closed the remaining gap from the bring-up above: exercised the actual business
workflow live through real HTTPS, not just auth/admin. A Manager and an Agent user
were created via the app's own code (no bootstrap script exists yet - noted as a
Phase 4 gap). Campaign-user-assignment issuance also has no API yet, so the agent
was assigned via a direct DB insert, consistent with how Phase 2/3 tests already do
this.

Flow exercised: Manager creates a campaign with full provenance -> creates four real
disposition definitions (a "complete" one with counts_as_connected/conversion, a
"requeue" one, a "callback" one requiring a future time, and the protected
"explicit_dnc" one) -> uploads a 5-row CSV -> the parse task genuinely round-tripped
through the real Celery/Redis broker to the real worker process (this deployment
runs with CELERY_TASK_ALWAYS_EAGER=false, so this is true async dispatch, not the
eager mode the pytest suite uses) and parsed all 5 rows valid -> preview reviewed ->
decision approved -> committed (5 inserted, 0 suppressed), and a replay with the same
idempotency key returned the identical result without double-inserting -> campaign
launched -> agent leased and completed items covering every disposition branch:
a normal completion, a requeue (returned to queued), an explicit DNC (contact
suppressed, verified for real in suppression_entries: source
explicit_contact_request, status active, real fingerprint and ciphertext present), a
callback (correctly excluded from the masked callback-list response's fields -
reference showed the imported name, never the number - then correctly reappeared
with is_callback:true once due, taking priority over the shared pool), and a skip
(rejected with no reason, succeeded with one, returned to queued). Also incidentally
re-confirmed the callback_at-must-be-in-the-future validation, triggered by an actual
test-authoring mistake (subtracted instead of added an offset) rather than a
deliberate negative test - it did exactly what it was supposed to.

Final reconciliation: GET /api/v1/agent/stats (5 total attempts, 4 connected, 2
conversions, 1 dnc_request) matched a direct query of campaign_contacts exactly -
Alice completed/connected_interested, Carol completed/connected_interested (via the
callback path), Dave suppressed/explicit_dnc, Bob and Eve still queued (Eve was
skipped and returned to the pool). Every number reconciled with no discrepancy.

No new bugs found in this pass - the three fixes from the bring-up above were
sufficient. This is the strongest evidence yet that Phases 1-3 plus the
security-hardening commit are functioning correctly as an integrated system, not
just as individually-reviewed pieces.

Test data (1 campaign, 2 users, 5 contacts, 5 call attempts, 1 suppression entry)
remains in the local database for further exploration, alongside the admin/target
users from the bring-up pass above - none of this is real production data, this is
still the same first-ever local Docker Desktop environment.
