# Review round (2026-09-07): three P1 findings on the recent Phase 5 work

[STATUS: DONE 2026-09-07; all three fixed with regression tests; integration 210 (+4),
unit 35, ruff/mypy/docker clean, no migration; pending commit/CI]

All three confirmed real in the code. Fixes below, each with a regression test.

## P1-1: delegated authority survives loss of the delegator's role
app/authz/service.py - `active_delegations` / `_capability_grants` honor a held
delegation on recipient + window + revocation only, never re-checking that the
DELEGATOR still holds the capability via a role. So ending (or expiring, or
rescoping) the delegator's backing role leaves the delegate holding the capability
until the delegation expires: an orphaned privilege.

Fix (request-time revalidation - self-correcting for EVERY authority-loss path, not
just end_role_assignment, and matches the existing request-time expiry design):
- In `_capability_grants`, include a held delegation for `capability` only if
  `has_scope_capability_via_role(delegator, capability, delegation's scope)` is still
  true. If the delegator's role-based authority is gone, the delegation grants nothing.
- Same revalidation in `users_with_any_capability`'s delegate union (a delegate whose
  delegation is no longer backed is not even a candidate).
- Plus, on role-end: `end_role_assignment` already revokes the affected user's own
  sessions; also invalidate the sessions of anyone holding a delegation FROM that user
  (new `authz.invalidate_delegate_sessions_for_delegator`), so a delegate's privilege
  state is re-derived at once, not only on their next lookup.
- Test: manager delegates a cap; delegate holds it; end the manager's role; delegate no
  longer holds it (same query) and the delegate's session is revoked.

## P1-2: delegation page exposes every active team
app/web/delegations.py - the team picker query is unscoped (`Team.status=='active'`),
so any authenticated reader (incl. a narrowly-scoped team leader) sees every team's
name+id across organizations.

Fix: build the team list from the caller's own role scope via `visible_team_ids`
(the exact helper `list_visible_users` uses for the delegate picker): all active teams
if they have installation/org-wide appointment authority, else only their scoped teams,
else none. create_delegation still enforces the precise per-capability role check, so
the picker can only ever be narrower than what the service allows.
- Test: a team-scoped user's /delegations page shows only their team, not a team in
  another org.

## P1-3: exported operator text can become an Excel formula
app/campaigns/export.py - cells are appended raw. Disposition labels are operator-set
and display names can enter unsanitized via manual user creation; openpyxl stores a
value like "=1+1" as a FORMULA, which runs when the Team Captain opens this privileged
raw-PII export.

Fix: run every exported text cell through the existing `sanitize_text`
(app/imports/parser.py) immediately before writing - it prepends "'" to any value
starting with = + - @ tab or CR, the same neutralization used on import. (Phone numbers
begin with "+" and are validated E.164, so they too get the leading "'": safe, uniform.)
- Test: build a workbook from a campaign whose disposition label and agent name begin
  with each dangerous prefix; assert every such cell is a string (not a formula) and is
  neutralized.

## P2 follow-up (2026-09-07): org-scoped delegators saw no teams in the web form
The P1-2 fix scoped the team picker through `visible_team_ids`, but that helper only
recognized installation-scoped, org-scoped-with-null-id, and team-scoped assignments -
NOT an org-scoped role with a specific organization id. Yet the authz layer
(`_scope_assignment_covers_target`) resolves a team's organization and DOES let such a
role cover that org's teams, so `create_delegation` authorizes the manager while the
picker showed them nothing (fail-closed: a usability gap, not a leak).

Fix: `visible_team_ids` now also collects org-scoped ids (scope_id != None) and unions in
all active teams belonging to those organizations (one query, only when not already
sees_everyone). This mirrors the authz coverage exactly and also fixes the same latent
gap for `list_visible_users`. Teams outside those orgs stay hidden (fail-closed kept).
- Test: an org-scoped manager sees their own org's teams, not another org's, and can
  create a team-scoped delegation for one of their teams through the web form.

## Verify
ruff/mypy, full suite vs real Postgres (Docker; Redis 16379), docker build, BUILD-LOG,
commit/push/CI. No migration.
