# Phase 4B plan: staged bulk-workforce import

Status: 4B-1 (users + explicit_deactivations) built, pushed for CI (2026-09-02).

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

## Out of scope for this pass

- `team_memberships`, `role_assignments`, `reporting_assignments`,
  `campaign_user_assignments`, `target_assignments` import types (4B-2/4B-3
  per above).
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
- [ ] CI result.

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
