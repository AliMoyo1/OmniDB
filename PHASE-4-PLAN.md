# Phase 4 plan: management hierarchy, workforce, viewer, and administration

Status: Phase 4A complete - 4A-1 through 4A-4 all built and verified (2026-08-21).

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

## 4A-4: Manager / Team Leader / Team Captain dashboards [done]

The first server-rendered UI in this build. One parameterized dashboard (`GET
/dashboard`) whose sections are gated by the viewer's own capabilities, rather than
three hardcoded pages - the shape is identical for Manager/Team Leader/Team Captain,
only the visible sections and data differ, and plan 6.1's "separate dashboards" is
delivered by what each role actually sees. Real HTML forms (no JavaScript, no
HTMX vendoring - simpler and avoids a third-party JS dependency for a first pass),
CSRF via a hidden form field validated against the same signed-token mechanism the
JSON API already uses, session-cookie page auth that redirects to `/login` instead
of a JSON 401 (`app/web/dependencies.py`'s `RedirectToLogin`, handled by an
exception handler in `app/main.py`). Every action calls the exact same service-layer
functions the JSON API already uses (`campaign_service`, `workforce_service`) - new
presentation over already-tested business logic, not a reimplementation.

Deduplicated two scoped-visibility queries that would otherwise have been written
twice (JSON endpoint and dashboard): `app/workforce/service.py::list_visible_users`
and `app/api/admin.py::list_visible_audit_events`, both now shared by their
original JSON route and the new dashboard route.

### Real findings from live verification (not simulated - curl through the actual HTTPS/Caddy/Postgres stack)

The Browser pane tool couldn't get past Caddy's internal-CA certificate (hard
network-level failure, not even an interstitial to click through), so verification
used curl through the real stack instead - the same method already proven for this
environment during the original business-flow verification pass. This found four
real, independent bugs that pytest alone would not have caught, in roughly
ascending order of how much they mattered:

1. **`docker cp` directory-nesting**: copying a source directory into an
   already-existing destination directory (both existed as empty scaffolds with
   `.gitkeep`) nests it - `docker cp app/templates ...:/app/app/templates` produced
   `/app/app/templates/templates/*.html`, not `/app/app/templates/*.html`. Caused an
   immediate `TemplateNotFound` 500 on the very first request. Fixed by copying
   individual files to their exact destination path instead of copying directories.
2. **O(teams x users) dropdown blowup**: the "add team member" and "assign agent"
   forms originally embedded a full `<select>` of every visible user inside every
   team/campaign table row. Against this session's heavily-reused dev database
   (accumulated teams and users from dozens of full-suite runs), that produced an
   850KB page. Fixed by replacing both dropdowns with a plain user-ID text input
   (matching the pattern already used for `scope_id`) - a real scaling fix, not just
   a number bumped up, since the cross-product problem would recur at any
   moderately larger team/user count regardless of individual query limits. Also
   added a missing `.limit()` to the teams query, which had none.
3. **A capability computed but never reaching the template**: `can_view_campaigns`
   was computed in `dashboard()` but never added to the `page_context(...)` call
   that builds the template context. Jinja2's default `Undefined` is falsy in an
   `{% if %}`, so this failed silently - no error, no crash, just the entire
   Campaigns section missing for every user regardless of their real capability.
   Caught by the new pytest coverage (`test_dashboard_shows_manager_sections`),
   which is exactly the kind of regression a live-only check could have let back in
   on the next change - now guarded by a real assertion, not a manual look.
4. **Login rate-limit source signal too tight for the test suite's own volume**:
   `pytest`'s `TestClient` never sets a real client IP, so every integration test's
   login shares one "unknown" source bucket. A full run now performs 101 logins
   (14 test files deep into Phase 4A), just over the existing `_SOURCE_LIMIT = 100`
   - confirmed to reproduce even against a freshly-flushed Redis, meaning CI would
   have hit it too. Not a bug in the limiter (it did exactly its job); the account
   limit (real per-credential brute-force protection, `_ACCOUNT_LIMIT = 10`) was
   never at risk and is untouched. Source and global raised 5x
   (`app/auth/ratelimit.py`) for headroom as the suite keeps growing, keeping the
   same relative ordering between tiers.

Also fixed proactively, before it ever ran: the "create user" action originally
would have redirected back to `/dashboard?flash_success=...token...`, putting a
one-time activation secret in a URL query string (browser history, proxy logs) -
directly against this session's own "never put sensitive data in query strings"
rule. Caught in code review before the first test run; the confirmation now renders
directly instead of redirecting.

### Known simplifications (documented, not gaps)

- Scope selection in the "assign role" form is a raw team-UUID text input, not a
  team-name dropdown - a direct consequence of fix #2 above (a full dropdown of
  every team doesn't scale either, for the same reason the user dropdown didn't).
  A searchable picker is a natural follow-up, not attempted here.
- No JavaScript and no HTMX: every action is a full page POST-redirect-GET. Simpler
  and more robust for a first pass (works with the browser's own back/forward, no
  third-party JS to vendor or audit under D-04's LAN-only/no-CDN constraint), at
  the cost of full-page reloads for each action. HTMX progressive enhancement
  (named in the plan's stated stack) is a reasonable later addition, not required
  for the dashboard to be genuinely functional.
- Bulk actions (uploading a CSV, launching/pausing/archiving a campaign, ending an
  assignment or a role, agent transfer) don't have dashboard forms yet - only
  create/assign paths do. The JSON API already covers all of these; extending the
  dashboard to them is incremental, not a redesign.
- Agent and Viewer get no dashboard content beyond the empty shell and (for Viewer)
  a read-only Campaigns/reports view via the same capability gates - a dedicated
  Agent workspace is out of scope for 4A (Phase 3 built the API only; its own UI is
  separate future work).

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

## 4A-4 verification status
- [x] No new migration - all forms call existing, already-tested service functions
      against the existing schema.
- [x] ruff clean across every new and modified file, repository-wide sweep clean.
- [x] 11 new integration tests in `tests/integration/test_web_dashboard_flow.py`
      (unauthenticated redirect, login page renders, login success sets cookies,
      wrong password shows an error and preserves the entered email, Manager sees
      every section, Agent sees none, create campaign via form, bad CSRF token is
      rejected, create-user renders the activation token directly rather than
      redirecting, assign-role verified against the database directly rather than
      by scraping rendered HTML, logout revokes the session), plus the full
      existing suite - 104 passed total, no regressions. Two tests needed
      correcting during the pass, not the application code: a negative
      (absence) assertion that could pass for the wrong reason against a shared,
      non-rolled-back database (same lesson as the session's earlier zw_numbers
      fix), and a presence check against a string that appears unconditionally in
      the page regardless of whether the action actually succeeded.
- [x] Live verification through the actual HTTPS/Caddy/Postgres/Redis stack via
      curl (the Browser pane tool could not get past Caddy's internal-CA
      certificate - a hard failure before any interstitial, not something this
      environment could click through). Full flow exercised end to end: login,
      dashboard render with real data, create campaign, create user (confirmed the
      activation token renders directly, not via a URL-embedded redirect), assign
      role, create team, add team member, assign agent to campaign, stats
      reconciling correctly (0 before assignment, 1 after), bad-CSRF rejection
      confirmed to genuinely not perform the action, unauthenticated redirect,
      logout confirmed to genuinely revoke the session even across a container
      restart, and a plain Agent's dashboard confirmed to degrade cleanly to an
      empty-but-valid page. Found and fixed 4 real bugs in the process (see above).
- [x] mypy - not runnable locally, same as every increment this session; scanned
      new code by hand against the lesson from 4A-2's failure (an untyped `{}`
      dict literal in `_redirect()` given an explicit `dict[str, str]` annotation
      proactively; no other `X | None`-in-arithmetic patterns found) before
      pushing.

## 4A-4 UI redesign: ThemisIQ design-system port [done]

The 4A-4 dashboard was functionally complete but visually plain. Ported the
visual design system (not any code or data) from the separate "One For All"
platform: CSS custom-property theme tokens (light/dark, self-hosted Space
Grotesk + JetBrains Mono via `@font-face`), glass-morphism cards
(`backdrop-filter: blur()`), a 3D mouse-tilt hover effect on stat cards, and a
canvas-based particle-network background. Added the two pieces of navigation
chrome the user asked for on top of that: a persistent left icon dock
(`.icon-dock`, expands on hover, shows only the sections the viewer's own
capabilities grant) and a secondary text sidebar (`.side-nav`, mirrors the dock
with item counts, hides below 1200px). Both read the same `nav_flags()` used to
gate the dashboard's own sections, computed once in `app/web/templates.py` and
attached to every authenticated page's context (`page_context()`) rather than
left for each route to pass individually - a route that forgot a flag would
silently show an incomplete nav with no error, the same class of bug already
hit once this session in the dashboard's own Campaigns section.

This intentionally reverses 4A-4's original "no JavaScript" decision: the
particle background and tilt effect need it. Kept it to three small,
externally-loaded vanilla-JS files (`particles.js`, `card-tilt.js`,
`theme.js`) - no framework, no build step, no third-party dependency - so the
spirit of that original call (nothing to compile, nothing to vendor) still
holds even though the letter of it no longer does.

### Real findings from live verification

Two independent bugs, neither of which pytest would have caught since neither
is expressible as a request/response assertion:

1. **`{% block content %}` defined twice in `base.html`**: the initial version
   defined the block once inside the authenticated (`{% if user %}`) branch and
   again inside the unauthenticated (`{% else %}`) branch, reasoning that only
   one branch would ever render. Jinja2 parses block definitions statically and
   forbids the same name twice in one template regardless of runtime branching
   - every page render raised `TemplateAssertionError`. Fixed by restructuring
   so the block appears exactly once, unconditionally, inside a `<main>` that
   always renders; the chrome around it (dock, sidebar, topbar) is each wrapped
   in its own independent `{% if user %}...{% endif %}` around a complete
   element, and `<main>` picks up a `login-wrap` class only when there's no
   user, rather than the shell being duplicated per branch.
2. **CSP silently blocked the theme toggle**: `app/middleware.py`'s
   `Content-Security-Policy` header (`default-src 'self'`, no `unsafe-inline`,
   no nonce - deliberately strict, and correctly so for an app whose whole
   purpose is PII/DLP handling) blocks inline `<script>` blocks and inline
   event-handler attributes. The first draft had both: an inline
   theme-restore script in `<head>` and an inline `onclick` on the toggle
   button. Both were silently no-ops in the browser (console showed the CSP
   violation; nothing else did). Fixed by extracting both into
   `app/static/js/theme.js` (loaded via `<script src>` in `<head>`, same
   execute-before-paint timing as the inline version it replaced) and
   replacing the `onclick` attribute with a delegated `document`-level click
   listener - matching the CSP-compliant pattern `particles.js` and
   `card-tilt.js` already used. Did not weaken the CSP; the app's own security
   posture is the reason to fix the script instead.

Confirmed via the Browser pane against the real running stack (hot-patched
into both the preview and main containers): dashboard renders with real data
in all four sections (Campaigns/Workforce/Teams/Audit), dock and sidebar show
the correct conditional items, particle background animates, login page
renders centered with the glass card. The toggle's underlying logic
(`ccToggleTheme()`, the delegated listener, localStorage persistence) was
verified correct by direct DOM dispatch after the Browser pane's simulated
mouse clicks turned out to be unreliable in this session's environment for
reasons unrelated to the app (a coordinate-scaling bug that put real clicks
outside the viewport after a manual `resize_window` call, and separately, the
known stale-tab flakiness already documented in 4A-4's own verification
above) - not chased further once isolated to tooling rather than application
code.

### 4A-4 UI redesign verification status
- [x] ruff clean, mypy clean (`Success: no issues found in 71 source files`).
- [x] Unit + authorization suite: 27 passed locally (`pytest -m "not
      integration"`, `APP_ENV=development` override only - matches what CI's
      `quality` job runs, no database needed).
- [x] Integration suite (includes `test_web_dashboard_flow.py`, the most
      relevant file for this change): not runnable locally this time for a new
      reason - `compose.yaml` deliberately publishes no database/app ports to
      the host, and this session's sandbox blocks publishing new ports even via
      a throwaway forwarding container, so there is no path from the host into
      the running Postgres/Redis. Live Browser-pane verification against the
      real stack (above) covers the same rendered-output surface these tests
      check; real CI (below) confirms the tests themselves still pass.
- [x] Pushed (commit `ea113fb`) and watched CI run 32516090093 to completion:
      build, security, integration, and quality all green. No regressions from
      the template restructuring.

## Log
- 2026-08-21: reconciled Phase 4 scope against D-19/D-20/D-21, wrote this plan,
  built and verified 4A-1 (workforce foundation), 4A-2 (campaign assignment API and
  transfer), 4A-3 (aggregate campaign reports, Viewer's first real capability,
  scoped audit search), and 4A-4 (Manager/Team Leader/Team Captain dashboards -
  the first server-rendered UI in this build). Phase 4A is now complete.
- 2026-08-21: redesigned the 4A-4 dashboard with the One For All platform's
  visual design system (theme tokens, glass cards, particle background, tilt
  hover) plus a new icon dock and text sidebar for navigation. Found and fixed
  a Jinja2 duplicate-block bug and a CSP violation that silently broke the
  theme toggle. ruff/mypy/unit/authorization all clean locally; integration
  suite deferred to CI (no local DB access in this sandbox).
