# Phase 5 operational workflows: the two remaining notification gaps

[STATUS: DONE 2026-09-07; integration 206 (+3), unit 35, ruff/mypy/docker clean, no
migration; pending commit/CI]

The two follow-ups noted at the end of PHASE-5-NOTIFICATIONS-PLAN.md, built as one
small increment. Both are additive and touch no schema.

## Gap 1: dormant email channel (the Phase 0 "in-app inbox + dormant email" decision)

Notifications are in-app only today. Add the SEAM for an email channel, wired but
OFF by default (the pilot ships in-app only; a later build adds real SMTP).

- Settings: `email_notifications_enabled: bool = False`.
- `app/notifications/email.py`:
  - `_channel_enabled()` -> reads the setting (indirection so tests can flip it).
  - `_deliver(to_address, subject, body)` -> the single real send point. STUB: logs
    that SMTP delivery is not yet implemented. A future build replaces just this.
  - `dispatch(db, notification) -> bool`: if not enabled, return False (dormant, in-app
    only - the pilot default, zero overhead, no user lookup). If enabled, look up the
    recipient's email and call `_deliver`; return whether it was dispatched.
- Wire into `notifications.service.notify()`: after the in-app row is flushed, call
  `email.dispatch(db, notification)`. One seam, every emitter becomes email-capable when
  the channel is switched on; no emitter churn. NOTE in code: real delivery must be
  POST-COMMIT (never email about an action that then rolls back) - the stub only logs,
  so it is safe now, and the future SMTP build enqueues a task after commit.
- Tests: dormant by default (dispatch returns False, `_deliver` never called, in-app row
  still created); enabled -> `_deliver` receives the recipient's real email + the title.

## Gap 2: broadcast a routine import whose uploader cannot self-approve it

Today `notify_pending_high_risk_approvers` broadcasts ONLY when `high_risk_rows > 0`.
But a ROUTINE-only job (no high-risk rows) can still contain rows the uploader has no
authority over (e.g. a team-membership add for a team they do not manage): the uploader
cannot self-approve it, yet no qualified approver is pinged, so it sits unseen. Close it
using the SAME hardened mechanism (candidate set filtered through the real can_access_job -
never a parallel reverse resolver).

- Rename `notify_pending_high_risk_approvers` -> `notify_pending_approvers` (it no longer
  only handles high-risk); update the single call site in `parse_job`.
- New `_uploader_can_self_approve_routine(db, job) -> bool`: build the routine committable
  rows' requirements and `_authority_over_requirements(db, job.uploader_id, ...)`. Empty
  routine rows -> vacuously True (an all-invalid job is unapprovable anyway, blocked by
  invalid_rows > 0; do not broadcast it).
- Trigger: broadcast when `high_risk_rows > 0` OR (routine-only AND NOT
  `_uploader_can_self_approve_routine`). High-risk short-circuits first, so a high-risk job
  never pays the routine row-load. The routine row-load runs once at parse time (like
  `_assert_rows_authorized` at decision time) - not a hot path, bounded to one job.
- Message differs by reason: high-risk keeps the existing "second approver" copy; routine
  says an approver with authority over the rows is needed (the uploader cannot approve it
  alone). Category stays `workforce_import.needs_review`.
- Tests: a routine job whose uploader lacks authority pings a qualified approver and NOT
  the uploader or an unrelated user; the existing "manager uploads a users-create ->
  broadcasts nobody" test stays green (requirement None -> uploader can self-approve).

## Verify
ruff/mypy, full suite vs real Postgres (Docker; Redis 16379), docker build, BUILD-LOG,
commit/push/CI. No migration.
