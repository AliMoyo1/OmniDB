# Phase 3 plan: agent workflow vertical slice

Scope: transactional leasing, idempotent completion, explicit DNC via disposition,
callbacks, skip handling, agent stats. Target/exemption display and agent self-service
exemption requests are DEFERRED (D-19/D-20). This is API-only; the desktop UI (two-column
agent page, keyboard behavior, watermark) is a separate follow-up once the API is proven.

## 3A: foundation - config, migration 0004, no-store headers  [x]
- [ ] settings: lease_duration_minutes, max_skips_before_review
- [ ] migration 0004: work_items.skip_count (separate from attempt_count so skip
      behavior doesn't distort call-attempt reporting later)
- [ ] Cache-Control: no-store on all /api/ responses (gap from Phase 1/2: plan 7.8
      requires this on personal-data responses; the lease endpoint is the first place a
      raw phone number leaves the server, making this load-bearing now)

## 3B: leasing service  [x]
- [ ] lease_next: campaign-assignment eligibility, campaign must be active, due
      callbacks owned by the agent take priority over shared-pool queued items,
      SELECT...FOR UPDATE SKIP LOCKED for queue selection (no duplicate active leases
      under concurrency), decrypt the one contact's phone for the response only,
      viewed-contact audit event without the raw number
- [ ] renew_lease: extend expiry only if lease_id matches and not expired
- [ ] expired-lease reclaim: leased items past lease_expires_at return to their
      pre-lease state (callback_wait if assigned_agent_id is set, else queued)

## 3C: completion service  [x]
- [ ] complete_work_item: idempotent on (agent_id, idempotency_key); validates lease
      ownership and disposition belongs to the campaign; encrypts notes; branches on
      disposition (causes_dnc / requires_callback_time / next_action)
- [ ] explicit DNC branch: create/reuse an active SuppressionEntry, and suppress EVERY
      pending work item for that contact across ALL campaigns (invariant 7 in full, not
      just the current campaign)
- [ ] callback branch: work item -> callback_wait, due_at = callback_at, ownership
      assigned to the completing agent
- [ ] requeue branch: attempt_count increments; past max_attempts routes to review
      instead of looping forever
- [ ] skip_work_item: mandatory reason, immutable audit event, past threshold routes
      to review

## 3D: agent API  [x]
- [x] POST /api/v1/work/next, POST /api/v1/work/{id}/complete,
      POST /api/v1/work/{id}/skip, POST /api/v1/work/{id}/lease/renew
- [x] GET /api/v1/agent/callbacks - masked (name or short reference, never the number)
- [x] GET /api/v1/agent/stats - aggregate from immutable attempts
- [x] WORK_QUEUE capability for the agent role

Note: this increment's commit got merged with a separate lint-cleanup commit (ruff was
installed and run for real for the first time partway through this increment; see
commit cfe4b42 for both the API code and the lint fixes together). Content is correct
and pushed; only the commit-message scope is imprecise.

## 3E: background jobs  [ ]
- [ ] expired-lease cleanup task on Celery Beat

## 3F: tests  [ ]
- [ ] integration: lease hides queue size, no assignment -> no work, idempotent
      completion, wrong lease rejected, DNC suppresses across campaigns, requeue +
      attempt-limit review routing, callback ownership, skip + reason + threshold,
      lease expiry reclaim, callback list never exposes the raw number
- [ ] concurrency: N agents leasing simultaneously from a small shared pool produces
      zero duplicate active leases (real threaded test against Postgres)

## Deferred to Phase 4 (documented, not built)
- Target/adjusted-target/exemption-status display on the agent view.
- Team Captain review/reassignment queues, DNC correction UI (ADR-009 override is a
  management action; the suppression MECHANISM built here is what it would act on).
- The actual two-column desktop UI, keyboard shortcuts, watermark, copy action -
  this phase is the API the UI will call.

## Log
- 2026-08-21: started.
