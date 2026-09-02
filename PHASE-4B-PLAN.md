# Phase 4B plan: staged bulk-workforce import

Status: 4B-1 (users + explicit_deactivations) done, CI green on `97615bc`.
4B-2 (team_memberships, role_assignments, reporting_assignments) done, CI
green on the first push, `9782c92` (2026-09-02).

## Scope note: sequencing versus the master plan

The user's roadmap orders this as 4B, then 4C (targets/exemptions), then the
remaining workflows, then the pilot. The master plan's own v0.3 delivery note
(section "Phase 4" delivery note) says the opposite for 4C specifically: it
"is effectively an embedded performance-management module and is the
strongest candidate to defer until after the first pilot proves the core
call-work loop." Recorded here for visibility, not acted on unilaterally -
4B is first either way, so it doesn't block starting. Worth a decision before
4C starts.

## Scope for this increment (4B-1)

Master plan 11.2 defines seven import types sharing one staged pipeline.
Building all seven as one increment would be unreviewable. This increment
builds the shared pipeline plus two of the seven types - deliberately picked
as a pair because the master plan's own one-line definition of this phase
names both pillars: "staged bulk-workforce import, **high-risk approval**,
atomic commit, and compensating reversal."

- **`users`** - create, update, reactivate. Routine risk tier.
- **`explicit_deactivations`** - deactivate only, its own template per
  "deactivation requires an explicit action column" and "high-risk rows
  cannot hide among ordinary updates." High-risk tier - this is what proves
  out the two-person high-risk approval path.

Deferred to later increments, once this pipeline is proven:
- **4B-2**: `team_memberships`, `role_assignments` (also high-risk -
  elevation), `reporting_assignments`.
- **4B-3**: `campaign_user_assignments` (transfers prorate targets per
  11.4/11.6 - depends on whatever 4C decision is made), plus reversal
  hardening once real usage surfaces edge cases.
- **`target_assignments`**: blocked entirely on 4C (no `target_policies`
  table exists yet to reference).

## Design

### Reuse versus new

Master plan 11.2: "Use a staged import wizard based on the safe
campaign-import pattern." Mirroring `app/imports/*` closely:

| Piece | Campaign-contact import | Workforce import |
|---|---|---|
| Upload quarantine, hashing | `app/imports/storage.py` | **reused unmodified** - already generic (no phone/campaign-specific logic) |
| CSV/XLSX bounded parsing | `app/imports/parser.py` | **reused unmodified** - same reason |
| Extension/signature/zip-bomb validation | `app/imports/validators.py` | **reused unmodified** - same reason |
| Row classification (phone fingerprint, suppression, dup) | `app/imports/classify.py` | **new**: `app/workforce_imports/classify.py` - identity match by `external_workforce_id`, not phone |
| Job/row/decision orchestration | `app/imports/service.py` | **new**: `app/workforce_imports/service.py` - same shape (create job, parse, preview, decide, commit, cleanup) plus high-risk approval and reversal, neither of which the contact importer has |
| Models | `app/models/imports.py` | **new**: `app/models/workforce_imports.py` |
| Celery tasks | `app/imports/tasks.py` | **new**: `app/workforce_imports/tasks.py`, same wrapper shape |
| HTTP surface | `app/api/campaigns.py` imports section | **new**: `app/api/workforce_imports.py` + `app/web/workforce_imports.py` |

A single shared `app/imports/` module was considered and rejected: the
campaign importer's commit logic is inseparably phone/suppression/DNC-shaped
(`lock_phone_fingerprint`, `Contact`, `WorkItem`), and forcing workforce rows
through the same `classify_row`/`commit_job` functions would mean threading
an `import_type` branch through code that currently has none - more coupling
for less clarity than two small sibling packages sharing the same lower-level
`parser`/`storage`/`validators`.

### Data model

Following the master plan's own field lists (10.2 `workforce_import_jobs`
and `workforce_import_rows`) rather than inventing new names, with two
additions justified below.

`WorkforceImportJob` (`workforce_import_jobs`):
`id`, `import_type` (`users` | `explicit_deactivations`, more values added
in 4B-2), `uploader_id`, `source_filename_display`, `generated_storage_key`,
`file_hash`, `state` (`quarantined` -> `parsing` -> `parsed` -> `committed` |
`failed` | `expired`; `reversed` is tracked via `reversed_at`, not a
terminal state, since a committed job stays committed - reversal is a
compensating action, not an undo of history), `total_rows`, `valid_rows`,
`warning_rows`, `invalid_rows`, `high_risk_rows`, `decision_version`,
`idempotency_key`, `error_summary`, `expires_at`, `created_at`,
`committed_at`, `reversed_at`.

`WorkforceImportRow` (`workforce_import_rows`): `id`, `import_job_id`,
`row_number`, `action` (`create` | `update` | `reactivate` | `deactivate`),
`external_workforce_id`, `normalized_identity` (matched user ID once
resolved, nullable pre-match), `parsed_values` (JSONB - the row's own
proposed field values), `validation_result` (`valid` | `warning` |
`invalid`), `validation_detail`, `conflict_type` (nullable -
`duplicate_in_file` | `unknown_identity` | `already_active` |
`already_inactive`, etc.), `risk_level` (`routine` | `high_risk`),
`decision` (nullable until committed - mirrors the job's own decision, not
a separate one), `committed_entity_id` (nullable).

Two additions beyond the spec's listed fields, both needed to make
"compensating reversal" real rather than aspirational:
- `committed_entity_type` (`user`, more types added in 4B-2) - the spec's
  `committed_entity_id` alone is ambiguous once more than one entity kind
  exists in the same table.
- `pre_commit_snapshot` (JSONB, nullable) - the exact field values
  overwritten at commit time. Reversal restores this snapshot **only if**
  the entity's current value still equals what this row itself produced
  (re-derived from `parsed_values`/`action`, not stored redundantly) -
  otherwise the row is reported as conflicting and left alone, per "allowed
  only if later changes do not conflict." This generalizes the same way
  across create/update/reactivate/deactivate without bespoke per-action
  undo code, and keeps every reversal decision auditable (the snapshot IS
  the evidence of what changed).

`WorkforceImportDecision` (`workforce_import_decisions`): same shape as the
campaign importer's `ImportDecision` (`import_job_id`, `decision_version`,
`decision`, `decided_by`, `note`), with one addition: `decision_tier`
(`standard` | `high_risk`). A job with any `high_risk` row needs both a
`standard` decision (the uploader or any capable reviewer clearing ordinary
rows) and a separate `high_risk` decision before commit - see below.

### High-risk approval (two-person rule)

Master plan 11.2 step 8 and 29.5: role elevation, bulk deactivation, and
similar need "the configured higher approval," and 29.5 lists "exemption
self-approval" and "unauthorized privilege" as failure points to design out
- generalized here to bulk import too, since the same shape of mistake
(uploader grants themselves cover via a file instead of a UI action) applies
equally.

Rule, enforced server-side in `commit_job` (not just at the HTTP layer, so
there is one place this can't be bypassed):
1. If `job.high_risk_rows == 0`: a single `standard` decision from anyone
   holding the capability the import type itself requires is sufficient
   (matches the ordinary campaign-import approval bar).
2. If `job.high_risk_rows > 0`: commit additionally requires a
   `high_risk`-tier decision, and that decision's `decided_by` must satisfy
   both:
   - **Not the uploader** (`authz.assert_not_self`-equivalent - reusing the
     existing helper's shape, separation of requester and approver).
   - **Sufficient standing for the specific effect**, checked per row, not
     once for the whole job: a `deactivate` row requires
     `workforce_service.can_manage_user(db, approver_id, target_user_id)` -
     the identical bar the existing one-at-a-time disable/reactivate screen
     already enforces (`app/web/workforce.py::disable_user_action`), so bulk
     deactivation can never grant more reach than the equivalent manual
     action would have. A `create`/`update`/`reactivate` row only reaches
     `high_risk` in a later increment (role elevation in `role_assignments`,
     4B-2) - none of `users`'s three actions are high-risk on their own.

This makes "a file cannot grant the uploader more authority" a real,
per-row, commit-time check against the *approver's* current live authority
- not a one-time gate at upload, which current state could have changed
since.

### Import flow (mapped to master plan 11.2 steps 1-13)

1. Uploader picks import type on `/workforce/imports/new` -> current
   template download (versioned filename, e.g.
   `workforce-users-template-v1.csv`) and a short data dictionary inline.
2. `POST /workforce/imports` (multipart) -> `create_import_job` quarantines
   the file exactly like `import_service.create_import_job` (hash, size
   limit, extension/signature check), audits `workforce_import.upload`.
3. Celery task `parse_workforce_import_job_task` calls `parse_job`, which
   streams rows through the type-specific classifier:
   - identity match: `external_workforce_id` against `User.workforce_id`
     (never by name/email - "names never determine identity matching").
   - `users`/`create`: reject if `external_workforce_id` already exists
     (that's an update, not a create - explicit action must match reality).
   - `users`/`update`/`reactivate`: reject (`unknown_identity`) if no match.
   - `explicit_deactivations`: reject if no match, or if already inactive
     (`already_inactive` - a warning, not blocking; matches "missing from
     file never means delete or deactivate" by requiring the row to exist
     and be explicit either way).
   - duplicate `external_workforce_id` within the same file ->
     `duplicate_in_file`, blocking.
   - `risk_level` set to `high_risk` for every `explicit_deactivations` row;
     `routine` for every `users` row in this increment.
4. Preview (`GET /workforce/imports/{id}/preview`) shows counts by
   action/risk tier and up to 5 invalid examples, same shape as
   `ImportPreviewOut` today.
5. Uploader must resolve blocking errors (re-upload) and can proceed with
   warnings acknowledged.
6. `PATCH /workforce/imports/{id}/decisions` records a `standard` decision;
   a second call from a different, sufficiently-privileged user records the
   `high_risk` decision when the job needs one.
7. `POST /workforce/imports/{id}/commit` re-locks the job row, re-validates
   both decisions are current for `decision_version`, re-checks each
   high-risk row's approver-authority live (not from preview time), then
   commits per-row inside the request's single DB transaction:
   - `create` -> `workforce_service.create_user` (issues an activation
     token exactly like the manual path - "creates activation invitations
     separately, never sends a password file").
   - `update` -> new `workforce_service.update_user` (field-level, only
     `display_name`/`start_date`/`end_date` in this increment - anything
     identity-shaped like `workforce_id`/`email` stays manual/unsupported
     via bulk, since those are the immutable-identity fields).
   - `reactivate` -> `workforce_service.reactivate_user`.
   - `deactivate` -> `workforce_service.disable_user`.
   Each row's `pre_commit_snapshot` is captured immediately before its
   mutation. Idempotent on `(uploader, idempotency_key)`, identical pattern
   to `import_service.commit_job`.
8. Result report: row outcomes (created/updated/reactivated/deactivated),
   no secrets, matches `ImportCommitOut`'s shape plus a per-row list.
9. `POST /workforce/imports/{id}/reverse` (new - the campaign importer has
   no equivalent): for each committed row, re-derive the entity's expected
   post-commit value and compare to its current value; matching rows get
   `pre_commit_snapshot` restored plus a fresh audit event
   (`workforce_import.reverse_row`); non-matching rows are left alone and
   listed as `skipped_conflict` in the result. Requires the same capability
   tier the original commit required (routine commit -> routine reverse;
   any high-risk row present -> high-risk reverse, same two-person rule).
   Sets `job.reversed_at`; does not change `job.state` (stays `committed` -
   history is never rewritten, matching 11.4's transfer-correction rule
   applied generally).

### Safeguards checklist (29.5), mapped to what enforces each one

- Immutable external workforce ID: identity match is
  `external_workforce_id` only, never re-derived from name/email; `create`
  rejects if it already exists, `update`/`reactivate`/`deactivate` reject if
  it doesn't.
- No implicit deletion when a row is absent: the importer only ever acts on
  rows present in the file; nothing is inferred from omission.
- No passwords or reset tokens in files: the `users` template has no
  password/credential column at all; activation tokens are generated
  server-side post-commit and never round-trip through the file or the
  result report.
- High-risk approval: see above.
- File-version compatibility and template expiry: template filename carries
  a version (`-v1`); parser rejects a header that doesn't match the current
  version's expected columns (blocking, not a silent best-effort mapping).
- Dry-run, row-level errors, warnings, high-risk summary: preview stage.
- Atomic transaction groups and idempotent retry: one commit transaction
  per job; `(uploader, idempotency_key)` replay returns the stored result.
- Compensating reversal after commit: see above.
- Notifications and activation delivery: activation tokens issued
  per-created-user through the existing `issue_activation_token` path;
  in-app notification delivery is explicitly out of scope for 4B (listed
  under "remaining operational workflows" as its own item, not built yet).
- Audit trace from every affected record to import, uploader, and approver:
  every commit/reverse row-level action is audited with `target_id` set to
  the affected user and `event_metadata` naming the import job.

## 4B-2 design: team_memberships, role_assignments, reporting_assignments

Extends the 4B-1 pipeline in place - same job/row/decision models, same
parse -> preview -> decide -> commit -> reverse flow, same two-person
high-risk rule. Nothing about the shared infrastructure changes; what's new
is per-type classify/commit/reverse logic and generalizing two things that
4B-1 left hardcoded to the `users`/`explicit_deactivations` shape.

### Scope cuts (and why they're not really new limitations)

- **No acting/time-limited appointments.** `workforce_service.assign_role`
  only ever creates a permanent, immediately-effective grant - it has no
  `effective_to`/expiry parameter, and no code anywhere in this build
  expires one automatically. Master plan 11.2's "role assignments and
  acting appointments" template nominally covers both, but building real
  acting-appointment expiry here would mean building the user's own
  separately-named roadmap item ("acting-role and delegation workflow")
  as a side effect of bulk import, not as its own increment. `role_
  assignments` in this pass creates permanent grants only.
- **No future-dated effective_from.** Matches the *existing*, already-
  shipped single-row flows exactly: `assign_role`, `add_team_membership`,
  and `set_reporting_line` all hardcode `effective_from=utcnow()` today,
  with no caller anywhere supplying a different one. Bulk import matching
  that isn't a new gap, just consistency with what the rest of the app
  already does.
- **`reporting_assignments` supports `set` only**, not a bare "remove
  supervisor, no replacement" action - the common case (replace or assign)
  maps directly onto `set_reporting_line`'s own supersede behavior; removal
  with no replacement is a rarer operation the master plan's field list
  doesn't clearly call for (`supervisor workforce ID, when applicable`
  reads as "who to set," not "whether to clear").

### Template columns

- `team_memberships`: `action` (`add` | `end`), `external_workforce_id`,
  `team_code`, `reason_code` (optional - team movement is routine HR
  housekeeping, not disruptive the way deactivation or role elevation is).
- `role_assignments`: `action` (`assign` | `end`), `external_workforce_id`,
  `role_code`, `scope_type` (`installation` | `organization` | `team`),
  `scope_code` (a `Team.external_code`, required only when
  `scope_type=team`), `reason_code` (**required** - matches
  `explicit_deactivations`'s bar; role changes always need a documented
  reason).
- `reporting_assignments`: `action` (`set`), `external_workforce_id`
  (subordinate), `supervisor_workforce_id`, `reason_code` (optional).

`scope_code` resolves against `Team.external_code`, never a raw UUID - the
file works in codes a human can type and audit, matching how
`external_workforce_id` already avoids raw user IDs. Unknown team codes are
a blocking error ("unknown supervisors, teams, campaigns, policies, or
roles are blocking errors").

### Risk tier

- `role_assignments` / `assign` -> **high_risk**, always. Master plan 11.2
  step 8 names "role elevations" explicitly; granting authority is the
  direction that needs the second approver, not removing it.
- `role_assignments` / `end`, `team_memberships` (both actions),
  `reporting_assignments` -> routine. None of these grant new authority.

### Generalizing the high-risk approver check

4B-1's `_assert_qualified_high_risk_approver` always checked
`can_manage_user` - the right bar for "may this approver act on this
*user*," but wrong for "may this approver *grant this specific role at
this specific scope*." A `role_assignments`/`assign` row needs the same
check the existing single-row grant endpoint already uses:
`authz.has_scope_capability(db, approver_id, ROLE_APPOINTMENT_CAPABILITY[role_code],
scope_type=scope_type, scope_id=scope_id)` (`app/api/workforce.py::assign_role`).
Generalized the approver check to dispatch on `job.import_type` /
`row.action` rather than assuming `can_manage_user` universally; the
resolved `scope_type`/`scope_id` are stored in the row's own
`parsed_values` at classify time so the commit-time and reversal-time
re-checks don't need to re-parse the raw `scope_code` string.

### Reversal

- `team_memberships`: no snapshot needed (pure boolean state, like 4B-1's
  create/reactivate/deactivate). Reversing `add` -> `end_team_membership`
  if still an active member, else conflict. Reversing `end` ->
  `add_team_membership` if still not an active member, else conflict.
- `role_assignments`: same shape. Reversing `assign` -> `end_role_
  assignment` if the specific (user, role, scope) grant is still active,
  else conflict. Reversing `end` -> `assign_role` again with the same
  role/scope if it's still ended, else conflict.
- `reporting_assignments`: needs a snapshot, like 4B-1's `users`/`update` -
  before calling `set_reporting_line`, record the subordinate's current
  active primary supervisor (or `None`) in `pre_commit_snapshot`. Reversing
  `set`: if the subordinate's current active primary supervisor is still
  the one this row set, restore the snapshot - call `set_reporting_line`
  again with the prior supervisor if one existed (which naturally
  supersedes the reversed line, matching how setting always supersedes),
  or `end_reporting_line` (new function, same shape as `end_role_
  assignment`/`end_team_membership`) if there wasn't one. Otherwise,
  conflict.

`reverse_job`'s entity lookup generalizes from a hardcoded `User` query to
dispatching on `row.committed_entity_type` (`user` | `team_membership` |
`role_assignment` | `reporting_assignment`).

### New in `workforce_service`

`end_reporting_line(db, line, *, ended_by, reason_code)` - the one gap in
the existing service layer this needs: every other entity this phase
touches already had a matching end-function (`end_role_assignment`,
`end_team_membership`), but reporting lines only ever had `set_reporting_
line`'s implicit supersede. Same shape as its two siblings.

## Out of scope for this pass

- `campaign_user_assignments` (4B-3 - transfers prorate targets per
  11.4/11.6, depends on whatever the 4C decision turns out to be) and
  `target_assignments` (blocked entirely on 4C).
- Acting/time-limited role appointments (see scope cuts above - a
  separately-named roadmap item, "acting-role and delegation workflow").
- In-app notification of uploaders/approvers (separate roadmap item).
- Multi-sheet single-workbook packaging (11.2 allows it; starting with one
  type per file, matching "prefer separate templates instead of one
  uncontrolled spreadsheet" - packaging is an additive convenience, not a
  correctness requirement).

## Verification status
- [x] Migrations 0011 (tables) and 0012 (`workforce_import_enabled` flag,
      seeded false - new high-risk feature, not an already-shipped one),
      confirmed correctly chained as head via `alembic history`. Models,
      `app/workforce_imports/*` service layer.
- [x] `users` and `explicit_deactivations` classification and commit paths,
      including the two-person high-risk rule (separation of duties plus a
      live per-row `can_manage_user` check, re-verified again at commit and
      at reversal, not just at decision time) and per-row reversal
      (snapshot-restore for `update`, natural inverse service call for
      create/reactivate/deactivate, conflict-skip when state has moved on).
- [x] Web (`/workforce/imports`) + JSON API (`/api/v1/workforce/imports`)
      surfaces, gated the same as the rest of workforce management (any
      appointment capability to use the surface at all; the high-risk
      per-row check is what actually narrows it). One-time activation
      tokens follow the same never-persisted, shown-once pattern as every
      other secret in this build (MFA setup key, bootstrap token) - kept
      out of `committed_result` so an idempotent replay can't reissue them.
- [x] 7 new integration tests
      (`tests/integration/test_workforce_imports_flow.py`): full create/
      update flow with a real activation token; bad-action and duplicate-
      in-file rejection; a malformed header failing parse cleanly (not
      silently best-effort mapped); the full high-risk path (self-approval
      rejected, wrong-scope approver rejected, premature commit rejected,
      right-scope approver succeeds); idempotent commit replay without
      reissuing tokens; reversal (self-approval rejected again, succeeds
      for a qualified non-uploader, cannot be repeated); the new rollout
      flag blocking uploads while off.
- [x] ruff and mypy clean repo-wide (92 source files under `app/`, matching
      what CI's quality job actually checks - `mypy app`, not `tests/`;
      pre-existing, unrelated mypy gaps in older test files were confirmed
      out of that scope, not introduced here). All new/changed templates
      parse through the real Jinja2 loader. All 12 new routes (6 JSON API,
      6 web) confirmed present in the generated OpenAPI schema. Unauthenticated
      requests to both the web list page and the template download redirect
      to `/login`; an unauthenticated JSON POST returns 401 - none of these
      touch the database. Full non-integration, non-performance suite passes
      locally (35/35, unaffected). No live database this session for the
      integration suite itself - pushing for CI to give the real answer.
- [x] CI green on `97615bc` after three real bugs found and fixed (see Log):
      an over-length FK constraint name, a fabricated actor UUID violating a
      real FK in a test fixture, and a genuine decision_version design bug
      in the two-tier approval flow. All four jobs passed - build, security,
      quality, integration (migrate up, migration reversibility, 136/136
      tests).

## Log
- 2026-09-02: reconciled scope against master plan sections 10.2, 11.2,
  29.5, and the Phase 4 delivery note; surveyed the existing campaign-import
  pipeline (`app/imports/*`) and workforce service
  (`app/workforce/service.py`) to decide what's reusable unmodified
  (parser/storage/validators) versus what needs a workforce-shaped sibling
  (classify/service/models/tasks/HTTP); designed the high-risk two-person
  rule and snapshot-based reversal; wrote this plan. Flagged the 4B-vs-4C
  sequencing tension against the master plan's own recommendation for the
  user's awareness rather than resolving it unilaterally.
- 2026-09-02: built migrations 0011/0012, `app/models/workforce_imports.py`,
  `app/workforce_imports/{classify,service,schemas,tasks}.py`, added
  `workforce_service.update_user` (a bounded field allowlist, deliberately
  excluding identity and workforce_status), wired `/api/v1/workforce/imports`
  and `/workforce/imports` (3 new templates, 2 new nav entries), registered
  a new `workforce_import_enabled` rollout flag seeded off. Caught and fixed
  three real bugs during self-review before ever pushing: `job.high_risk_rows`
  was initially counting warning-tier high-risk rows too, which would have
  forced an unnecessary approval step for a job with nothing actually
  high-risk to commit; two `scalar_one_or_none()` lookups against
  never-hard-deleted users were defensively None-checked in a way that
  didn't match this codebase's own established "trust the internal
  invariant" convention elsewhere (fixed to `scalar_one()`); activation
  tokens were briefly being returned from a code path that would have
  reissued them on an idempotent commit replay. ruff/mypy clean, full
  non-integration suite green locally (35/35), 7 new integration tests
  written covering the two-person high-risk rule and reversal specifically.
  Pushing for CI to confirm against a real Postgres - no live database this
  session.
- 2026-09-02: first CI run failed at "Migrate up," before any test ran -
  `sqlalchemy.exc.IdentifierError: Identifier
  'fk_workforce_import_decisions_import_job_id_workforce_import_jobs'
  exceeds maximum length of 63 characters`. A real gap in this session's
  verification routine: every prior migration this session was checked with
  `alembic history` (reads migration files only) but that never compiles
  the DDL, so an over-length identifier had no way to surface without a
  live Postgres connection - which this session hasn't had all session.
  Fixed by shortening that one constraint's explicit name (58 chars);
  confirmed by compiling the exact `CreateTable` DDL offline against
  `sqlalchemy.dialects.postgresql.dialect()`, which reproduces Postgres's
  63-character identifier check without needing a live database at all.
  While building that check, ran it across every model
  (`app.models.Base.metadata`) and found the same class of bug already
  latent in two pre-existing tables (`work_items`, `call_attempts`) - their
  migrations already hand-shorten the name (matching what this fix now also
  does), so the mismatch is cosmetic (model-metadata-only, never applied to
  a real database) and pre-existing, not introduced here. Left those two
  alone - out of scope, not something this change broke. Re-verified
  ruff/mypy/`alembic history`, re-pushed.
- 2026-09-02: second CI run got past migration (confirming the fix) but
  failed all 7 new integration tests at setup, with the rest of the suite
  (129 pre-existing tests) unaffected - `ForeignKeyViolation` on
  `feature_flags.updated_by`. The autouse fixture that turns the new
  `workforce_import_enabled` flag on for every test called
  `flags_service.set_flag(..., actor_id=uuid.uuid4())`: a fabricated UUID,
  not a real user, and `updated_by` is a genuine foreign key to `users.id`.
  One other test had the identical mistake in its own direct `set_flag`
  call. This class of bug is a real, live-database-only check - unlike the
  identifier-length issue, there's no way to catch a foreign-key violation
  by inspecting schema or models offline; only an actual constrained insert
  catches it, which is exactly what CI is for here. Fixed both call sites to
  use a real user id (`make_user(...)` for the fixture; the test's own
  manager for the direct call), grepped the whole file for every remaining
  bare `uuid.uuid4()` to confirm nothing else feeds an FK-constrained
  column (the two `team_a`/`team_b` scope IDs are safe - `RoleAssignment.
  scope_id` carries no FK, confirmed against the model). Re-verified ruff,
  mypy, and test collection; re-pushed.
- 2026-09-02: third CI run got past setup - 133 of 136 tests passed - but
  surfaced two more issues, one cosmetic and one a real design bug caught
  only by exercising the full two-tier approval path against real Postgres:
  - `record_audit`'s `reason_code` was truncated to 100 characters
    (`str(exc)[:100]`, copied from the campaign importer's own equivalent
    line) but `AuditEvent.reason_code` is `String(50)` - the campaign
    importer's own error messages happen to stay under 50 in practice, so
    this was already latently wrong there too, just never triggered. This
    session's new "file header is missing required column(s): ..." message
    is longer and hit it. Fixed to `[:50]` (the full message is already
    preserved separately in `job.error_summary`, `String(1000)`).
  - The real one: `job.decision_version` is a single counter shared by both
    tiers, incremented on every decision call regardless of tier. `_latest_
    decision(tier=X)` filtered for a decision AT `job.decision_version` -
    which meant recording the high-risk decision (bumping the shared
    counter) made the already-recorded standard decision unfindable at the
    new "current" version, since it was recorded at the old one. commit_job
    would then either wrongly report "not yet approved" or - as CI actually
    hit it - reject a stale-version commit built from the standard
    decision's own (now superseded) version number. Same bug would have
    quietly broken the web detail page's decision-status display the same
    way, since `current_decisions()` calls the same lookup. Fixed
    `_latest_decision` to find each tier's own most recent decision
    independent of the other tier's version bumps, keeping
    `job.decision_version` as what it should have been all along: a
    guard against a *third*, later decision superseding the one just acted
    on, not a per-tier filter. Fixed the two tests that exercised the full
    high-risk path to commit against the version returned by whichever
    decision call happened last (matching how the web UI already behaves,
    since it re-reads `job.decision_version` fresh on every page load
    rather than caching it). Re-verified ruff, mypy, and the full
    non-integration suite (35/35); re-pushed.
- 2026-09-02: fourth CI run green - build, security, quality, integration
  (migrate up, migration reversibility, 136/136 tests) all passed on
  `97615bc`. 4B-1 done.
- 2026-09-02: built 4B-2 (`team_memberships`, `role_assignments`,
  `reporting_assignments`) - see the "4B-2 design" section above. No
  migration needed; `action`/`committed_entity_type` were already
  general-purpose string columns. Added `workforce_service.
  end_reporting_line` (the one gap in the existing service layer - every
  other entity this phase touches already had a matching end-function).
  Generalized `_assert_qualified_high_risk_approver` to dispatch the
  correct authority check per import type (`can_manage_user` for
  deactivations, `has_scope_capability` against the exact role/scope for
  role grants) and `reverse_job` to dispatch per `committed_entity_type`
  instead of assuming every row is a user. Caught two real bugs during
  self-review before pushing:
  - `_REVERSE_ACTION`, a flat action-string-to-label lookup carried over
    from 4B-1, collided: `"end"` means "re-add a team membership" for one
    import type and "re-grant a role" for another, and a shared table keyed
    on the bare action string can't tell them apart. Fixed by having each
    `_reverse_*_row` function return its own outcome label directly (`str |
    None` - `None` for conflict), matching how the commit-side functions
    already work, and removed the shared table entirely.
  - `disable_user`/`assign_role` both call `authz.assert_not_self`, and
    nothing stopped a high-risk row's target from being the same person as
    the approver reviewing it - a real, if narrow, gap that would have
    surfaced as an unhandled 500 (aborting the whole commit) instead of a
    clean per-row conflict. Retrofitted a catch onto `_commit_deactivation_
    row` too, not just the new role-assignment path, since the same gap was
    already live in already-shipped 4B-1 code.
  Wrote 4 new integration tests covering the genuinely new mechanisms: team
  membership add/end with both directions of reversal, role-grant high-risk
  approval (self-approval rejected, wrong-scope approver rejected, right-
  scope approver succeeds, reversal ends the grant), role-ending as routine
  (not high-risk), and reporting-line set/reversal - including one case
  caught by manually tracing the state transitions while writing the test:
  reversing an *earlier* reporting-line import after a *later* one has
  already been reversed is correctly refused (the earlier row's own entity
  was superseded and never resurrected, even though the net effect
  coincidentally matches what reversing it would have produced) - fixed the
  test's own assertion to match that correct, conservative behavior rather
  than assuming success. ruff/mypy clean repo-wide (92 files, matching
  CI's `mypy app` scope), the same offline FK-identifier-length check as
  4B-1's fix confirms no new violations, all 11 tests in the file collect,
  full non-integration suite green locally (35/35). No live database this
  session - pushing for CI to give the real answer.
- 2026-09-02: CI green on the first push - build, security, quality,
  integration (migrate up, migration reversibility, 140/140 tests) all
  passed on `9782c92`. 4B-2 done.
- 2026-09-02: while designing 4B-3's authority check (`campaign_user_
  assignments` needs `has_campaign_capability(ASSIGN_CAMPAIGN_AGENT, ...)`),
  found a real gap in already-shipped 4B-1/4B-2 code, not just something to
  design correctly going forward. Every *manual*, one-row-at-a-time screen
  this build has checks a real per-target or per-scope authority before
  acting - `can_manage_user` for reactivating/deactivating a user or setting
  a reporting line, `has_scope_capability(APPOINT_TEAM_CAPTAIN, "team",
  team_id)` for team membership, `has_scope_capability(ROLE_APPOINTMENT_
  CAPABILITY[role], scope_type, scope_id)` for ending a role assignment.
  The bulk-import commit path only ever checked that bar for the two
  actions already wired into the high-risk tier (deactivation, role grant)
  - every *routine* row (`users`/reactivate, `team_memberships` both
  actions, `role_assignments`/end, `reporting_assignments`) was gated by
  nothing more than "holds any appointment capability at all," the same
  blanket bar that's correctly the *only* bar for `users`/create (which has
  no manual scope check either). That's a real instance of "a file cannot
  grant the uploader more authority" (11.2's own safeguard) being violated
  for those five action types: someone with any appointment capability
  anywhere could bulk-add or remove team members for a team they have no
  authority over, or reactivate a user they could never touch through the
  one-row screen.

  Fixed by generalizing the per-row authority dispatch
  (`_row_authority_ok`) that already existed for the two high-risk actions
  to cover every action across every type, and applying it uniformly: a
  `standard` decision now requires the decider to pass this check for every
  *routine* row, re-verified again at commit and at reversal - the exact
  same "check at decision, re-verify live at commit and reverse" shape the
  high-risk tier already had, just extended to the tier that had been
  skipping it. No self-approval requirement on the routine path (that stays
  high-risk-only; a single sufficiently-authorized person completing a
  routine bulk action alone matches the manual screens exactly, which never
  needed a second approver either). One new integration test proves the
  fix concretely with the clearest case: a Team Leader scoped to team B
  cannot approve a `team_memberships` file adding someone to team A, where
  before this fix they could.

  This is disclosed here rather than fixed silently because it changes
  behavior in code that already shipped and passed CI twice (4B-1, 4B-2) -
  worth being explicit that "CI green" proved the code did what it was
  designed to do, not that the design itself was complete; this gap was
  only found by re-deriving the authority model from scratch while
  designing the next type, not by anything CI could have caught.
