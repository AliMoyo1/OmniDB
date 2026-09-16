# Phase 4C: Administrative User Directory and Offline Account Activation

**Product:** CipherContact  
**Repository:** OmniDB  
**Status:** Proposed for later implementation  
**Prepared:** 2026-09-16  
**Deployment model:** Local network, HTTPS, no application email service

## 1. Executive outcome

This phase will give authorized staff one clear place to manage user access without
weakening CipherContact's existing security controls.

The completed phase will provide:

1. Bulk account creation from CSV or Excel through the existing staged workforce
   import.
2. An administrative user directory with search, filters, pagination, status,
   effective roles, teams, activation state, and last login.
3. User-level actions for activation-code issuance, password reset, MFA reset,
   disable, and reactivate, shown only when the signed-in person has the required
   authority.
4. A practical offline activation process because CipherContact will not send email.
5. Targeted authentication improvements without arbitrary password-expiry rules,
   brittle complexity rules, or duplicate HSTS configuration.

This plan replaces the implementation approach proposed in
`PHASE-4B-ADMIN-AUTH-PLAN.md`. It does not replace the already implemented Phase 4B
workforce-import controls.

## 2. Required user outcomes

### 2.1 Bulk account creation

An authorized workforce administrator can download the users template, complete it,
upload a `.csv` or `.xlsx` file, review the validation result, approve it, and create
the valid accounts in one controlled operation.

The import must continue to use this sequence:

`quarantine -> validate -> parse -> classify -> preview -> approve -> atomic commit -> cleanup`

There must not be a second endpoint that opens an Excel file and directly creates
users row by row.

### 2.2 Administrative user directory

An authorized administrator can find every user they are allowed to manage and see:

- display name;
- login email;
- workforce ID;
- active or inactive status;
- activation/MFA state;
- effective role or roles and their scope;
- team membership;
- last successful login, or `Never`;
- date the identity was created.

The directory must support search, filters, deterministic sorting, and server-side
pagination so that it still works when the organization has thousands of users.

### 2.3 Administrative actions

The same area will provide capability-controlled access to:

- start a users bulk import;
- open a user's existing workforce profile;
- issue or replace an activation code;
- reset a password;
- reset MFA;
- disable or reactivate a user;
- review the audit trail for the selected user, where authorized.

Buttons are convenience only. Every request must independently verify the actor's
authority on the server.

## 3. Current baseline to preserve

CipherContact already has important parts of this phase:

| Existing capability | Current implementation | Required treatment |
|---|---|---|
| Individual account creation | `/workforce` creates an identity and shows one activation code | Preserve; improve navigation and state display |
| Bulk workforce import | `/workforce/imports` accepts CSV/XLSX and enforces validation, preview, approvals, scoped authority, atomic commit, audit, and reversal | Reuse; do not replace |
| User status and roles | `/workforce` shows active/inactive state and effective role names for up to 100 visible users | Add search, filters, pagination, teams, activation state, and last login |
| Last login | `users.last_login_at` is updated after successful login | Display it; no new column is required |
| Activation tokens | Random, single-use, 24-hour tokens stored as hashes; new issuance invalidates older unused tokens | Reuse for offline activation |
| Password reset | Super Admin API action clears the password, revokes sessions, and issues a new activation token | Add a safe browser workflow |
| MFA reset | Super Admin API action clears enrollment and revokes sessions | Add a safe browser workflow |
| Step-up authentication | Sensitive API actions require recent password and MFA confirmation | Reuse for all sensitive browser actions |
| HSTS | Set at the LAN and VPS Caddy TLS edges | Do not duplicate in application middleware |
| TOTP verification throttling | Present in both API and browser enrollment paths | Preserve and test |

## 4. Decisions

### 4.1 Reuse the staged import

Bulk account creation will remain an import type inside the existing workforce-import
pipeline. This preserves bounded uploads, file-type checks, macro/external-link
rejection, required-header validation, duplicate detection, authorization checks,
blocking-error handling, approval records, atomic commit, and reversal.

The administration page may contain a compact users-import form, but it must post to
the existing workforce-import route and service. It must not parse or create users
itself.

### 4.2 Use offline, on-demand activation

CipherContact will not send activation codes by email. For accounts created in bulk,
the preferred process is therefore:

1. Commit the identities without creating hundreds of short-lived activation codes.
2. Mark the users as `Activation not issued`.
3. When a person is ready to onboard, an authorized administrator confirms their
   identity and selects `Issue activation code`.
4. The administrator recently reauthenticates if necessary.
5. CipherContact displays that one person's code and expiry time once.
6. The administrator hands it to that person through the approved private process.
7. The person opens `/activate`, enters the code, creates their password, signs in,
   and enrolls MFA.

If the code expires or is lost, the administrator issues a replacement. Replacement
must invalidate every prior unused code for that user.

### 4.3 Do not create a bulk secrets file

CipherContact must not provide a CSV, Excel file, or print-all page containing every
user's activation code. That would create another sensitive account database that
could be copied, retained, or sent to the wrong recipient.

The UI may offer `Copy code` and an optional one-person print view, but only on the
one-time result page for a single selected user. It must not persist the plaintext
code after that response.

### 4.4 Keep technical and business authority separate

The page will be capability-driven rather than tied to a single role name.

| User type | Directory visibility | Import capability | Credential reset capability |
|---|---|---|---|
| Super Administrator | All identities in the installation | Only if separately granted appropriate workforce authority | Password, MFA, and activation recovery for other users |
| Manager | Organization-wide workforce view in the current single-organization deployment | Yes, within existing appointment authority | No, unless separately granted `RESET_USER_AUTH` |
| Team Leader | Users within authorized team/organization scope | Yes, only for rows within their authority | No |
| Team Captain | Agents and users within authorized scope | Yes, only for permitted user rows | No |
| Agent or Viewer | No administrative directory | No | No |

Holding Super Administrator authority must not silently grant campaign or workforce
business powers. Conversely, holding Manager authority must not grant password or MFA
reset powers.

### 4.5 Use the existing server-rendered application

Use FastAPI, Jinja templates, the existing CSS/design system, standard HTML forms,
and the current CSRF/session controls. Do not add HTMX, Alpine, a new SPA, or a second
admin layout solely for this phase.

## 5. User experience

### 5.1 User administration page

Add `/admin/users` as the user-access administration route. It may be opened by a
person who has either credential-administration authority or an applicable workforce
appointment capability. Its content changes according to capability and scope.

The page contains:

1. Summary cards for visible users: total, active, inactive, awaiting activation,
   MFA setup required, and dormant/never logged in.
2. Search across display name, login email, and workforce ID.
3. Filters for status, activation state, effective role, and team.
4. A paginated table with 50 rows by default and a hard maximum of 100 rows per page.
5. A `Start bulk account import` panel for users with import authority. This panel
   downloads the existing users template and posts to the existing import pipeline.
6. Row actions shown only when the actor has the corresponding capability.

Use stable sorting such as `(display_name, user_id)` or `(created_at, user_id)` so a
person is not duplicated or skipped while paging.

### 5.2 Account-state labels

The UI derives a plain-language state from existing account and activation records:

| State | Meaning |
|---|---|
| Disabled | The account cannot sign in; this state overrides the labels below |
| Activation not issued | No password exists and there is no valid unused activation token |
| Activation code active | No password exists and a valid unused activation token exists |
| Activation code expired | No password exists and the latest unused code has expired |
| MFA setup required | A password exists but TOTP enrollment is incomplete |
| Ready | The account is active, has a password, and has completed MFA enrollment |

`Last login` is separate from activation state. A ready user who has never completed a
successful login displays `Never`.

The state query must be set-based. Do not issue one database query per row.

### 5.3 User detail and actions

Selecting a row opens a user-administration detail page or the existing workforce
profile with an additional security panel. It displays only metadata, never password
hashes or TOTP secrets.

Sensitive actions require:

- an active authenticated session;
- completed MFA;
- a valid CSRF token;
- the required capability and scope;
- recent reauthentication;
- a confirmation screen naming the target user;
- self-reset prevention where already required;
- an audit event recording actor, target, outcome, and reason, but no secret.

### 5.4 One-time code result page

After successful issuance, render a page directly rather than redirecting with the
code in the URL. Show:

- the intended user's display name and workforce ID;
- the activation code;
- the exact expiry date and time in the configured timezone;
- a warning that it is single-use and will not be shown again;
- `Copy code` and `Done` controls;
- no list of other users or other codes.

The page inherits `Cache-Control: no-store`. The code must not appear in URLs,
application logs, audit metadata, analytics, Sentry events, or database plaintext.

## 6. Service and data design

### 6.1 Shared directory query

Create a shared query/service function that accepts:

- actor ID;
- search text;
- status filter;
- activation-state filter;
- role filter;
- team filter;
- page size and cursor/page offset;
- sort order.

It returns the page rows and total visible count. It must begin with the actor's
authorized user scope and only narrow from there. A filter must never widen scope.

Super Administrator visibility is based on `RESET_USER_AUTH` at installation scope.
Business-role visibility reuses `visible_team_ids`, appointment capabilities, and
`can_manage_user` semantics from the workforce service.

Where a record is outside scope, detail/action routes should return the same response
as a nonexistent record so they do not disclose that the user exists.

### 6.2 Activation state

Reuse `activation_tokens`. It already records `user_id`, hashed token, purpose,
expiry, use time, issuing administrator, and creation time.

Determine current activation status with a bounded subquery for the latest relevant
token per listed user. Do not load or expose `token_hash` to templates.

No `last_password_changed_at` field is required for this phase because password
expiry is not being implemented.

### 6.3 Separate identity creation from code issuance

Refactor the workforce creation service so identity creation can occur with or
without immediate activation issuance. Preserve current behavior for manual single
user creation while allowing the bulk-import commit path to request identity-only
creation.

A low-risk compatible shape is:

```python
def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    workforce_id: str | None,
    created_by: uuid.UUID,
    issue_activation: bool = True,
) -> tuple[User, str | None]:
    ...
```

Requirements:

- Keep `issue_activation=True` as the default so existing single-user callers do not
  silently change.
- The bulk `users/create` commit path uses `issue_activation=False` only after the
  deferred-activation feature is enabled.
- Audit identity creation separately from activation-code issuance.
- Update type annotations and all callers explicitly.

### 6.4 Activation issuance service

Add one shared service operation for initial activation and password-reset issuance.
It must:

1. lock the target user row;
2. verify the target is active and eligible for the requested operation;
3. verify actor authority in the caller before the service is invoked;
4. invalidate older unused activation credentials through the existing token issuer;
5. create a new single-use token with the existing 24-hour expiry;
6. return plaintext only to the current response;
7. audit the issuance without the token;
8. leave no partial changes if the transaction fails.

Concurrent issuance requests must result in only the newest token remaining usable.

### 6.5 Feature-controlled cutover

Add an audited flag named `deferred_bulk_activation_enabled`, initially disabled.
This requires a small data migration to seed the flag but no user-table schema
change.

When disabled, the current bulk-import token behavior remains available during the
first deployment. When enabled, bulk user imports create identities without codes
and direct the administrator to the pending-activation directory.

The server must enforce the flag in the workforce-import service, not only in the
template.

## 7. Routes and components

The exact module split can follow existing conventions. The expected change surface
is:

| Area | Expected change |
|---|---|
| `app/workforce/service.py` | Add paginated scoped directory query and optional deferred activation during identity creation |
| `app/workforce_imports/service.py` | Stop issuing codes during bulk user commit when the feature flag is active |
| `app/auth/service.py` | Reuse/extend safe activation issuance and expose no secret state |
| `app/auth/ratelimit.py` | Add an explicit activation-attempt limiter rather than overloading login-rate keys |
| `app/auth/router.py` | Apply shared password policy and activation throttling to API activation |
| `app/web/auth_pages.py` | Apply the same shared password policy and activation throttling to browser activation |
| `app/api/admin.py` | Reuse shared reset/issuance services; retain JSON endpoints for compatibility |
| `app/web/admin_users.py` | New server-rendered directory, detail, issue-code, reset-password, and reset-MFA routes |
| `app/web/templates.py` | Add capability-derived user-administration navigation flag |
| `app/main.py` | Register the new web router |
| `app/templates/admin_users.html` | Directory, filters, pagination, and import entry point |
| `app/templates/admin_user_detail.html` | Account state and authorized actions |
| `app/templates/activation_code_issued.html` | One-user, one-time code result |
| `app/templates/workforce_import_committed.html` | Show account results and link to pending activation instead of a bulk token table when deferred mode is active |
| `app/flags/service.py` | Register deferred activation flag |
| `migrations/versions/` | Seed the new flag, default disabled |
| `tests/` | Add authorization, integration, concurrency, browser-flow, and regression coverage |

Suggested browser routes:

| Method | Route | Purpose |
|---|---|---|
| GET | `/admin/users` | Scoped, filtered, paginated directory |
| GET | `/admin/users/{user_id}` | Scoped account detail |
| POST | `/admin/users/{user_id}/activation-code` | Issue or replace an initial activation code |
| POST | `/admin/users/{user_id}/reset-password` | Clear password, revoke sessions, and issue a replacement activation code |
| POST | `/admin/users/{user_id}/reset-mfa` | Clear MFA enrollment and revoke sessions |

No new public JSON bulk-import endpoint is required.

## 8. Targeted authentication hardening

### 8.1 Password policy

Keep the existing minimum length for compatibility unless the product owner approves
a broader policy change. Add an offline blocklist of common and context-specific
passwords and permit passphrases.

Do not require an uppercase letter, lowercase letter, number, and symbol. Do not add
routine 90-day password expiry. Current NIST and OWASP guidance favors adequate
length, compromised/common-password blocking, rate limiting, and MFA over predictable
composition and periodic-rotation rules.

The validation function must be shared by the API and browser activation endpoints so
one route cannot accept a password the other rejects.

### 8.2 Activation throttling

Add a dedicated activation-attempt limiter with explicit thresholds and tests. It
must cover both API and browser activation, use privacy-preserving hashed keys, audit
rate-limited outcomes without storing the submitted token, and fail closed in
production if Redis is unavailable.

Do not implement a permanent account lockout. A hostile person who knows an email or
workforce ID must not be able to keep another user locked out indefinitely.

### 8.3 Explicitly excluded changes

This phase will not add:

- application email or SMS delivery;
- self-service registration;
- default or shared passwords;
- bulk activation-token exports;
- security questions;
- password hints;
- mandatory periodic password changes;
- arbitrary password composition rules;
- a duplicate application-level HSTS header;
- OAuth/OIDC or Active Directory integration;
- a new frontend framework.

## 9. Authorization and security invariants

The implementation is not complete unless all of these remain true:

1. Agents and Viewers cannot enumerate users or invoke administrative actions.
2. A Team Leader or Team Captain cannot discover or act on a person outside their
   effective scope.
3. A Manager cannot reset passwords or MFA merely because they can manage workforce
   records.
4. A Super Administrator cannot gain campaign/workforce business powers merely
   because they can reset credentials.
5. UI visibility never substitutes for a server-side permission check.
6. Sensitive actions require recent reauthentication and CSRF validation.
7. No one can reset their own password or MFA through the administrative route.
8. Resetting password or MFA revokes the target's active sessions.
9. Disabling a user continues to revoke sessions and active leases.
10. Activation codes are random, short-lived, single-use, hashed at rest, and
    displayed only once.
11. Issuing a new code invalidates all older unused codes for that user.
12. Tokens never enter filenames, query strings, logs, audit metadata, monitoring,
    analytics, or downloadable bulk files.
13. Invalid import rows prevent approval and commit; imports do not partially create
    the valid subset while silently skipping blocking errors.
14. Upload limits, file-signature checks, XLSX expansion limits, and macro/external
    link rejection remain active.

## 10. Implementation phases

### Phase A: Read-only directory

- Add the shared scoped, filtered, paginated query.
- Add `/admin/users` and navigation visibility.
- Display status, roles, teams, activation state, creation time, and last login.
- Add search and filters.
- Add authorization, pagination, and query-count tests.

This phase is read-only and can ship without changing activation behavior.

### Phase B: Safe browser administration actions

- Add user detail/security panel.
- Add recent-reauthentication handling for server-rendered forms.
- Add one-user activation-code issuance.
- Add password- and MFA-reset actions by reusing shared services.
- Add confirmation, result, failure, audit, concurrency, and session-revocation tests.

### Phase C: Connect the existing users import

- Put a users-import card on the administration page for authorized workforce roles.
- Post to the existing workforce-import pipeline.
- Add the deferred-bulk-activation flag, disabled by default.
- Teach bulk user commit to create identities without codes when enabled.
- Replace the bulk token result table with counts and a link filtered to `Activation
  not issued`.
- Verify the existing import approval, reversal, and audit behavior is unchanged.

### Phase D: Targeted authentication hardening

- Add the shared common-password blocklist check.
- Add dedicated activation throttling to both activation routes.
- Verify current login, TOTP, reset, session, and Caddy-header behavior remains clean.

### Phase E: Documentation and operational handoff

- Update the Super Administrator, Manager, Team Leader, and Team Captain manuals.
- Add the offline identity-verification and code-handoff procedure to the operations
  documentation.
- Document expired/lost code replacement and account-recovery escalation.
- Train administrators never to place activation codes in a shared spreadsheet.

## 11. Test plan

### 11.1 Unit tests

- Activation-state derivation for every state in section 5.2.
- Password blocklist matching, including case normalization and passphrases.
- Activation limiter thresholds, expiry, privacy-preserving keys, and production
  fail-closed behavior.
- Stable pagination and filter construction.

### 11.2 Authorization tests

- Super Administrator sees all identities but does not gain business actions.
- Manager sees organization users but no credential-reset actions.
- Team Leader and Team Captain see only authorized scope.
- Agent and Viewer receive denial.
- Forged direct requests fail even when the corresponding button was hidden.
- Inaccessible and nonexistent target IDs produce the same response.
- Self-reset remains blocked.

### 11.3 Integration tests

- Search by display name, email, and workforce ID.
- Filter by active/inactive, role, team, activation state, never logged in, and dormant.
- Page through a multi-page fixture without duplicates or missing rows.
- Show a populated last-login timestamp after successful login and `Never` before it.
- Import valid CSV and XLSX user files through the existing pipeline.
- Reject missing/unknown headers, header-only files, invalid rows, duplicates, macros,
  external links, oversized uploads, and over-expanded XLSX files.
- Prevent commit until required approvals are present.
- With deferred mode disabled, preserve current behavior.
- With deferred mode enabled, create identities without issuing codes.
- Issue one code on demand and activate successfully.
- Prove the code is single-use and expires after 24 hours.
- Prove reissue invalidates the previous unused code.
- Prove two concurrent issuance attempts leave only the newest token usable.
- Password reset revokes all sessions and returns only one replacement code.
- MFA reset revokes all sessions and requires re-enrollment.
- Sensitive browser actions reject stale reauthentication and invalid CSRF.
- No response, audit record, application log, or persisted job result contains a
  plaintext activation token after the one-time response.

### 11.4 Browser smoke test

Use synthetic users only:

1. Sign in as a Super Administrator and confirm the full directory and reset controls.
2. Sign in as a Manager and confirm organization users and import entry point, with no
   password/MFA reset controls.
3. Sign in as a Team Leader and Team Captain and confirm scope restrictions.
4. Import three synthetic users from XLSX.
5. Approve and commit the import.
6. Issue one user's activation code at handoff time.
7. Activate that user, create a password, enroll MFA, and sign in.
8. Confirm their last login and state change appear in the directory.
9. Reset that user's password and verify the old session no longer works.
10. Confirm the old activation code and any superseded code fail.

### 11.5 Regression and static checks

At minimum run:

```text
ruff check .
mypy app
pytest tests/unit tests/authorization
pytest tests/integration/test_auth_flow.py
pytest tests/integration/test_workforce_flow.py
pytest tests/integration/test_web_workforce_flow.py
pytest tests/integration/test_workforce_imports_flow.py
pytest tests/integration/test_admin_user_management_flow.py
pytest
```

Database-backed integration tests must run in the Compose/Linux environment where
the `postgres` and `redis` service names resolve. A Windows-only static pass is not a
substitute for those tests.

## 12. Performance and capacity checks

Before enabling the feature for production:

- load at least 10,000 synthetic users with representative roles and memberships;
- verify a 50-row directory page renders in under two seconds on the target server;
- verify query count remains bounded as page size grows;
- verify role/team/activation filters do not perform per-row database queries;
- verify exporting or downloading the complete directory is not introduced by this
  phase.

If search performance misses the target, add measured indexes through a reviewed
migration. Do not add speculative database extensions or indexes that break the
supported test database environments.

## 13. Rollout

1. Take a current database backup and validate that it can be listed/restored.
2. Deploy the code and flag-seeding migration with
   `deferred_bulk_activation_enabled=false`.
3. Run migrations and the focused production-like smoke tests.
4. Verify the read-only directory and actions with synthetic accounts.
5. Confirm administrators understand the offline handoff procedure.
6. Enable `deferred_bulk_activation_enabled` with an audited reason.
7. Run one small synthetic XLSX import and complete one activation end to end.
8. Monitor authentication failures, rate-limit events, and Sentry errors without
   capturing credentials.
9. Update the manuals only after the deployed UI and wording are final.

## 14. Rollback

- If the directory or browser actions fail, disable their navigation/flag and retain
  the existing workforce pages and JSON reset endpoints.
- If deferred bulk activation fails, disable
  `deferred_bulk_activation_enabled`. This restores the prior immediate-token behavior
  for subsequent imports without deleting user records.
- Do not delete imported identities to roll back. Use the existing audited reversal or
  explicit disable workflow where appropriate.
- Revert the application release through the established release mechanism only after
  confirming the database migration is backward compatible.
- The flag-seeding migration is additive. Its downgrade removes only that flag row and
  must not alter users, roles, activation tokens, or audit records.

## 15. Failure points and mitigations

| Failure | Mitigation |
|---|---|
| Administrator leaves the result page before recording a code | Permit a new one-user issuance; invalidate the abandoned code |
| A code is given to the wrong person | Require identity verification, make codes short-lived and single-use, and provide immediate reissue/invalidation |
| Bulk users are created but nobody can activate | Directory filter shows `Activation not issued`; issue codes individually when users are present |
| User listing leaks another team's identities | Build scope into the base query and test direct URL/request attacks |
| Multiple simultaneous issuances produce two valid codes | Lock the user and invalidate all prior unused codes in the same transaction |
| Directory becomes slow | Paginate, use set-based role/team/state queries, measure with 10,000 synthetic users, add indexes only from evidence |
| Reset action is clicked accidentally | Require named confirmation, recent reauthentication, and an audited POST |
| A shared activation-code spreadsheet is created manually | Do not provide a bulk export and train administrators on one-user handoff |
| Redis is unavailable during activation | Fail closed in production and show a non-sensitive retry message |
| Old and new behavior conflict during deployment | Keep deferred issuance behind an audited flag until the new directory/actions are verified |

## 16. Definition of done

This phase is complete only when all of the following are true:

- Bulk CSV/XLSX account creation uses the existing staged import and passes its full
  regression suite.
- An authorized Super Administrator can view every account, including status, roles,
  teams, activation state, creation time, and last login.
- Managers, Team Leaders, and Team Captains see only users in their authorized scope.
- Search, filters, and pagination work with at least 10,000 synthetic users.
- Import, activation issuance, password reset, MFA reset, disable, and reactivate
  actions appear only to authorized users and are independently enforced server-side.
- Bulk-created users can be committed without issuing activation codes.
- An authorized administrator can issue one code when the intended person is ready.
- The code is shown once, expires in 24 hours, is single-use, and replacement makes
  older codes unusable.
- No email, bulk token download, default password, shared password, or secret-bearing
  URL is introduced.
- Password and MFA reset revoke the target's active sessions.
- Audit logs identify who performed each sensitive action without recording secrets.
- `ruff check .`, `mypy app`, focused suites, the complete database-backed test suite,
  and the production-like browser smoke test all pass.
- Updated role manuals match the deployed behavior.

## 17. References

- `docs/decisions/adr/ADR-004A-authentication.md`
- `docs/decisions/adr/ADR-005C-workforce-identity.md`
- `docs/decisions/adr/ADR-017-notifications.md`
- `docs/architecture/CipherContact - Detailed Implementation Plan v0.3.md`
- NIST SP 800-63B: <https://pages.nist.gov/800-63-4/sp800-63b.html>
- OWASP Authentication Cheat Sheet:
  <https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html>
- OWASP Authorization Cheat Sheet:
  <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>
- OWASP File Upload Cheat Sheet:
  <https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html>

