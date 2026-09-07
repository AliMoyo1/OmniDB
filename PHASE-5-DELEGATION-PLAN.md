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

## Increment 2 (later): web UI to grant/revoke/view delegations.

## Verify
ruff/mypy, no migration (table exists), full suite vs real Postgres (Docker;
Redis 16379), docker build, BUILD-LOG, commit/push/CI.
