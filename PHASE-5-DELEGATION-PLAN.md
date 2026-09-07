# Phase 5 operational workflows: acting-role / delegation

Reconciliation: the `Delegation` model exists (app/models/authz.py: delegator/
delegate/capability_set(JSONB)/scope_type/scope_id/effective_from/to/reason_code/
approved_by/revoked_at) and its table is in baseline 0001, but nothing reads or
writes it. Spec: plan v0.3 sections 11.3 + "delegations" - a delegation grants a
capability_set at a scope for a time window; it "never includes capabilities the
delegator does not hold, never permits self-approval, and expires automatically";
role-elevation / DNC-correction / closed-period-reopening and other non-delegable
caps are excluded; requires reason + effective period + appointing authority +
audit + step-up; expired/revoked grants no capability (request-time check).

## Increment 1: resolution + management (service + API)  [STATUS: DONE 2026-09-02; integration 197; no migration; pending commit/CI]

### Authz-core integration (the security-critical part)
A delegation is structurally a role assignment for scope purposes (both carry
scope_type/scope_id). So integrate via ONE new grant source, leaving the scope-
covering logic untouched (it reads only scope_type/scope_id):
- `_ScopedGrant` Protocol (scope_type, scope_id) - both RoleAssignment and
  Delegation satisfy it; retype the 3 scope-covering helpers to it.
- `_active_delegations(db, user_id)` - the delegate's non-revoked, in-window
  delegations (request-time expiry, so no job needed for correctness).
- `_capability_grants(db, user_id, capability)` - role assignments granting the
  cap (via ROLE_CAPABILITIES) PLUS active delegations whose capability_set holds
  it. Both types feed the SAME scope logic.
- Rewrite has_capability / has_assigned_capability / has_scope_capability /
  scope_capabilities_matched / campaign_scope_filter to iterate
  `_capability_grants` instead of role-assignments-filtered-by-ROLE_CAPABILITIES.
  With zero delegations the behavior is byte-identical, so existing role tests
  stay green. This makes BOTH single and bulk paths (incl. import approval and
  campaign visibility) delegation-aware CONSISTENTLY - no partial integration.
- `users_with_any_capability` also unions in delegate ids (so a delegated
  approver is in the broadcast candidate set).

### Management (app/authz/delegations.py)
- NON_DELEGABLE = {TECHNICAL_CONFIG, RESET_USER_AUTH, CREATE_MANAGER, MANAGE_ROLES}
  (role-elevation / system-config; plan's baseline non-delegable set).
- `create_delegation(...)`: delegate != delegator (no self-delegation); caps
  non-empty, all known, none non-delegable; delegator holds EVERY cap at the
  scope (has_scope_capability) - can't delegate authority you lack; window valid
  (effective_to > effective_from if set). approved_by = delegator. Audit
  `delegation.create`. Invalidate the delegate's sessions (privilege change).
- `revoke_delegation(...)`: only the delegator; set revoked_at; audit
  `delegation.revoke`; invalidate delegate's sessions.
- `list_delegations(db, *, delegator_id/delegate_id)`.

### API (app/api/delegations.py) - plan 11.x endpoints
- POST /api/v1/delegations (create; delegator = current user; require_csrf +
  require_recent_reauthentication per plan 11.3 step 4).
- DELETE /api/v1/delegations/{id} (revoke; require_csrf).
- GET /api/v1/delegations (the caller's, as delegator or delegate).

### Tests
Authz honors an active delegation (grants a scoped cap the delegate's roles
don't); expired / future / revoked / out-of-scope grants nothing; non-delegable
rejected; self-delegation rejected; can't delegate a cap you don't hold at the
scope; revoke works + drops the grant; end-to-end (a delegate can now approve a
scoped import / access a campaign they couldn't before); existing role tests
unchanged.

## Increment 2: web console to grant / revoke / view delegations  [STATUS: DONE 2026-09-07; integration 203, unit 35, ruff/mypy/docker clean; pending commit/CI]

### Authz hardening (do first, before the UI widens exposure)
The authz refactor made `has_scope_capability` delegation-inclusive, so
increment 1's `create_delegation` (which used it for the authority check) would
let a delegate RE-delegate a capability they only hold via a delegation. That
creates chains: revoke A->B and B->C is orphaned (B->C's authority was checked
only at creation). Fix: a delegate may EXERCISE a delegated capability but not
re-delegate it, so the grant-authority check must read role-based authority only.
- add `_role_capability_grants` (role assignments only) + `has_scope_capability_via_role`
  in app/authz/service.py; `_capability_grants` now composes the role helper + delegations.
- `create_delegation` authority check switches to `has_scope_capability_via_role`.
- test: a delegate who holds a cap only via delegation cannot re-delegate it.

### Web (app/web/delegations.py + app/templates/delegations.html)
Personal page (like the inbox), no hard capability gate: it shows the user's own
granted + held delegations. The create form appears only when the user actually
holds a delegable capability.
- GET /delegations: "Delegations you granted" (with a Revoke button each, unless
  already revoked/expired) and "Delegations you hold" (read-only); a create form
  populated from `list_visible_users` (delegate picker, self excluded), the
  role-derived delegable caps (`capabilities_for(effective_roles) - NON_DELEGABLE`)
  as checkboxes, a scope_type select with team + campaign pickers (campaigns
  scoped by `campaign_scope_filter(VIEW_CAMPAIGN)`), a UTC effective window, and a
  reason. A per-row status (active / scheduled / expired / revoked) computed in
  Python. Resolve delegator/delegate names and team/campaign scope names into maps.
- POST /delegations (verify_form_csrf): STEP-UP enforced - if not
  is_recently_reauthenticated(session), redirect with a flash telling them to
  confirm identity at Account security first (the API used
  require_recent_reauthentication; this is the web equivalent). scope_id resolved
  from the field matching scope_type (no free-text UUID; no inline JS - CSP forbids
  it). Parse the datetime-local inputs as UTC-aware. Map DelegationError -> flash.
- POST /delegations/{id}/revoke (verify_form_csrf): CSRF only (de-escalation, matches
  the API's DELETE). Map DelegationNotFound / NotAuthorizedToRevoke -> flash.
- Register the router in app/main.py; add a dock + side-nav entry (active_section
  'delegations') gated on can_manage_workforce (the roles that own delegable authority).

### Tests (tests/integration/test_delegation_web.py)
Page renders granted + held; create grants (end-to-end: delegate gains the cap);
create without recent reauth is refused (step-up); revoke drops the grant;
non-delegable / unknown / self / cap-not-held rejected with a flash; a delegate
cannot re-delegate a delegation-only capability.

## Verify
ruff/mypy, no migration (table exists), full suite vs real Postgres (Docker;
Redis 16379), docker build, BUILD-LOG, commit/push/CI.
