# Phase 5 operational workflows: notification inbox (increment 1)

Reconciliation (2026-09-02): of the four operational workflows on the roadmap -
notification inbox, acting-role/delegation, completed-campaign export+retention,
real infra drills - the inbox is fully greenfield (zero code), delegation has a
dead `Delegation` model wired to nothing, retention has model fields
(`Campaign.completed_at` / `retention_delete_after`) + an existing Celery beat
schedule but no export/automation, and infra drills already have
backup/restore/restore-test scripts + a runbook (an ops run, not a build).

Starting with the notification inbox: greenfield, pilot-critical (a Phase 0
decision: "in-app inbox notifications + dormant email capability"), and the
natural complement to the just-hardened two-person import workflow - an approver
/uploader should be *notified*, not only discover state by polling a list.

## Increment 1: inbox core + one proven emitter

Scope kept deliberately tight (the 4B review history says small, verifiable
increments win).

DONE (2026-09-02): model + migration 0016 + service + JSON API + web inbox +
nav badge (global via page_context) + 3 emitters (decide/commit/reverse notify
the uploader when someone else acts) + 4 integration tests. ruff/mypy clean,
OpenAPI registers all 7 routes, migration up/down/reapply clean, docker build
clean, full suite green vs real Postgres/Redis (integration 170, unit 35).
Committed + pushed (see BUILD-LOG.md). CI: pending.

- [x] Model `Notification` (app/models/notifications.py): recipient_user_id (FK
      users), category (str), title (str), body (str|None), related_entity_type
      /related_entity_id (nullable - e.g. workforce_import_job), read_at
      (nullable). UUIDMixin+TimestampMixin. Register in models/__init__.py.
      Index on (recipient_user_id, read_at) for the unread-count / inbox query.
      "Dormant email capability" = leave room for a future channel but build
      in-app only now (no email field churn - a follow-up adds it).
- [ ] Migration 0016 (additive: new table only).
- [ ] Service (app/notifications/service.py): notify(...) create; list_for_user
      (unread_only, limit); unread_count; mark_read(id, user) and
      mark_all_read(user) - a user may only read/mark their OWN notifications
      (recipient check, not a blanket capability - this is per-user private data).
- [ ] JSON API (app/api/notifications.py): GET list (+unread filter), GET
      unread-count, POST {id}/read, POST read-all. Auth: get_current_user; each
      returns only the caller's own.
- [ ] Web inbox (app/web/notifications.py + template): server-rendered list,
      mark-read / mark-all-read forms (CSRF), and an unread badge via nav_flags.
- [ ] Wire ONE real emitter (single known recipient - no broadcast-set yet):
      workforce_imports record_decision -> notify the uploader "approved/
      rejected"; commit_job -> notify uploader "committed"; reverse_job ->
      notify uploader "reversed". Recipient is always job.uploader_id.
- [ ] Tests (integration): emitter fires on decide/commit; API returns only the
      caller's own notifications (cannot read another user's); mark-read flips
      read_at and only the recipient may; unread_count correct; web inbox renders
      + badge count.
- [ ] ruff/mypy, migration up/down/reapply, full suite vs real Postgres (Docker),
      docker build. Commit, push, watch CI, confirm, record.

## Follow-ups (later increments, NOT this one)
- Notify-approvers broadcast (recipient set = users who can_access + hold
  approval authority) when an import needs a decision.
- Dormant email channel (a delivered_channels / email dispatch stub).
- Then: acting-role/delegation wiring, then completed-campaign export+retention.
