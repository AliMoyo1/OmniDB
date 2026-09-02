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

## 2026-08-21: Root-caused and fixed the standing CI failure (test isolation, not app logic)

Every CI run this session had failed on `pytest -m integration`, always the same 10
tests in `tests/integration/test_work_flow.py` from `test_complete_work_item_is_idempotent`
onward, always `.json()` raising on a 204-empty-body response from `POST
/api/v1/work/next`. Reproduced it exactly locally (10 failed, 25 passed, identical
names) by fully resetting Postgres (`alembic downgrade base` + `upgrade head`) and
Redis (`FLUSHALL`) before running the suite the same way CI does. Diagnostic prints
added directly to a copy of the failing test (Campaign/CampaignContact/WorkItem/
CampaignUserAssignment state, DB `now()`, raw lease response) showed the campaign's
one contact was already `status=suppressed` with zero work items *before* the test's
own lease call - the app was correctly reporting no work available; nothing to fix
there.

Root cause: `tests/integration/conftest.py::zw_numbers()` is deterministic - it always
returns the same handful of numbers, always starting with the exact same base number,
regardless of which test calls it or how many numbers are requested. Several tests
correctly and permanently suppress whichever number they touch, as real DNC behavior
requires: `test_imports_flow.py::test_suppression_added_after_preview_is_excluded_at_commit`
inserted a suppression entry directly for that same shared base number (via its own,
separately-deterministic `_zw_number()` helper), and
`test_work_flow.py::test_explicit_dnc_suppresses_the_contact_across_campaigns`
legitimately disposes a contact as DNC, which is its actual test purpose. Since
integration tests share one real Postgres database with no per-test rollback, either
one permanently poisons that number for every later test in the same run that reuses
it via `_setup_campaign_with_agent()`'s default `contact_count=1` - which is most of
the file. This was a test-fixture collision, not an application defect: DNC
suppression being real, permanent, and cross-campaign is exactly invariant 7 working
as designed.

Fixed `zw_numbers()` to draw randomly from a 10,000-value trailing-digit space instead
of a fixed deterministic sequence, so unrelated tests can no longer collide on the
same number (commit-message-worthy detail: the ZW example number's national digits
are `1312345`, a synthetic sequential placeholder, not a real assigned range, so
varying its trailing 4 digits stays safely within valid-number territory). Pointed
`test_suppression_added_after_preview_is_excluded_at_commit` at the same fixture
instead of its own separate, equally-deterministic helper. While in the same area,
also fixed an already-known, separate, non-CI-blocking bug in
`tests/concurrency/test_leasing_concurrency.py::_setup()`: it inserted a `Contact` per
number unconditionally instead of checking for an existing one first, so it would
`IntegrityError` if ever re-run against a non-fresh database - now matches the real
import pipeline's get-or-create pattern (`app/imports/service.py`'s `commit_job`).

Along the way, found one more real, unrelated failure once the 204/JSONDecodeError
noise cleared: `test_callback_disposition_schedules_and_masks_reference` passed a
`callback_at` one minute in the *past*, which the security-hardening commit's
`complete_work_item` now correctly rejects (callback times must be in the future -
this is the same validation already noted as "incidentally re-confirmed" during the
live-flow verification entry above, from an actual test-authoring mistake). Fixed by
scheduling a future callback, then backdating `WorkItem.due_at` directly via the DB
afterward to still exercise the "due callback is immediately re-leasable" path, the
same pattern `test_lease_expiry_reclaim_returns_item_to_queue` already uses in the
same file.

Verified clean: full suite (`pytest` with no marker filter, 62 tests) green from a
cold reset, green again immediately re-run against the same warm database with no
reset (confirming the get-or-create fix actually holds), and `ruff check` clean on
all four touched files (one real finding along the way - S311 on `random.randint`,
resolved by switching to `secrets.randbelow`, not a suppression).

Files changed: `tests/integration/conftest.py`, `tests/integration/test_imports_flow.py`,
`tests/integration/test_work_flow.py`, `tests/concurrency/test_leasing_concurrency.py`.

Pushed and watched the real run on GitHub Actions (run 32488321998): quality,
security, integration (including the "Integration tests" step, the exact step that
had failed every single prior run this session), and build all passed. This is the
first genuinely green CI run this project has had.

## 2026-08-21: Phase 4A-1 - workforce foundation (users, roles, teams, reporting lines)

Reconciled the master plan's Phase 4 against the decision log first: D-19 (target
policy) and D-20 (exemptions) are deferred, D-21 defers bulk import in favor of
manual user creation, which leaves 4A (dashboards, manual workforce/role/campaign
assignment, viewer scope, audit search) as the only in-scope slice of the original
Phase 4A/4B/4C split. Wrote PHASE-4-PLAN.md with the full 4A breakdown before
building. Every core data model this increment needed (`Organization`, `Team`,
`TeamMembership`, `RoleAssignment`, `ReportingAssignment`, `Delegation`) and every
capability constant the plan's section 6.3 matrix needs already existed from
Phase 1 - this was new service/API code against an existing, well-anticipated
schema, not new modeling work.

Built `app/workforce/service.py` (create/disable/reactivate a user, assign/end a
role, create a team, add/end team membership, set a reporting line) and
`app/api/workforce.py` (`/api/v1/workforce`, kept separate from `/api/v1/admin`'s
Super Admin technical surface). Role-appointment authorization is looked up per
target role_code against the plan's own capability matrix (Manager appoints Team
Leader, Team Leader appoints Team Captain, either appoints Agent, `super_admin` is
never assignable through this API), checked with `authz.has_scope_capability` - the
same function `create_campaign` already uses, not a new authorization primitive.
Every role grant or end now calls `authz.invalidate_sessions_on_privilege_change`,
a hook Phase 1 built and nothing had ever called until this increment. Disabling a
user reclaims their active work-item leases too (plan 6.4 names both explicitly),
which needed one small addition to `app/work/service.py`:
`reclaim_leases_for_user`, mirroring the existing `reclaim_expired_leases` but
filtered by owner instead of expiry.

Found one real, pre-existing gap while building this: `role_assignments` and
`reporting_assignments` have existed since the baseline migration but nothing had
ever written to them outside test fixtures, so neither had the partial-unique-index
protection this codebase already uses everywhere else for "one active X" invariants
(`uq_cua_one_primary_active` for campaigns, `uq_team_memberships_active`). Added
migration 0008 (`uq_role_assignments_active`, `uq_reporting_assignments_primary`,
both `NULLS NOT DISTINCT` for the same reason 0006 needed it) so a race between two
concurrent appointments can't create two overlapping active grants - not just
application-level checking. Verified the downgrade/upgrade round-trip.

Also hit a real Docker gotcha worth remembering: `docker compose run` starts a
*fresh* container from the built image, not from the running, `docker cp`-patched
container - so `alembic upgrade head` run via `docker compose run` silently didn't
see migration 0008 at all, while `docker compose exec` (which runs inside the
already-running, patched container) did. Any migration work done by patching a live
container's filesystem directly needs `exec`, not `run`.

Wrote 14 integration tests (`tests/integration/test_workforce_flow.py`) covering
the appointment-capability matrix in both directions (correct role/scope succeeds,
wrong scope or too-low a role 403s), self-appointment and self-supervision guards,
re-granting the same role superseding rather than stacking, session invalidation on
role end, the disable-reclaims-active-lease path end to end, and that a Team
Leader's user listing is scoped to their own team and cannot see another team's
members. One test needed correcting, not the code: a literal
Manager-acting-on-themselves self-supervision test always 403s before reaching the
self-supervision check, because `can_manage_user` only ever authorizes appointment
capability held over a role strictly *below* the target's - no role can pass that
check against an identically-roled target, including itself. Documented as a real
design property in PHASE-4-PLAN.md, not a bug: the service-layer guards stay as
defense in depth for future callers (bulk import, delegation) that won't all go
through this same authorization gate.

Full 76-test suite (62 existing plus 14 new) green from a cold reset, ruff clean
repository-wide. mypy still isn't runnable in this dev environment (same Python
3.14 mismatch noted since Phase 1) - CI's blocking mypy step is this increment's
first real type check, same as everything else built this session.

Remaining in Phase 4A: campaign-user/team assignment API and transfer (D-18),
protected/scoped audit search and a first real Viewer capability, and the
Manager/Team Leader/Team Captain dashboards themselves (the first server-rendered
UI in this build - `app/templates` and `app/static` are still empty scaffold
directories). See PHASE-4-PLAN.md for the full breakdown.

## 2026-08-21: Phase 4A-2 - campaign assignment API and transfer (D-18)

Added the real API for something only test fixtures could do before: assign a team
or an agent to a campaign, end either assignment, and transfer an agent between
campaigns. New capability `ASSIGN_CAMPAIGN_AGENT` (plan 6.3's "Assign Agent to
campaign" and "Move Agent between campaigns" rows share the same three grantees -
Manager, Team Leader, Team Captain - so one capability covers both), authorized
through the existing `has_campaign_capability`, no new authorization primitive.

A real design question came up immediately: should assigning an agent who already
has an active primary assignment elsewhere just supersede it, the way 4A-1's role
re-assignment does? Decided no - a campaign move has real consequences (an agent
mid-lease, a scheduled callback) that only a proper transfer preflight handles
safely, so `assign_agent_to_campaign` now refuses that case (409, directing the
caller to transfer) rather than opening a second, unsafe path to the same state.

D-18's own decision text just says "adopt transfer preflight, ops picks defaults" -
the actual lease/callback treatment was left for this build to choose. Picked
"return to the source queue" for a transferred agent's active lease, matching the
same treatment 4A-1's `reclaim_leases_for_user` already uses for a disabled user.
For callbacks specifically, traced `_next_callback_candidate` before assuming
"retain" (do nothing) was safe, and it isn't: leasing a due callback requires an
active assignment on that same campaign, so a callback left assigned to an agent
whose campaign assignment just ended would become silently unleaseable by anyone -
exactly the "orphaned callback" failure point the plan names as something a
transfer must not cause. Both cases now go through one new function,
`app/work/service.py::release_campaign_work_for_agent`, scoped to a single (agent,
campaign) pair - a deliberately different, narrower tool than 4A-1's
`reclaim_leases_for_user`, which is right for a full account disable but too broad
for a single-campaign transfer (it would touch every campaign the agent is on).

Destination "staffing capacity" preflight is real but intentionally minimal: it
only applies when the assignment names a team and that team has an active
`CampaignTeamAssignment` with a set capacity on the destination campaign;
otherwise unlimited. Target proration is correctly absent - D-19/D-20 defer the
whole target subsystem, so there's nothing to prorate in this pilot. Transfer
checks `ASSIGN_CAMPAIGN_AGENT` on **both** the source and destination campaign, so
authority to move agents out of one campaign doesn't imply authority to place them
into an unrelated one.

10 new integration tests, including one specifically proving the pending-callback
case releases cleanly instead of orphaning (leases a callback-disposition item,
schedules a future callback, transfers the agent before it's due, confirms the
work item is back in the shared queue with no owner and no due_at - not stuck in
callback_wait). Full suite - 86 passed, no regressions. ruff clean repository-wide.
No new migration needed: campaign_team_assignments and campaign_user_assignments
have existed since Phase 2A, this was new service/API code against an existing,
already-correct schema.

Remaining in Phase 4A: protected/scoped audit search and a first real Viewer
capability (4A-3), and the Manager/Team Leader/Team Captain dashboards (4A-4, the
first server-rendered UI in this build).

## 2026-08-21: Phase 4A-3 - aggregate campaign reports, Viewer's first capability, scoped audit search

`ROLE_VIEWER` had been `set()` since Phase 1 - this increment gives it something
real: `VIEW_CAMPAIGN` (metadata only, never contacts) plus a new
`VIEW_CAMPAIGN_REPORTS`, both already scope-checked through the existing
`has_campaign_capability` machinery, so a Viewer's "explicit assigned report scope"
(plan 6.1) is just their ordinary `RoleAssignment` scope - no separate concept
needed. `app/reporting/campaign_stats.py` extends Phase 3's `agent_stats.py`
pattern (live counts over immutable `call_attempts`) to a whole campaign: total
contacts, active assigned agents, total attempts, connected, conversions, DNC
requests. New `GET /api/v1/campaigns/{campaign_id}/stats`, granted to Manager, Team
Leader, Team Captain, and Viewer - the actual reporting surface Viewer exists for.

Scoped audit search hit a real design problem immediately. `GET /api/v1/admin/
audit-events` was Super-Admin-only and fully unscoped, and the obvious fix -
extend `VIEW_AUDIT` to Manager/Team Leader/Team Captain and filter by
`AuditEvent.team_id`/`organization_id` - doesn't work, because almost no
`record_audit(...)` call site anywhere in this build actually sets those columns.
Retrofitting every existing call site across four phases of work was out of
proportion for this increment, so scoped visibility is resolved differently
instead: installation- or organization-wide `VIEW_AUDIT` sees every event (Super
Admin, Manager); a team-scoped grant (Team Leader, Team Captain) sees only events
whose actor was themselves or an active member of their own team, reusing
`team_memberships` the same way 4A-1's user-listing scope filter already does. A
real, non-leaking slice of the trail, not a fully general resolver - and
documented as the known simplification it is rather than a silent gap.

Learned from 4A-2's CI failure and applied it proactively this time:
`campaign_stats.py`'s two count queries use `db.scalar(...) or 0` from the start,
the same fix 4A-2 needed as a follow-up push after mypy caught `int | None` used
in a comparison.

7 new integration tests, including one that reconciles campaign stats exactly
against a real multi-disposition attempt sequence (mirroring the rigor of the
earlier agent_stats live-verification pass), one proving Viewer can read a
campaign and its stats but is denied everything else including audit search, and
one proving the team-scoped audit filter specifically (a team member's action is
visible, an outsider's is not). Full suite - 93 passed, no regressions. ruff clean
repository-wide. No new migration needed.

Remaining in Phase 4A: the Manager/Team Leader/Team Captain dashboards (4A-4, the
first server-rendered UI in this build - `app/templates` and `app/static` are
still empty scaffold directories, and Jinja2Templates/StaticFiles aren't wired
into `app/main.py` yet).

## 2026-08-21: Phase 4A-4 - Manager/Team Leader/Team Captain dashboards (Phase 4A complete)

The first server-rendered UI in this build: one parameterized `GET /dashboard`
whose sections are gated by the viewer's own capabilities rather than three
hardcoded pages - the page shape is identical for every role, only what's visible
differs, which is how plan 6.1's "separate dashboards" actually gets delivered.
Plain HTML forms, no JavaScript, no HTMX vendored (the plan names HTMX as the
stack, but a first pass didn't need it and D-04's LAN-only/no-CDN constraint makes
a third-party JS dependency something to justify, not default to). CSRF via a
hidden form field validated against the same signed-token mechanism the JSON API
already uses; session-cookie page auth that redirects to `/login` (a new
`RedirectToLogin` exception with a handler in `app/main.py`) instead of a JSON 401.
Every action calls the exact same service-layer functions the JSON API already
uses - new presentation over already-tested logic. Deduplicated two scoped-
visibility queries that would otherwise have been written twice for the JSON route
and the dashboard route (`workforce_service.list_visible_users`,
`admin.list_visible_audit_events`).

Verification couldn't use the Browser pane tool as planned: Caddy's internal-CA
certificate isn't trusted, and the tool hard-fails on the TLS error before any
interstitial appears - nothing to click through. Fell back to curl through the
real HTTPS/Caddy/Postgres/Redis stack, the same method already proven during the
original business-flow verification pass, and it was thorough: full login,
dashboard render, create campaign, create user (confirmed the one-time activation
token renders directly rather than living in a URL), assign role, create team, add
team member, assign agent to campaign, stats reconciling exactly (0 then 1 after
assignment), a bad CSRF token confirmed to genuinely block the action rather than
just redirect past it, unauthenticated access confirmed to redirect, logout
confirmed to genuinely revoke the session even across a container restart, and a
plain Agent's dashboard confirmed to degrade to a clean, empty, non-broken page.

That live pass found four real, independent bugs pytest alone would not have
caught:

1. A `docker cp` gotcha, not an app bug, but worth remembering: copying a source
   directory into an already-existing destination directory nests it rather than
   replacing its contents (both `app/templates` and `app/static` already existed
   as empty scaffolds with `.gitkeep`), producing `/app/app/templates/templates/
   *.html` and an immediate `TemplateNotFound` 500 on the very first request.
   Fixed by copying individual files to their exact destination path.
2. The "add team member" and "assign agent" forms originally embedded a full
   dropdown of every visible user inside every team/campaign table row - an
   O(teams x users) blowup that produced an 850KB page against this session's
   heavily-reused dev database (dozens of full-suite runs' worth of accumulated
   teams and users). Fixed by replacing both dropdowns with a plain user-ID text
   input, matching the pattern already used for scope selection - a real scaling
   fix, not a number bumped up, since the cross-product problem would recur at any
   moderately larger team/user count. Also added a missing `.limit()` on the teams
   query, which had none at all.
3. `can_view_campaigns` was computed but never made it into the template context
   dict - Jinja2's default `Undefined` is falsy in an `{% if %}`, so the entire
   Campaigns section silently vanished for every user regardless of their real
   capability, no error, no crash. Now guarded by a real pytest assertion
   (`test_dashboard_shows_manager_sections`), not just a manual look.
4. The login rate limiter's source-signal threshold (100 attempts per 15 minutes)
   turned out to be tighter than the test suite's own login volume: `TestClient`
   never sets a real client IP, so every integration test's login shares one
   bucket, and a full run now performs 101 of them. Confirmed this reproduces even
   against a freshly-flushed Redis, meaning CI would hit it too - not a bug in the
   limiter (it did exactly its job), just a threshold picked before this session's
   test suite grew this large. The account limit - real per-credential brute-force
   protection - was never at risk and stayed untouched; source and global raised
   5x for headroom.

Also fixed proactively, before it ever ran: the original "create user" action
would have redirected to `/dashboard?flash_success=...` with the one-time
activation token embedded in the query string - a real instance of this session's
own "never put sensitive data in a URL" rule, caught in review rather than in
testing. The confirmation now renders directly instead of redirecting.

11 new integration tests, full suite green (104 passed), ruff clean repository-
wide, no new migration. This closes out Phase 4A entirely (4A-1 workforce
foundation, 4A-2 campaign assignment and transfer, 4A-3 reports/Viewer/audit
search, 4A-4 dashboards) - see PHASE-4-PLAN.md for the full breakdown and known
simplifications (no bulk-action forms yet, no team-name picker, no HTMX/JS).

## 2026-08-21: Phase 4A-4 UI redesign - ThemisIQ design-system port, dock, sidebar

The 4A-4 dashboard worked but looked plain. Ported the visual design system
(theme tokens, self-hosted fonts, glass-morphism cards, 3D tilt hover, canvas
particle background) from the separate One For All platform - visual code
only, no data or business logic crossed between the two projects. Added a
persistent icon dock and a text sidebar, both reading the dashboard's own
`nav_flags()` (now centralized in `app/web/templates.py` and attached to
every authenticated page via `page_context()`, rather than left for each
route to compute and pass separately).

This reverses 4A-4's original "no JavaScript" call, on purpose - the particle
background and tilt effect need it. Scoped it to three small vanilla-JS files
with no framework and no build step, to keep the spirit of that original
decision even though not the letter of it.

Two real bugs found in live verification, neither one pytest would catch:

1. `base.html`'s first draft defined `{% block content %}` once in the
   authenticated branch and again in the unauthenticated branch, on the
   reasoning that only one branch renders at a time. Jinja2 resolves block
   definitions statically and rejects the same name twice in one template
   regardless of runtime branching - every page raised
   `TemplateAssertionError`. Restructured so the block appears exactly once,
   with the surrounding chrome each independently gated by its own
   `{% if user %}` around a complete element instead of the shell being
   duplicated per branch.
2. The theme toggle was a silent no-op: `app/middleware.py`'s CSP
   (`default-src 'self'`, no `unsafe-inline`, no nonce - deliberately strict
   for a PII/DLP app, and correctly so) blocks inline scripts and inline
   event-handler attributes. The first draft had an inline theme-restore
   script and an inline `onclick`. Fixed by extracting both into
   `app/static/js/theme.js` and swapping the `onclick` for a delegated
   `document`-level click listener, matching the CSP-compliant pattern
   `particles.js`/`card-tilt.js` already used - not by weakening the policy.

ruff and mypy clean, 27/27 unit + authorization tests passing locally
 (`APP_ENV=development` override, no database needed - matches CI's `quality`
 job exactly). The integration suite - the most relevant one here, since it
includes `test_web_dashboard_flow.py` - could not run locally this time:
`compose.yaml` deliberately exposes no database ports to the host, and this
session's sandbox blocks publishing new ports even via a throwaway forwarding
container. Live Browser-pane verification against the real running stack
covers the same rendered output (all four dashboard sections render with real
data, dock and sidebar show correct conditional items, particle background
animates, login page renders correctly). Pushed (commit `ea113fb`) and
watched CI run 32516090093 to completion: build, security, integration, and
quality all green, no regressions - see PHASE-4-PLAN.md's "4A-4 UI redesign"
section for the full breakdown.

## 2026-08-22: Agent Workbench and one-active-contact invariant

Built the first complete browser workflow for Agents at `/agent/work`. The
keyboard-first workbench reveals one leased contact, displays the approved
campaign metadata and hold countdown, records dispositions and encrypted
notes, schedules callbacks in the campaign timezone, supports justified skip
and lease renewal, and keeps callback phone numbers masked until leased. Agent
logins now land on this workbench through the existing dashboard redirect;
management navigation remains capability-gated.

Closed a backend race discovered while designing the page: repeated Next calls
from multiple tabs could give one Agent more than one active contact. Leasing is
now serialized per Agent, returns the existing lease on refresh or double-click,
and is backed by migration `0009_single_active_agent_lease`, which safely
normalizes any historical duplicates before creating a PostgreSQL partial
unique index on active lease ownership.

Verification used disposable PostgreSQL 16 and Redis containers, separate from
the live deployment. A clean database migrated through all nine revisions.
The application image built and imported successfully; Ruff passed repository-
wide; the new files passed Ruff formatting; mypy passed all 72 source files;
and the full suite passed with 111 tests. Coverage includes same-Agent
concurrent leasing, refresh-resume, form CSRF, authorization, completion,
required skip reasons, and preventing a second raw number from appearing.

Pushed to `main` as commit `4f67d17`. GitHub Actions run `32570647307`
completed green across build, security, quality, migration reversibility, and
integration. Before live deployment, created and restore-list checked encrypted
backup `backups/ciphercontact-20260822T113729Z.dump.gpg` (111682 bytes, SHA-256
`3d84b953905c88338cb12529f33f22498344c559133c7dce34bc61bf7de9108f`).
The live database migrated to `0009_single_active_agent_lease (head)` and web,
worker, beat, and Caddy were recreated from the verified image. PostgreSQL,
Redis, web, and worker reported healthy; HTTPS `/healthz` returned 200; `/login`
returned 200 with CSP, HSTS, `Cache-Control: no-store`, frame denial, and MIME
sniffing protection. Recent application logs contained no errors.

## 2026-08-22: Campaign Control Room

Added a dedicated, server-rendered Campaign Control Room at `/campaigns` and
capability-gated it from the shared navigation. Managers can create a provenance
complete draft, stage an import, review aggregate validation results, approve and
commit it, manage dispositions, assign Agents, and launch, pause, or archive a
campaign. Viewers keep read-only campaign visibility without management forms.

The pages deliberately expose only aggregate import and campaign information.
Raw phone numbers, names, and contact metadata remain in the existing protected
data path and never render in this management interface. All state-changing forms
use the established CSRF and authorization controls, and import commit preserves
the existing decision-version and idempotency safeguards. No migration was needed.

Added five integration tests covering authentication, form CSRF, read-only Viewer
access, and the manager create -> stage -> review -> commit -> disposition ->
assignment -> launch workflow. The full 116-test suite passed before the final
visual-only CSP and favicon cleanup. The current source then passed Ruff and mypy
(73 source files) and built successfully into the disposable application image.

Real-browser validation used a separate PostgreSQL and Redis network: a Manager
logged in, opened `/campaigns`, and saw the seeded draft. The final pass completed
with zero browser-console errors and zero failing network responses. The login
heading's blocked inline style was moved to the stylesheet, and a same-origin SVG
favicon was added, so the strict CSP remains intact without browser noise.

The Campaign Control Room was pushed as `9aed835`; GitHub Actions run
`32574111227` completed green across build, security, quality, migration
reversibility, and integration. Before deployment, created and restore-list
checked encrypted backup `backups/ciphercontact-20260822T125305Z.dump.gpg`
(111703 bytes, SHA-256
`295fc2ac4c73ea8dc7df137b6d14834d7f2f457aedb5a4ad3f6e5b0901df5ae7`).
Rebuilt and recreated web, worker, beat, and Caddy only. PostgreSQL and Redis
remained healthy, the live schema stayed at `0009_single_active_agent_lease
(head)`, and HTTPS `/healthz` and `/login` returned 200. The protected
`/campaigns` route redirected unauthenticated requests to `/login` with CSP and
HSTS headers present; recent application logs contained no errors.

## 2026-08-28: Phase 5A - concurrency and load tests

Started Phase 5 (production hardening and controlled pilot). Most of it isn't
buildable here - it needs real infrastructure, real people, and real approvals -
so asked the user which slice to start with; they picked concurrency and load
tests (master plan step 3). See PHASE-5-PLAN.md for the full reconciliation and
why the eight-hour soak test (step 4) is out of scope for this pass.

Audited the codebase for check-then-act sequences with no lock spanning the
read and the write, the same shape `tests/concurrency/` already covers for
leasing and DNC suppression, and found one this session hadn't tested:
`app/campaigns/service.py::_check_staffing_capacity` read a count and the
caller inserted a row afterward with nothing serializing two concurrent
callers. A new concurrency test
(`tests/concurrency/test_staffing_capacity_concurrency.py`) races N concurrent
`assign_agent_to_campaign` calls against one under-capacity team; fixed by
locking the `CampaignTeamAssignment` row (`FOR UPDATE`) for the transaction,
the same shape of fix leasing already uses. Diagnosed the race by reading the
code rather than an empirical local red run - local Docker Desktop went down
mid-increment (processes running, CLI and every container unresponsive) - and
verified through CI instead, which doesn't depend on local Docker.

Also added a first load test under `tests/performance/` (an empty scaffold
since Phase 1): 60 agents racing 60 items under full contention, printing
leasing throughput. New `performance` pytest marker, deliberately excluded
from both CI job selectors - shared CI runners give meaningless throughput
numbers, and Phase 5 itself expects real performance validation on approved
hardware, not commodity CI.

Pushed and hit an unrelated, pre-existing CI failure: five commits from a
different tool (`CodexSandboxOffline`, 2026-08-22) had redesigned the login
page and left `test_login_page_renders_and_has_a_form` broken on `main` for
six days - a stale exact-string assertion, not a real bug (the redesign added
`class="login-card"` to the form tag; the test still checked for the tag
without it). Told the user rather than silently fixing or silently ignoring
it, since it meant another tool had been working on the same repo
uncoordinated with this session; asked to fix and continue. Fixed the
assertion, re-pushed, CI green (build/security/integration/quality all pass,
commit `c10dd0d`).

## 2026-08-28: Workforce Control Room

The Campaign Control Room and Agent Workbench each gave their domain a proper
list+detail page; workforce management was still confined to the dashboard's
original inline sections, which only ever exposed the create half of what
`app/workforce/service.py` and its JSON API (`app/api/workforce.py`) already
supported. `disable_user`, `reactivate_user`, `end_role_assignment`,
`end_team_membership`, and `set_reporting_line` were all fully built, tested at
the API level, and had no web route at all. Phase 6 (validated analytics) - the
next phase in the master plan - is explicitly scoped to start only after pilot
approval and depends on the Phase 4C target-policy work the decision log
deferred, so it wasn't a real option yet; this gap was.

Added `/workforce` (list: users and teams, matching the existing
`campaign_list.html`/`campaign_detail.html` list+detail pattern and reusing its
generic `.ops-*` CSS building blocks rather than inventing new ones),
`/workforce/users/{id}` (roles with grant/end, reporting line with a
set-supervisor form, team memberships read-only with a link to the owning
team), and `/workforce/teams/{id}` (roster with add/remove). Every route is a
thin layer over the existing, already-tested service functions - no new
service logic, no new migration. Left the dashboard's original inline
workforce/teams sections and their routes in place rather than removing them,
matching how the Campaign Control Room's own addition left `dashboard.py`'s
older campaign routes alongside it; updated the dock and sidebar nav links to
point at the new pages instead of `/dashboard#workforce` / `/dashboard#teams`.

Local Docker Desktop was still down (see the Phase 5A entry above - a Windows
logon-type/HCS permission error blocking WSL2 VM creation, unresolved as of
this entry), so this could not get a live-browser or real-Postgres pass this
time. Verified everything that does not need a running database: ruff and
mypy clean, all four new templates parse through the real Jinja2 loader with
no syntax errors (the same class of bug - a duplicate block, an undefined
variable - that has bitten this build before), `app.main` imports cleanly
with the new router registered (confirmed via the generated OpenAPI schema:
all 12 new routes present), and an unauthenticated `TestClient` request to
`/workforce` redirects to `/login` without touching the database. Added seven
new integration tests (`tests/integration/test_web_workforce_flow.py`)
covering the create-user round trip and every lifecycle action list above,
plus the unauthenticated and unauthorized-agent redirects; these could only be
collected, not run, locally - pushed for CI to give the real answer.

CI caught a real bug static checks and template parsing could not:
`user_detail`'s reporting-line query used `db.scalar(select(ReportingAssignment,
User)...)`, which collapses a multi-entity select down to just the first
entity, not the `(assignment, user)` tuple the template's `reporting_line[1]`
lookup expected - `db.scalar()` on a two-entity select silently drops the
joined row's second column rather than erroring, so nothing before a real
request through Jinja2 could have caught it. `test_set_reporting_line` failed
with `UndefinedError` on the very first CI run. Fixed by switching to
`db.execute(...).first()`, the same pattern the team-memberships query on the
same page already used correctly one section up - checked the rest of the
file for the same `db.scalar()`-on-a-joined-select mistake and found no other
instance. Re-pushed; CI green across all four jobs, 97 of 97 tests passing
(commit `31b1529`).

## 2026-08-28: Audit trail page

The last dashboard-embedded section without its own page. The master plan
names "protected audit search" as in-scope for Phase 4A, but only the
"protected" half existed - `list_visible_audit_events` (shared by the
dashboard's inline table and the JSON `GET /api/v1/admin/audit-events`) took a
`limit` and nothing else. Added optional `action` (substring, case-insensitive),
`result` (exact match against the three real values in use - success, failure,
denied), and `since`/`until` (date range) filters to that one shared function,
so the JSON endpoint and the new page both gained real search for free. The
filters narrow within whatever the actor's own visibility scope already allows
- they never widen it; a team-scoped Team Leader filtering by action still
only sees their own team's events.

Added `/audit`: a filter form (plain GET, so results are bookmarkable/
shareable, no CSRF needed since nothing state-changing happens) over a table of
matching events, each row resolving the actor's display name via one batched
lookup query (not N+1) rather than showing a bare UUID. Reused the same
`.ops-panel`/`.mapping-grid` chrome as the other control rooms; the results
table itself uses the plain `table`/`.table-wrap` CSS from this build's
original design pass, since the newer row-based `.assignment-list` pattern
suits two-column data and audit rows have six.

Verified everything not requiring a live database (still true as of this
entry - see the Phase 5A and Workforce Control Room entries above for why):
ruff and mypy clean, `audit.html` parses through the real Jinja2 loader, the
new route is present in the generated OpenAPI schema, and an unauthenticated
request redirects to `/login` without touching the database. This time,
learning from the reporting-line bug two entries up, double-checked every new
`db.scalar()`/`db.scalars()` call against its select()'s entity count before
pushing - the batched actor lookup is a single-entity `select(User)`, correctly
using `.scalars()`. Seven new integration tests: three at the JSON/service
level in `tests/integration/test_reporting_and_audit_flow.py` (action filter
narrows without widening scope, result filter, date-range filter proven
against a manually-backdated event) and four at the web level in
`tests/integration/test_web_audit_flow.py` (unauthenticated redirect,
unauthorized-agent redirect, a Manager's own login event renders, and the
filter form round-trips its submitted value back into the input while
genuinely narrowing results). Pushed for CI to confirm.

CI green across all four jobs on the first push (commit `ee6d5e6`) - the
extra care after the reporting-line bug held up. Every dashboard-embedded
section now has its own dedicated page (Campaigns, Agent Workbench,
Workforce, Audit); the original dashboard remains as a summary/overview only.

## 2026-08-28: Phase 5B - manual security review

Master plan Phase 5 requires container, dependency, dynamic, and manual
security review before pilot. CI already covers dependency (pip-audit) and
secrets (gitleaks) on every push; dynamic testing needs a running app,
unavailable with local Docker still down. This was the manual pass.

Tried the `security-review` skill first - it's built for reviewing a PR
diff, and it ran against the session's primary working directory (a
different, unrelated project) with nothing staged, not this repo. Read the
security-critical code directly instead: CSRF issuance and verification,
session/login handling, every state-changing route across all four web
control rooms (campaigns, workforce, audit, agent work) checked for CSRF
coverage and for authorization against the real loaded resource's scope
rather than attacker-supplied scope fields, the PII encryption/fingerprinting
layer, and a sweep for raw/interpolated SQL (none found).

Found one real, exploitable gap - in code from earlier today.
`app/web/workforce.py::team_detail` gated the management actions on a
team's page (add/remove member) but never checked whether the requester
could view the page at all. `workforce_list` already hides a team's tile
unless the viewer holds `can_manage_workforce` or `can_manage_teams`, but
`team_detail` had no equivalent check underneath it - any authenticated
user, including a plain Agent with no appointment capability, could load any
team's full roster by navigating straight to its URL. Confirmed
`user_detail`, the Campaign Control Room, and the Agent Workbench do not
have the equivalent gap - isolated to this one route. Fixed by adding the
same gate `workforce_list` already uses, with a new regression test
(`test_agent_cannot_view_a_team_detail_page_by_url`).

Two lower-severity items noted for awareness, not fixed now: encryption
key-rotation isn't actually wired up (`decrypt` parses the ciphertext's
version prefix but never uses it - harmless today since only one key has
ever been configured, but the first real rotation would silently break old
values), and `X-Forwarded-For` is trusted without confirming Caddy
overwrites rather than passes through client-supplied values (affects
rate-limit keying and audit-log IP accuracy only, no auth or data impact -
worth folding into Phase 5's own LAN/TLS verification pass). No SQL
injection, no XSS (autoescaping on throughout, no `|safe`/`Markup` anywhere),
no other CSRF or authorization gaps found. See PHASE-5-PLAN.md's "5B" section
for the full write-up. ruff and mypy clean, full non-integration suite
passing locally; pushed for CI.

CI green across all four jobs (commit `5140567`, run 33167864900), including
the new regression test running for real against CI's Postgres.

## 2026-08-28: Phase 5C - runbooks

Master plan Phase 5 lists "completed runbooks" as a deliverable. Tried the
`operations:runbook` skill first; it returned its own formatting template
rather than drafted content, so wrote `RUNBOOKS.md` directly against that
template, verifying every command against the real codebase instead of
approximating: `scripts/backup.sh`/`restore.sh`/`restore-test.sh` turned out
to already exist and already work (this session's earlier BUILD-LOG entries
had only referenced their output in prose, never the scripts themselves),
`scripts/release-manifest.sh`, every service's restart policy and healthcheck
in `compose.yaml` (beat has none - the runbook says so and gives a manual
verification instead of pretending one exists), and the real distinction
between `/healthz` (liveness only) and `/readyz` (an actual Postgres+Redis
check, gated by a health token) in `app/main.py`.

Six runbooks: deploy/release, rollback (built on the master plan's own
non-breaking-rollback spine - roll back the app image first, database restore
only with the incident owner's explicit approval, never delete volumes as a
routine step), backup and restore, service restart, cold boot, and disk
space. The cold-boot runbook's troubleshooting table includes the Docker
Desktop WSL2/HCS failure this session hit firsthand this week - a Windows
logon-type permission error blocking VM creation, and separately a full data
reset after a Docker Desktop self-update - real, specific, first-hand
knowledge rather than a generic "check if Docker is running" line.

Deliberately honest about what's still missing rather than writing around it:
the contacts table is role placeholders with no real names yet, flagged at
the top of the file; `deploy/monitoring/` is an empty scaffold, so the
disk-space runbook is a manual check, not a response to a real alert;
rollback's "pause new leases" step is approximated by stopping web/worker
entirely, since no finer-grained pause flag exists yet. Writing the runbooks
is done; actually drilling them against the real stack (what the master plan
calls "cold-boot, service-restart, disk-alert, backup, and restore drills")
still needs Docker, which is down until the user's own check on Saturday.

## 2026-08-28: Feature flags

Master plan section 21.2 names eight server-enforced, audited rollout flags
and none existed. Built the infrastructure and wired up the five that gate
something real today: `campaign_import_enabled` (`import_service.
create_import_job`), `campaign_launch_enabled` (`campaign_service.
launch_campaign`), `shared_pool_enabled` (`work_service.lease_next` - checked
*after* the existing-lease resume path, so it pauses new leasing without
abandoning work an agent already holds), `callbacks_enabled` (`work_service.
complete_work_item`, only when a callback is actually being scheduled), and
`viewer_enabled` (`workforce_service.assign_role`, only for new Viewer
grants - existing Viewer assignments keep working). `retention_execution_
enabled` and `analytics_enabled` are seeded but inert - nothing exists yet to
gate. `ai_enabled` is hard-locked false in code, not just documented as
"permanently false for MVP" - `set_flag` refuses to enable it, the same shape
as ADR-009's protected DNC semantic code.

This also closes a real gap found while writing RUNBOOKS.md's rollback
procedure: there was no way to pause new leasing without stopping the whole
web/worker service. `shared_pool_enabled` is that switch now.

Storage is a real table (migration 0010, seeded with values matching current
shipped behavior - a flag defaulting to off for an already-working feature
would be a regression, not a rollout gate), not env vars, so toggling is
audited and needs no redeploy. Every check happens once in the service layer;
the ten web/JSON API call sites just gained one more `except
FeatureDisabledError`. New `/flags` page and `/api/v1/flags`, gated by
`MANAGE_ROLES` for a first pass.

Verified everything not requiring a live database: ruff and mypy clean across
all 81 source files, every new/changed template parses through the real
Jinja2 loader, all four new routes present in the generated OpenAPI schema,
an unauthenticated request to `/flags` redirects to `/login` without
touching the database, and the new migration is confirmed correctly chained
as head via `alembic history` (reads migration files only, no DB connection
needed for that specific check). 17 new integration tests
(`tests/integration/test_flags_flow.py`) prove each enforcement point both
ways - off rejects, on is unaffected - plus the seeded defaults, the audit
trail, and the ai_enabled lock through all three surfaces (service, web,
JSON API); these could only be collected, not run, locally. Local Docker
Desktop is still down (see the Phase 5A entry), so this is another push-for-
CI-to-confirm, same as everything else built while it's been unavailable.

First CI run found one real bug - in the test fixture, not the flags feature.
`_draft_campaign` built a campaign with zero contacts; `launch_campaign`'s own
separate precondition ("no committed imported contacts") tripped once the
flag check passed, which is exactly what should happen - the off-path
assertion had already proven the flag itself worked before the fixture's gap
surfaced. Gave the fixture a committed contact, re-pushed, CI green across
all four jobs (commit `e84fa3d`, run 33170144544, 122/122 integration tests).

## 2026-09-02: Review of two Codex-authored PRs (isolated deployment, MFA enrollment)

Two PRs landed from the parallel Codex UI/deployment track since the feature-
flags work above, merged to `main` while this session had no visibility into
them: `1ae2e65` "Add isolated deployment and secure bootstrap activation"
(PR #1) and `1512225` "Enforce secure browser TOTP enrollment" (PR #2). Asked
to check what changed in the repo; reviewed both in full rather than
assuming CI-green was sufficient, since both touch auth/secrets.

**PR #1** adds `app/ops/bootstrap_super_admin.py` (an operator-only CLI, no
HTTP route, for creating exactly the first Super Admin) plus
`deploy/vps/{compose.yaml,app.env.example,ciphercontact.caddy.example}`.
Verified: the advisory-lock pattern (`db_locks.py::lock_initial_super_admin`)
matches the existing leasing lock's shape; a second bootstrap attempt is
rejected once any active Super Admin exists; the one-time activation token is
written with `O_EXCL|O_NOFOLLOW`, mode 0600, and a resolved-path containment
check before write, with the token file removed again if the surrounding
transaction doesn't commit; `compose.yaml` publishes no host ports, runs the
app as non-root with `cap_drop: ALL`, `read_only` root filesystem, and puts
Postgres/Redis on an `internal: true` network reachable only from `web`/
`worker`/`beat`; the `bootstrap` service is a separate `ops`-profile
container never started by a plain `up`.

**PR #2** adds `app/web/security.py` (`/security/mfa`) and reworks the
current-user dependencies (`app/auth/dependencies.py`,
`app/web/dependencies.py`) into two tiers: `get_authenticated_user`/
`require_authenticated_page_user` (valid session only) and `get_current_user`/
`require_page_user` (also requires `totp_enrolled` and `session.mfa_state ==
MFA_SATISFIED`) - the enforcing versions kept the original names, so every
pre-existing caller across `app/web/*.py`, `app/api/*.py`, and
`app/authz/dependencies.py::require_capability` (which `app/api/work.py`
sits behind) now enforces MFA without having been touched individually.
Verified by grep that only the two legitimately pre-MFA surfaces
(`app/web/security.py` itself and the JSON `totp_enroll`/`totp_verify`/
`reauthenticate` endpoints in `app/auth/router.py`) use the non-enforcing
dependency. A `RedirectToMfaEnrollment` exception + handler in `app/main.py`
sends a gated browser request to `/security/mfa` the same way
`RedirectToLogin`/`InvalidFormCsrf` already did. Enrollment itself requires a
fresh password (+ existing TOTP if any) re-authentication step-up before a
secret can be created or verified, both steps rate-limited and audited, and
successful verification revokes every other session for the user before
issuing a new `MFA_SATISFIED` one. The secret is rendered once, in a POST
response only, never a GET/URL/log. Test fixtures in
`tests/integration/conftest.py` were retrofitted centrally (`make_user`/
`make_user_with_role` now default `totp_enrolled=True`; `login()` computes a
live `pyotp` code) rather than touching every existing test individually.

No defects found in either PR. Confirmed ruff and mypy clean repo-wide (84
source files) and both PRs' CI green on both the feature branch and the
`main` merge commit (build/security/integration/quality). One side effect
worth recording: PR #1's `ciphercontact.caddy.example` has the edge Caddy
overwrite (not append) `X-Forwarded-For`/`X-Real-IP`, closing finding #3 from
the 5B security review below - see that entry and PHASE-5-PLAN.md, updated
to reflect it. Finding #2 from that same review (encryption key-rotation not
wired up) is untouched by either PR and remains open.

## 2026-09-02: Phase 4B-1 - staged bulk-workforce import (users + deactivations)

The user handed over the remaining roadmap in one message: 4B (bulk
workforce import), 4C (targets and exemptions), the remaining operational
workflows, then the pilot. Full detail in `PHASE-4B-PLAN.md`. Started with
4B since it's a prerequisite for a real pilot either way (bulk-onboarding
10-20 pilot users by hand doesn't reflect real operating conditions), and
scoped the first increment to two of the master plan's seven import types -
`users` (create/update/reactivate) and `explicit_deactivations` - picked
specifically because they're a matched pair: routine risk and high risk, so
building both together proves out the phase's two defining mechanisms
(staged approval and the two-person high-risk rule) rather than just the
easy half.

Mirrors the existing, already-proven campaign-contact import pipeline
(`app/imports/*`) at the architecture level, not the code level: bounded
CSV/XLSX parsing, upload quarantine, and file validation
(`app/imports/{parser,storage,validators}.py`) are genuinely generic and
reused unmodified. Everything campaign-shaped (phone fingerprints,
suppression, `Contact`/`WorkItem`) is not, so classification, orchestration,
models, and the HTTP surface are a new sibling package
(`app/workforce_imports/`) rather than a shared one forced to branch on
import type.

Two things the campaign importer doesn't have, because nothing in this
build needed them until now:

**Two-person high-risk approval.** A job with any high-risk row (every
`explicit_deactivations` row, by definition) needs a *second* decision, from
someone who is not the uploader and who independently holds real authority
over every affected user (`workforce_service.can_manage_user`, the same
check the one-at-a-time disable screen already uses) - checked live at
decision time, re-checked live again at commit time, and re-checked a third
time at reversal, since authority that existed when a decision was recorded
is not assumed to still hold whenever commit or reversal actually run.

**Compensating reversal.** New `WorkforceImportRow.pre_commit_snapshot`
captures the fields a row overwrote, immediately before it overwrites them.
Reversing an `update` row restores that snapshot, but only if the field's
current value still equals what the row itself produced - if anything else
has touched it since, the row is left alone and reported as conflicting
rather than guessed at. `create`/`reactivate`/`deactivate` rows don't need a
snapshot at all: their reversal is just the natural inverse service call
(deactivate undoes a create or a reactivate; reactivate undoes a
deactivate), gated by the identical live-state check.

New `workforce_import_enabled` rollout flag (migration 0012), seeded
**false** - unlike every flag seeded in migration 0010, this gates a feature
that doesn't exist anywhere yet, so there's nothing already-working to
protect by defaulting it on. Checked once, in `create_import_job`, matching
`campaign_import_enabled`'s own scope (blocks new uploads only; an
already-parsed job can still be reviewed, committed, or reversed while the
flag is off, same "pause new work, don't abandon what's in flight" shape).

Activation tokens for newly-created users follow the same never-persisted,
shown-once pattern as every other secret in this build: returned directly
in the commit response and rendered once
(`workforce_import_committed.html`, same reasoning as `user_created.html`),
never written into `committed_result` (which persists indefinitely to back
idempotent replay), and empty on a replayed commit rather than reissued.

Caught three real bugs during self-review, before ever pushing: `high_risk_rows`
was initially counting warning-tier rows (a no-op reactivate-already-active
or deactivate-already-inactive) too, which would have forced an unneeded
approval step on a job with nothing actually high-risk left to commit; two
`scalar_one_or_none()` lookups against users (which are never hard-deleted,
only deactivated) were defensively None-checked in a way that didn't match
this codebase's own established "trust the internal invariant" convention
used elsewhere (e.g. the campaign importer's own `commit_job`) - fixed to
`scalar_one()`; and the commit result's activation tokens were, for one
draft, reachable from the idempotent-replay code path, which would have
reissued a one-time secret on a second call with the same idempotency key.

Verified everything not requiring a live database: ruff and mypy clean
across all 92 `app/` source files (matching what CI's quality job actually
checks - confirmed the handful of pre-existing mypy gaps surfaced by
checking `tests/` too belong to older, unrelated test files and are out of
that scope, not something to fix here), every new template through the real
Jinja2 loader, all 12 new routes (6 JSON API, 6 web) present in the
generated OpenAPI schema, unauthenticated requests to the new web and API
surfaces correctly redirect/401 without touching the database, migrations
0011/0012 confirmed correctly chained as head via `alembic history`, and
the full non-integration suite passing locally (35/35, unaffected). 7 new
integration tests (`tests/integration/test_workforce_imports_flow.py`)
cover the full create/update/reactivate flow with a real activation token,
bad-action and duplicate-in-file rejection, a malformed template header
failing parse cleanly, the complete high-risk path (self-approval rejected,
wrong-scope approver rejected, premature commit rejected, right-scope
approver succeeds), idempotent replay without reissuing tokens, and
reversal (rejected for the uploader, succeeds for a qualified non-uploader,
cannot be repeated). No live database this session, so these could only be
collected, not run - pushing for CI to give the real answer.
