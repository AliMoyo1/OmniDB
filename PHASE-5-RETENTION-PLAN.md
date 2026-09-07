# Phase 5 operational workflows: completed-campaign export + retention (ADR-020)

Reconciliation (2026-09-02): model fields `Campaign.completed_at` /
`retention_delete_after` and `CampaignContact.completed_at` exist but are unset
by any lifecycle logic; a Celery beat schedule exists (worker.py) to hang tasks
on; openpyxl is already a dependency. No completion detection, no export, no
deletion today. Campaign lifecycle is draft -> active -> paused/archived (no
"completed" state).

ADR-020: a campaign is complete when every number has a final disposition, the
calling agent recorded, and a usage record. In this schema every terminal path
(normal completion line 754, per-lease suppression 273, cross-campaign DNC sweep
638) sets `CampaignContact.completed_at` together with disposition + agent - so
the reliable per-contact "done" signal is `completed_at IS NOT NULL`, and a
campaign is complete iff it is active, has >=1 contact, and has zero contacts
with completed_at NULL. On completion: 60-day countdown, Team Captain Excel
export (raw numbers/dispositions/agents - an audited exception to no-raw-export),
manual delete before the countdown, hard auto-delete at 60 days. Audit events
and DNC-suppression evidence are NOT removed by deletion.

Built in three safe increments (system coherent at each step):

## Increment A: completion detection + countdown  [STATUS: DONE 2026-09-02, migration 0017; integration 177; pending commit/CI]
- New campaign status "completed"; RETENTION_DAYS=60 (ADR-020).
- app/campaigns/retention.py: `campaign_is_complete(db, campaign)`,
  `mark_campaign_completed(db, campaign, *, now)` (status=completed,
  completed_at, retention_delete_after=now+60d, audit, notify created_by via the
  inbox), `detect_completed_campaigns(db) -> int` (scan active campaigns, mark
  the newly-complete). Marking "completed" is safe: an active->completed
  campaign has no leasable work left (all contacts done), and launch/pause/
  archive already reject non-matching states.
- app/campaigns/tasks.py + register `detect-completed-campaigns` in worker beat
  (hourly - a 60-day countdown needs no finer granularity) + add app.campaigns to
  autodiscover.
- Countdown on the campaign detail page (days remaining until
  retention_delete_after).
- Tests: all-contacts-done campaign gets marked completed w/ 60d countdown +
  owner notified; an outstanding-contact campaign does not; detail shows the
  countdown; idempotent (re-running detect doesn't re-mark/re-notify).

## Increment B: Team Captain Excel export  [STATUS: DONE 2026-09-02; new cap EXPORT_COMPLETED_CAMPAIGN; integration 180; pending commit/CI]
- app/campaigns/export.py: build an xlsx (openpyxl) of a COMPLETED campaign's
  contacts - decrypted phone, final disposition, agent, completed_at. Gated by a
  Team-Captain-level capability over the campaign; audited (who exported, when).
  The one authorized raw-PII export (ADR-020 flag).
- API + web download endpoint (stream the file; never a link, per download
  rules - it's an authenticated app response the user triggers).
- Tests: a completed campaign exports rows w/ the right columns; a non-completed
  campaign refuses; authz gate; an audit event is written.

## Increment C: deletion (manual + auto)  [STATUS: not started]
- app/campaigns/retention.py: `delete_completed_campaign_data(db, campaign, *,
  actor_id, reason)` - deletes CampaignContacts + WorkItems + CallAttempts +
  now-orphaned Contacts for the campaign, but NEVER audit events or
  SuppressionEntries (evidence retained). Only for a completed campaign. Audited.
- Manual: Team Captain endpoint (API+web), requires completed state, audited.
- Auto: `purge-expired-campaign-data` beat task deleting completed campaigns past
  retention_delete_after. Idempotent.
- Tests: manual delete removes contact rows but keeps audit + suppression
  evidence; auto-delete purges only past-countdown campaigns; a not-yet-expired
  or not-completed campaign is untouched; authz + audit.

## Verify each increment
ruff/mypy, migration if any, full suite vs real Postgres (Docker; Redis on host
16379 this machine - Hyper-V reserves 6379), docker build, BUILD-LOG entry,
commit/push/CI.
