# Phase 2 plan: canonical data model and safe import pipeline

Status: ALL INCREMENTS BUILT (2026-08-20). Scope note: target policies, exemptions, and
bulk-workforce import are DEFERRED from the first pilot (D-19/D-20/D-21), so they are not
built here. DNC follows ADR-009 (label + auto-skip + Team Captain override); retention
follows ADR-020 (completion-triggered) and is not yet automated (Phase 4/scheduled job).

## 2A: data model + migration 0002  [done]
Campaigns, campaign_team/user_assignments, disposition definitions, contacts (encrypted
phone + keyed-HMAC fingerprint), campaign_contacts, suppression_entries, batches,
work_items, call_attempts (immutable), import_jobs/rows/decisions. Constraints: unique
active phone fingerprint, unique campaign+contact, one non-terminal work item per
campaign contact, one active PRIMARY campaign assignment per agent (D-17 MVP rule),
unique active suppression per fingerprint, call-attempt idempotency, import-job
idempotency.

## 2B: phone protection  [done]
app/security/phone.py: normalize_to_e164, keyed-HMAC fingerprint (ADR-019), protect()
combining parse + Fernet-encrypt + fingerprint.

## 2C: import pipeline  [done]
Quarantine storage (generated key, O_EXCL, streamed size limit, sha256), upload
validators (extension, CSV/ZIP signature mismatch, XLSX container structure, macro and
external-link rejection, expanded-size cap before decompression), bounded CSV/XLSX
parser (row/column/cell limits, XLSX read with data_only=True so formulas are never
evaluated, formula-like-prefix sanitization for stored text), classification (in-file
vs in-campaign duplicates, DNC match), and the orchestrating service: create -> parse ->
preview -> versioned decision -> atomic commit -> cleanup. Commit re-validates
duplicates and suppression against CURRENT state, requires an "approve" decision at the
current decision_version, is idempotent per (uploader, idempotency_key), enforces
mandatory provenance, and deletes the quarantine file on success. Celery worker +
parse/cleanup tasks (app/worker.py, app/imports/tasks.py).

## 2D: campaign + import API  [done]
app/campaigns/service.py (lifecycle with real preconditions; create_disposition enforces
invariant 9 - causes_dnc=True only for the "explicit_dnc" semantic code). app/api/
campaigns.py: campaigns_router + imports_router, capability-gated with scope_type=
"campaign" so per-campaign scope activates automatically once Phase 4 adds scoped role
assignments. New capabilities: CREATE_CAMPAIGN, VIEW_CAMPAIGN, MANAGE_CAMPAIGN,
PAUSE_CAMPAIGN, LAUNCH_CAMPAIGN, ARCHIVE_CAMPAIGN.

## 2E: compose worker/beat  [done]
worker + beat services on the internal data network only. Shared "quarantine" named
volume on web + worker (separate container filesystems otherwise). Dockerfile now
pre-creates and chowns /var/lib/ciphercontact/quarantine to the non-root app user before
switching USER, so the volume inherits correct ownership on first mount.

## 2F: tests  [done]
tests/ subdirectories packaged with __init__.py for reliable cross-file imports.
tests/integration/conftest.py: shared client/manager_client/agent_client fixtures and
make_user_with_role/login/csrf_headers helpers. tests/unit/test_phone.py: E.164 parsing,
fingerprint determinism/uniqueness/non-plain-hash, encrypt roundtrip. tests/integration/
test_imports_flow.py: full upload->parse->preview->approve->commit->idempotent-replay->
launch flow with real counts; DNC-after-preview exclusion; commit rejected without
approval and with a stale decision_version; launch rejected without contacts/provenance;
malicious-file rejection (ZIP-signature CSV, macro-enabled XLSX); unknown phone_column
fails fast; disposition DNC-policy enforced both directions; Agent cannot create a
campaign.

## Verification status
- [x] py_compile across app/migrations/tests after every increment
- [x] docker compose config (including worker/beat + shared quarantine volume)
- [x] Manual line-by-line trace of migration 0002/0003 DDL: FK creation order and
      reverse-order downgrade drops both verified correct by hand, THEN confirmed for
      real 2026-08-21 - migrations 0002/0003 applied and round-tripped cleanly against
      genuine Postgres 16, no issues found (the hand trace was accurate)
- [ ] CI green on GitHub Actions specifically (still not observed)
- [x] docker compose up on the host - done 2026-08-21 (see BUILD-LOG.md). Real upload
      through the API not yet exercised (login/session/CSRF/admin flows were, via curl)

## Known simplifications (documented, not bugs)
- Campaign/import authorization checks organization-or-broader role scope; true
  team/campaign-scoped role assignment issuance is Phase 4 work. The capability calls
  already pass scope_type="campaign" so scoping activates automatically once Phase 4
  lands, with no route changes needed.
- Column mapping (phone_column/name_column/metadata_columns) is supplied by the
  uploader per upload rather than pre-approved and stored on the campaign; a persisted,
  campaign-owner-approved schema (plan 11.8 Stage C item 2) is a Phase 4 refinement.
- No malware/antivirus scanning of uploads (plan 9.6 "where available") - not available
  in this environment; container/signature/structure validation stands in for Phase 2.
- Suppression-entry write paths (administrator entry, imported master list) are not
  built in Phase 2; only the read/match path used by classification and commit exists.
  Entries are currently seeded directly (see the DNC test). Explicit DNC via agent
  disposition is Phase 3 (work-item completion, not yet built).
- Retention auto-delete (ADR-020, 60-day countdown) is not automated yet; the
  Campaign.completed_at/retention_delete_after columns exist but nothing sets or acts
  on them yet - that is Phase 4/background-job work.

## Log
- 2026-08-20: built and pushed 2A through 2F.
