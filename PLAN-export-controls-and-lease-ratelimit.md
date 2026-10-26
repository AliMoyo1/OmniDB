# Build: export accountability controls + agent-lease rate limiting

[STATUS: DONE 2026-10-26; both gaps coded with tests; integration 365, unit 66,
ruff/mypy(app) clean; local docker build BLOCKED by a network TLS-interception proxy
(pip cannot verify pypi, not a code issue), left for CI's build job; commit/push/CI next]

Closes the two code-level gaps behind Risk memo findings 4 and 3 (the governance and
pen-test portions are being handled separately by the owner). No schema migration:
nothing new is stored as a column (audit metadata is JSONB, the rate limit is Redis).

Confirmation: owner said "do number 1 and 2" in chat on 2026-10-26. This is a chat
go-ahead, not a formal sign-off.

## Decisions (ledger)

- D1. Export justification/approver/recipient are CAPTURED and AUDITED at export time,
  not run through a second-person approval workflow. Why: the owner asked to capture
  these; a full async approval flow (like imports) is a much larger feature and was not
  requested. Cost: accountability rests on the exporter's attestation plus the audit
  record, not an independent approval gate. How to change: add a pending-export +
  approver-decision workflow later if governance needs true separation of duties.
- D2. Watermark = a print header/footer "RESTRICTED - do not share" on the Contacts
  sheet (always) plus a second "Export Info" sheet carrying classification, campaign,
  exporter, timestamp, recipient, approver and justification. Why: visible label on any
  printout, and a durable in-file provenance record, without disturbing the Contacts
  sheet (so existing data/injection tests stay valid). Cost: not a cryptographic or
  per-cell watermark; a determined exporter can delete the sheet. How to change: swap for
  a stronger scheme (locked sheet, document-level encryption/password) if required.
- D3. The export route changes from GET (one-click link) to POST with a small form
  (justification, approver, recipient; all required) and CSRF. Why: a GET cannot capture
  or require these fields. Cost: the "Export to Excel" control becomes a short form.
- D4. Lease rate limiting lives in the SERVICE (work_service.lease_next), not the routes.
  Why: it is the single server-side chokepoint both the JSON API and the web workbench
  call, which is exactly finding 3's own wording ("server-side on every API call, not
  the UI"); one change covers both and they cannot drift. Cost: the service now depends
  on the rate limiter (Redis). How to change: move to a route dependency if the service
  must stay transport-agnostic.
- D5. The "anomaly monitoring" signal is a WARNING log on each trip (captured by the
  existing structured logging and Sentry), not a new DB audit table. Why: minimal, needs
  no transaction handling, and is caller-independent. Cost: trips are in the logs, not
  queryable as audit events. How to change: add a record_audit call on the trip (and
  commit it on the route's rate-limited branch) if a queryable trail is wanted.
- D6. Per-agent limit only (no global/source tier) at 300 leases / 15 min. Why: the
  scrape vector is one agent account; a global tier would accumulate across the whole
  test suite (the login limiter's documented headroom problem), while a per-agent counter
  with distinct agents per test is immune. 300/15min is ~20/min, far above any human
  calling pace and tripped instantly by a script, with headroom so no single test's
  per-agent lease count trips it. How to change: tune _WORK_LEASE_LIMIT.

## Gap 1: export accountability + watermark

- app/campaigns/export.py: add `ExportContext` dataclass; `build_export_workbook(..., context=None)`
  sets the Contacts footer/header watermark always and appends the "Export Info" sheet when
  context is given (free-text fields run through sanitize_text, same formula-injection guard).
  `export_completed_campaign(..., exporter_name, justification, approver, recipient)` builds the
  context and adds the three fields to the `campaign.export` audit metadata.
- app/web/campaigns.py: export route GET -> POST (verify_form_csrf), read the three required
  Form fields (reject empty), pass user.display_name as exporter_name.
- app/templates/campaign_detail.html: replace the GET link with a compact POST form.
- Tests: update the 3 existing GET export tests to POST; add (a) a full-flow export that
  asserts the audit metadata carries justification/approver/recipient and the workbook has
  an Export Info sheet with the RESTRICTED label, free-text sanitized; (b) empty required
  field -> rejected, no export.

## Gap 2: agent-lease rate limiting

- app/auth/ratelimit.py: add `_WORK_LEASE_LIMIT` + `check_and_increment_work_lease(agent_id)`
  (per-agent, namespace "work_lease_attempts", reuses _increment/_key, fails closed in prod).
- app/work/service.py: new `LeaseRateLimited`; at the top of lease_next, check the limiter,
  log a warning and raise on trip (before any DB work).
- app/api/work.py: map LeaseRateLimited -> 429 {"code": "rate_limited"}.
- app/web/agent_work.py: map LeaseRateLimited -> flash "leasing too quickly".
- Tests: (a) limiter returns True up to the limit then False for one agent; (b) API lease
  returns 429 and leases nothing when the limiter says no (monkeypatched); (c) web lease
  shows the flash on trip.

## Verify
ruff + mypy app, targeted tests, full `pytest -m integration` + unit vs real Postgres
(Docker; Redis 16379), docker build, update BUILD-LOG, commit, push, watch CI. No migration.
