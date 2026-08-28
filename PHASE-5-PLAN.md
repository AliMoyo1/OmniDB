# Phase 5 plan: production hardening and controlled pilot

Status: 5A (concurrency and load tests), 5B (manual security review), and 5C
(runbooks) complete (2026-08-28). Backup/restore and cold-boot/service-restart
*drills* - actually running these runbooks against the real stack, not just
writing them - still need Docker, on hold until it's back. Rest of Phase 5
(container/dynamic security review, LAN cert/Tailscale distribution, training,
the real pilot) not started.

## Scope reconciliation against the decision log

The master plan (`docs/architecture/CipherContact - Detailed Implementation Plan
v0.3.md`, section "Phase 5") is "production hardening and controlled pilot": release
candidate, security review, concurrency/performance/soak tests, cert and Tailscale
distribution, restore drills, training, a real pilot with 10-20 users, and multi-
stakeholder go-live approval.

Most of that is not buildable in this environment - it needs the user's real
infrastructure, real people, and real approvals. What's genuinely engineering work
that can be built and verified here is split into sub-phases as it's picked up; this
file only plans the sub-phase currently in progress, not all of Phase 5 up front,
matching how Phase 4 was broken into 4A/4B/4C rather than planned monolithically.

Asked the user which slice to start with; chose **concurrency & load tests**
(master plan Phase 5 step 3 - "run full concurrency and performance tests"). Step
4's eight-hour soak/resource-growth test needs production-like hardware and a
sustained real-world run, out of scope for this sub-phase - noted as a follow-up for
the user to run during actual pilot prep using whatever this sub-phase produces.

## 5A: concurrency and load tests [done]

`tests/concurrency/` already covers two real invariants under genuine concurrent
Postgres transactions (separate thread, separate session, real races, not
simulation): leasing (`SELECT ... FOR UPDATE SKIP LOCKED` - no duplicate leases) and
DNC suppression (advisory lock ordering against both leasing and import commit).
Both were written in earlier phases and both still pass.

Before adding anything new, audited the rest of the codebase for other check-then-act
sequences with the same shape (read a count/state, decide, then write, with no lock
holding the decision and the write in the same atomic scope) and no existing test.
Found one, from this session's own 4A-2 work:

`app/campaigns/service.py::_check_staffing_capacity` reads
`COUNT(active CampaignUserAssignment rows for this campaign+team)`, compares it to
`CampaignTeamAssignment.staffing_capacity`, and raises if at or over. The caller
(`assign_agent_to_campaign` or `transfer_agent`) then inserts a new active
assignment. There is no row lock, no advisory lock, and no DB-level constraint
enforcing the capacity - two concurrent assignment calls for the same (campaign,
team) can both read the same under-capacity count before either commits, and both
succeed, landing the team over its configured capacity.

Plan:
1. Write a concurrency test that races N concurrent `assign_agent_to_campaign`
   calls (distinct agents) against one (campaign, team) whose capacity is smaller
   than N, using the same real-thread/real-session/real-Postgres pattern as the
   existing two files. Confirm it demonstrates the race (over-capacity acceptance)
   against the current code first - a red test proving the bug is real, not assumed.
2. Fix `_check_staffing_capacity` to hold a row lock on the `CampaignTeamAssignment`
   row for the transaction's duration (`SELECT ... FOR UPDATE`), serializing
   concurrent capacity checks for the same team assignment - the same shape of fix
   the leasing path already uses, applied to the one place that needed it. This
   covers both call sites (`assign_agent_to_campaign` and `transfer_agent`)
   automatically since both go through the one function.
3. Re-run the test to confirm it now passes (capacity is genuinely enforced under
   real concurrency, not just in the sequential/single-request case the existing
   integration tests already cover).
4. Add a basic throughput/load test under `tests/performance/` (currently an empty
   scaffold from Phase 1): a larger-scale version of the leasing race - more agents,
   more work items, asserting the whole pool drains with no duplicate leases and
   within a bounded time - to have at least one real number for "how does leasing
   behave under load" rather than none. Marked with a new `performance` pytest
   marker, deliberately *not* wired into CI's `integration` job: shared CI runners
   give meaningless/noisy throughput numbers, and the master plan itself says real
   performance validation happens on approved hardware, not commodity CI - these
   are for the user to run on real hardware during pilot prep, and for local sanity
   checks now.
5. ruff/mypy/existing suite clean, new tests passing locally against the docker
   Postgres via the same mechanism used earlier this session (`.venv` + a temporary
   non-persistent port-forward, or whatever proves reachable at the time), then
   push and watch CI for the authoritative result on the new `concurrency`-marked
   test (CI's `integration` job already runs everything under `tests/`, so the new
   staffing-capacity test rides along automatically; the new `performance`-marked
   test does not, by design).

## 5A verification status
- [x] Staffing-capacity race test written
      (`tests/concurrency/test_staffing_capacity_concurrency.py`).
- [~] Confirmed red against unfixed code by inspection, not by an empirical
      local run: Docker Desktop went down mid-session (its processes were
      running but its CLI and every container, including the one this session
      had been using all along, stopped responding - not this sandbox's
      earlier network restriction, a genuine local outage) partway through
      this increment, before the test had been run once. Read
      `_check_staffing_capacity` line by line instead: a plain `SELECT`
      (no `FOR UPDATE`, no advisory lock) followed by a separate `INSERT` in
      the caller, with nothing else in either code path that would serialize
      two concurrent callers - the same shape of race
      `test_leasing_concurrency.py` already proves is real for leasing
      without a lock, and already proves is closed once one is added. Treating
      this as sufficient to proceed rather than blocking the whole increment
      on Docker coming back, since CI verification (below) does not depend on
      local Docker at all and gives a genuine answer either way.
- [x] Fix applied (`FOR UPDATE` row lock on the `CampaignTeamAssignment` row
      in `_check_staffing_capacity`, covering both call sites -
      `assign_agent_to_campaign` and `transfer_agent` - since both already
      went through this one function).
- [ ] Test confirmed green: pending CI (local Docker still down as of this
      write-up).
- [x] Load/throughput test added under `tests/performance/`
      (`test_leasing_throughput.py`, 60 agents x 60 items, full-contention
      drain, prints leases/sec). New `performance` pytest marker added,
      deliberately excluded from *both* CI jobs that run test files
      (`quality`'s `not integration` become `not integration and not
      performance` - it has no database service and would otherwise try to
      collect a DB-dependent test; `integration`'s plain `pytest -m
      integration` already excludes it by construction). Verified locally via
      `--collect-only` that the marker split is exactly right: quality's
      selector still collects the same 27 tests as before these changes,
      integration's selector picks up the new staffing-capacity test and nothing
      from the performance file.
- [x] ruff and mypy clean on every changed file (checked locally, no Docker
      needed for either).
- [x] Pushed (commit `e4cbbfe`). CI's `integration` job failed - but on
      `test_login_page_renders_and_has_a_form`, not on anything from this
      increment (that test, and everything else, including the new
      staffing-capacity test, passed). Traced it to five commits from a
      different tool (`CodexSandboxOffline`, 2026-08-22) that redesigned the
      login page (video background, new copy, "premium UI system") and had
      been sitting on `main` with this one test broken for six days -
      unrelated to and predating this session's Phase 5A work, just inherited
      by pushing on top of it. Confirmed by inspection that it's a stale
      assertion, not a real regression: the test still checked for the exact
      literal `<form method="post" action="/login">`, and the redesign added
      `class="login-card"` to that tag - the Codex commit that added several
      *new* assertions for its own markup in this same test function simply
      missed updating this pre-existing one to match. Asked the user how to
      handle a pre-existing break from unrelated, uncoordinated work in the
      same repo; asked to fix and continue. Fixed
      (commit `c10dd0d`) and re-pushed.
- [x] CI green on `c10dd0d` (run 33158752533): build, security, integration,
      quality all passed. `main` is clean again; the new staffing-capacity
      test passed for real against CI's Postgres.

## 5B: manual security review [done]

Master plan Phase 5 step 2: "container, dependency, dynamic, and manual security
review." CI already covers dependency (pip-audit) and secret-scanning (gitleaks)
on every push; dynamic testing needs a running app, unavailable with local Docker
still down. This was the manual pass: read the security-critical code directly -
CSRF (`app/auth/csrf.py`, `app/web/dependencies.py::verify_form_csrf`), session/
login handling (`app/web/auth_pages.py`), and, route by route, every web
"control room" module (`campaigns.py`, `workforce.py`, `audit.py`,
`agent_work.py`) checking that each state-changing action holds `verify_form_csrf`
and checks the real capability against the *actual loaded resource's* scope, not
attacker-supplied scope fields - the classic IDOR shape. Also checked the PII
encryption/fingerprinting layer (`app/security/encryption.py`,
`app/security/phone.py`) and swept for raw/interpolated SQL (none found -
everything goes through parameterized SQLAlchemy Core/ORM).

Tried the `security-review` skill first; it's built for diffing a PR/branch, not
auditing a whole existing app, and it ran against the session's *primary* working
directory (a different, unrelated project) rather than this repo, with nothing
staged to diff - unusable here. Did the review directly instead.

### Findings

1. **Broken access control, `app/web/workforce.py::team_detail` (fixed)** - the
   route checked authorization for the *management actions* on a team's page
   (`can_manage_team`, gating add/remove-member forms) but never checked whether
   the requester was authorized to *view* the page at all. `workforce_list`
   already gates whether a team's tile - and link - appears on the list page
   behind `can_manage_workforce OR can_manage_teams`; `team_detail` had no
   equivalent check, so any authenticated user, including a plain Agent with no
   appointment capability, could load any team's full roster (names, workforce
   IDs) by navigating straight to `/workforce/teams/{team_id}` - a classic forced-
   browsing gap: the list page hides it, the endpoint underneath doesn't
   independently enforce it. Confirmed `user_detail`, `campaigns.py`, and
   `agent_work.py` do NOT have the equivalent gap (each checks a real capability
   or resource-ownership condition before rendering anything) - isolated to this
   one route. Fixed by adding the same `can_manage_workforce OR can_manage_teams`
   gate `workforce_list` already uses, so "can see the tile" and "can open the
   page" are the same bar again. New regression test:
   `test_agent_cannot_view_a_team_detail_page_by_url` in
   `tests/integration/test_web_workforce_flow.py`.
2. **Encryption key-rotation is not actually implemented (not fixed - noted for
   awareness)** - `app/security/encryption.py::decrypt` parses the `v{n}:` version
   prefix off the ciphertext but never uses it; every decrypt uses whatever key is
   currently configured, regardless of which version encrypted the value. Not
   exploitable today (only one key has ever been configured, so nothing is
   actually broken yet), but the versioning scheme implies rotation is supported
   when it isn't - the first real key rotation would silently fail to decrypt
   every value encrypted under the old key. Worth a real fix before this build
   ever rotates `FIELD_ENCRYPTION_KEY` for real, not before pilot.
3. **`X-Forwarded-For` trusted without a known-proxy check (not fixed - noted for
   awareness, not a reportable vulnerability)** - `app/web/auth_pages.py::_client_ip`
   takes the first value from `X-Forwarded-For` unconditionally. If a client can
   ever reach the app directly (bypassing Caddy) or Caddy doesn't overwrite rather
   than append client-supplied values, a request could spoof its apparent source
   IP. The only consequences are IP-based login rate-limit keying (rate-limiting
   is explicitly out of scope for this kind of review - it fails safe, not open)
   and less accurate `source_ip` on audit events - no auth bypass, no data
   exposure. Worth confirming Caddy's proxy config overwrites rather than trusts
   incoming `X-Forwarded-For` as part of Phase 5's LAN/TLS verification pass, not
   urgent on its own.

No SQL injection, no XSS (Jinja2 autoescaping is on throughout, confirmed no
`|safe`/`Markup` usage on any user-controlled value), no CSRF gaps (every state-
changing route checked holds `verify_form_csrf`; `/login` correctly has none,
since there's no session yet to bind a token to), no hardcoded secrets, no
authentication bypass found elsewhere.

### 5B verification status
- [x] Full manual pass across auth, CSRF, authorization (all four control rooms),
      PII encryption/fingerprinting, and a SQL-injection sweep.
- [x] Fix applied for the one real finding (`team_detail` view-authorization
      gap), with a regression test.
- [x] ruff and mypy clean; full non-integration suite passes locally (27/27).
- [x] CI green on `5140567` (run 33167864900): build, security, integration,
      quality all passed, including the new team_detail regression test
      running for real against CI's Postgres.

## 5C: runbooks [done]

Master plan Phase 5 lists "completed runbooks" as a deliverable and "support
and incident owners can execute runbooks" as a success criterion. Tried the
`operations:runbook` skill; it returned its own formatting template rather than
generated content (evidently a style guide to apply, not an autonomous
drafting agent) - wrote the runbooks directly against that template, using
real commands and behavior read from the actual codebase rather than
approximating from memory: `scripts/backup.sh`/`restore.sh`/`restore-test.sh`
(already-working encrypted-backup tooling this session had only referenced in
prose before), `scripts/release-manifest.sh`, every service's `restart:`
policy and healthcheck (or lack of one - `beat` has none) from `compose.yaml`,
and the real `/healthz` (liveness only) vs `/readyz` (genuine Postgres+Redis
check, health-token-gated) distinction from `app/main.py`.

Six runbooks in `RUNBOOKS.md`: deploy/release, rollback (built directly on the
master plan's own non-breaking-rollback spine - image first, database restore
only with incident-owner approval, never delete volumes), backup and restore,
service restart (dependency order matters - `web`/`worker` need `postgres`/
`redis` healthy first), cold boot, and disk space. Folded in the Docker
Desktop WSL2/HCS failure mode this session hit firsthand (a Windows logon-type
permission error blocking VM creation, and separately a data-reset after a
Docker Desktop self-update) as a real troubleshooting entry in the cold-boot
runbook, since it's genuine first-hand knowledge, not a hypothetical.

Being honest about what's still a gap rather than papering over it: contacts
are role placeholders (no real names/phone numbers exist yet - flagged at the
top of the file), `deploy/monitoring/` is an empty scaffold so the disk-space
runbook is a manual check, not a response to a real alert, and rollback's
"pause new leases" step is approximated by stopping `web`/`worker` entirely
since no finer-grained pause flag exists.

### 5C verification status
- [x] Verified every command/behavior claim against the actual scripts and
      source (`compose.yaml`, `app/main.py`, `scripts/*.sh`) rather than
      writing from memory or convention alone.
- [ ] Actually running these runbooks against the real stack (the drills the
      master plan asks for, not just having them written) - needs Docker,
      on hold.
- [x] Committed and pushed (docs-only change, no app code - CI will run but
      isn't the meaningful verification here; reading the runbook against the
      real stack is).

## Log
- 2026-08-21: reconciled Phase 5 scope, asked the user which slice to start with
  (concurrency & load tests chosen), audited for untested check-then-act races,
  found the campaign staffing-capacity gap, wrote this plan.
- 2026-08-21: wrote the staffing-capacity concurrency test, applied the
  `FOR UPDATE` fix, added the throughput test under `tests/performance/` and
  the new `performance` marker, fixed CI's `quality` job selector to exclude
  it. Local Docker Desktop went down mid-increment (unrelated to this
  session's earlier port-publishing restriction - a real local outage, CLI
  and containers both unresponsive); diagnosed the race by code inspection
  instead of an empirical local red run, and moved straight to push + CI
  rather than block on Docker recovering, since CI's Postgres is independent
  of it either way.
- 2026-08-28: pushed 5A; CI's integration job failed on a pre-existing,
  unrelated break inherited from a different tool's login-page redesign that
  had been on `main` unfixed since 2026-08-22 (see verification status above).
  Asked the user how to handle it; fixed the stale test assertion and
  re-pushed on their instruction. CI green on the re-push (run 33158752533,
  commit `c10dd0d`) - 5A done.
- 2026-08-28: ran 5B (manual security review) directly after the skill's diff-
  review mode turned out not to fit a whole-app audit. Found and fixed a real
  broken-access-control gap (team_detail's missing view check), noted two
  lower-severity operational findings (encryption key-rotation not actually
  wired up, X-Forwarded-For trust) for awareness rather than immediate fixes.
  ruff/mypy/local suite clean; pushing for CI. CI green
  (commit `5140567`, run 33167864900) - 5B done.
- 2026-08-28: wrote 5C (RUNBOOKS.md - deploy/release, rollback, backup and
  restore, service restart, cold boot, disk space), verified every command
  against the real scripts/compose.yaml/app code rather than writing from
  memory. Actually drilling them against the real stack still needs Docker.
