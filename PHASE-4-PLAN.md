# Phase 4 plan: management hierarchy, workforce, viewer, and administration

Status: 4A-1 built and verified (2026-08-21).

## Scope reconciliation against the decision log

The master plan (`docs/architecture/CipherContact - Detailed Implementation Plan
v0.3.md`, section "Phase 4") splits the original Phase 4 into 4A/4B/4C because the
bundled 3-to-5-week estimate wasn't credible for the surface area. The decision log
then deferred two of those three sub-phases from the first pilot entirely:

- **4A** (Manager/Team Leader/Team Captain dashboards, manual non-bulk user/
  membership/role/reporting-line/campaign assignment, viewer scope, protected audit
  search) - **in scope**. D-18 (transfer preflight, decided) also lives here.
- **4B** (staged bulk-workforce import) - **deferred**, D-21: create pilot users
  manually instead.
- **4C** (target policies, exemptions, proration, performance periods) - **deferred**,
  D-19/D-20: this is effectively an embedded performance-management module and the
  plan itself names it "the strongest candidate to defer until after the first pilot
  proves the core call-work loop."

So "Phase 4" in this build means 4A only. The authoritative capability matrix is
plan section 6.3; the role hierarchy and scope rules are 6.2 and 6.4. `app/authz/
capabilities.py` already defines every capability constant 6.3's non-target/non-bulk
rows need (`CREATE_MANAGER`, `APPOINT_TEAM_LEADER`, `APPOINT_TEAM_CAPTAIN`,
`CREATE_AGENT`, `MANAGE_ROLES`) and the starter `ROLE_CAPABILITIES` map already
matches those rows - Phase 1 anticipated this correctly, so 4A needs no new
capability constants, only the service/API/UI layers that use the ones already there.
Likewise every core data model (`Organization`, `Team`, `TeamMembership`,
`RoleAssignment`, `ReportingAssignment`, `Delegation`, `CampaignTeamAssignment`,
`CampaignUserAssignment`) already exists from Phase 1/2A - 4A is new service and API
code against an existing schema, not a new migration, except where noted below.

## 4A-1: workforce foundation - users, roles, teams, reporting lines [done]

The prerequisite everything else in 4A needs: an actual API to create a user and
grant a role, where today only test fixtures do this via direct DB insert (flagged
as a gap in both PHASE-2-PLAN.md and PHASE-3-PLAN.md).

- `app/workforce/service.py`: `create_user` (mirrors `reset_password`'s
  activation-token pattern - a new user gets an activation token back, no password
  is ever set by an admin), `assign_role` / `end_role_assignment`, `disable_user` /
  `reactivate_user`, `create_team`, `add_team_membership` / `end_team_membership`,
  `set_reporting_line`.
- Role-appointment capability is looked up per target role_code
  (`ROLE_MANAGER -> CREATE_MANAGER`, `ROLE_TEAM_LEADER -> APPOINT_TEAM_LEADER`,
  `ROLE_TEAM_CAPTAIN -> APPOINT_TEAM_CAPTAIN`, `ROLE_AGENT -> CREATE_AGENT`,
  `ROLE_VIEWER -> MANAGE_ROLES`, matching the diagram's "Manager grants report
  scope to Viewer") and checked with `authz.has_scope_capability` against the
  assignment's own target scope - the same function `create_campaign` already uses,
  not a new authorization primitive. `super_admin` is intentionally not assignable
  through this API; it stays a manual/ops-provisioned role, consistent with how the
  very first Manager account in this build was created outside the app.
- Self-appointment and self-supervision are blocked (`authz.assert_not_self`,
  reused) - plan 6.4's "requester and approver are not the same person" and 6.5's
  explicit self-approval test case.
- Assigning a role the target already actively holds at the same scope ends the
  prior assignment before creating the new one (no overlapping duplicates), and
  every role grant or end calls `authz.invalidate_sessions_on_privilege_change`
  (built in Phase 1, never called until now) per plan 6.4: "role changes rotate or
  invalidate sessions."
- Disabling a user revokes sessions **and** reclaims active leases - plan 6.4 names
  both explicitly. Lease reclaim needed a small addition: `app/work/service.py`
  gains `reclaim_leases_for_user`, mirroring the existing `reclaim_expired_leases`
  (callback leases return to `callback_wait`, shared-pool leases to `queued`) but
  filtered by owner instead of expiry.
- `set_reporting_line` rejects self-supervision and ends any prior active `primary`
  assignment for the same (subordinate, context) pair first, mirroring the D-17
  one-active-primary-assignment pattern already used for campaigns.
- `app/api/workforce.py` (`/api/v1/workforce`): users, roles, disable/reactivate,
  teams, memberships, reporting line. Kept separate from `/api/v1/admin` (Super
  Admin technical actions - password/2FA reset, audit search) since this is the
  business-hierarchy surface Manager/Team Leader/Team Captain use day to day, not a
  technical-config surface.
- User listing is scope-filtered (Super Admin and org-wide Manager see everyone;
  Team Leader/Team Captain see only users who share an active team membership with
  them), the same "don't leak across scope" principle the security-hardening commit
  already enforced for campaigns.

Deferred out of 4A-1 specifically (not out of Phase 4 - just later increments):
- Delegation/acting-role API. The model exists; wiring delegated capabilities into
  `has_capability`'s resolution path is a distinct, non-trivial change worth its own
  increment rather than folding into the foundational one.
- Anything Viewer can actually view (no `VIEW_ANALYTICS`-style capability or report
  endpoint exists yet - that belongs with 4A-4/reporting, not the identity/role
  foundation).

## 4A-2: campaign assignment API and transfer (D-18) [done]

Added a real, capability-gated API for what only test fixtures could do before
(flagged as a gap in PHASE-2-PLAN.md and PHASE-3-PLAN.md): assign a team or an
agent to a campaign, end either assignment, and transfer an agent between campaigns.
`tests/integration/conftest.py::assign_agent_to_campaign` stays as a fast direct-
DB test-setup helper (same call as 4A-1 made for `make_user_with_role` staying
alongside the real workforce API) - it isn't removed, just no longer the only way.

- New capability `ASSIGN_CAMPAIGN_AGENT` (plan 6.3's "Assign Agent to campaign" /
  "Move Agent between campaigns" rows share the same grantees: Manager, Team
  Leader, Team Captain), checked with the existing `has_campaign_capability`.
- `assign_agent_to_campaign` deliberately refuses to move an agent who already has
  an active primary assignment elsewhere (409, "use transfer instead") rather than
  silently superseding it the way 4A-1's role re-assignment does - a campaign move
  has real consequences (in-flight leases, pending callbacks) that only
  `transfer_agent`'s preflight handles safely; letting a plain assign silently
  supersede would open a second, unsafe path to the same state.
- D-18's decision text ("adopt transfer preflight, ops picks defaults") left the
  lease/callback treatment for us to pick a default for. Chose "return to the
  source queue" for leases - the same treatment `reclaim_leases_for_user` (4A-1)
  already uses for a disabled user - and, after tracing `_next_callback_candidate`,
  found that "retain" (do nothing) is actually unsafe for callbacks specifically:
  leasing a due callback requires an active assignment on that same campaign, so a
  callback left assigned to an agent whose assignment just ended would be silently
  unleaseable by anyone - exactly the "orphaned callback" failure point the plan
  names. Both lease and callback release now go through one new function,
  `app/work/service.py::release_campaign_work_for_agent`, scoped to a single
  (agent, campaign) pair - distinct from 4A-1's `reclaim_leases_for_user`, which is
  correct for a full account disable but too broad for a single-campaign transfer.
- Destination "staffing capacity" preflight: real but minimal - only applies when
  the assignment names a `team_id` and that team has an active `CampaignTeamAssignment`
  with a set `staffing_capacity` on the destination; otherwise unlimited. The fuller
  staffing/workforce-allocation model is 4C territory (deferred with targets).
- Target proration is correctly absent from `transfer_agent` - D-19/D-20 defer the
  whole target subsystem from this pilot, so there is nothing to prorate yet.
- Transfer checks `ASSIGN_CAMPAIGN_AGENT` on **both** the source and destination
  campaign - an actor authorized to move agents out of campaign A cannot use that
  same authority to place them into a campaign B they have no standing over.

## 4A-3: protected audit search and Viewer role [done]

Two related pieces, both scoped narrowly on purpose rather than building the full
plan 6.3 "View analytics" surface in one pass.

**Aggregate campaign reports.** New `app/reporting/campaign_stats.py`, extending
the exact pattern Phase 3's `agent_stats.py` already established (live counts over
immutable `call_attempts`, not a precomputed rollup): total contacts, active
assigned agents, total attempts, connected, conversions, DNC requests for one
campaign. New `GET /api/v1/campaigns/{campaign_id}/stats`, gated by a new capability
`VIEW_CAMPAIGN_REPORTS` (deliberately separate from `VIEW_CAMPAIGN` - reports are
totals only, plan 6.1's line for Viewer: "no raw contact, note, import-row, or DNC
access"). Granted to Manager, Team Leader, Team Captain, and Viewer.

**Viewer's first real capability.** `ROLE_VIEWER` was `set()` since Phase 1 - gave
it `VIEW_CAMPAIGN` (campaign metadata: name, status, provenance - never contacts)
and the new `VIEW_CAMPAIGN_REPORTS`, both already scope-checked through the existing
`has_campaign_capability`/`campaign_scope_filter` machinery, so a Viewer's assigned
report scope is exactly their `RoleAssignment.scope_type`/`scope_id` - no separate
"report scope" concept needed.

**Scoped audit search.** `GET /api/v1/admin/audit-events` was Super-Admin-only and
fully unscoped. Extended `VIEW_AUDIT` to Manager/Team Leader/Team Captain, but a
real design problem surfaced immediately: `AuditEvent.team_id`/`organization_id`
exist on the model but almost no `record_audit(...)` call site across this entire
build actually sets them, so a scope filter keyed on those columns would see
everything as "unscoped" and leak globally. Rather than retroactively touching
every existing call site (large, invasive, out of proportion for this increment),
scoped visibility is resolved a different way: installation- or organization-wide
`VIEW_AUDIT` sees every event (Super Admin, Manager in this single-org pilot); a
team-scoped grant (Team Leader, Team Captain) sees only events whose
`actor_user_id` is themselves or an active member of their own team(s), reusing
`TeamMembership` the same way 4A-1's user-listing scope filter does. This is a
real, non-leaking, genuinely useful slice of the trail ("what did my people do")
rather than a fully general target-type-aware resolver - documented here as the
known simplification it is, not a hidden gap.

## 4A-4: Manager / Team Leader / Team Captain dashboards [not started]

The first server-rendered UI in this build. `app/templates/` and `app/static/` exist
as empty scaffold directories only - Jinja2Templates/StaticFiles aren't wired into
`app/main.py` yet, there's no HTML-page session auth pattern (only JSON API auth
exists today), and no base layout. Treated as its own increment rather than folded
into the API work above, since it's a materially different kind of work (needs
browser-based verification per the UI workflow, not just pytest).

## 4A-5: tests and the authorization-negative matrix [ongoing alongside each increment]

Plan 6.5's test matrix (wrong team, lower role, expired assignment, guessed UUID
belonging to someone else's scope, self-approval, etc.) applies directly to 4A-1 and
4A-2's new endpoints. Each increment ships with its own integration tests rather
than deferring verification to the end, matching how Phases 1-3 were built and
reviewed.

## 4A-1 verification status
- [x] Migration 0008 (partial unique indexes on role_assignments and
      reporting_assignments, `NULLS NOT DISTINCT`) applied and round-tripped
      (downgrade -1 / upgrade head) against real Postgres.
- [x] ruff clean across every new and modified file, repository-wide sweep clean.
- [x] 14 new integration tests in `tests/integration/test_workforce_flow.py`, plus
      the full existing 62-test suite still green (76 passed total) - no regressions.
- [ ] mypy - not runnable in this dev environment (documented Python 3.14 mismatch,
      see PHASE-1-PLAN.md); CI's blocking mypy step is the first real check of this
      increment's type annotations.
- [ ] Live verification through the actual HTTPS/Caddy stack (curl or browser) - the
      integration tests already exercise real Postgres, real session cookies, and
      real CSRF tokens end to end; a separate manual pass wasn't done for this
      increment specifically.

### A design property worth noting, found while writing the tests
`can_manage_user` only ever authorizes appointment capability held over a role
*strictly below* the target's own role - by construction, no role can ever satisfy
this check against a target holding the same role (a Manager can't "manage" another
Manager this way, let alone themselves). This means an actor can never target
themselves through `disable_user`, `reactivate_user`, or `set_reporting_line`: the
403 from `can_manage_user` fires before any of those functions' own self-approval or
self-supervision guards would matter. Those service-layer guards stay in place as
defense in depth for future callers of the service functions directly (bulk import,
delegation), but the first self-supervision test had to be rewritten to use a
Manager acting on a separate Agent (naming that agent as their own supervisor)
rather than a Manager acting on themselves, to isolate the domain check from the
authorization gate. Not a bug - just worth knowing before extending this area.

## 4A-2 verification status
- [x] No migration needed - campaign_team_assignments and campaign_user_assignments
      have existed since migration 0002; this was new service/API code against an
      existing schema, same story as 4A-1.
- [x] ruff clean across every new and modified file, repository-wide sweep clean.
- [x] 10 new integration tests in `tests/integration/test_campaign_assignment_flow.py`
      (assign/end team and agent, the double-primary guard, staffing capacity, a
      full transfer that both moves the assignment and releases a leased item, the
      pending-callback-not-orphaned case specifically, capability required on both
      campaigns, destination-not-active rejection, and two authorization-negative
      cases), plus the full existing suite - 86 passed total, no regressions.
- [ ] mypy / real CI - not runnable locally, same as every increment this session;
      pending the next push.
- [ ] Live verification through the actual HTTPS/Caddy stack - not done separately
      for this increment either, same reasoning as 4A-1 (the integration tests
      already exercise the real stack end to end).

## 4A-3 verification status
- [x] No migration needed - reports are computed live over existing tables, and
      audit scoping reuses the existing team_memberships table.
- [x] ruff clean across every new and modified file, repository-wide sweep clean.
- [x] 7 new integration tests in `tests/integration/test_reporting_and_audit_flow.py`
      (campaign stats reconciled exactly against a real multi-disposition attempt
      sequence, Viewer can read a campaign and its stats but nothing else including
      audit search, Viewer's visibility is scope-bounded to their assigned team,
      Agent is denied both stats and audit search, Manager's org-wide audit search
      works, and the team-scoped audit visibility test specifically), plus the full
      existing suite - 93 passed total, no regressions.
- [ ] mypy / real CI - not runnable locally; applied the `db.scalar(count) or 0`
      fix learned from 4A-2's CI failure proactively in campaign_stats.py's two
      count queries, but this is unverified until the actual push.

## Log
- 2026-08-21: reconciled Phase 4 scope against D-19/D-20/D-21, wrote this plan,
  built and verified 4A-1 (workforce foundation), 4A-2 (campaign assignment API and
  transfer), and 4A-3 (aggregate campaign reports, Viewer's first real capability,
  scoped audit search).
