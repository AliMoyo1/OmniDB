# Phase 2 plan: canonical data model and safe import pipeline

Scope note: target policies, exemptions, and bulk-workforce import are DEFERRED from the
first pilot (D-19/D-20/D-21), so they are not built here. DNC follows ADR-009 (label +
auto-skip + Team Captain override); retention follows ADR-020 (completion-triggered).

## 2A: data model + migration 0002  [in progress]
- [ ] campaigns, campaign_team_assignments, campaign_user_assignments, campaign_disposition_definitions
- [ ] contacts (encrypted phone + keyed-HMAC fingerprint), campaign_contacts, suppression_entries
- [ ] batches, work_items, call_attempts
- [ ] import_jobs, import_rows, import_decisions
- [ ] constraints/indexes: unique fingerprint, unique (campaign,contact), one active work item per contact, unique active suppression, attempt idempotency, state checks

## 2B: phone protection
- [ ] app/security/phone.py: parse (phonenumbers + default region) -> E.164, keyed-HMAC fingerprint, Fernet-encrypt

## 2C: import pipeline
- [ ] quarantine upload (generated name, size limit, hash), outside webroot
- [ ] bounded CSV/XLSX parsing (openpyxl read-only; reject macros/formulas/external links; row/col/cell limits)
- [ ] classification: valid/invalid, in-file dup, in-campaign dup, DNC suppression match, missing provenance
- [ ] decision versioning
- [ ] commit-time revalidation + atomic commit (contacts, campaign_contacts, batch, work_items, audit) + idempotency
- [ ] Celery worker (app/worker.py) + parse task; cleanup + expiry jobs

## 2D: campaign + import API
- [ ] /api/v1/campaigns CRUD + launch/pause/archive (provenance mandatory at launch, D-13)
- [ ] /api/v1/campaigns/{id}/imports upload, /imports/{id} status, /preview, /decisions, /commit
- [ ] campaign capabilities added to the authz map

## 2E: compose worker/beat
- [ ] add worker + beat services; Celery + Beat config

## 2F: tests
- [ ] unit: phone normalize + fingerprint determinism; classification; disposition effects
- [ ] integration: import create->parse->preview->commit (atomic, idempotent), DNC-after-preview excluded at commit, duplicate handling, malicious-file rejection

## Log
- 2026-08-20: started 2A.
