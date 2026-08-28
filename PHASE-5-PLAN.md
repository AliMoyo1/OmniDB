# Phase 5 plan: production hardening and controlled pilot

Status: in progress - starting with 5A (concurrency and load tests).

## Scope reconciliation against the decision log

The master plan (`docs/architecture/CipherContact - Detailed Implementation Plan
v0.3.md`, section "Phase 5") is "production hardening and controlled pilot": release
candidate, security review, concurrency/performance/soak tests, cert and Tailscale
distribution, restore drills, training, a real pilot with 10-20 users, and multi-
stakeholder go-live approval.

Most of that is not buildable in this environment - it needs the user's real
infrastructure, real people, and real approvals. What's genuinely engineering work
that can be built and verified here is split into sub-phases as it's picked up; this
file only plans the sub-phase currently in progress, not all of Phase 5 up front,
matching how Phase 4 was broken into 4A/4B/4C rather than planned monolithically.

Asked the user which slice to start with; chose **concurrency & load tests**
(master plan Phase 5 step 3 - "run full concurrency and performance tests"). Step
4's eight-hour soak/resource-growth test needs production-like hardware and a
sustained real-world run, out of scope for this sub-phase - noted as a follow-up for
the user to run during actual pilot prep using whatever this sub-phase produces.

## 5A: concurrency and load tests [in progress]

`tests/concurrency/` already covers two real invariants under genuine concurrent
Postgres transactions (separate thread, separate session, real races, not
simulation): leasing (`SELECT ... FOR UPDATE SKIP LOCKED` - no duplicate leases) and
DNC suppression (advisory lock ordering against both leasing and import commit).
Both were written in earlier phases and both still pass.

Before adding anything new, audited the rest of the codebase for other check-then-act
sequences with the same shape (read a count/state, decide, then write, with no lock
holding the decision and the write in the same atomic scope) and no existing test.
Found one, from this session's own 4A-2 work:

`app/campaigns/service.py::_check_staffing_capacity` reads
`COUNT(active CampaignUserAssignment rows for this campaign+team)`, compares it to
`CampaignTeamAssignment.staffing_capacity`, and raises if at or over. The caller
(`assign_agent_to_campaign` or `transfer_agent`) then inserts a new active
assignment. There is no row lock, no advisory lock, and no DB-level constraint
enforcing the capacity - two concurrent assignment calls for the same (campaign,
team) can both read the same under-capacity count before either commits, and both
succeed, landing the team over its configured capacity.

Plan:
1. Write a concurrency test that races N concurrent `assign_agent_to_campaign`
   calls (distinct agents) against one (campaign, team) whose capacity is smaller
   than N, using the same real-thread/real-session/real-Postgres pattern as the
   existing two files. Confirm it demonstrates the race (over-capacity acceptance)
   against the current code first - a red test proving the bug is real, not assumed.
2. Fix `_check_staffing_capacity` to hold a row lock on the `CampaignTeamAssignment`
   row for the transaction's duration (`SELECT ... FOR UPDATE`), serializing
   concurrent capacity checks for the same team assignment - the same shape of fix
   the leasing path already uses, applied to the one place that needed it. This
   covers both call sites (`assign_agent_to_campaign` and `transfer_agent`)
   automatically since both go through the one function.
3. Re-run the test to confirm it now passes (capacity is genuinely enforced under
   real concurrency, not just in the sequential/single-request case the existing
   integration tests already cover).
4. Add a basic throughput/load test under `tests/performance/` (currently an empty
   scaffold from Phase 1): a larger-scale version of the leasing race - more agents,
   more work items, asserting the whole pool drains with no duplicate leases and
   within a bounded time - to have at least one real number for "how does leasing
   behave under load" rather than none. Marked with a new `performance` pytest
   marker, deliberately *not* wired into CI's `integration` job: shared CI runners
   give meaningless/noisy throughput numbers, and the master plan itself says real
   performance validation happens on approved hardware, not commodity CI - these
   are for the user to run on real hardware during pilot prep, and for local sanity
   checks now.
5. ruff/mypy/existing suite clean, new tests passing locally against the docker
   Postgres via the same mechanism used earlier this session (`.venv` + a temporary
   non-persistent port-forward, or whatever proves reachable at the time), then
   push and watch CI for the authoritative result on the new `concurrency`-marked
   test (CI's `integration` job already runs everything under `tests/`, so the new
   staffing-capacity test rides along automatically; the new `performance`-marked
   test does not, by design).

## 5A verification status
- [x] Staffing-capacity race test written
      (`tests/concurrency/test_staffing_capacity_concurrency.py`).
- [~] Confirmed red against unfixed code by inspection, not by an empirical
      local run: Docker Desktop went down mid-session (its processes were
      running but its CLI and every container, including the one this session
      had been using all along, stopped responding - not this sandbox's
      earlier network restriction, a genuine local outage) partway through
      this increment, before the test had been run once. Read
      `_check_staffing_capacity` line by line instead: a plain `SELECT`
      (no `FOR UPDATE`, no advisory lock) followed by a separate `INSERT` in
      the caller, with nothing else in either code path that would serialize
      two concurrent callers - the same shape of race
      `test_leasing_concurrency.py` already proves is real for leasing
      without a lock, and already proves is closed once one is added. Treating
      this as sufficient to proceed rather than blocking the whole increment
      on Docker coming back, since CI verification (below) does not depend on
      local Docker at all and gives a genuine answer either way.
- [x] Fix applied (`FOR UPDATE` row lock on the `CampaignTeamAssignment` row
      in `_check_staffing_capacity`, covering both call sites -
      `assign_agent_to_campaign` and `transfer_agent` - since both already
      went through this one function).
- [ ] Test confirmed green: pending CI (local Docker still down as of this
      write-up).
- [x] Load/throughput test added under `tests/performance/`
      (`test_leasing_throughput.py`, 60 agents x 60 items, full-contention
      drain, prints leases/sec). New `performance` pytest marker added,
      deliberately excluded from *both* CI jobs that run test files
      (`quality`'s `not integration` become `not integration and not
      performance` - it has no database service and would otherwise try to
      collect a DB-dependent test; `integration`'s plain `pytest -m
      integration` already excludes it by construction). Verified locally via
      `--collect-only` that the marker split is exactly right: quality's
      selector still collects the same 27 tests as before these changes,
      integration's selector picks up the new staffing-capacity test and nothing
      from the performance file.
- [x] ruff and mypy clean on every changed file (checked locally, no Docker
      needed for either).
- [ ] Pushed, CI green - next step.

## Log
- 2026-08-21: reconciled Phase 5 scope, asked the user which slice to start with
  (concurrency & load tests chosen), audited for untested check-then-act races,
  found the campaign staffing-capacity gap, wrote this plan.
- 2026-08-21: wrote the staffing-capacity concurrency test, applied the
  `FOR UPDATE` fix, added the throughput test under `tests/performance/` and
  the new `performance` marker, fixed CI's `quality` job selector to exclude
  it. Local Docker Desktop went down mid-increment (unrelated to this
  session's earlier port-publishing restriction - a real local outage, CLI
  and containers both unresponsive); diagnosed the race by code inspection
  instead of an empirical local red run, and moved straight to push + CI
  rather than block on Docker recovering, since CI's Postgres is independent
  of it either way.
