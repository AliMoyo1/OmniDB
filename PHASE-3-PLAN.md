# Phase 3 plan: agent workflow vertical slice

Status: ALL INCREMENTS BUILT (2026-08-21). API-only; the desktop UI (two-column agent
page, keyboard behavior, watermark) is a separate follow-up once the API is proven on
the host. Target/adjusted-target/exemption display stays deferred (D-19/D-20).

## 3A: foundation - config, migration 0004, no-store headers  [done]
lease_duration_minutes, max_skips_before_review settings. Migration 0004 adds
work_items.skip_count (kept separate from attempt_count). Fixed a gap carried from
Phase 1/2: Cache-Control: no-store (+ Pragma: no-cache) on every /api/ response (plan
7.8) - load-bearing now since the lease endpoint is the first place a raw phone number
leaves the server.

## 3B: leasing service  [done]
lease_next: SELECT ... FOR UPDATE OF work_items SKIP LOCKED for queue selection (no
duplicate active leases under concurrency - see 3F). Due callbacks the agent already
owns take priority over shared-pool queued work, both gated on the campaign being
active. Decrypts exactly one contact's phone for the response, no queue-size or
other-contact leakage, plus a viewed-contact audit event without the raw number.
renew_lease and reclaim_expired_leases (pre-lease-state-aware) also here.

## 3C: completion service  [done]
complete_work_item is idempotent per (agent_id, idempotency_key), checked before any
write. Validates lease ownership and that the disposition belongs to the campaign and
is active. Branches on disposition: causes_dnc sweeps every non-terminal work item for
the contact across ALL campaigns to suppressed (invariant 7 in full, including closing
the current lease as a side effect of the same sweep); requires_callback_time schedules
a callback owned by the completing agent; otherwise next_action drives complete/
requeue-with-attempt-limit/review. skip_work_item requires a reason and routes to
review past a threshold, deliberately without its own idempotency key since the
lease_id is already single-use.

## 3D: agent API  [done]
POST /api/v1/work/next (returns a raw Response for the no-work 204 case rather than
mixing an Optional response_model with a dynamic status code), .../complete, .../skip,
.../lease/renew. GET /api/v1/agent/callbacks (masked reference, never the number) and
/stats (from app/reporting/agent_stats.py, live aggregates over immutable attempts).
WORK_QUEUE capability for the agent role.

## 3E: background jobs  [done]
app/work/tasks.py: expired-lease reclaim on Celery Beat every 2 minutes.

## 3F: tests  [done]
tests/integration/test_work_flow.py: no-assignment 204, lease response shape (proves
no extra leakage by checking the exact key set), idempotent completion, wrong-lease
rejection, DNC suppression swept across two separate campaigns for the same contact
(verified via direct DB check), requeue-then-review at attempt limit, callback
scheduling + masked callback-list reference + immediate re-lease as a callback,
skip validation and threshold-to-review, lease-expiry reclaim, lease renewal.
tests/concurrency/test_leasing_concurrency.py: 10 agents, each in its own thread with
its own DB session (genuinely separate Postgres connections, not simulated), lease
simultaneously from a 6-item shared pool; asserts every item goes to exactly one agent
and every leased item has a distinct owner.

## Real bugs found and fixed via this session's first genuine ruff run
- app/api/campaigns.py (Phase 2D): app.authz.* imported before app.auth.dependencies -
  wrong alphabetical order, would have failed the blocking ruff-check CI step.
- app/api/work.py: unused get_current_user import.
- tests/integration/conftest.py: leftover timezone.utc reference after an earlier
  auto-fix converted the rest of the file to the datetime.UTC alias.
- test_work_flow.py: caught and removed dead/nonsensical placeholder code left over
  from drafting the DNC cross-campaign test.
- 66 false-positive B008 findings (FastAPI's Depends(...)-as-default pattern) resolved
  via the standard extend-immutable-calls config, not suppression.
See the "Lint cleanup" commit for the full list; ruff is now installed and used for
the rest of this build (it's a static parser, unaffected by the local Python
3.14-vs-pinned-SQLAlchemy-2.0 runtime mismatch discovered in this same pass).

## Verification status
- [x] py_compile across app/tests/migrations after every increment
- [x] Real ruff check (not just py_compile) across the entire repository - zero errors
- [x] docker compose config
- [x] Manual trace of the leasing/completion service logic, including race-window
      reasoning for the DNC-sweep-vs-concurrent-lease scenario
- [x] docker compose up + alembic upgrade - done 2026-08-21 via Docker Desktop. Beat's
      reclaim_expired_leases_task confirmed executing live on its 2-minute schedule
      (Beat dispatched it, worker ran it, succeeded, returned 0 - correct for an empty
      database).
- [x] Leasing/completion exercised live with real work items - done 2026-08-21 (see
      BUILD-LOG.md): a full campaign->import->launch flow fed 5 real contacts through
      an agent leasing loop covering every disposition branch (complete, requeue back
      to queued, explicit DNC with a verified suppression_entries row, callback with
      the masked callback-list response then correct is_callback:true priority re-lease
      once due, and skip with reason validation). GET /api/v1/agent/stats reconciled
      exactly against a direct database query with zero discrepancy. No new bugs found
      in this pass.
- [x] CI green on GitHub Actions - confirmed 2026-08-21, run 32488321998: quality,
      security, integration (incl. the "Integration tests" step that had failed every
      prior run this session), and build all passed. Root cause was a test-isolation
      bug in the shared `zw_numbers()` fixture, not an application defect - see
      BUILD-LOG.md.

## Known simplifications (documented, not bugs)
- Campaign-user-assignment issuance has no API yet (tests insert directly); that's
  Phase 4 workforce management.
- "Today" in agent stats is a UTC calendar day, not the campaign's own timezone -
  acceptable for a first pass, worth revisiting once a campaign's timezone field is
  actually exercised operationally.
- Callback ownership doesn't yet participate in campaign-transfer handoff (Phase 4).
- No requeue_policy_id / attempt-timing rules beyond a flat max_attempts + a single
  global max_skips_before_review; per-campaign policy tuning is a later refinement.

## Log
- 2026-08-21: built and pushed 3A through 3F, plus a real ruff pass across the whole
  repository that found and fixed issues predating this phase.
