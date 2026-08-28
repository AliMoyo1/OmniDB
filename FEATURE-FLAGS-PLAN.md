# Feature flags plan

Status: done (2026-08-28). CI green on `e84fa3d`.

## Scope

Master plan section 21.2 ("Rollout and rollback strategy") names eight
server-enforced, audited flags as the intended mechanism for a gradual pilot
rollout: `campaign_import_enabled`, `campaign_launch_enabled`,
`shared_pool_enabled`, `callbacks_enabled`, `viewer_enabled`,
`retention_execution_enabled`, `analytics_enabled`, `ai_enabled` (permanently
false). "Flags must be server-enforced and audited. They are not a substitute
for authorization" - a flag is a second, orthogonal gate alongside the
existing capability checks, not a replacement for them.

This also closes a real gap found while writing `RUNBOOKS.md`'s rollback
procedure: there is currently no way to pause new work-item leasing without
stopping the whole `web`/`worker` service. `shared_pool_enabled` becomes that
switch.

## Design

**Storage:** a real DB table (`feature_flags`), not env vars or a config
file - toggling a flag must be auditable and must not require a redeploy,
matching how every other piece of mutable state in this build already works.
One row per flag: `flag_key` (PK, string), `enabled` (bool), `updated_by`,
`created_at`/`updated_at` (via the existing `TimestampMixin`).

**Service (`app/flags/service.py`):** `is_enabled(db, flag_key) -> bool` (fails
safe - an unseeded/unknown key reads as disabled, never silently permissive),
`set_flag(db, flag_key, enabled, *, actor_id, reason_code=None)` (audited via
the existing `record_audit`, same pattern as every other state change in this
build), `list_flags(db)`. `FeatureDisabledError` is the shared exception
service-layer checks raise; callers already have an exception-handling block
at each site listed below and just gain one more `except`.

**`ai_enabled` is hard-locked false** - `set_flag` refuses to enable it,
regardless of caller, full stop. The master plan says "permanently false for
MVP," and enforcing that in code (raise, don't just document) matches this
build's own established pattern for a hard invariant (ADR-009's
`PROTECTED_DNC_SEMANTIC_CODES` is the same shape: one specific thing the code
itself refuses to allow, not a convention trusted to hold).

**Seeded defaults (migration inserts all eight rows immediately - a flag
defaulting to a value that breaks an already-shipped, currently-working
feature is a regression, not a rollout):**

| Flag | Default | Why |
|---|---|---|
| `campaign_import_enabled` | `true` | Already shipped and working |
| `campaign_launch_enabled` | `true` | Already shipped and working |
| `shared_pool_enabled` | `true` | Already the only leasing path that exists |
| `callbacks_enabled` | `true` | Already shipped and working |
| `viewer_enabled` | `true` | Viewer role already shipped (4A-3) |
| `retention_execution_enabled` | `false` | No retention-execution code exists yet - nothing to gate, matches current reality |
| `analytics_enabled` | `false` | Phase 6 doesn't exist yet - inert until it does |
| `ai_enabled` | `false` | Permanently - see above |

**Enforcement points** - one check per feature, in the service layer (not
duplicated per web/API caller), so both surfaces get it for free:

| Flag | Checked in | Real effect when off |
|---|---|---|
| `campaign_import_enabled` | `import_service.create_import_job` | No new import can be started (in-flight ones already parsed/committed are unaffected) |
| `campaign_launch_enabled` | `campaign_service.launch_campaign` | A draft campaign cannot move to active |
| `shared_pool_enabled` | `work_service.lease_next`, checked *after* the existing-lease resume path so an agent can still finish what they already hold - matches "pause new leases," not "abandon in-progress work" | No agent can acquire a **new** lease; resuming/completing an existing one still works |
| `callbacks_enabled` | `work_service.complete_work_item`, only when `callback_at` is actually supplied | A completion without a callback still works; scheduling one is rejected |
| `viewer_enabled` | `workforce_service.assign_role`, only when `role_code == "viewer"` | No **new** Viewer grant; existing Viewer assignments are untouched (a lighter-touch rollout gate, not a live kill switch for people already using it) |
| `retention_execution_enabled` | nowhere yet | Inert - no retention-execution code exists to gate |
| `analytics_enabled` | nowhere yet | Inert - Phase 6 doesn't exist |
| `ai_enabled` | `set_flag` itself (see above) | Cannot be turned on at all |

Ten call sites need the new `except FeatureDisabledError` added (5 features x
web + JSON API each): `app/web/campaigns.py` (`upload_import`,
`campaign_lifecycle`'s launch branch), `app/api/campaigns.py` (`upload_import`,
`launch_campaign`), `app/web/agent_work.py` (`next_contact`,
`complete_contact`), `app/api/work.py` (`lease_next`, `complete_work_item`),
`app/web/workforce.py` (`assign_role_action`), `app/api/workforce.py`
(`assign_role`).

**New surface to view/toggle flags:** `/flags` (web) + `GET/POST
/api/v1/flags` (JSON), gated behind a real capability - reusing `MANAGE_ROLES`
(already the highest-privilege, broadest capability this build has for
non-Super-Admin operators) rather than inventing a new one for a first pass.
`ai_enabled`'s row renders read-only/disabled in the UI, not just relying on
the backend rejection.

## Out of scope for this pass

- Wiring `retention_execution_enabled`/`analytics_enabled` to real behavior -
  nothing exists yet for them to gate.
- A dedicated `FEATURE_FLAG` capability separate from `MANAGE_ROLES` - revisit
  if flag management needs to be delegated more narrowly than "can manage
  roles" during real pilot ops.

## Verification status
- [x] Migration 0010 (confirmed correctly chained as head via `alembic
      history`, which reads migration files only - no live DB needed for
      that check), model, service, `ai_enabled` hard-lock.
- [x] All five enforcement points wired, with 17 new integration tests
      (`tests/integration/test_flags_flow.py`) proving both directions per
      flag where relevant: off rejects cleanly, on is unaffected, and for
      shared_pool_enabled specifically, off blocks a *new* lease but not
      resuming one already held. Also covers the seeded-defaults migration
      data, the audit trail on `set_flag`, and the `ai_enabled` hard lock
      (cannot be enabled via the service, the web page, or the JSON API).
- [x] `/flags` web page + JSON API, capability-gated (`MANAGE_ROLES`), with
      `ai_enabled` genuinely unreformable through either surface - the web
      page also renders it as non-toggleable rather than just relying on the
      backend rejection.
- [x] ruff and mypy clean repo-wide (81 source files), all four new/changed
      templates parse through the real Jinja2 loader, all four new routes
      confirmed present via the generated OpenAPI schema, unauthenticated
      `/flags` redirects to `/login` without touching the database, full
      non-integration suite passes locally (27/27, unaffected) - no live DB
      available this session for the integration suite itself.
- [x] CI green on `e84fa3d` (run 33170144544): build, security, integration
      (122/122), quality all passed. First push (`4093fc7`) surfaced one real
      bug, but in the test's own fixture, not the flag logic - the off-path
      assertion had already passed before the fixture's missing-contact issue
      hit on the re-enabled path. Fixed and confirmed green.

## Log
- 2026-08-28: reconciled scope against master plan section 21.2, confirmed
  `shared_pool` is a real (if currently unused) model field and
  `retention_execution`/`analytics` have no existing code to gate, found the
  five real enforcement points and their exact call sites, wrote this plan.
- 2026-08-28: built migration 0010, the `FeatureFlag` model, `app/flags/
  service.py`, wired all five enforcement points and their ten web/JSON API
  call sites, built `/flags` + `/api/v1/flags`, wrote 17 integration tests.
  ruff/mypy clean repo-wide, full non-integration suite passes locally,
  migration confirmed correctly chained via `alembic history`. Pushing for
  CI to give the real answer on the integration suite (no live DB this
  session).
- 2026-08-28: first CI run failed one test - `_draft_campaign`'s fixture had
  no contacts, so `launch_campaign`'s own separate precondition tripped once
  the flag check passed, not a flag bug (the off-path assertion had already
  succeeded). Fixed the fixture, re-pushed, CI green (commit `e84fa3d`, run
  33170144544) - done.
