# CipherContact - Detailed Implementation Plan v0.3

**Status:** Proposed implementation plan  
**Date:** 2026-08-20  
**Primary use:** Desktop-first internal web application for work laptops  
**Source:** CallAgent DB - System Specification v0.1 (original brief; product renamed to CipherContact on 2026-08-20)  
**Source file:** C:\Users\isadmin\Downloads\CallAgent DB - System Spec.md  
**Source SHA-256:** 0B52EF6A90F056D4A3DCA273347436F0DC6CCE86D12C8A217D95E1FF0083B7D3  
**Revision note:** v0.3 (2026-08-20) applied review corrections (session-store authority, keyed phone fingerprint, callback-list masking, notification and approval channel), split Phase 4, re-sliced the first pilot, and set the build repository to https://github.com/AliMoyo1/OmniDB. v0.2 expanded workforce hierarchy, campaign mobility, target exemptions, and bulk-user management on 2026-08-20.  
**Implementation authorization:** This document is a plan. It does not authorize deployment or production data processing.

## 1. Executive outcome

CipherContact should be delivered as a secure internal campaign and call-work management system. It should preserve the strongest ideas from the original specification:

- One active contact at a time for each agent.
- Campaign imports with validation and preview.
- Hierarchical Manager, Team Leader, Team Captain, and Agent administration.
- Call dispositions, notes, callbacks, and progress tracking.
- Global do-not-call suppression.
- Aggregate reporting without general raw-number exports.
- Private access from office work laptops and approved remote management devices.

The original specification is a useful product brief, but it is not yet an implementation-ready engineering specification. This plan inserts a foundation phase before feature development and resolves the main security, privacy, data-integrity, networking, and operational gaps.

The production MVP must not include AI lead scoring, sentiment analysis, or automated agent routing. Those functions require a separate post-pilot approval decision.

## 2. Project boundaries

CipherContact is a new project and must have its own repository, documentation, secrets, environments, backups, release process, and deployment host.

### 2.1 Repository rule

- Do not place CipherContact source, plans, assets, migrations, or deployment files in the ThemisIQ repository.
- Obtain approval for the permanent project path before scaffolding the application.
- Build repository (provided): https://github.com/AliMoyo1/OmniDB. The application in this plan is CipherContact; the repository is named OmniDB. Confirm whether to rename the product to OmniDB or keep CipherContact as the product name inside the OmniDB repository.
- Recommended local working path, subject to approval: C:\Projects\OmniDB.
- Keep production secrets, backup keys, and imported data outside the source repository.

### 2.2 Environment separation

Use distinct configurations for:

- Local development.
- Automated test.
- Staging or pilot.
- Production.

Production data must never be copied into development. Synthetic or irreversibly de-identified fixtures must be used for tests and demonstrations.

## 3. Objectives and non-goals

### 3.1 Objectives

1. Give agents a fast, keyboard-friendly desktop workflow that reveals only the contact currently being worked.
2. Prevent cross-team and cross-role access through server-enforced authorization.
3. Ensure no contact can be allocated to two agents at the same time.
4. Ensure explicit do-not-call requests become effective immediately across all active and future work.
5. Make campaign imports staged, reviewable, idempotent, and transaction-safe.
6. Preserve an append-only audit history without writing raw personal data into operational logs.
7. Operate privately without public application ports.
8. Recover from host, disk, container, or database failure within an agreed recovery window.
9. Scale to at least 50 concurrent agents on the approved production hardware.
10. Provide evidence-based release gates rather than relying on feature completion alone.
11. Allow agents to move between campaigns without changing historical results.
12. Support approved full and partial target exemptions without treating exempt agents as failed performers.
13. Support bulk onboarding, updates, scoped role assignments, campaign assignments, and explicit deactivation through a staged import.
14. Preserve the history of reporting lines, acting roles, campaign assignments, targets, exemptions, and approvals.

### 3.2 Non-goals for the production MVP

- A mobile-first application.
- A native mobile application.
- A WebRTC or integrated telephone dialer.
- Public internet access.
- Offline browser storage of contact data.
- General raw phone-number export.
- AI lead scoring.
- Sentiment analysis.
- Automated performance-management decisions.
- Multi-region or active-active deployment.
- Unreviewed support for arbitrary spreadsheet formats.

## 4. Decisions required before implementation

The following decisions block parts of the build. Record each approved answer in an Architecture Decision Record.

| ID | Decision | Recommended starting position | Owner | Blocks |
|---|---|---|---|---|
| D-01 | Permanent repository and project path | Provided: dedicated OmniDB repository (https://github.com/AliMoyo1/OmniDB), outside ThemisIQ. Confirm product-name reconciliation (CipherContact vs OmniDB) | Product owner and technical lead | Phase 1 |
| D-02 | Operating jurisdiction and direct-marketing rules | Confirm Zimbabwe and any client-specific obligations with qualified counsel | Product owner and privacy lead | Phase 0 exit |
| D-03 | Production host operating system | Supported Linux LTS on an x86 mini PC is preferred; document Windows requirements if Windows hosting is mandatory | IT and technical lead | Deployment design |
| D-04 | Client access topology | LAN HTTPS for agents and Tailscale HTTPS for approved remote privileged users | IT and security | Phase 1 |
| D-05 | Server certificate trust | Managed private CA deployed to work laptops, or another approved internal certificate solution | IT | Pilot |
| D-06 | Identity source | Corporate OIDC if available; otherwise local accounts with privileged-role MFA | Security and product owner | Auth build |
| D-07 | Team structure | One organization with explicit teams and scoped memberships unless multi-organization support is confirmed | Product owner | Data model |
| D-08 | Explicit DNC authority | Immediate global suppression, with privileged audited correction only | Legal, privacy, and product owner | Import and agent workflows |
| D-09 | Data retention | Define periods by raw upload, campaign record, notes, audit, backup, and suppression evidence | Privacy and legal | Production |
| D-10 | Backup destination and key custody | Encrypted off-device destination with separate recovery-key custody | IT and security | Pilot |
| D-11 | Supported laptops and browsers | Managed Windows laptops using current Edge or Chrome | IT and UX | UX acceptance |
| D-12 | Expected volume | Confirm contacts per campaign, campaigns per year, concurrent agents, and audit retention | Product owner | Capacity test |
| D-13 | Campaign data provenance | Require source, acquisition date, purpose, lawful basis or consent reference, and vendor details where applicable | Privacy and campaign operations | Import commit |
| D-14 | On-call and support ownership | Named owner for production incidents, backups, patching, and access revocation | Product owner and IT | Go-live |
| D-15 | Business hierarchy | Manager, Team Leader, Team Captain, and Agent, with Super Admin separate and Viewer optional | Product owner | Role model |
| D-16 | Role scope | Roles are effective-dated assignments at installation, organization, team, or campaign scope, not one mutable role on users | Product owner and security | Data model |
| D-17 | Campaign concurrency | Decide whether an agent may work one campaign per shift or several campaigns with explicit priorities and allocation weights | Operations | Queue design |
| D-18 | Campaign transfer cutoff | Define treatment of active leases, callbacks, targets, and effective time when an agent changes campaigns | Operations | Agent workflow |
| D-19 | Target policy | Define metric, period, base value, proration, rounding, calendars, ramp-up, and campaign attribution | Manager and operations | Reporting |
| D-20 | Exemption approval | Recommended: Agent or Team Captain requests, Team Captain endorses where needed, Team Leader approves, Manager handles exceptions and closed periods | Product owner and HR or operations | Target workflow |
| D-21 | Bulk-user authority | Manager and scoped Team Leader may stage imports; role elevation, bulk deactivation, and Manager creation require higher approval | Security and product owner | Workforce import |
| D-22 | Workforce identity key | Use an immutable external employee or workforce ID where available; never match users by display name | IT or HR operations | Bulk import |
| D-23 | Notification and approval channel | If reliable SMTP is not guaranteed on the office network, make an in-application notification and approval inbox the primary channel, with email as an optional secondary | IT and product owner | Provisioning and approvals |

No production implementation should begin until D-01 through D-08 have a recorded owner and resolution. D-09 through D-14, plus D-23, may be finalized during the foundation phase but must be complete before the pilot.

## 5. Non-negotiable system invariants

These are business and security rules, not optional implementation preferences.

1. Every authenticated request uses HTTPS.
2. The application, PostgreSQL, and Redis are not directly published to the public internet.
3. An agent receives no list endpoint containing multiple raw phone numbers. This includes the agent's own callback queue, which returns masked references only; a raw number is revealed only by leasing one work item.
4. A user cannot access data outside their authorized organization and team scope.
5. Every work item has at most one valid active lease.
6. Completing the same work item twice with the same idempotency key has one business effect.
7. An explicit DNC request suppresses the contact before another work item can be issued.
8. Import preview results are revalidated during the commit transaction.
9. A custom disposition label cannot silently change protected behavior such as global suppression.
10. Raw phone numbers, passwords, session tokens, and free-text notes are excluded from operational logs.
11. Production database migrations are backed up, reviewed, forward-compatible where possible, and reversible through a documented recovery procedure.
12. A backup is not considered successful until restoration has been tested.
13. AI features remain disabled unless a later approval gate is completed.
14. Historical role, reporting-line, campaign, target, exemption, and approval records are never rewritten to represent a current state.
15. Campaign eligibility comes from an active effective-dated campaign assignment, not a campaign ID stored on users.
16. An agent transfer stops new work at the approved cutoff while preserving completed attempts under the original campaign.
17. A target exemption never deletes or changes actual work completed.
18. An exempt period is reported as exempt or adjusted, not as zero performance.
19. A user may not approve their own exemption, role elevation, or privileged bulk action.
20. A bulk file never contains passwords and never deactivates a user merely because the user is absent from the file.
21. PostgreSQL is authoritative for session validity and revocation. Any Redis session data is a non-authoritative cache, and a revocation is not complete until it is effective in PostgreSQL.
22. Phone fingerprints are keyed HMACs, not plain hashes, so that a database compromise cannot recover numbers by enumeration.

## 6. Roles and authorization

### 6.1 Roles

| Role | Scope | Primary responsibilities |
|---|---|---|
| Super Admin | Entire installation | Technical configuration, initial Manager administration, security settings, protected audit access, retention execution, and incident support |
| Manager | Authorized organization, portfolio, or business unit | Campaign ownership, Team Leader appointment, target-policy approval, workforce oversight, exemption escalation, cross-team analytics, and privileged operational approvals |
| Team Leader | Assigned teams, Team Captains, and campaigns | Team Captain supervision, campaign staffing, campaign transfers, target assignment, exemption approval within policy, bulk-user staging, and multi-team reporting |
| Team Captain | Assigned direct teams and campaigns | Day-to-day agent supervision, queue and callback oversight, campaign allocation requests, exemption requests or endorsements, work review, and direct-team reporting |
| Agent | Active team and campaign assignments plus own work | Lease one contact, record an outcome, create a callback, add notes, request an exemption, and view approved personal metrics and target status |
| Viewer | Explicit assigned report scope | Read aggregate reports only, with no raw contact, note, import-row, or DNC access |
| Service Account | Specific background task | Run imports, scheduled jobs, backups, or monitoring with narrowly scoped credentials |

Super Admin is a technical role and does not automatically become a business Manager. Viewer remains optional. Manager, Team Leader, Team Captain, and Agent are business roles.

### 6.2 Recommended hierarchy and scope

~~~mermaid
flowchart TD
    S["Super Admin<br/>Technical installation scope"]
    M["Manager<br/>Organization or portfolio scope"]
    L["Team Leader<br/>One or more teams and campaigns"]
    C["Team Captain<br/>Direct operational team"]
    A["Agent<br/>Time-bound campaign assignment"]
    V["Viewer<br/>Explicit report scope"]
    S -. "appoints or supports" .-> M
    M --> L
    L --> C
    C --> A
    M -. "grants report scope" .-> V
~~~

The diagram is the normal reporting model, not an authorization shortcut. Permissions are granted through explicit scoped assignments.

Key rules:

- A user record represents identity, not current rank, team, campaign, or target.
- A user may hold more than one non-conflicting role assignment when acting, covering leave, or working in separate scopes.
- A higher business role does not automatically receive raw contact access. Raw contact access still requires an explicit operational need.
- A Manager may work as an Agent only when an explicit Agent role and campaign assignment exist.
- Acting or delegated roles require start time, end time, reason, appointing authority, and audit.
- Removing a role ends the assignment. It does not delete history.
- Reporting lines and campaign assignments are independently effective-dated.

### 6.3 Capability matrix

The final matrix must be approved in Phase 0. Recommended defaults:

| Capability | Super Admin | Manager | Team Leader | Team Captain | Agent | Viewer |
|---|---|---|---|---|---|---|
| Technical system configuration | Yes | No | No | No | No | No |
| Create or appoint Manager | Yes | No | No | No | No | No |
| Appoint Team Leader | Support only | Yes, in scope | No | No | No | No |
| Appoint Team Captain | Support only | Yes | Yes, in scope | No | No | No |
| Create or invite Agent | Support only | Yes | Yes | Yes, direct team if enabled | No | No |
| Create campaign | Support only | Yes | Draft in scope | No by default | No | No |
| Launch, pause, or archive campaign | Emergency support | Yes | Pause or request launch in scope | Request or operational pause | No | No |
| Assign Agent to campaign | Emergency support | Yes | Yes | Recommend or assign within delegated scope | No | No |
| Move Agent between campaigns | Emergency support | Yes | Yes | Request or delegated move | No | No |
| Define target policy | No by default | Yes | Propose | No | No | No |
| Assign target from approved policy | No by default | Yes | Yes | View and propose | No | No |
| Request target exemption | No | Yes for subordinate | Yes for subordinate | Yes for direct Agent | Yes for self | No |
| Approve target exemption | Emergency support only | Yes | Yes within policy | No self or final approval by default | No | No |
| Reopen closed target period | No by default | Yes with audit | Request | Request | No | No |
| Bulk user or assignment upload | Support only | Yes | Stage or commit in scope | Stage direct-team Agent rows if enabled | No | No |
| Bulk deactivation or role elevation | Emergency support | Approve in scope | Request | No | No | No |
| View analytics | System health | Authorized portfolio | Assigned teams | Direct team | Own | Explicit aggregate scope |
| Correct DNC suppression | Emergency support | Privileged approval | Request | Request | No | No |

### 6.4 Authorization model

- Default deny every route and service method.
- Resolve organization, team, role, and object scope on the server.
- Never trust a team ID, campaign ID, agent ID, role, or status supplied by the browser.
- Put object-level authorization in a shared service layer, not only in route decorators or templates.
- Managers act only within an active authorized portfolio or organization assignment.
- Team Leaders act only within active team, campaign, and supervisory assignments.
- Team Captains act only within active direct-team and campaign assignments.
- Agents may act only on a valid work-item lease owned by their active session identity.
- Viewers receive pre-approved aggregate report scopes.
- Disabling a user immediately revokes active sessions and leases.
- Role changes rotate or invalidate sessions.
- Sensitive actions require reauthentication or step-up authentication where practical.
- Approval services verify that requester and approver are not the same person.
- Expired role, reporting, campaign, target, and delegation assignments grant no capability.
- Role hierarchy is not implemented as unbounded inheritance. Each sensitive capability is explicit.

### 6.5 Authorization test matrix

For every object endpoint, test:

- No session.
- Expired session.
- Disabled user.
- Correct role and correct team.
- Correct role and wrong team.
- Lower role attempting a privileged action.
- Guessed UUID belonging to another team.
- Object moved to another team after the session was created.
- User membership removed while a request is in flight.
- Service account using an endpoint outside its stated purpose.
- Expired acting-role assignment.
- Manager attempting an action outside the assigned portfolio.
- Team Leader attempting an action for another Team Leader’s team.
- Team Captain attempting to approve their own request.
- Agent attempting to work a campaign before assignment start or after assignment end.
- User holding different roles in two scopes and attempting to apply the stronger role to the weaker scope.
- Bulk import attempting privilege elevation outside the uploader’s authority.
- Closed target period being changed without authorized reopening.

## 7. Desktop-first UX plan

### 7.1 Supported desktop conditions

Design and test first for:

- 1366 x 768 at 100%, 125%, and 150% Windows scaling.
- 1920 x 1080 at 100%, 125%, and 150% Windows scaling.
- Managed Edge and Chrome.
- Keyboard and mouse use.
- Intermittent office-network latency without offline persistence.

The application should remain usable in smaller browser windows, but phone layouts must not determine the primary information architecture.

### 7.2 Agent workspace

Use a two-column desktop layout:

- Left column: campaign context, progress toward the connected target, current contact name, phone number, approved metadata, watermark, and copy action.
- Right column: disposition controls, conditional fields, callback scheduling, notes, validation messages, and submit action.
- Header: agent identity, team, session status, and logout.
- Footer or status strip: today’s approved metrics, keyboard-help toggle, connection state, and application version.

The primary outcome action must remain visible at 1366 x 768 without requiring the agent to scroll.

### 7.3 Keyboard behavior

- Do not override Ctrl+C globally.
- Do not use Ctrl+N because browsers use it for a new window.
- Do not use Escape to silently skip work.
- Do not submit a notes field with plain Enter.
- Suggested starting pattern: Alt plus a disposition number, Alt+C for the explicit copy action, Ctrl+Enter for submit, and an explicit skip button with a required reason.
- Shortcuts must be inactive while incompatible fields have focus.
- Users must be able to view, disable, or remap single-character shortcuts.
- Every action must also be possible with standard keyboard navigation and pointer input.

### 7.4 Management workspaces

Use role-specific data-dense desktop dashboards:

- Persistent team and campaign filters.
- Campaign status cards linked to detail pages.
- Wide agent-performance and callback tables.
- Sort, filter, pagination, and saved views.
- Bulk actions with selection counts and confirmation summaries.
- Import jobs with progress, warnings, and decision queues.
- DNC review limited to correction and administrative exceptions, not delayed enforcement of explicit opt-out requests.
- Manager view: portfolios, Team Leaders, campaign capacity, target policies, exemption escalations, cross-team performance, and workforce changes.
- Team Leader view: Team Captains, staffing by campaign, transfer queue, target assignments, exemption approvals, and team comparison.
- Team Captain view: direct Agents, current campaign allocation, callbacks, leases, exemption requests, work review, and daily operational exceptions.

### 7.5 Workforce and campaign-assignment UI

- Display current and scheduled assignments with start and end times.
- Show campaign priority or allocation percentage when simultaneous work is allowed.
- Provide a transfer wizard with preflight results for active leases, callbacks, open reviews, and target effects.
- Require an effective time and reason for every move.
- Preview who will own callbacks after the move.
- Show history separately from current state.
- Support acting roles and temporary coverage without overwriting normal reporting lines.
- Prevent drag-and-drop or bulk moves from bypassing approval and preflight.

### 7.6 Target and exemption UI

- Show actual output, base target, adjusted target, and exemption status as separate fields.
- Display Exempt or Adjusted rather than 0% when evaluation is not applicable.
- Let Agents view and request an exemption without entering unnecessary sensitive details.
- Let Team Captains endorse or request on behalf of direct Agents.
- Let Team Leaders approve within policy.
- Route retroactive, unusually long, overlapping, or closed-period cases to Manager.
- Show approval history, effective period, adjustment method, reason category, and non-sensitive evidence reference.
- Require a preview of target impact before approval.
- Provide a mass-adjustment workflow for verified system outage, insufficient campaign data, or business interruption.

### 7.7 Accessibility

Target WCAG 2.2 AA:

- Visible focus indicators.
- Logical focus order.
- No keyboard traps.
- Text and non-text contrast.
- Status not communicated by color alone.
- Accessible names and descriptions.
- Error summaries linked to fields.
- Minimum control target size and spacing.
- Screen-reader announcements for new work items, expired sessions, import progress, and validation errors.
- Reduced-motion support for moving watermarks or animated indicators.

### 7.8 Secure browser behavior

- Send Cache-Control: no-store on authenticated and personal-data responses.
- Do not install a service worker that caches authenticated pages.
- Do not store contacts, notes, tokens, or work-item payloads in localStorage or IndexedDB.
- Clear sensitive page state on logout and session expiry.
- Show a session-expiry warning before unsaved notes are lost.
- Use a watermark as a deterrent, not as a claim of screenshot prevention.
- Do not disable right-click or claim that CSP can block developer tools.
- Do not overwrite the user’s clipboard after copying.

## 8. Target architecture

### 8.1 Recommended topology

~~~mermaid
flowchart LR
    A["Agent work laptops<br/>Office LAN only"] -->|"HTTPS 443"| C["Caddy LAN ingress"]
    P["Manager, Team Leader, and admin devices<br/>Tailscale approved"] --> T["Tailscale Serve HTTPS"]
    C --> W["CipherContact web and API"]
    T --> W
    W --> D["PostgreSQL"]
    W --> R["Redis"]
    W --> Q["Background worker"]
    Q --> D
    Q --> R
    B["Backup process"] --> D
    B --> O["Encrypted off-device backup"]
    M["Monitoring"] --> W
    M --> D
    M --> R
~~~

### 8.2 Network rules

- Caddy is the only service listening on the office LAN.
- Caddy listens on HTTPS port 443 and redirects or rejects plain HTTP.
- The host firewall permits LAN HTTPS only from the approved agent network or VLAN.
- Tailscale Serve provides the privileged remote HTTPS path.
- The web application listens only on a private container network.
- PostgreSQL and Redis listen only on the data container network.
- Do not publish port 8123, 5432, or 6379 to the host.
- Do not enable Tailscale Funnel.
- Tailscale grants allow only approved Manager, Team Leader, administrator, and support identities.
- Health and metrics endpoints are not exposed to untrusted LAN users.

### 8.3 Production services

| Service | Purpose | External exposure |
|---|---|---|
| caddy | LAN TLS termination and reverse proxy | Office LAN HTTPS only |
| tailscale | Private remote path and Serve configuration | Tailnet only |
| ciphercontact-web | Jinja2, HTMX, API, auth, and business services | Private application network |
| worker | Imports, report rollups, retention, and administrative jobs | None |
| scheduler | Singleton scheduling for approved periodic jobs | None |
| postgres | Primary transactional store | Private data network |
| redis | Session cache, rate limits, and background queue. PostgreSQL is authoritative for session validity | Private data network |
| backup | Encrypted database and configuration backups | Outbound to approved backup target only |
| monitoring | Health collection and alert dispatch | Admin-only path |

### 8.4 Host baseline

Recommended production baseline, subject to measured load:

- x86-64 mini PC.
- Four physical or efficient cores.
- 8 GB RAM minimum, 16 GB recommended.
- 256 GB SSD minimum, 512 GB recommended.
- Gigabit Ethernet.
- UPS or properly maintained laptop battery.
- Full-disk encryption.
- Secure Boot where supported.
- BIOS or UEFI password.
- USB boot disabled after recovery media is prepared.
- Automatic sleep and hibernation disabled for the server.
- Documented patch and controlled reboot window.
- Disk-health, temperature, free-space, and backup monitoring.

Do not use a microSD card as the primary production database medium.

If a Windows host is mandatory, the plan must additionally define BitLocker recovery-key custody, Docker or WSL2 automatic startup, update and reboot behavior, service ownership, sleep controls, and recovery after an interrupted Windows update.

## 9. Application security plan

### 9.1 Threat model

Protect against:

- An agent attempting to enumerate or bulk-extract contacts.
- A management user attempting to access another portfolio, team, campaign, or direct-report scope.
- A compromised browser session.
- A malicious or malformed spreadsheet.
- A stolen or failed server disk.
- An attacker on the office network.
- A leaked Tailscale credential.
- A privileged user altering audit evidence.
- A retry, race, or refresh causing duplicate work or duplicate outcomes.
- Accidental exposure through logs, browser caches, backups, or reports.
- Misuse of productivity metrics for unsupported conclusions.

Document assets, actors, trust boundaries, abuse cases, mitigations, residual risk, and accepted risk before Phase 1 exits.

### 9.2 Authentication

Preferred order:

1. Corporate OIDC with existing MFA and lifecycle management, if available.
2. Local accounts with Argon2id password hashing and MFA for super admins and captains.

Local-account requirements:

- No shared accounts.
- No default passwords.
- Cryptographically random, time-limited activation or reset credentials.
- Force password establishment during activation.
- Prevent account enumeration through response text and timing.
- Rate limit by account, source, and global abuse signals, not only by IP.
- Record successful and failed authentication events without recording passwords.
- Disable an account and revoke all sessions in one transaction.
- Rehash passwords when approved parameters change.

### 9.3 Sessions

- Use opaque, random server-side sessions for browsers.
- Store only a high-entropy session identifier in a host-only Secure, HttpOnly, SameSite cookie.
- Store a hash of the session token server-side.
- Treat PostgreSQL as the authoritative store for session existence, validity, and revocation. If Redis is used for sessions, use it only as a short-lived read-through cache, and propagate every logout, disable, and privilege change to PostgreSQL first so a stale cache entry cannot keep a revoked session alive.
- Rotate the identifier after login, privilege change, password change, MFA change, and sensitive recovery.
- Enforce idle and absolute expiration on the server.
- Make timeouts configurable by role after usability testing.
- Invalidate the server-side session on logout.
- Display and permit revocation of active privileged sessions.
- Do not use stateless JWT as a second browser-authentication model.

### 9.4 CSRF and request integrity

- Use synchronizer CSRF tokens for state-changing browser requests.
- Validate Origin and Fetch Metadata headers as defense in depth.
- Reject state changes sent by GET.
- Require an idempotency key for imports, work completion, DNC changes, and other retry-sensitive writes.
- Use request IDs for tracing, but never include personal data in them.
- Configure CORS as denied by default.

### 9.5 Password and secret storage

- Use pwdlib with Argon2id or another approved current implementation.
- Keep database, Redis, encryption, backup, and Tailscale credentials out of ordinary Compose environment values where supported.
- Mount secrets as read-only files with restrictive permissions.
- Use separate credentials per service.
- Rotate credentials on a schedule and after suspected exposure.
- Remove a Tailscale bootstrap credential after persistent node enrollment where the selected enrollment model permits it.
- Keep field-encryption and backup keys separate from the protected data and include tested recovery-key custody.
- Compute phone fingerprints as keyed HMAC-SHA256 over the normalized E.164 number, never as a plain hash, because the phone-number space is small enough to enumerate. Hold the HMAC key in the same separated custody as the field-encryption key and version it so it can be rotated.

### 9.6 Upload security

- Permit only CSV and XLSX in the MVP.
- Validate extension, media type, signature, and internal container structure.
- Generate storage names. Never use a client filename as a filesystem path.
- Store uploads outside the webroot in a non-executable quarantine.
- Apply limits to compressed size, expanded size, rows, columns, cell length, formulas, hyperlinks, and processing time.
- Reject macro-enabled formats and external links unless separately approved.
- Run malware or sandbox checks where available.
- Parse in a worker with CPU, memory, time, and filesystem limits.
- Escape all rendered values.
- Neutralize spreadsheet-formula injection in any approved download.
- Delete quarantine files after successful commit or after the retention window for a failed import.

### 9.7 Application and container hardening

- Pin Python dependencies with hashes.
- Pin production container images to reviewed immutable digests.
- Run application containers as non-root.
- Use a read-only root filesystem where practical.
- Drop Linux capabilities not explicitly required.
- Do not mount the Docker socket into application containers.
- Set memory, CPU, process, and file limits.
- Add application, worker, PostgreSQL, Redis, Caddy, and Tailscale health checks.
- Make startup depend on service health, not only container creation order.
- Self-host CSS, JavaScript, icons, and fonts.
- Use a CSP compatible with HTMX without claiming it can prevent inspection.
- Add HSTS, frame-ancestor restrictions, Referrer-Policy, Permissions-Policy, and MIME-sniffing protection.

### 9.8 Audit and security logging

Audit events must be append-only at the application level and include:

- Event ID and timestamp in UTC.
- Actor ID, role, organization, and team.
- Action and result.
- Target type and target ID.
- Request or correlation ID.
- Source IP and normalized user-agent summary.
- Redacted before-and-after metadata for sensitive configuration changes.
- Reason code for overrides, corrections, reassignments, exports, and retention actions.

Do not record:

- Raw phone numbers.
- Passwords or reset credentials.
- Session tokens.
- Full notes.
- Entire uploaded rows.
- Secret values.

Protect audit records through restricted access, tamper detection, retention, and a periodic copy to an independently protected destination.

### 9.9 DLP limitations and residual risk

One-record display, watermarks, rate limits, and audit alerts reduce casual bulk extraction but cannot prevent:

- Screenshots.
- Photography.
- Manual transcription.
- A compromised endpoint.
- An authorized user misusing the one contact currently visible.

If stronger prevention is required, add managed endpoint controls, browser policies, device posture, screen-capture policy, and employment or operational controls. State the remaining risk explicitly in the production risk acceptance.

## 10. Revised data model

### 10.1 Modeling approach

Separate canonical identity, campaign participation, work allocation, attempts, and suppression. Avoid placing every concern in a single phone_numbers table.

### 10.2 Core entities

#### organizations

Use only if multiple legal organizations or clients must be isolated. If the confirmed scope is one organization, do not implement incomplete multi-organization behavior.

Key fields:

- id
- name
- status
- created_at
- updated_at

#### teams

Key fields and rules:

- id
- organization_id, if organizations are used
- external_code
- name
- parent_team_id, nullable
- default_timezone
- status
- created_at
- updated_at
- Unique active external code within its organization.
- Parent relationships may represent a portfolio, Team Leader group, or Team Captain unit, but permissions still come from scoped role assignments.

#### users

Key fields and rules:

- id
- external_workforce_id
- email or approved login identifier
- display_name
- password_hash for local accounts only
- identity_provider_subject for OIDC accounts
- workforce_status
- start_date
- end_date
- active
- created_at
- updated_at
- last_login_at
- disabled_at
- Unique immutable external workforce ID when one is available.
- Unique normalized login identifier.
- No role column if a user can hold more than one scoped membership.

#### team_memberships

Key fields and rules:

- id
- team_id
- user_id
- membership_status
- effective_from
- effective_to
- created_by
- created_at
- ended_at
- Unique overlapping active membership is prohibited for the same user and team.
- Team membership does not itself grant Manager, Team Leader, Team Captain, or Agent capability.

Super-admin status should be represented explicitly at the installation scope rather than inferred from who created the account.

#### role_assignments

Represent business and technical authority independently from users.

Key fields and rules:

- id
- user_id
- role_code
- scope_type
- scope_id
- effective_from
- effective_to
- status
- appointment_type
- reason_code
- appointed_by
- approved_by, when required
- created_at
- ended_at
- version

Role codes include super_admin, manager, team_leader, team_captain, agent, viewer, and narrowly defined service roles.

Scope types may include installation, organization, portfolio, team, campaign, and report_scope. Reject a role and scope combination that is not explicitly allowed.

Appointment types may include permanent, acting, temporary_cover, delegated, and emergency_support.

Rules:

- A role becomes effective only inside its approved time range.
- Overlapping assignments are permitted only when their capabilities and scopes do not conflict.
- Role removal ends the assignment rather than deleting it.
- Privilege elevation requires step-up authentication and an authorized approver.
- A user cannot approve their own role assignment.

#### reporting_assignments

Key fields and rules:

- id
- subordinate_user_id
- supervisor_user_id
- context_type
- context_id
- effective_from
- effective_to
- assignment_type
- status
- assigned_by
- reason_code
- created_at
- ended_at

Use this for operational reporting lines, acting coverage, and temporary supervision. Do not assume the current reporting line from who created the user.

#### delegations

Key fields and rules:

- id
- delegator_user_id
- delegate_user_id
- capability_set
- scope_type
- scope_id
- effective_from
- effective_to
- reason_code
- approved_by
- revoked_at
- created_at

Delegation never includes capabilities the delegator does not hold, never permits self-approval, and expires automatically.

#### sessions

Key fields and rules:

- id
- user_id
- token_hash
- created_at
- last_seen_at
- idle_expires_at
- absolute_expires_at
- revoked_at
- source summary
- MFA state
- Index active sessions by user and expiry.

#### campaigns

Key fields and rules:

- id
- owning_scope_type
- owning_scope_id
- owner_manager_role_assignment_id
- name
- description
- purpose
- data_source
- data_obtained_at
- lawful_basis_or_consent_reference
- default_region
- timezone
- status
- created_by
- created_at
- updated_at
- launched_at
- archived_at
- Status transitions are validated by the service layer.

#### campaign_team_assignments

Campaigns may involve several teams over time.

Key fields and rules:

- id
- campaign_id
- team_id
- effective_from
- effective_to
- status
- staffing_capacity
- assigned_by
- created_at

One team leaving a campaign does not archive the campaign or remove another team’s assignments.

#### campaign_user_assignments

This is the source of truth for who may work or supervise a campaign.

Key fields and rules:

- id
- campaign_id
- user_id
- team_id
- campaign_role
- assignment_type
- effective_from
- effective_to
- status
- priority
- allocation_percentage
- shift_or_schedule_reference
- target_policy_id, nullable
- assigned_by
- approved_by, when required
- reason_code
- created_at
- ended_at
- version

Campaign roles include manager, team_leader, team_captain, and agent. They do not replace the corresponding business role assignment.

Assignment types may include primary, secondary, overflow, training, temporary_cover, and callback_only.

Rules:

- An Agent campaign assignment requires an active Agent role and active team membership.
- A user cannot lease campaign work outside the assignment time range.
- Allocation percentages and priorities are required when several campaigns are active at once.
- The sum and interpretation of allocation percentages are validated by the approved campaign-concurrency policy.
- Ending an assignment never changes completed attempts.
- A transfer creates a new assignment and ends the old assignment at an effective cutoff.

#### campaign_disposition_definitions

Key fields and rules:

- id
- campaign_id
- label
- stable_semantic_code
- next_action
- requires_notes
- requires_callback_time
- counts_as_connected
- counts_as_conversion
- causes_global_suppression
- requeue_policy_id
- display_order
- active
- Only approved protected semantic codes may cause global suppression.

#### contacts

Key fields and rules:

- id
- phone_ciphertext or protected canonical phone value
- phone_fingerprint for exact matching (keyed HMAC-SHA256 of the normalized E.164 number; key held in separate custody and versioned)
- display_name, if global identity is approved
- created_at
- suppression_status cache, if derived safely
- Unique active phone fingerprint within the approved organization scope.
- Preserve field-encryption key version if field encryption is used.

#### campaign_contacts

Key fields and rules:

- id
- campaign_id
- contact_id
- original_phone_protected
- campaign_name_value
- approved_metadata
- source_row_reference
- status
- imported_at
- completed_at
- Unique contact within a campaign unless an explicitly approved repeat-work rule exists.
- Metadata keys, types, size, and display permissions are validated against a campaign schema.

#### batches

Use batches as an optional management grouping, not as the only queue mechanism.

Key fields:

- id
- campaign_id
- name
- assignment_mode
- assigned_agent_id, nullable for a shared pool
- status
- created_at
- started_at
- completed_at

#### work_items

Key fields and rules:

- id
- campaign_contact_id
- batch_id
- campaign_user_assignment_id
- assigned_agent_id, nullable for shared pool
- state
- priority
- due_at
- attempt_count
- max_attempts
- lease_owner_id
- lease_id
- lease_expires_at
- version
- created_at
- updated_at
- completed_at
- Only one current work item per campaign contact unless an explicit repeat-work transition creates the next item.
- Enforce one active lease through a transaction and appropriate unique or exclusion constraints.

Suggested states:

- queued
- leased
- callback_wait
- completed
- suppressed
- review
- cancelled

#### call_attempts

Treat attempts as immutable business events.

Key fields and rules:

- id
- work_item_id
- campaign_contact_id
- agent_id
- campaign_user_assignment_id
- disposition_definition_id
- semantic_outcome
- notes_ciphertext or protected notes
- self_reported_duration_seconds
- explicit_dnc_requested
- callback_at
- idempotency_key
- created_at
- correction_of_attempt_id, when a correction is required
- Unique idempotency key within the actor and endpoint scope.

Do not overwrite an attempt to hide a correction. Add a correcting event with reason and authorization.

#### target_policies

Define how a target is calculated before assigning it to a person.

Key fields and rules:

- id
- name
- metric_code
- period_type
- base_value
- scope_type
- scope_id
- effective_from
- effective_to
- proration_method
- rounding_method
- working_calendar_id
- ramp_profile_id, nullable
- campaign_attribution_method
- status
- created_by
- approved_by
- version
- created_at

Metric codes must distinguish calls attempted, contacts connected, qualified outcomes, conversions, and sales. A policy may use only one clearly defined primary metric.

#### performance_periods

Key fields and rules:

- id
- period_type
- starts_at
- ends_at
- timezone
- status
- closed_at
- closed_by
- reopened_at
- reopened_by
- reopen_reason
- version

Suggested states are open, calculating, under_review, closed, and reopened.

#### target_assignments

Key fields and rules:

- id
- user_id
- campaign_id, nullable for an approved cross-campaign target
- campaign_user_assignment_id, nullable only when cross-campaign is explicitly intended
- target_policy_id
- performance_period_id
- base_target
- prorated_target
- status
- assigned_by
- created_at
- version

Create a separate target assignment for each target-bearing scope. Do not store the current target on users or batches.

#### target_exemption_requests

Key fields and rules:

- id
- user_id
- target_assignment_id
- request_type
- reason_category
- requested_effective_from
- requested_effective_to
- requested_adjustment_method
- requested_adjustment_value
- non_sensitive_note
- evidence_reference, nullable
- requested_by
- endorsed_by, nullable
- status
- submitted_at
- decided_at
- decided_by
- decision_reason
- version

Request types include full_exemption, proportional_reduction, fixed_reduction, excluded_hours, and administrative_correction.

Reason categories may include approved_leave, illness_without_diagnosis, training, bereavement_or_authorized_absence, system_outage, equipment_or_network_failure, insufficient_campaign_data, campaign_transfer, new_starter_ramp, and business_interruption.

Do not store diagnoses or unnecessary medical details in the request.

#### target_adjustments

Approved target effects are immutable adjustment events.

Key fields and rules:

- id
- target_assignment_id
- exemption_request_id, nullable for an approved mass business adjustment
- adjustment_type
- amount_or_percentage
- effective_from
- effective_to
- created_by
- approved_by
- created_at
- reversed_by_adjustment_id, nullable

Actual work remains unchanged. Reports calculate and display base target, prorated target, approved adjustment, effective target, actual output, and evaluation status.

#### target_snapshots

Freeze reproducible results at period close.

Key fields and rules:

- id
- target_assignment_id
- performance_period_id
- metric_definition_version
- base_target
- prorated_target
- total_adjustment
- effective_target
- actual_value
- evaluation_status
- calculated_at
- closed_at
- source_event_watermark

Evaluation status includes on_target, below_target, above_target, exempt, not_applicable, and under_review. Reopening a period creates a new version rather than silently changing the old snapshot.

#### suppression_entries

Key fields and rules:

- id
- organization_scope
- phone_fingerprint (same keyed HMAC construction as contacts, so suppression matches contacts exactly)
- protected_phone_value when operationally required
- source
- effective_at
- status
- created_by
- created_at
- corrected_by
- corrected_at
- correction_reason
- Unique effective suppression per phone fingerprint and organization scope.

Suggested sources:

- imported_master_list
- explicit_contact_request
- administrator_entry
- legal_or_compliance_entry

#### import_jobs

Key fields and rules:

- id
- team_id
- campaign_draft_id
- uploader_id
- source_filename_display
- generated_storage_key
- file_hash
- state
- parser_version
- total_rows
- valid_rows
- invalid_rows
- duplicate_rows
- suppression_hits
- decision_version
- created_at
- expires_at
- committed_at

#### import_rows and import_decisions

Store the minimum needed for a time-limited preview and decision process:

- Row number or source reference.
- Protected original values.
- Parsed and canonical values.
- Validation outcome.
- Duplicate category.
- Suppression match indicator without exposing the master list.
- Authorized uploader or approver decision.
- Decision actor and timestamp.

Delete or minimize row-level staging data after commit according to retention policy.

#### workforce_import_jobs and workforce_import_rows

Use a separate staged import domain for users, roles, reporting lines, campaign assignments, and targets.

Key job fields:

- id
- import_type
- uploader_id
- uploader_scope
- source_filename_display
- generated_storage_key
- file_hash
- state
- total_rows
- valid_rows
- warning_rows
- invalid_rows
- high_risk_rows
- decision_version
- created_at
- committed_at
- reversed_at

Supported import types:

- users
- team_memberships
- role_assignments
- reporting_assignments
- campaign_user_assignments
- target_assignments
- explicit_deactivations

Key row fields:

- row_number
- action
- external_workforce_id
- normalized_identity
- target_scope_reference
- parsed_values
- validation_result
- conflict_type
- risk_level
- proposed_effect
- decision
- committed_entity_id
- created_at

Missing rows never imply deletion or deactivation. Sensitive actions require an explicit action value and appropriate approval.

#### audit_events

Use the fields described in the audit section. Partition or archive by time if volume requires it, while retaining queryability and tamper evidence.

#### metric_rollups

Create derived daily or campaign rollups only after metric definitions are approved. Rollups must be reproducible from immutable attempts and must not become the source of truth for operational state.

### 10.3 Required constraints

- Foreign keys for every ownership relation.
- Check constraints for state values and non-negative counters.
- UTC timestamps in storage, with campaign timezones used for display and business windows.
- Unique constraints for normalized identifiers, active memberships, idempotency, and approved work-item uniqueness.
- No cascade deletion of audit, suppression, or attempt evidence.
- Deletion workflows must be explicit, authorized, audited, and retention-aware.
- Database counters are updated in the same transaction as the underlying event or calculated from events.
- Time ranges for role, reporting, campaign, target, and exemption records are validated for prohibited overlap.
- Campaign work cannot reference an inactive or out-of-period campaign assignment.
- Exemption approver cannot equal requester, subject, or prohibited endorsing role under the approved policy.
- Closed target snapshots cannot be updated in place.
- Bulk imports cannot grant a capability outside the uploader and approver scopes.

### 10.4 Key indexes

At minimum:

- Active membership by user and team.
- Effective role assignment by user, role, scope, and time.
- Active reporting line by subordinate, supervisor, context, and time.
- Campaign assignment by campaign, user, team, role, status, and effective time.
- Campaign by owning scope and status, plus campaign-team assignment by campaign, team, and effective time.
- Campaign contact by campaign and contact.
- Contact by phone fingerprint.
- Effective suppression by phone fingerprint.
- Work-item queue by state, due time, priority, campaign, team, and assigned agent.
- Active lease by owner and expiry.
- Attempt by campaign contact, agent, and created time.
- Audit by actor, target, action, and time.
- Import job by team, uploader, state, and expiry.
- Target assignment by user, campaign, policy, period, and status.
- Exemption request by subject, approver scope, status, and effective time.
- Target snapshot by user, campaign, and period.
- Workforce import by uploader, type, state, and created time.

Validate every proposed index using realistic query plans before production. Avoid adding redundant indexes merely because a column appears in a filter.

## 11. Core workflow specifications

### 11.1 User provisioning and offboarding

1. An authorized user creates an identity using the immutable external workforce ID where available.
2. The system validates that login identifier, employee ID, team, reporting line, and intended role are not conflicting.
3. The system creates team membership separately from role and campaign assignments.
4. The authorized workflow creates effective-dated role and reporting assignments.
5. The system creates a time-limited activation flow without a default shared password.
6. The user establishes authentication and, where required, MFA.
7. The system records identity, activation, membership, role, and reporting events.
8. Campaign assignments and targets are added only through their own authorized workflows.
9. On disable or offboarding, the system revokes sessions and active leases, ends future capability, and runs an impact preview for callbacks, assignments, approvals, and direct reports.
10. Any leased work returns safely to the queue after a defined grace and audit event.
11. A rehire reactivates the matched workforce identity through a new lifecycle event rather than creating a duplicate person.

Failure controls:

- Activation credentials expire and are single use.
- Repeating the activation completion is idempotent.
- Disabling a user cannot leave a valid session.
- An agent with a removed membership cannot submit an old leased record.
- Ending one role does not end unrelated authorized roles.
- Offboarding cannot silently orphan direct reports, callbacks, exemption approvals, or future campaign assignments.
- Display name and email are not treated as the immutable workforce identity.

### 11.2 Bulk user and workforce import

Use a staged import wizard based on the safe campaign-import pattern.

#### Supported files

Prefer separate templates instead of one uncontrolled spreadsheet:

1. Users and lifecycle actions.
2. Team memberships and reporting lines.
3. Role assignments and acting appointments.
4. Campaign assignments and transfers.
5. Target assignments.
6. Explicit deactivations.

A multi-sheet workbook may package these templates, but each sheet retains its own validation and approval rules.

#### Required identity and action fields

User rows should include:

- action
- external_workforce_id
- login_identifier
- display_name
- workforce_status
- start_date
- end_date, when known

Assignment rows should include:

- external_workforce_id
- role or assignment type
- scope code
- effective_from
- effective_to
- supervisor workforce ID, when applicable
- reason code

Campaign rows should additionally include:

- campaign code
- team code
- campaign role
- assignment type
- priority
- allocation percentage
- schedule reference
- target policy code, when approved

Do not include passwords, reset codes, medical details, or raw authentication secrets.

#### Import flow

1. Authorized Manager or scoped Team Leader selects an import type.
2. System provides the current versioned template and data dictionary.
3. File enters quarantine with a generated name and file hash.
4. Parser validates headers, types, identity matches, scope, dates, overlaps, supervisors, teams, campaigns, and target policies.
5. System classifies each row as valid, warning, blocking error, or high-risk action.
6. Preview shows creates, updates, reactivations, role elevations, transfers, target changes, and deactivations separately.
7. Uploader resolves all blocking errors and explicitly accepts warnings.
8. Role elevations, Manager appointments, bulk deactivations, closed-period target changes, and cross-team moves require the configured higher approval.
9. System locks the import and revalidates current scope and referenced records.
10. Commit runs atomically for the approved file or approved transaction group.
11. System creates activation invitations separately. It never sends a password file.
12. Result report lists safe row identifiers, outcomes, and actions without exposing secrets.
13. Repeating the same import idempotency key returns the original result.

#### Bulk-import safeguards

- Missing from file never means delete or deactivate.
- Deactivation requires an explicit action column.
- Names never determine identity matching.
- Unknown supervisors, teams, campaigns, policies, or roles are blocking errors.
- A file cannot grant the uploader more authority.
- High-risk rows cannot hide among ordinary updates.
- Default commit is all-or-nothing for each security-sensitive transaction group.
- Reversal creates compensating end or reactivation events and is allowed only if later changes do not conflict.
- Every committed row links to the import job and approver.

### 11.3 Role, reporting-line, acting, and delegation workflow

1. Requester selects user, role, scope, effective period, appointment type, and reason.
2. Server verifies requester authority and checks prohibited overlap.
3. Preview shows new capabilities, direct reports, campaign access, target approval scope, and conflicts.
4. Required approver completes step-up authentication.
5. Server creates the role, reporting, or delegation assignment.
6. Sessions are rotated or revoked when privilege changes.
7. A future-dated job or request-time check activates and expires capability based on the effective period.
8. A replacement reporting line or acting appointment does not delete the ordinary line.
9. Ending or revoking an assignment records reason and impact.

Acting coverage must be time-limited. Delegation cannot include DNC correction, closed-period reopening, role elevation, or another non-delegable capability unless explicitly approved by policy.

### 11.4 Campaign assignment, movement, and allocation

#### Supported assignment modes

- Primary: normal campaign for the defined period or shift.
- Secondary: eligible after primary campaign rules.
- Overflow: eligible only when approved capacity conditions apply.
- Training: limited work and separate target treatment.
- Temporary cover: time-limited replacement.
- Callback only: may complete inherited callbacks but receives no new general work.

Recommended MVP rule:

- Start with one primary work campaign per Agent at a time, plus explicit callback-only obligations.
- Add simultaneous weighted campaign allocation only after operations defines priority, percentages, and target attribution.

#### Assignment flow

1. Manager or Team Leader selects campaign, Agent, team, assignment type, effective period, and allocation rule.
2. Server confirms active Agent role, team membership, campaign-team eligibility, training or clearance, and schedule.
3. Preview shows overlapping assignments, active leases, callbacks, target impact, and expected capacity.
4. Approver confirms the change.
5. Server writes a new campaign assignment and audit event.
6. Queue service begins or ends eligibility at the effective time.
7. Agent and affected Team Captain receive notification.

#### Campaign transfer flow

1. Initiator selects source campaign, destination campaign, effective cutoff, and reason.
2. System runs preflight:
   - Active lease.
   - Due and future callbacks.
   - Open review cases.
   - Current and future target assignments.
   - Shift and schedule overlap.
   - Destination capacity and eligibility.
3. Initiator chooses an allowed lease treatment:
   - Agent completes before cutoff.
   - Lease returns to source queue.
   - Team Captain resolves exceptional work.
4. Initiator chooses callback treatment:
   - Agent retains callback-only assignment.
   - Callback transfers to a named eligible Agent.
   - Callback returns to a supervised handoff queue.
5. System prorates or closes target assignments according to the approved policy.
6. Source campaign assignment ends at the cutoff.
7. Destination campaign assignment begins at the cutoff.
8. Completed attempts remain attributed to the source campaign and original assignment.
9. Notifications and audit events are created.

After a transfer becomes effective, correction is a new compensating assignment or transfer event. Do not rewrite the original history.

### 11.5 Target policy and assignment

1. Manager creates or versions a target policy.
2. Policy defines metric, period, base value, working calendar, proration, rounding, ramp-up, and campaign attribution.
3. Team Leader may propose changes but cannot silently alter an approved policy.
4. Manager approves the version and effective date.
5. Team Leader assigns the policy to eligible Agents and campaigns.
6. Server calculates base and prorated targets for each performance period.
7. Agent and Team Captain can view the target source, period, and calculation.
8. Mid-period policy changes create a new effective-dated assignment or approved adjustment.

Reports must show:

- Base target.
- Prorated target.
- Approved adjustment.
- Effective target.
- Actual output.
- Evaluation status.
- Campaign attribution.
- Policy and formula version.

Do not combine connected, conversion, and sale into one ambiguous metric.

### 11.6 Target exemption and adjustment

#### Request and approval

1. Agent, Team Captain, Team Leader, or Manager starts a request within their allowed scope.
2. Request identifies target assignment, effective period, request type, reason category, and non-sensitive note.
3. Server calculates the proposed target effect.
4. Team Captain may endorse a direct Agent request but cannot provide final self-interested approval.
5. Team Leader approves within the approved duration, timing, and adjustment policy.
6. Manager handles retroactive requests beyond the ordinary window, long exemptions, overlapping requests, closed periods, mass adjustments, and appeals.
7. Approval creates an immutable target-adjustment event.
8. Agent, Team Captain, and Team Leader receive the decision.

#### Full and partial treatment

- Full exemption: evaluation status becomes exempt for the approved scope. Actual work remains visible.
- Proportional reduction: target is reduced based on approved time or capacity.
- Fixed reduction: approved amount is subtracted from the prorated target.
- Excluded hours: target calculation removes an approved period.
- Administrative correction: fixes a confirmed assignment, calendar, outage, or system error.

Effective target is calculated from the approved policy, proration, and immutable adjustments. It cannot be less than zero.

#### Safeguards

- No self-approval.
- No diagnosis or unnecessary medical detail.
- No double reduction for overlapping exemptions.
- No silent retrospective edit.
- Evidence access is more restricted than ordinary performance data.
- A mass outage adjustment requires affected scope, verified incident reference, previewed impact, and Manager approval.
- Revocation creates a reversing adjustment.
- An exemption does not automatically remove an Agent from work. Availability and work assignment are separate decisions.

### 11.7 Performance-period calculation, closure, and appeal

1. Open period displays live actuals and provisional effective targets.
2. At period end, system stops ordinary target changes.
3. Background calculation verifies campaign attribution, assignments, exemptions, and actual event totals.
4. Team Leader reviews exceptions.
5. Manager closes the period.
6. System stores immutable target snapshots.
7. Late changes require an authorized reopen reason.
8. Recalculation creates a new version and retains the original snapshot.
9. Agent may submit an appeal or correction request within the approved window.
10. Reports identify provisional, closed, reopened, and under-review results.

### 11.8 Campaign import and launch

#### Stage A: Create draft

1. Manager creates a campaign draft, or an authorized Team Leader creates a scoped draft.
2. Server confirms active role, ownership scope, and campaign-team authority.
3. Authorized campaign owner records source, purpose, acquisition date, lawful-basis or consent reference, default region, timezone, and approved metadata mapping.

#### Stage B: Quarantine upload

1. Browser uploads CSV or XLSX to an import-job endpoint.
2. Server generates an import ID and storage key.
3. Server streams the file with a strict size limit.
4. File is quarantined outside the webroot.
5. Malware, signature, container, and expansion checks run.
6. The worker records a file hash and parser version.

#### Stage C: Parse and normalize

1. Detect or confirm the phone column.
2. Map optional columns using an authorized campaign-owner-approved schema.
3. Parse phone numbers with the campaign default region.
4. Preserve the protected original value.
5. Produce canonical exact-match fingerprints.
6. Validate row, column, and cell limits.
7. Reject or escape active spreadsheet content.

#### Stage D: Classify

For each row:

- Valid or invalid.
- Duplicate within the file.
- Duplicate already in this campaign.
- Prior contact in another authorized campaign.
- Effective DNC suppression match.
- Metadata warning.
- Missing provenance or required field.

Cross-team history must not reveal another Manager’s or Team Leader’s campaign, outcome, or customer data. Present a neutral conflict reason or route the case to an authorized privileged decision.

#### Stage E: Preview and decisions

Show:

- Total rows.
- Valid rows.
- Invalid rows with masked examples.
- In-file duplicates.
- Existing campaign duplicates.
- Suppression hits as counts only.
- Warnings and blocking errors.
- Proposed batch or shared-pool allocation.
- Data-retention effect.

Uploader and approver decisions are versioned. A preview is not authorization to insert stale data later.

#### Stage F: Atomic commit

1. Authorized uploader submits the import-job ID, decision version, and idempotency key.
2. Server locks the import job.
3. Server verifies that the job is still eligible and the decision version matches.
4. Server revalidates DNC suppression and duplicate state.
5. Server creates or links contacts, campaign contacts, batches, and work items in one transaction.
6. Server writes an audit event and marks the import committed.
7. Server returns the same result for a repeated idempotency key.
8. Only after a successful commit may the campaign be launched.

If any invariant fails, roll back the entire commit. Do not partially launch a campaign.

#### Stage G: Cleanup

- Delete or minimize quarantined raw files and staging rows according to policy.
- Preserve the import manifest, file hash, counts, decisions, parser version, and audit evidence.
- Alert on jobs that remain failed or pending beyond the allowed window.

### 11.9 Work-item acquisition

1. Agent requests the next work item.
2. Server verifies session, active membership, Agent role, effective campaign assignment, schedule, account state, campaign state, and daily safety limits.
3. Inside one transaction, server prioritizes:
   - Due callbacks assigned to the agent.
   - Explicitly assigned queued work.
   - Eligible shared-pool work from the active campaign allocation rules.
4. Server selects an eligible row with a locking strategy suitable for queue consumers.
5. Server creates a lease with owner, lease ID, expiry, and version.
6. Server records a viewed-contact audit event.
7. Server returns one approved contact payload and lease reference.

The response must not include total hidden queue size or unrelated contact identifiers.

### 11.10 Work completion

1. Agent selects a disposition.
2. UI requests any required notes, callback time, or confirmation.
3. Client sends the lease reference, version, disposition ID, conditional fields, and idempotency key.
4. Server confirms the lease is active and owned by the agent.
5. Server validates the disposition against the campaign configuration.
6. Server creates an immutable attempt.
7. Server applies the semantic effect:
   - Complete.
   - Requeue.
   - Schedule callback.
   - Send to review.
   - Suppress globally.
8. Server updates derived counters in the same transaction or queues a safe rollup.
9. Server closes the lease and writes an audit event.
10. Client requests the next item separately or receives a safe continuation token.

Repeated completion with the same idempotency key returns the original result. A different completion against a closed lease is rejected with a clear conflict response.

### 11.11 Skip behavior

- Skip is an explicit action, not the browser Escape key.
- Require a configured reason.
- Apply a server-side per-agent and per-campaign policy.
- Increment skip count through an immutable event.
- Requeue or route to Team Captain review according to policy.
- Prevent endless skip loops through attempt limits and review rules.
- Report skip rates only with context and without assuming misconduct.

### 11.12 Callback behavior

- Store callback time in UTC plus the campaign timezone used for interpretation.
- Validate permitted contact hours and any applicable quiet-time rules.
- Due callbacks receive priority for the owning agent.
- If the Agent is unavailable beyond an approved threshold, route to a reassignment queue controlled by the Team Captain.
- When an Agent leaves a campaign, callbacks follow the approved transfer preflight. They are never silently orphaned.
- A retained callback requires a callback-only or other valid campaign assignment.
- A callback does not require a worker to rewrite queue records every minute. Eligibility can be evaluated transactionally when requesting work, with background jobs used for reminders and escalation.
- The agent callback list shows masked references and callback times only. The raw number is revealed one item at a time by leasing, consistent with the one-record rule.
- Editing or cancelling a callback is authorized and audited.

### 11.13 Explicit DNC workflow

1. Agent selects the protected explicit-DNC disposition.
2. UI clearly confirms that the contact asked not to be called again.
3. Submission starts one database transaction.
4. Server inserts or confirms the effective suppression entry.
5. Server changes every pending work item for that contact to suppressed.
6. Server closes the current lease and records the attempt.
7. Server prevents new imports or work allocation for the same phone fingerprint.
8. Audit event records the source and effective time without exposing the number in logs.

A Team Captain does not approve whether the request becomes effective. A privileged correction requires a reason, additional authorization, and a new audit event. The contact remains suppressed until the correction transaction completes.

### 11.14 Campaign pause, archive, and retention

- Pausing prevents new leases but does not erase completed attempts.
- Existing leases either expire naturally or are recalled according to a documented policy.
- Archiving removes the campaign from active workflows but does not imply indefinite retention.
- Retention jobs apply approved deletion, anonymization, or legal-hold rules.
- Destructive retention actions produce counts, approvals, and audit evidence.
- Restoring an archived campaign does not automatically reactivate old contacts or bypass current DNC checks.

### 11.15 Notification and approval delivery

Provisioning activation, exemption decisions, role elevation, campaign transfers, and period-close events all depend on reaching a person reliably. Office email may be unavailable or unreliable (see D-23 and section 29.7).

- Provide an in-application notification and approval inbox as the primary channel. A user with a valid session sees pending activations, approvals, and decisions without depending on email.
- Treat email or any other external channel as an optional secondary notification, never the only path to complete provisioning or an approval.
- Never place raw phone numbers, notes, exemption reasons, or other sensitive detail in a notification body or subject.
- Activation still uses a single-use, time-limited credential. If email is unavailable, an authorized administrator may hand the activation link to the user through an approved offline process, and the credential remains single-use and expiring.

## 12. API plan

### 12.1 Conventions

- Prefix JSON APIs with /api/v1.
- Use server-rendered HTML and HTMX for primary pages.
- Use UUIDs or similarly non-sequential identifiers, while still enforcing authorization.
- Use one documented error format with code, message, field errors, request ID, and retry guidance.
- Require idempotency keys on retry-sensitive writes.
- Use cursor or bounded pagination on administrative lists.
- Apply rate limits by authenticated identity and action, with IP as an additional signal.
- Never put session IDs, phone numbers, or notes in URLs.
- Require optimistic versions for role, campaign assignment, exemption, target, and bulk approval changes.
- Return stable conflict codes such as assignment_expired, assignment_overlap, approval_required, target_period_closed, lease_conflict, and stale_version.

### 12.2 Proposed endpoints

#### Authentication

| Method | Route | Purpose |
|---|---|---|
| POST | /api/v1/auth/login | Establish an authenticated session |
| POST | /api/v1/auth/logout | Revoke the current session |
| GET | /api/v1/auth/me | Return current identity and scoped memberships |
| POST | /api/v1/auth/reauthenticate | Step-up for a sensitive action |
| GET | /api/v1/auth/sessions | List the current user’s active sessions |
| DELETE | /api/v1/auth/sessions/{session_id} | Revoke an owned or administratively authorized session |

#### Campaign drafts and imports

| Method | Route | Purpose |
|---|---|---|
| POST | /api/v1/campaigns | Create a campaign draft |
| GET | /api/v1/campaigns | List authorized campaigns |
| GET | /api/v1/campaigns/{campaign_id} | Read authorized campaign detail |
| PATCH | /api/v1/campaigns/{campaign_id} | Update a draft or permitted configuration |
| POST | /api/v1/campaigns/{campaign_id}/imports | Create and upload an import job |
| GET | /api/v1/imports/{import_id} | Read import status |
| GET | /api/v1/imports/{import_id}/preview | Read the authorized preview |
| PATCH | /api/v1/imports/{import_id}/decisions | Save versioned import decisions |
| POST | /api/v1/imports/{import_id}/commit | Revalidate and atomically commit |
| POST | /api/v1/campaigns/{campaign_id}/launch | Launch a valid committed campaign |
| POST | /api/v1/campaigns/{campaign_id}/pause | Pause new work leases |
| POST | /api/v1/campaigns/{campaign_id}/archive | Archive under retention rules |

#### Agent work

| Method | Route | Purpose |
|---|---|---|
| POST | /api/v1/work/next | Atomically lease one eligible work item |
| POST | /api/v1/work/{work_item_id}/complete | Record an idempotent outcome |
| POST | /api/v1/work/{work_item_id}/skip | Record a reasoned skip |
| POST | /api/v1/work/{work_item_id}/lease/renew | Renew an eligible active lease |
| GET | /api/v1/agent/stats | Return approved personal metrics |
| GET | /api/v1/agent/callbacks | Return the agent’s own callback queue as masked references and times, never raw numbers |

#### Workforce and management operations

| Method | Route | Purpose |
|---|---|---|
| GET | /api/v1/users | List users within authorized scope |
| POST | /api/v1/users | Create or invite a scoped user |
| PATCH | /api/v1/users/{user_id} | Update permitted identity or lifecycle fields |
| POST | /api/v1/users/{user_id}/disable | Disable and run impact handling |
| POST | /api/v1/users/{user_id}/reactivate | Reactivate a matched workforce identity |
| GET | /api/v1/teams/{team_id}/members | List authorized team members |
| POST | /api/v1/teams/{team_id}/memberships | Create an effective-dated membership |
| POST | /api/v1/role-assignments | Create a scoped role assignment |
| PATCH | /api/v1/role-assignments/{assignment_id} | End or amend a permitted future role assignment |
| POST | /api/v1/reporting-assignments | Create a reporting or acting assignment |
| POST | /api/v1/delegations | Create a permitted time-limited delegation |
| DELETE | /api/v1/delegations/{delegation_id} | Revoke an authorized delegation |
| GET | /api/v1/workforce/imports/template | Download a versioned bulk template |
| POST | /api/v1/workforce/imports | Upload and validate a workforce import |
| GET | /api/v1/workforce/imports/{import_id}/preview | Review creates, updates, risks, and errors |
| POST | /api/v1/workforce/imports/{import_id}/approve | Approve high-risk rows within scope |
| POST | /api/v1/workforce/imports/{import_id}/commit | Atomically commit the approved transaction group |
| POST | /api/v1/workforce/imports/{import_id}/reverse | Create an authorized compensating reversal |
| GET | /api/v1/campaigns/{campaign_id}/batches | List batches and aggregate progress |
| POST | /api/v1/campaigns/{campaign_id}/batches | Create a batch or shared-pool configuration |
| PATCH | /api/v1/batches/{batch_id} | Pause, resume, or reassign under policy |
| GET | /api/v1/campaigns/{campaign_id}/assignments | List authorized current, scheduled, and historical assignments |
| POST | /api/v1/campaigns/{campaign_id}/assignments | Create an effective-dated assignment |
| POST | /api/v1/campaign-assignments/{assignment_id}/transfer-preview | Preview lease, callback, and target effects |
| POST | /api/v1/campaign-assignments/{assignment_id}/transfer | Commit an authorized campaign transfer |
| POST | /api/v1/campaign-assignments/{assignment_id}/end | End an assignment at an approved cutoff |
| GET | /api/v1/teams/{team_id}/callbacks | Review team callback obligations |
| GET | /api/v1/teams/{team_id}/reports | Return authorized aggregate reports |

#### Targets and exemptions

| Method | Route | Purpose |
|---|---|---|
| GET | /api/v1/target-policies | List policies within authorized scope |
| POST | /api/v1/target-policies | Create a policy draft |
| POST | /api/v1/target-policies/{policy_id}/approve | Approve an effective-dated policy version |
| POST | /api/v1/target-assignments | Assign an approved policy to an eligible scope |
| GET | /api/v1/users/{user_id}/targets | Read authorized base, adjusted, and actual values |
| POST | /api/v1/target-exemptions | Submit an exemption or adjustment request |
| POST | /api/v1/target-exemptions/{request_id}/endorse | Endorse a direct-report request |
| POST | /api/v1/target-exemptions/{request_id}/approve | Approve within role and policy |
| POST | /api/v1/target-exemptions/{request_id}/reject | Reject with a recorded reason |
| POST | /api/v1/target-exemptions/{request_id}/withdraw | Withdraw an eligible pending request |
| POST | /api/v1/target-exemptions/{request_id}/reverse | Create an authorized reversing adjustment |
| GET | /api/v1/performance-periods | List authorized periods and status |
| POST | /api/v1/performance-periods/{period_id}/close | Close and snapshot an approved period |
| POST | /api/v1/performance-periods/{period_id}/reopen | Reopen with Manager authority and reason |

#### Suppression and administration

| Method | Route | Purpose |
|---|---|---|
| POST | /api/v1/suppressions/imports | Import an authorized master suppression list |
| POST | /api/v1/suppressions | Add an authorized suppression |
| POST | /api/v1/suppressions/{suppression_id}/correct | Perform a privileged audited correction |
| GET | /api/v1/suppressions/stats | Return aggregate suppression statistics |
| GET | /api/v1/admin/audit-events | Search authorized audit metadata |
| GET | /api/v1/admin/health | Return protected system-health summary |
| POST | /api/v1/admin/retention-runs | Start an approved retention run |

There must be no general agent or viewer endpoint that lists raw contacts or suppression entries.

## 13. Background jobs and consistency

### 13.1 Select one job system

Do not run Celery or ARQ and APScheduler as overlapping scheduling systems. Select one supported background-job stack during Phase 1.

Recommended default for this deployment:

- Celery worker.
- Redis broker.
- Celery Beat as a singleton scheduler.
- PostgreSQL as the source of truth for business state.

If the team selects another job system, record the reasons, maintenance status, retry model, scheduling behavior, and operational ownership in an Architecture Decision Record.

### 13.2 Approved background jobs

- Import parsing and validation.
- Workforce bulk-import parsing, validation, commit preparation, and cleanup.
- Effective-dated role, reporting, delegation, and campaign-assignment expiry processing.
- Target-period calculation, snapshot generation, and exception reporting.
- Import quarantine cleanup.
- Aggregate metric rollups.
- Retention candidate generation and approved retention execution.
- Audit-log protected copy.
- Callback reminders and escalation notifications.
- Expired lease cleanup.
- Stale import-job cleanup.
- Pending exemption, approval, acting-role, campaign-transfer, and period-close notifications.
- Backup status verification and alerting, while the backup itself may run in a dedicated container or host process.

### 13.3 Job requirements

Every background task must:

- Be safe to retry.
- Use an explicit idempotency or deduplication key.
- Record start, completion, result, duration, and safe error details.
- Set a maximum execution time.
- Use bounded retries with backoff.
- Send repeatedly failed work to an operator-visible failed-job state.
- Enforce team scope where the job operates on scoped data.
- Use database locks or uniqueness constraints when only one instance may act.
- Avoid keeping personal data in broker payloads. Pass identifiers and load authorized data inside the worker.

### 13.4 Callback eligibility

Do not depend on a job that rewrites every due callback once per minute. The work-allocation query should treat a callback as eligible when due_at is at or before the current time. Background jobs may notify, escalate, or summarize due callbacks, but queue correctness remains transactional in PostgreSQL.

## 14. Build, dependency, and release engineering

### 14.1 Proposed application stack

| Layer | Proposed choice | Planning note |
|---|---|---|
| Backend | Current supported Python plus FastAPI | Pin an approved version and upgrade deliberately |
| Templates | Jinja2 | Auto-escaping remains enabled |
| Interaction | HTMX | Self-host, pin, and test CSP behavior |
| Styling | Compiled Tailwind CSS or maintainable custom CSS | No production Play CDN |
| ORM and migrations | SQLAlchemy 2 and Alembic | Review every generated migration |
| Database | PostgreSQL | Source of truth for transactional business state |
| Session cache and queue | Redis | Internal only, authenticated where practical, no business source of truth. PostgreSQL is authoritative for session validity and revocation |
| Jobs | Celery and Celery Beat, subject to ADR | One worker system only |
| Reverse proxy | Caddy | LAN TLS and security headers |
| Remote private access | Tailscale Serve | No public Funnel |
| Testing | pytest, Hypothesis, Playwright, axe-core, and Locust or equivalent | Pin tools in test lockfiles |

Do not use an open lower bound such as FastAPI 0.110+ in production requirements. Use reviewed, locked versions with hashes and a controlled update process.

### 14.2 Suggested repository structure

~~~text
ciphercontact/
  app/
    api/
    auth/
    campaigns/
    imports/
    work/
    suppressions/
    reporting/
    audit/
    templates/
    static/
  migrations/
  tests/
    unit/
    integration/
    authorization/
    concurrency/
    e2e/
    security/
    performance/
  deploy/
    compose/
    caddy/
    tailscale/
    backup/
    monitoring/
  docs/
    architecture/
    decisions/
    operations/
    privacy/
    testing/
  scripts/
  pyproject.toml
  lockfile
  README.md
~~~

### 14.3 Branch and review policy

- Protect the production branch.
- Require review for authentication, authorization, migrations, deployment, retention, suppression, and cryptographic changes.
- Keep commits scoped and traceable to plan requirements.
- Require tests and security checks before merge.
- Never commit secrets, raw imports, database dumps, or production logs.
- Generate an SBOM for release images.
- Record the source commit, image digest, migration revision, and configuration version for every release.

### 14.4 Continuous integration checks

Run on each pull request:

1. Formatting and linting.
2. Static type checks.
3. Unit tests.
4. PostgreSQL and Redis integration tests.
5. Authorization-negative tests.
6. Migration upgrade test from the previous supported schema.
7. Dependency vulnerability scan.
8. Secret scan.
9. Static security analysis.
10. Container build and image scan.
11. Browser smoke tests for critical workflows.
12. Documentation-link and plan-traceability checks.

Block release on:

- A failing required test.
- An unexplained authorization regression.
- A critical or high exploitable vulnerability without written risk acceptance.
- An irreversible migration without a tested recovery procedure.
- A secret-detection finding.
- An image that cannot be traced to a reviewed source commit.

### 14.5 Migration policy

Use expand, migrate, contract:

1. Expand with additive tables or nullable columns.
2. Deploy code capable of reading old and new representations.
3. Backfill in bounded, restartable jobs.
4. Verify counts and invariants.
5. Switch reads and writes.
6. Keep the old representation through an observation window.
7. Contract only after rollback to the old release is no longer required and a current backup has been restored successfully.

Do not combine a destructive schema change with the first deployment of dependent application behavior.

## 15. Production deployment plan

### 15.1 Container networks

Define separate networks:

- edge: Caddy, Tailscale, and web proxy connectivity.
- app: web, worker, scheduler, and approved service communication.
- data: web, worker, PostgreSQL, and Redis.
- backup: backup process, PostgreSQL, and approved outbound target.

Use internal networks where supported. Document every permitted path.

### 15.2 Persistent storage

Persistent volumes include:

- PostgreSQL data.
- Tailscale state.
- Caddy certificate state.
- Short-lived upload quarantine.
- Temporary import workspace.
- Encrypted local backup staging, if required.
- Monitoring state, if local monitoring is selected.

Redis persistence is not a substitute for PostgreSQL. Decide whether session and queue recovery needs Redis AOF, then test restart behavior.

### 15.3 Secret delivery

- Create secrets outside the repository.
- Limit filesystem permissions to the owning service.
- Mount secret files read-only.
- Keep a documented inventory, owner, creation date, rotation date, and recovery process.
- Validate that secrets do not appear in Compose output, process listings, logs, or support bundles.
- Back up encryption keys separately from encrypted data.

### 15.4 Preflight before first production start

Verify and record:

- Approved host and disk encryption.
- BIOS or UEFI controls.
- Ethernet and static address or stable internal DNS.
- Time synchronization.
- Host firewall.
- Private CA trust on pilot laptops.
- Tailscale node identity and grants.
- No public port forwarding.
- No direct 8123, 5432, or 6379 listener.
- Required secrets present with correct permissions.
- Pinned image digests.
- Available disk and memory.
- Backup destination reachable.
- Recovery keys available to authorized custodians.
- Current release manifest.
- Database migration dry run.
- Restore test completed on the release candidate.

### 15.5 First-start sequence

1. Start PostgreSQL and verify health.
2. Apply reviewed migrations using a one-shot migration task.
3. Start Redis and verify health.
4. Start the web service, worker, and scheduler.
5. Start Caddy and verify LAN HTTPS.
6. Start Tailscale and verify the approved private HTTPS route.
7. Bootstrap the first super admin through a one-time local process.
8. Remove or invalidate bootstrap material.
9. Run application smoke tests.
10. Run an encrypted backup.
11. Restore that backup in an isolated test environment.
12. Record go-live evidence and release manifest.

### 15.6 Deployment failure controls

- Do not run Docker volume deletion as part of rollback.
- Do not prune images or volumes broadly on the production host.
- Do not delete the previous release image until the observation window ends.
- Stop if preflight discovers an unknown listener, unencrypted disk, missing backup key, failed migration dry run, or failed restore.
- If migration fails, preserve logs and database state, then restore or roll forward using the approved migration procedure.
- If LAN certificate trust fails, do not bypass TLS warnings for production use.

## 16. Privacy, retention, and data governance

### 16.1 Data inventory

Classify at minimum:

- User identity and account data.
- External workforce identifiers, employment lifecycle state, team membership, reporting lines, acting roles, and delegations.
- Campaign assignment history, allocation rules, and transfer reasons.
- Target policies, target assignments, exemption categories, adjustment events, approvals, appeals, and period snapshots.
- Contact names and phone numbers.
- Campaign-source and consent or lawful-basis evidence.
- Campaign metadata.
- Free-text notes.
- Call outcomes and callbacks.
- DNC suppression evidence.
- Audit metadata.
- Browser and network security metadata.
- Raw uploaded files.
- Staged import rows.
- Reports and metric rollups.
- Backups.

For each category, record purpose, source, owner, access roles, storage, encryption, retention, deletion method, backup treatment, and legal basis.

### 16.2 Candidate retention schedule

These are planning candidates, not final legal decisions.

| Data category | Candidate treatment | Final approval required from |
|---|---|---|
| Raw successful upload | Delete shortly after verified atomic commit | Privacy and operations |
| Failed or abandoned upload | Delete after a short troubleshooting window | Privacy and operations |
| Import staging rows | Minimize or delete after commit and reconciliation | Privacy |
| Workforce bulk-upload file | Delete shortly after verified commit and reconciliation | HR or operations, privacy, and security |
| Workforce import-row staging | Minimize or delete after the approved troubleshooting window | HR or operations and privacy |
| Role, reporting, and campaign assignment history | Retain for an approved accountability and dispute period | HR or operations, legal, and privacy |
| Exemption notes and evidence | Restrict access and retain only as long as the approved operational or legal purpose requires | HR or operations, legal, and privacy |
| Target snapshots and approval history | Retain for the approved performance-review and dispute period | Product, HR or operations, and privacy |
| Active campaign contacts | Retain only while required for the approved campaign purpose | Product and privacy |
| Completed campaign contacts | Archive for a defined period, then delete or de-identify | Legal and privacy |
| Call notes | Shorter retention than core audit data unless justified | Product, legal, and privacy |
| DNC evidence | Retain the minimum fingerprint, source, and effective evidence needed to honor suppression | Legal and privacy |
| Audit events | Retain according to security, accountability, and investigation requirements | Security and legal |
| Authentication security events | Retain for an approved security-monitoring window | Security |
| Backups | Rotate so expired data does not remain indefinitely | IT, privacy, and legal |
| Aggregate metrics | Retain only if de-identified and still necessary | Product and privacy |

Archiving is not a retention policy. Every category needs a time or event trigger and an approved end action.

### 16.3 Data-subject and correction workflows

Plan and test:

- Source and purpose disclosure.
- Access request.
- Correction request.
- Objection to direct marketing.
- Deletion or restriction where applicable.
- DNC confirmation without exposing the master list.
- Search across active data, staging, archives, and backups.
- Legal-hold exceptions.
- Identity verification.
- Response evidence and audit.

### 16.4 Campaign provenance

Do not launch a campaign without:

- Named source.
- Acquisition date or period.
- Purpose.
- Responsible team.
- Lawful-basis or consent reference.
- Vendor or client source details where applicable.
- Country or jurisdiction assumptions.
- Approved contact windows.
- Retention rule.

Missing provenance is a blocking import error, not a warning that a management user can casually ignore.

### 16.5 Notes minimization

- Provide guidance beside the notes field.
- Prohibit unnecessary sensitive personal data.
- Set length limits.
- Encrypt or otherwise strongly protect notes.
- Exclude notes from ordinary agent ranking and broad viewer reporting.
- Define a correction process without silently overwriting history.

### 16.6 Workforce, target, and exemption privacy

- Use a reason category and minimal non-sensitive note instead of detailed medical or personal information.
- Store supporting evidence outside ordinary manager dashboards and expose it only to specifically authorized reviewers.
- Separate operational exemption status from confidential evidence.
- Do not reveal one Agent’s exemption reason to peers or unrelated supervisors.
- Reports show Exempt or Adjusted without disclosing why, unless the viewer is authorized for the case.
- Restrict bulk workforce files because they contain identity, hierarchy, and access-control information.
- Treat role, campaign, target, and exemption exports as sensitive administrative data.
- Define whether HR or operations is the authoritative source for workforce status and who may correct identity mismatches.
- Ensure automated alerts do not place private exemption details in email, chat, or log messages.

## 17. Backup, recovery, and continuity

### 17.1 Recovery objectives

Approve explicit targets before pilot. Candidate initial targets:

- Recovery point objective: no more than one hour of committed work lost.
- Recovery time objective: restore core service within four hours during supported operations.

If the organization accepts different targets, document the consequence and adjust backup frequency, storage, staffing, and tests.

### 17.2 Backup contents

Protect:

- PostgreSQL database.
- Required configuration.
- Release manifest.
- Alembic migration state.
- Caddy and Tailscale state where needed for recovery.
- Encryption-key references and separately stored recovery material.
- Approved persistent files.
- Current runbooks and asset inventory.

Do not back up short-lived raw imports longer than their approved retention.

### 17.3 Backup design

- Use encrypted off-device storage.
- Keep at least one recovery copy protected from ordinary production credentials.
- Use a logical PostgreSQL backup for portability.
- Evaluate continuous WAL archiving if the approved recovery point requires it.
- Sign or hash backup artifacts and verify integrity.
- Alert on missed, incomplete, undersized, unexpectedly large, or unverified backups.
- Rotate according to the approved retention schedule.
- Ensure deletion and retention changes eventually age out of backup sets.

### 17.4 Restore test

At least monthly during production, and before each high-risk release:

1. Provision an isolated clean restore environment.
2. Retrieve the selected backup and required keys.
3. Verify integrity.
4. Restore PostgreSQL.
5. Apply any required release-compatible configuration.
6. Start the matching application release.
7. Run automated integrity and smoke tests.
8. Verify counts, suppressions, memberships, work-item state, and audit continuity.
9. Record actual recovery time and any data gap.
10. Destroy the restore environment securely after evidence is retained.

### 17.5 Failure scenarios to rehearse

- PostgreSQL container corruption.
- Host SSD failure.
- Accidental deletion of a campaign.
- Failed destructive migration.
- Lost Redis state.
- Lost Tailscale node state.
- Expired or unavailable TLS certificate.
- Ransomware or malicious local administrator.
- Power interruption during import commit.
- Backup key unavailable.
- Disk-full condition.

## 18. Observability and operational runbooks

### 18.1 Health checks

Provide:

- Liveness: process can respond.
- Readiness: required dependencies and migration state are valid.
- Database connectivity and transaction check.
- Redis connectivity.
- Worker heartbeat.
- Scheduler singleton status.
- Tailscale connectivity.
- Caddy certificate status.
- Backup freshness.
- Disk, memory, CPU, temperature, and time synchronization.

Do not expose detailed health data to agents or unauthenticated LAN users.

### 18.2 Metrics

System metrics:

- Request count, error rate, and latency percentiles.
- Authentication success and failure rate.
- Work-lease acquisition conflicts and expiries.
- Idempotent replay count.
- Import duration, failure rate, and row counts.
- Background-job queue depth and failure rate.
- Database connections, locks, query latency, and storage.
- Redis memory and evictions.
- Backup age and restore-test age.
- Disk free space and growth.

Business metrics must be defined separately from system health and must not contain raw personal data in metric labels.

### 18.3 Alerts

Alert an accountable person for:

- Service unavailable.
- Repeated login abuse.
- Cross-scope authorization denial spike.
- DNC transaction failure.
- Duplicate-work invariant violation.
- Import worker repeatedly failing.
- Scheduler missing or duplicated.
- Backup missed or restore evidence expired.
- Disk-space threshold breached.
- Database corruption or replication or WAL failure if used.
- Tailscale or certificate expiry.
- Unexpected outbound network activity.
- Audit copy or tamper check failure.

### 18.4 Required runbooks

- Start, stop, and health verification.
- Release deployment.
- Migration failure.
- Application rollback.
- Backup and restore.
- Disk-full response.
- User disable and session revocation.
- Lost or compromised privileged credential.
- Tailscale key or node compromise.
- TLS or private-CA failure.
- DNC enforcement incident.
- Suspected data exfiltration.
- Malicious upload.
- Lost host or SSD replacement.
- Retention run and legal hold.
- Incident evidence preservation.
- Pilot-user support and escalation.

Each runbook must state owner, prerequisites, commands or UI steps, validation, failure branches, rollback, and evidence to retain.

## 19. Verification strategy

### 19.1 Test-data rules

- Use synthetic phone numbers reserved for examples or structurally valid non-live fixtures.
- Do not use real campaign exports in automated tests.
- Generate teams, users, contacts, and notes specifically for test scenarios.
- Keep performance data reproducible through a seeded generator.
- Mark test fixtures clearly so they cannot be imported into production accidentally.

### 19.2 Unit tests

Cover:

- Phone parsing and normalization.
- Phone fingerprint generation.
- Disposition semantic effects.
- State-transition validation.
- Lease expiry calculation.
- Callback eligibility.
- Retention eligibility.
- Metric formulas.
- Redaction.
- Permission predicates.
- Idempotency-key behavior.
- Effective-dated role and reporting-line evaluation.
- Campaign-assignment overlap and eligibility.
- Campaign allocation priority and percentage validation.
- Transfer cutoff behavior.
- Target proration, rounding, ramp-up, and attribution.
- Full, proportional, fixed, and excluded-hours adjustments.
- Overlapping-exemption prevention.
- Closed-period snapshot and reopen versioning.
- Bulk-row identity, action, and privilege classification.

Use property-based testing for phone inputs, parser boundaries, state transitions, and retry sequences.

### 19.3 Integration tests

Run against real PostgreSQL and Redis versions matching production:

- Migrations from each supported prior schema.
- Session creation, rotation, expiry, and revocation.
- CSRF rejection.
- Team and role enforcement.
- Acting-role activation and expiry.
- Delegation limits and automatic expiry.
- Bulk workforce import preview, approval, atomic commit, safe retry, and compensating reversal.
- Campaign assignment creation, transfer, active-lease handling, callback handoff, and target proration.
- Exemption request, endorsement, approval, rejection, reversal, and self-approval prevention.
- Performance-period calculation, closure, reopening, and snapshot history.
- Import commit transaction and rollback.
- DNC revalidation during commit.
- Work acquisition and completion.
- Callback priority.
- Background-job retry and deduplication.
- Retention selection and protected deletion.
- Audit-event append behavior.

SQLite is not an acceptable substitute for PostgreSQL transaction and locking tests.

### 19.4 Authorization and privacy tests

- Attempt every management-role action outside its authorized scope.
- Attempt every Manager action outside the assigned portfolio.
- Attempt every Team Leader action against another Team Leader’s teams.
- Attempt every Team Captain action against a non-direct Agent.
- Attempt privileged action through an expired acting role or delegation.
- Attempt to use a stronger role held in another scope.
- Attempt every agent action against another agent’s lease.
- Attempt viewer access to raw contacts, notes, imports, and DNC entries.
- Remove membership between read and write operations.
- Disable an account with active sessions and leases.
- Verify no sensitive value appears in application logs, access logs, metrics, traces, error pages, URLs, or browser cache.
- Verify aggregate reports cannot be manipulated to reveal an individual contact through small groups.

### 19.5 Concurrency tests

At minimum:

- Fifty agents request work simultaneously from the same shared pool.
- No work item receives more than one active lease.
- Two submits race against the same lease.
- The same idempotency key is retried after a network timeout.
- Different idempotency keys submit conflicting results.
- A lease expires during submission.
- DNC suppression races with work acquisition.
- Campaign pause races with work acquisition.
- Campaign transfer races with Agent completion.
- Import commit races with a new suppression entry.
- Worker restart occurs during import parsing and commit preparation.
- Campaign transfer races with work acquisition, callback completion, and target calculation.
- Role expiry races with approval or bulk commit.
- Exemption approval races with period close.
- Two approvers act on the same exemption version.
- Workforce bulk commit races with a user disable or role change.
- Mass outage adjustment races with individual exemption approval.

### 19.6 Upload-abuse tests

- Wrong extension and valid signature.
- Valid extension and wrong signature.
- Oversized compressed file.
- Excessive expanded ZIP size.
- Excessive rows, columns, or cell length.
- Corrupt XLSX structure.
- Macro-enabled workbook.
- External links.
- Formula payloads.
- Path traversal filename.
- Duplicate filename.
- Parser timeout.
- Unicode and right-to-left control characters.
- HTML and script content in cells.
- CSV encoding variations.
- Workforce file attempts implicit deletion by omitting existing users.
- Workforce file contains password or reset-code columns.
- Workforce file attempts out-of-scope Manager or Team Leader creation.
- Duplicate external workforce ID with different login or display details.
- Circular reporting line or a user supervising themselves.
- Overlapping campaign assignments with invalid allocation percentages.

### 19.7 Browser and UX tests

Use Playwright or an equivalent tool for:

- Login and logout.
- Agent lease, copy, disposition, callback, skip, and session expiry.
- Manager or authorized Team Leader campaign import and commit.
- Manager, Team Leader, and Team Captain dashboards and scope.
- Bulk workforce upload, preview, high-risk approval, commit, and safe result report.
- Campaign assignment, transfer preflight, callback handoff, and effective cutoff.
- Agent target display, exemption request, endorsement, approval, and appeal.
- Performance-period close and authorized reopen.
- DNC correction authorization.
- Error, empty, loading, and offline-network states.
- Browser back, refresh, duplicate tab, and repeated submit.
- 1366 x 768 and 1920 x 1080.
- Windows-equivalent scaling checks at 100%, 125%, and 150%.
- Keyboard-only operation.
- Screen-reader labels and announcements.
- axe-core accessibility checks, followed by manual accessibility review.

### 19.8 Performance and soak tests

Test with an approved production-like dataset:

- Fifty concurrent agents.
- Ten thousand-row import.
- Fifty thousand active campaign contacts.
- Approved workforce volume with current and historical assignments.
- A representative bulk workforce import, including role and campaign rows.
- Approved audit-retention volume.
- Concurrent import, agent activity, reporting, and backup.
- Eight-hour soak with normal workload.

Measure p50, p95, and p99 rather than only averages.

Candidate performance gates:

| Scenario | Candidate gate |
|---|---|
| Login | p95 under 500 ms on the office LAN |
| Lease next work item | p95 under 250 ms |
| Complete outcome | p95 under 300 ms |
| Management campaign page | p95 under 1 second for normal authorized scope |
| Ten thousand-row import | Preview available within 60 seconds on approved hardware |
| Workforce import | Approved workforce-volume preview and commit meet the Phase 0 target with no partial privilege change |
| DNC suppression | Effective transaction completes before another eligible lease can be returned |
| Fifty-agent concurrency | Zero duplicate active leases and acceptable latency |

Finalize gates after Phase 0 and hardware benchmarking.

### 19.9 Security tests

- Dependency and container scanning.
- Secret scanning.
- Static application-security checks.
- Authenticated dynamic scan in staging.
- Manual review of auth, authorization, import, DNC, and audit paths.
- Manual review of role elevation, acting access, bulk deactivation, campaign transfer, exemption approval, and closed-period reopening.
- Session fixation, CSRF, stored XSS, injection, path traversal, file-parser abuse, and IDOR tests.
- Host firewall and exposed-port verification.
- Tailscale-grant negative tests.
- Backup confidentiality and key-separation review.

### 19.10 Operational tests

- Cold boot after power loss.
- Controlled host reboot.
- PostgreSQL restart under load.
- Redis restart.
- Worker and scheduler restart.
- Certificate renewal.
- Tailscale state recovery.
- Disk-space alert and recovery.
- Encrypted backup and clean-host restoration.
- Application rollback after a failed release.

## 20. Phased implementation plan

Durations are planning bands for a small experienced team. Re-estimate after Phase 0. A single developer performing product, security, QA, and operations work will require more time.

### Phase 0: Definition, governance, and desktop prototype

**Indicative duration:** 1 to 2 weeks

#### Objective

Resolve the decisions that would otherwise cause architecture, compliance, or UX rework.

#### Prerequisites

- Product owner available.
- Manager, Team Leader, Team Captain, and Agent representatives available.
- IT owner identified.
- Privacy or legal reviewer identified.

#### Step-by-step work

1. Approve the permanent project location and repository ownership.
2. Confirm Manager, Team Leader, Team Captain, Agent, Super Admin, and Viewer counts, scopes, appointment rules, and segregation of duties.
3. Confirm operating jurisdiction, campaign provenance requirements, DNC handling, contact windows, and retention ownership.
4. Inventory actual work laptops, browser management, screen sizes, scaling, and network.
5. Select and document the production host operating system.
6. Decide LAN certificate trust and Tailscale topology.
7. Create a data inventory and preliminary data-flow diagram.
8. Run a lightweight threat-model workshop.
9. Define connected, conversion, sale, callback, no-answer, and DNC as distinct business concepts.
10. Decide exclusive or simultaneous campaign assignments, allocation rules, transfer cutoffs, and callback ownership.
11. Define target metrics, periods, calendars, proration, rounding, ramp-up, and campaign attribution.
12. Define exemption request, endorsement, approval, escalation, privacy, mass-adjustment, appeal, period-close, and reopen rules.
13. Define joiner, mover, leaver, rehire, acting-role, delegation, and bulk-upload workflows.
14. Approve immutable external workforce identity and template ownership.
15. Prototype Agent, Team Captain, Team Leader, and Manager desktop workflows.
16. Test the prototype with at least two Agents and one representative of every management role.
17. Create ADRs for all resolved blocking decisions.
18. Approve release gates and pilot size.
19. Re-estimate the remaining phases.

#### Deliverables

- Approved scope and non-goals.
- Role and authorization matrix.
- Organizational hierarchy, reporting-line, acting-role, and delegation rules.
- Campaign assignment, transfer, and callback-handoff rules.
- Target policy, exemption, period-close, and appeal rules.
- Versioned bulk-workforce templates and approval matrix.
- Data inventory and provenance requirements.
- Threat model.
- DNC and retention policy decisions.
- Desktop wireframes and keyboard map.
- Network and host ADRs.
- Test and release-gate baseline.
- Updated delivery estimate.

#### Resources

- Product owner.
- Manager, Team Leader, and Team Captain representatives.
- Two representative agents.
- HR or workforce-operations representative where applicable.
- Senior full-stack engineer.
- IT or network engineer.
- Security reviewer.
- Privacy or legal reviewer.
- Figma, diagrams.net, or equivalent prototyping tool.

#### Likely failure points

- Treating archiving as indefinite retention.
- Assuming all work laptops can trust a private certificate.
- Failing to distinguish connection from conversion.
- Designing around a phone viewport despite laptop use.
- Leaving DNC enforcement to management discretion.
- Storing one mutable role or campaign ID on users.
- Allowing transfers to rewrite historical attempts.
- Treating exemptions as a zero target or zero performance result.
- Collecting sensitive medical detail in exemption requests.
- Allowing a bulk file to imply deletion or privilege elevation.
- Starting code before repository and host decisions are approved.

#### Success criteria

- D-01 through D-08 are resolved and recorded.
- Prototype is usable at 1366 x 768 and 150% scaling.
- Users can complete the agent flow without hidden controls or accidental shortcuts.
- Legal and privacy owners accept the DNC and provenance approach.
- Threat model has named owners for every high-risk mitigation.
- Role, campaign, target, exemption, and bulk-import decision tables are approved.
- Prototype demonstrates campaign transfer preflight and separate base, adjusted, and actual target values.

#### Verification evidence

- Signed decision log.
- Prototype test notes.
- Role matrix.
- Data-flow diagram.
- Threat-model report.
- Phase re-estimate.

#### Non-breaking and rollback approach

No production system exists yet. Keep all outputs as reviewed documents and prototypes. Do not create production accounts or ingest real data.

### Phase 1: Secure platform foundation

**Indicative duration:** 2 to 3 weeks

#### Objective

Create a deployable skeleton with secure identity, scoped authorization, audit events, migrations, CI, and private HTTPS.

#### Step-by-step work

1. Scaffold the dedicated repository.
2. Configure locked dependencies, linting, typing, tests, and secret scanning.
3. Define environment configuration and secret-file loading.
4. Implement initial PostgreSQL schema for users, teams, memberships, scoped roles, reporting assignments, delegations, sessions, and audit events.
5. Implement opaque sessions, CSRF, login, logout, expiry, and revocation.
6. Implement local Argon2id auth or approved OIDC.
7. Add privileged-role MFA where local auth is used.
8. Implement server-side authorization helpers and default-deny route policy.
9. Add Caddy LAN HTTPS and Tailscale Serve in a non-production environment.
10. Create Compose services, networks, health checks, non-root execution, and pinned image references.
11. Add structured redacted logs and protected health endpoints.
12. Add initial encrypted backup and restore automation.
13. Create CI checks and release manifest generation.
14. Run security and exposed-port tests.
15. Implement role and delegation effective-time checks and automatic expiry.
16. Implement privilege-change session rotation and self-approval prevention.

#### Deliverables

- Running authenticated skeleton.
- Team-scoped access model.
- Manager, Team Leader, Team Captain, Agent, acting-role, and delegation scope.
- Initial migration set.
- Append-only audit foundation.
- Private HTTPS paths.
- CI pipeline.
- Backup and restore proof.
- Foundation runbooks.

#### Resources

- Senior backend or full-stack engineer.
- IT or network engineer.
- Security reviewer.
- Test PostgreSQL, Redis, Caddy, and Tailscale environment.
- Managed pilot laptop for certificate and browser testing.

#### Likely failure points

- Mixing server sessions and JWT.
- Trusting route parameters for team scope.
- Caddy or Tailscale exposing the app port unexpectedly.
- Secure cookies failing because a path still uses HTTP.
- Session invalidation leaving active leases possible later.
- Expired acting access remaining usable.
- A higher role in one scope leaking into another scope.
- Secrets appearing in Compose inspection or logs.
- A backup that cannot be restored.

#### Success criteria

- All authenticated traffic is HTTPS.
- No direct host listener exists for app, PostgreSQL, or Redis.
- Cross-team authorization-negative tests pass.
- Cross-portfolio, cross-Team Leader, cross-Team Captain, expired-role, and self-approval tests pass.
- Session rotation, expiry, revocation, and CSRF tests pass.
- Privileged MFA or approved OIDC is operational.
- A clean environment restores successfully from the foundation backup.
- CI blocks secrets and required-test failures.

#### Verification evidence

- Port-scan output.
- TLS and cookie inspection.
- Authorization test report.
- Restore log.
- Image digest and SBOM.
- Security-review sign-off.

#### Non-breaking and rollback approach

- Use additive migrations only.
- Keep the previous working image and migration revision.
- Snapshot the test database before migration tests.
- Roll back application images without deleting volumes.

### Phase 2: Data model and safe import pipeline

**Indicative duration:** 2 to 4 weeks

#### Objective

Create canonical contact, campaign, suppression, import, and work-item structures, then deliver staged transaction-safe imports.

#### Step-by-step work

1. Implement campaigns, campaign-team assignments, campaign-user assignments, target policies, performance periods, target assignments, exemption requests, adjustments, snapshots, disposition definitions, contacts, campaign contacts, batches, work items, suppression entries, import jobs, rows, and decisions.
2. Add database constraints and indexes for required invariants.
3. Implement protected phone storage and exact-match fingerprinting.
4. Create CSV and XLSX quarantine and validation.
5. Implement bounded parsing in a worker.
6. Add phone parsing using a maintained library and explicit default region.
7. Implement metadata allowlists and campaign mapping.
8. Implement in-file, campaign, authorized-history, and DNC classification.
9. Build the desktop import preview and decision UI.
10. Implement decision versioning.
11. Implement commit-time revalidation.
12. Commit contacts, campaign contacts, batches, work items, and audit evidence atomically.
13. Add idempotency and safe retry.
14. Add cleanup and import-expiry jobs.
15. Test malicious files, partial failures, and concurrency.
16. Implement campaign-assignment overlap, eligibility, priority, allocation, and effective-time constraints.
17. Implement target-policy versioning, target proration, and immutable adjustment rules.

#### Deliverables

- Revised schema and migrations.
- Campaign mobility and target-management schema.
- Secure import pipeline.
- Import preview and decision flow.
- Atomic commit and launch prerequisites.
- Suppression filtering.
- Import audit and cleanup.

#### Resources

- Backend engineer.
- Frontend or full-stack engineer.
- QA engineer.
- Privacy reviewer for metadata and retention.
- Representative clean, dirty, duplicate, and hostile synthetic files.

#### Likely failure points

- Treating a preview as current at commit time.
- Partial campaign creation after an error.
- Exposing another team’s campaign history through duplicate messages.
- Keeping raw uploads indefinitely.
- Unbounded XLSX expansion or cell content.
- Incorrect phone assumptions based on hardcoded prefixes.
- Encryption-key loss making recovery impossible.
- Invalid campaign overlap or target attribution corrupting later performance reports.

#### Success criteria

- Repeating commit with the same idempotency key has one effect.
- A new DNC entry created after preview is excluded during commit.
- Any commit failure rolls back all business inserts.
- Cross-team duplicate information is not disclosed.
- Hostile upload tests pass.
- Raw quarantine cleanup follows policy.
- Ten thousand rows meet the approved preview target.
- Campaign and target constraints reject invalid overlap, missing eligibility, and stale versions.

#### Verification evidence

- Transaction and rollback test report.
- DNC race test.
- Upload-security test corpus results.
- Query plans for critical indexes.
- Retention cleanup evidence.
- Key-recovery test.

#### Non-breaking and rollback approach

- Keep imports in draft until explicit commit.
- Use additive schema changes.
- Keep parser version and import manifest for diagnosis.
- Never delete original uploader data outside the approved quarantine policy.
- Feature-flag campaign launch until the phase exit gate passes.

### Phase 3: Agent workflow vertical slice

**Indicative duration:** 2 to 3 weeks

#### Objective

Deliver the complete desktop agent cycle with transactional leasing, outcomes, callbacks, skips, DNC, and personal metrics.

#### Step-by-step work

1. Implement queue eligibility and ordering.
2. Implement atomic work-item leasing.
3. Add lease renewal and expiry.
4. Build the two-column desktop agent page.
5. Implement focus-safe keyboard behavior.
6. Implement contact copy and watermark deterrence.
7. Build disposition conditional fields.
8. Implement idempotent work completion.
9. Implement explicit DNC suppression transaction.
10. Implement skip reasons and review routing.
11. Implement callback scheduling, due priority, and escalation.
12. Add approved personal metrics from immutable attempts.
13. Add session-expiry warning and safe state clearing.
14. Run concurrency, accessibility, and browser-behavior tests.
15. Enforce campaign-assignment eligibility in every lease.
16. Display campaign identity, base target, adjusted target, actual output, and exemption status separately.
17. Add Agent self-service exemption request and status view.

#### Deliverables

- Complete agent workspace.
- Work lease service.
- Attempt history.
- Callback service.
- Immediate DNC enforcement.
- Agent metrics.
- Assignment-aware target and exemption view.
- Agent help and shortcut documentation.

#### Resources

- Full-stack engineer.
- QA engineer.
- UX or accessibility reviewer.
- Two or more representative agents.
- Production-like laptop and network conditions.

#### Likely failure points

- Two agents receiving the same contact.
- A browser retry recording two outcomes.
- Lease expiry losing valid work.
- Escape, Enter, Ctrl+C, or Ctrl+N causing accidental behavior.
- DNC submission failing after the next record is allocated.
- Free-text notes containing unnecessary sensitive data.
- Metrics presenting self-reported duration as verified call duration.
- Agent receiving work before assignment start or after assignment end.
- Exempt Agent displayed as a failed or zero-performing Agent.

#### Success criteria

- Fifty-agent concurrency test produces zero duplicate active leases.
- Retry tests produce one attempt and one business transition.
- DNC race test prevents any later lease.
- The core action fits at 1366 x 768.
- Keyboard-only and accessibility tests pass.
- Agent pilot users complete the workflow without instruction-related errors.
- Assignment cutoff and target-display tests pass.

#### Verification evidence

- Concurrency report.
- Idempotency report.
- DNC transaction trace.
- Playwright recordings or screenshots.
- Accessibility report.
- Agent usability notes.

#### Non-breaking and rollback approach

- Keep the agent feature behind a pilot-team flag.
- Use lease expiry rather than destructive manual cleanup.
- Preserve immutable attempts during rollback.
- If the new UI fails, pause new leases and return to the prior pilot build without deleting work state.

### Phase 4: Management hierarchy, workforce, targets, viewer, and administration

**Indicative duration:** 3 to 5 weeks

#### Objective

Deliver Manager, Team Leader, Team Captain, workforce lifecycle, bulk upload, campaign mobility, target and exemption management, safe reporting, viewer scope, and administrative controls.

#### Delivery note (v0.3)

This phase bundles at least five substantial subsystems, and the 3-to-5-week band is not credible for that surface area. Split and re-estimate it as:

- Phase 4A: Manager, Team Leader, and Team Captain dashboards, plus manual (non-bulk) user, membership, role, reporting-line, and campaign assignment. Includes viewer scope and protected audit search.
- Phase 4B: Staged bulk-workforce import, high-risk approval, atomic commit, and compensating reversal.
- Phase 4C: Target policies, assignments, proration, exemptions, adjustments, performance periods, and snapshots. This is effectively an embedded performance-management module and is the strongest candidate to defer until after the first pilot proves the core call-work loop.

Re-estimate each sub-phase after Phase 0 rather than treating the original band as a commitment.

#### Step-by-step work

1. Build separate Manager, Team Leader, and Team Captain dashboards.
2. Add team membership, reporting line, acting role, delegation, joiner, mover, leaver, and rehire lifecycle.
3. Add versioned bulk templates for users, memberships, roles, reporting lines, campaign assignments, targets, and explicit deactivation.
4. Add workforce upload quarantine, validation, preview, high-risk approval, atomic commit, and compensating reversal.
5. Add campaign-team and campaign-user assignment management.
6. Add campaign transfer preflight for active leases, callbacks, target effects, schedule, and destination capacity.
7. Add assignment modes, priority, allocation percentage, effective time, and callback-only coverage.
8. Add approved target policy creation and versioning.
9. Add target assignment, proration, ramp-up, rounding, calendar, and campaign attribution.
10. Add exemption request, endorsement, approval, rejection, reversal, escalation, and appeal.
11. Add mass adjustment for verified outage, insufficient data, or business interruption.
12. Add performance-period review, close, snapshot, and authorized reopen.
13. Add batches, shared pools, pause, resume, and reassignment.
14. Add callback oversight and overdue escalation.
15. Add DNC statistics and privileged correction flow.
16. Add skip and attempt review queues.
17. Add aggregate campaign, target, exemption-status, and Agent reports without exposing confidential reasons.
18. Implement Viewer report scopes.
19. Add protected audit-event search.
20. Add retention-run preview, approval, and Super Admin configuration with reauthentication.
21. Test cross-portfolio, cross-team, cross-role, expired-assignment, self-approval, bulk privilege, and closed-period attacks.

#### Deliverables

- Manager, Team Leader, and Team Captain dashboards.
- Workforce identity, role, reporting, acting, delegation, and lifecycle management.
- Staged bulk-workforce import.
- Campaign assignment and transfer management.
- Target policies, assignments, exemptions, adjustments, snapshots, and appeals.
- Work assignment controls.
- Callback and review queues.
- Aggregate reports.
- Viewer role.
- Admin and audit surfaces.

#### Resources

- Full-stack engineer.
- QA engineer.
- Team Captain representatives.
- Manager and Team Leader representatives.
- HR or workforce-operations representative where applicable.
- Security reviewer.
- Privacy reviewer for reports and small-group disclosure.

#### Likely failure points

- Managers or Team Leaders viewing outside their assigned scope.
- Team Captains viewing another direct team through filters or report parameters.
- Reassignment overriding a completed attempt.
- Campaign transfer orphaning callbacks or applying the wrong target.
- Acting roles or delegations failing to expire.
- Bulk import creating duplicates, implied deactivation, or unauthorized privilege.
- Exemption self-approval, double reduction, sensitive-data leakage, or closed-period rewrite.
- Viewer reports leaking contact-level data.
- Small aggregate groups enabling inference.
- Team Captain correction weakening explicit DNC without proper authority.
- Bulk action applying to hidden or stale selection.

#### Success criteria

- Authorization-negative matrix passes for all routes.
- Bulk actions show exact scope and reject stale versions.
- Viewer cannot access raw contacts, notes, imports, or DNC entries.
- DNC correction requires privileged authorization, reason, and audit.
- Reports reconcile to immutable attempts.
- Management users can perform authorized daily operations without database access.
- Manager, Team Leader, Team Captain, and Agent capability tests match the approved matrix.
- Campaign transfers preserve history and resolve every active lease, callback, and target impact.
- Bulk imports are idempotent, atomic by transaction group, and cannot imply deletion.
- Exempt reports preserve actual work and display base, adjusted, and effective target separately.
- Closed-period reopening creates a new version and full audit history.

#### Verification evidence

- Authorization test report.
- Report-reconciliation results.
- Manager, Team Leader, and Team Captain UAT notes.
- Manager and Team Leader UAT notes.
- Bulk-import reconciliation and reversal results.
- Transfer and callback-handoff evidence.
- Target, exemption, and period-close calculation report.
- Audit samples.
- Privacy review.

#### Non-breaking and rollback approach

- Feature-flag viewer and sensitive admin tools separately.
- Feature-flag bulk workforce, campaign transfer, target management, and exemption approval separately.
- Use optimistic versions for bulk operations.
- Use compensating effective-dated events instead of deleting role, transfer, target, or exemption history.
- Preserve prior report definitions until new reports reconcile.
- Pause a faulty management feature without pausing agent disposition recording.

### Phase 5: Production hardening and controlled pilot

**Indicative duration:** 2 to 4 weeks, including observation

#### Objective

Prove the system under realistic security, load, recovery, and human workflows before full rollout.

#### Step-by-step work

1. Build the release candidate from a reviewed commit.
2. Complete container, dependency, dynamic, and manual security review.
3. Run full concurrency and performance tests on production-like hardware.
4. Run eight-hour soak and resource-growth tests.
5. Verify LAN certificate distribution and Tailscale grants.
6. Complete cold-boot, service-restart, disk-alert, backup, and restore drills.
7. Train Managers, Team Leaders, Team Captains, Agents, IT support, and the incident owner.
8. Load only an approved pilot dataset.
9. Pilot with 10 to 20 users including every business role.
10. Monitor errors, workflow abandonment, DNC events, duplicate-work signals, campaign-transfer conflicts, bulk-import errors, exemption approvals, target-calculation exceptions, latency, and support requests.
11. Resolve pilot findings through controlled releases.
12. Obtain product, security, privacy, IT, and operations go-live approval.

#### Deliverables

- Release candidate.
- Security and performance reports.
- Restore evidence.
- Training materials.
- Completed runbooks.
- Pilot report.
- Risk acceptance.
- Go-live decision.

#### Resources

- Product owner.
- Technical lead.
- QA and security reviewers.
- IT or network engineer.
- Privacy or legal reviewer.
- 10 to 20 pilot users.
- Production-like host and backup target.

#### Likely failure points

- Treating feature completion as production readiness.
- Bypassing TLS warnings during pilot.
- Testing backups without restoring them.
- Expanding pilot scope before high-risk findings are closed.
- Unclear incident ownership.
- Host sleep, thermal, disk, or update behavior interrupting service.
- Unsupported browser or laptop management assumptions.

#### Success criteria

- No open critical security, DNC, authorization, data-integrity, or recovery defect.
- Performance gates pass on approved hardware.
- Restore meets the approved recovery objective.
- Pilot produces no duplicate leases or unresolved DNC failure.
- Pilot campaign moves preserve callbacks, history, and correct target attribution.
- Pilot bulk import, exemption, and period-close workflows reconcile to their source events.
- Support and incident owners can execute runbooks.
- Users complete core workflows at acceptable error and completion rates.
- Formal go-live approval is recorded.

#### Verification evidence

- Signed release checklist.
- Security report.
- Load and soak report.
- Restore report.
- Pilot metrics and feedback.
- Training attendance.
- Accepted risk register.

#### Non-breaking and rollback approach

- Start with one pilot team.
- Maintain the prior tested image and database backup.
- Pause new leases before rollback.
- Preserve attempts and audit events.
- Roll back the application image first.
- Restore the database only when the migration or data state requires it and the incident lead approves.
- Never delete production volumes as a normal rollback step.

### Phase 6: Validated analytics

**Indicative duration:** 2 to 3 weeks after pilot approval

#### Objective

Add metrics that have defined meaning, reliable source data, and an agreed operational use.

#### Step-by-step work

1. Approve a metric dictionary.
2. Separate connected, qualified, conversion, and sale.
3. Identify lead-source and assignment factors that affect comparisons.
4. Define whether self-reported call duration is useful enough to display.
5. Build reproducible rollups from immutable attempts.
6. Add campaign velocity, callback success, and completion forecasting.
7. Add privacy thresholds for small groups.
8. Validate every dashboard against source queries.
9. Review rankings and remove unsupported performance conclusions.
10. Add metric versioning when formulas change.
11. Report actual output, base target, adjusted target, effective target, and exemption evaluation status separately.
12. Add campaign-transfer and multi-campaign attribution rules to every affected metric.

#### Success criteria

- Every displayed metric has a definition, owner, source query, refresh schedule, and limitation.
- Dashboard values reconcile to source events.
- Viewers receive only authorized aggregates.
- Rankings do not present lead mix as individual performance.
- Formula changes are versioned and auditable.

#### Non-breaking and rollback approach

- Keep analytics read-only.
- Feature-flag each dashboard.
- Preserve old formula versions until reconciliation is complete.
- Never let a reporting failure block core agent operations.

### Phase 7: AI decision gate

**Duration:** Not estimated until approved

AI remains out of scope unless all of the following exist:

- Clear business problem and measurable baseline.
- Sufficient representative data.
- Ground-truth outcome definition.
- Privacy and legal approval.
- Bias and fairness assessment.
- Explainability appropriate to the use.
- Human review and override.
- Monitoring for drift and harmful feedback loops.
- Safe fallback with AI disabled.
- No automatic inference of DNC or consent.
- No automated employment or disciplinary decision.

Lead scoring and smart assignment require controlled evaluation because routing high-scoring leads to historically successful agents can create a self-reinforcing loop. Sentiment analysis of short notes should be rejected unless a later study proves a specific safe benefit.

## 21. Rollout and rollback strategy

### 21.1 Rollout stages

1. Developer environment with synthetic data.
2. Automated integration environment.
3. Staging on production-like hardware.
4. Internal technical smoke test.
5. Pilot Manager, Team Leader, Team Captain, and two Agents.
6. Pilot team of 10 to 20 users.
7. One production team.
8. Additional teams only after an observation gate.

### 21.2 Feature flags

Suggested independent flags:

- campaign_import_enabled
- campaign_launch_enabled
- shared_pool_enabled
- callbacks_enabled
- viewer_enabled
- retention_execution_enabled
- analytics_enabled
- ai_enabled, permanently false for MVP

Flags must be server-enforced and audited. They are not a substitute for authorization.

### 21.3 Release preflight

Before every production release:

- Confirm approved commit and image digest.
- Confirm migration revision and dry-run result.
- Confirm current backup and recent restore evidence.
- Confirm disk, memory, certificates, Tailscale, and secret availability.
- Confirm no unresolved critical incident.
- Confirm rollback image and instructions.
- Pause high-risk background jobs if required.
- Notify affected users of a maintenance window.

### 21.4 Rollback triggers

Rollback or pause the affected feature when:

- Cross-team access is possible.
- DNC enforcement fails.
- Duplicate active leases occur.
- Migration corrupts or loses data.
- Authentication or session revocation fails.
- Raw personal data appears in logs or reports.
- Error rate or latency breaches the agreed threshold for a sustained period.
- Backup or recovery assumptions are invalidated.

### 21.5 Rollback order

1. Stop new campaign launches or new leases if needed.
2. Preserve logs, metrics, audit events, and database state.
3. Disable the affected feature flag.
4. Roll back to the previous application image if schema remains compatible.
5. Run integrity checks.
6. Restore the database only when required and authorized.
7. Verify DNC, membership, lease, and attempt invariants.
8. Reopen service gradually.
9. Complete an incident review before reattempting the release.

## 22. Risk register

| Risk | Likelihood | Impact | Prevention and detection | Trigger or response |
|---|---|---|---|---|
| Cross-team data exposure | Medium | Critical | Explicit membership model, shared authorization service, negative tests | Disable affected route, revoke sessions, investigate access |
| DNC request not enforced | Medium | Critical | Transactional global suppression, race tests, alerts | Pause new leases and campaign launches until reconciled |
| Duplicate work allocation | Medium | High | Row locks, leases, constraints, concurrency tests | Pause shared pool, inspect work-item state |
| Duplicate outcome after retry | Medium | High | Idempotency keys and unique constraints | Reconcile attempts and block affected endpoint |
| Malicious spreadsheet | Medium | High | Quarantine, limits, generated names, parser isolation, scanning | Quarantine job, disable imports if systemic |
| Raw data in logs | Medium | High | Redaction library, tests, restricted logging | Stop affected logging, rotate or purge under incident process |
| Private CA not trusted on laptops | Medium | High | Pilot device inventory and managed certificate deployment | Stop rollout, do not allow TLS bypass |
| Tailscale misconfiguration | Low to medium | High | Grants, negative tests, no Funnel, node monitoring | Revoke node or key and disable remote path |
| Host disk failure | Medium | High | SSD monitoring, encrypted off-device backup, restore drills | Replace host and execute restore runbook |
| Backup unusable | Medium | Critical | Scheduled clean-host restores and alerts | Block high-risk releases and repair backup chain |
| Server sleeps or reboots unexpectedly | Medium | Medium to high | Power settings, update window, UPS, boot tests | Run startup and integrity checks |
| Disk fills with audit or uploads | Medium | High | Quotas, retention, free-space alerts | Pause imports, preserve DB, free approved space |
| Metrics drive unfair conclusions | Medium | High | Metric dictionary, context, privacy review, no AI discipline | Hide affected metric and correct guidance |
| AI creates feedback loop | High if enabled | High | Keep disabled, separate approval gate | Disable AI and revert to deterministic assignment |
| Scope grows before foundation is complete | High | High | Phase gates and product-owner approval | Freeze new features until gate closes |
| Key loss prevents recovery | Low to medium | Critical | Separate key custody and recovery drills | Invoke key-recovery and incident process |
| Privileged insider alters evidence | Low | High | Append-only audit, protected copy, access monitoring | Preserve external audit copy and investigate |
| Raw-data retention exceeds policy | Medium | High | Automated retention reports and approved deletion | Pause imports if cleanup is failing |
| Phone normalization causes false matches | Medium | High | Maintained library, original value, review path, test corpus | Quarantine ambiguous rows |
| Agent endpoint is compromised | Medium | High | Managed device controls, least privilege, anomaly detection | Revoke sessions, disable account, investigate views |
| Role scope leaks across portfolio or team | Medium | Critical | Effective-dated scoped roles, explicit capabilities, negative tests | Revoke assignment and sessions, disable affected capability |
| Acting role or delegation fails to expire | Medium | High | Request-time effective checks, expiry job, alerts, tests | Revoke delegation and inspect actions after expiry |
| Campaign transfer orphans callbacks | Medium | High | Transfer preflight, required callback treatment, handoff queue | Pause transfer feature and reconcile callbacks |
| Campaign move rewrites historical performance | Low to medium | High | Immutable attempts and effective-dated assignments | Restore report from events and correct with compensating records |
| Overlapping campaign allocation exceeds capacity | Medium | Medium to high | Percentage and schedule constraints, preview, Manager policy | Block new leases from invalid overlap |
| Target formula is ambiguous or changes silently | Medium | High | Versioned metric dictionary and policies | Hide affected metric and recalculate under approved version |
| Exemption creates unfair or zero performance result | Medium | High | Separate actual, base, adjusted, effective, and status fields | Reopen affected period and create corrected snapshot |
| Exemption stores sensitive personal detail | Medium | High | Reason categories, minimal notes, restricted evidence | Restrict access, remediate stored data, review notifications |
| Self-approval or approval conflict | Medium | High | Segregation checks and step-up authentication | Reverse decision through audited adjustment |
| Bulk workforce upload elevates privilege | Medium | Critical | Scope validation, high-risk classification, higher approval | Disable import commit, revoke new roles, investigate |
| Bulk upload creates duplicate person | Medium | High | Immutable workforce ID and conflict preview | Quarantine rows and merge only through approved identity process |
| Missing bulk row deactivates user | Low after control | Critical | Explicit action required, no implicit deletion | Block import and restore lifecycle state if defect occurs |
| Closed target period is silently rewritten | Low to medium | High | Immutable snapshots and authorized reopen versions | Freeze reports and reconstruct versions |
| Provisioning or approval stalls because email is unavailable | Medium | Medium to high | In-application inbox as primary channel, offline activation fallback | Switch to in-app inbox, hand activation link through approved offline process |

Risk owners and acceptance dates must be added before the production pilot.

## 23. Resource plan

### 23.1 People

Minimum roles, which may be combined only where conflicts are managed:

- Product owner.
- Call-center operations lead.
- Manager representative.
- Team Leader representative.
- Team Captain representative.
- HR or workforce-operations owner for identity, leave, and lifecycle inputs where applicable.
- Senior full-stack engineer.
- QA engineer.
- Security reviewer.
- IT or network engineer.
- Privacy or legal reviewer.
- Production operations owner.
- Representative captains and agents.

### 23.2 Hardware

- Production x86 host with SSD and recommended memory.
- Separate staging or recoverable equivalent.
- Encrypted off-device backup target.
- UPS or tested battery protection.
- Managed Windows test laptop at common resolution and scaling.
- Recovery media and separately stored recovery keys.

### 23.3 Software and services

- Git repository and protected CI.
- Python and dependency lock tooling.
- PostgreSQL.
- Redis.
- FastAPI, SQLAlchemy, Alembic, Jinja2, and HTMX.
- Celery and Celery Beat if approved.
- Caddy.
- Tailscale.
- pytest and Hypothesis.
- Playwright and axe-core.
- Locust or equivalent load tool.
- Dependency, secret, SAST, and container scanners.
- Central or protected log and alert destination.

### 23.4 Documentation

Maintain:

- Product requirements.
- Architecture and data-flow diagrams.
- ADRs.
- Data dictionary.
- Permission matrix.
- Workforce hierarchy, role-scope, reporting-line, acting-role, and delegation guide.
- Campaign assignment and transfer policy.
- Target policy, exemption, period-close, and appeal guide.
- Versioned bulk-workforce templates and data dictionary.
- API contract.
- Threat model.
- Privacy and retention policy.
- Test plan and evidence.
- Deployment and rollback runbook.
- Backup and restore runbook.
- Incident-response runbooks.
- Release manifests and change log.

## 24. Definition of Done

A feature is done only when:

- Requirement and scope are documented.
- Threat and privacy impact are considered.
- Authorization is server-enforced.
- Current behavior is derived from effective-dated assignments rather than destructive history edits.
- Database invariants exist where practical.
- Happy-path, failure, retry, and authorization-negative tests pass.
- Logs and errors are redacted.
- Accessibility is verified for affected UI.
- Performance impact is measured where relevant.
- Migrations and rollback are documented.
- Campaign transfer, callback handoff, target effect, and bulk-import reversal are documented where affected.
- Operations and support documentation are updated.
- Product owner accepts behavior.
- Security or privacy reviewer accepts high-risk changes.
- Evidence is attached to the release record.

## 25. Production go-live checklist

### Product and operations

- [ ] Phase 0 decisions are approved.
- [ ] Pilot workflows are accepted by Managers, Team Leaders, Team Captains, and Agents.
- [ ] Hierarchy, campaign concurrency, transfer, target, exemption, and bulk-upload policies are approved.
- [ ] Metric definitions are approved.
- [ ] Support and incident owners are named.
- [ ] Training is complete.

### Security

- [ ] HTTPS is enforced on LAN and Tailscale paths.
- [ ] No direct app, PostgreSQL, or Redis host port is exposed.
- [ ] Privileged MFA or approved OIDC is active.
- [ ] Session, CSRF, authorization, and rate-limit tests pass.
- [ ] Critical and high findings are closed or formally accepted.
- [ ] Secrets and recovery keys have approved custody.
- [ ] Audit tamper detection and protected copy are operational.

### Data and privacy

- [ ] Campaign provenance fields are mandatory.
- [ ] Explicit DNC is immediate and global.
- [ ] Retention schedule is approved and automated.
- [ ] Raw uploads are cleaned up as required.
- [ ] Data-subject and correction procedures are documented.
- [ ] Production logs contain no prohibited personal data.
- [ ] Exemption evidence and reason access is restricted.
- [ ] Bulk workforce files and staging data follow approved cleanup.

### Reliability

- [ ] Fifty-agent concurrency test passes.
- [ ] Campaign transfer, callback handoff, and assignment-cutoff tests pass.
- [ ] Bulk workforce import, safe retry, privilege rejection, and reversal tests pass.
- [ ] Target proration, exemption, period-close, and reopen reconciliation passes.
- [ ] Ten thousand-row import test passes.
- [ ] Eight-hour soak test passes.
- [ ] Backup is current.
- [ ] Clean-host restore meets the approved recovery target.
- [ ] Cold boot and service-restart drills pass.
- [ ] Disk, certificate, Tailscale, worker, and backup alerts are tested.

### UX and accessibility

- [ ] 1366 x 768 and 1920 x 1080 tests pass.
- [ ] 100%, 125%, and 150% scaling tests pass.
- [ ] Keyboard-only operation passes.
- [ ] Accessibility review passes.
- [ ] Shortcut conflicts are resolved.
- [ ] Session-expiry behavior protects unsaved work without local PII persistence.

### Release and rollback

- [ ] Release commit, image digest, SBOM, and migration revision are recorded.
- [ ] Previous tested image remains available.
- [ ] Rollback runbook has been rehearsed.
- [ ] No rollback step deletes production volumes.
- [ ] Go-live sign-off is recorded.

## 26. Recommended Architecture Decision Records

Create at least:

- ADR-001: Dedicated CipherContact project boundary.
- ADR-002: Desktop-first responsive UX.
- ADR-003: LAN HTTPS plus Tailscale private privileged access.
- ADR-004: Opaque server-side browser sessions.
- ADR-005: Team membership and object-level authorization.
- ADR-005A: Manager, Team Leader, Team Captain, Agent, and optional Viewer capability matrix.
- ADR-005B: Effective-dated role, reporting-line, acting-role, and delegation assignments.
- ADR-005C: Workforce identity matching and joiner, mover, leaver, and rehire behavior.
- ADR-006: Canonical contact and campaign-contact separation.
- ADR-006A: Effective-dated campaign-user assignment, campaign concurrency, transfer cutoff, and callback handoff.
- ADR-007: Transactional work leases and idempotent completion.
- ADR-008: Staged import with commit-time revalidation.
- ADR-009: Immediate global explicit-DNC suppression.
- ADR-010: One background-job and scheduler stack.
- ADR-011: Self-hosted frontend assets.
- ADR-012: Backup, recovery objectives, and key custody.
- ADR-013: AI excluded from the MVP.
- ADR-014: Target metric, period, calendar, proration, rounding, ramp-up, and attribution.
- ADR-015: Exemption request, approval, privacy, mass adjustment, appeal, and period reopening.
- ADR-016: Staged bulk-workforce import, high-risk approval, explicit deactivation, and compensating reversal.
- ADR-017: Notification and approval delivery, with an in-application inbox as the primary channel.
- ADR-018: Session validity is authoritative in PostgreSQL, with Redis used only as a non-authoritative cache.
- ADR-019: Keyed HMAC phone fingerprints held in separated key custody.

Each ADR should record context, decision, alternatives, consequences, security and privacy effect, migration or rollback effect, owner, and date.

## 27. Reference resources

### Security

- OWASP Password Storage Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Session Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP CSRF Prevention Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- OWASP File Upload Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- OWASP Logging Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- FastAPI security guidance: https://fastapi.tiangolo.com/tutorial/security/

### Infrastructure

- Tailscale Docker guidance: https://tailscale.com/docs/features/containers/docker
- Tailscale Serve guidance: https://tailscale.com/docs/features/tailscale-serve
- Docker Compose production guidance: https://docs.docker.com/compose/how-tos/production/
- Docker Compose secrets: https://docs.docker.com/compose/how-tos/use-secrets/
- Docker Compose trust model: https://docs.docker.com/compose/trust-model/
- Caddy documentation: https://caddyserver.com/docs/

### Data and database

- PostgreSQL backup and restore: https://www.postgresql.org/docs/current/backup.html
- PostgreSQL locking clauses: https://www.postgresql.org/docs/current/sql-select.html
- SQLAlchemy documentation: https://docs.sqlalchemy.org/
- Alembic documentation: https://alembic.sqlalchemy.org/
- Google libphonenumber: https://github.com/google/libphonenumber

### UX and accessibility

- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- WAI keyboard accessibility: https://www.w3.org/WAI/WCAG22/Understanding/keyboard.html
- HTMX documentation: https://htmx.org/docs/
- Tailwind installation guidance: https://tailwindcss.com/docs/installation

### Privacy and legal review

- Zimbabwe Cyber and Data Protection Act: https://zimlii.org/akn/zw/act/2021/5/eng%402022-03-11/source
- POTRAZ: https://www.potraz.gov.zw/

Legal links are starting resources only. Qualified review is required before production processing.

## 28. Workforce-design approach options

### Option A: Effective-dated scoped assignments

**Recommended.**

Identity, business role, reporting line, campaign assignment, target assignment, and exemption are separate effective-dated records.

Advantages:

- Preserves history.
- Supports acting roles and temporary cover.
- Supports campaign changes and future schedules.
- Keeps actual work separate from target treatment.
- Makes authorization and approvals auditable.
- Supports safe bulk import and correction.

Costs:

- More tables and explicit services.
- Requires effective-time, overlap, and version testing.

### Option B: Static hierarchy columns on users

Examples would include user.role, user.manager_id, user.campaign_id, user.target, and user.exempt.

Advantages:

- Fast initial build.
- Simple screens for current state.

Disadvantages:

- Overwrites history.
- Cannot safely represent acting roles, campaign moves, multiple campaigns, proration, or appeals.
- Encourages fragile reporting and privilege bugs.

This option is rejected.

### Option C: General policy engine

All permissions, approvals, assignments, and target rules are expressed in a generic policy language.

Advantages:

- Highly flexible.

Disadvantages:

- Excessive complexity for the initial product.
- Harder to test, explain, operate, and audit.

This option is deferred unless the approved requirements outgrow the explicit domain model.

## 29. Workforce and operational factors not to overlook

### 29.1 Organizational lifecycle

- Joiners, movers, leavers, and rehires.
- Temporary staff, contractors, trainees, and probation or ramp-up periods.
- Acting Managers, Team Leaders, and Team Captains.
- Delegation while a supervisor is absent.
- A user holding different roles in different scopes.
- Supervisor change during a target period.
- Team merge, split, rename, closure, and reassignment.
- Future-dated changes and cancellation before the effective date.
- Emergency disable that overrides future scheduled access.

### 29.2 Campaign staffing

- Whether Agents may work one or several campaigns in the same shift.
- Priority, allocation percentage, and fairness across simultaneous campaigns.
- Campaign capacity and minimum staffing.
- Skills, language, product knowledge, training completion, licensing, client clearance, and conflict restrictions.
- Destination readiness before a campaign move.
- Agent choice versus supervisor-controlled routing.
- Campaign pause, restart, early closure, or lead exhaustion.
- Agents returning to a previous campaign.
- Temporary overflow and callback-only assignments.
- Active leases, pending callbacks, open reviews, and notes during transfer.
- Campaign-specific contact hours, timezone, and holidays.

### 29.3 Target fairness and accuracy

- Daily, weekly, monthly, campaign, and cross-campaign periods.
- Partial shift, approved leave, illness, training, bereavement, and authorized absence.
- New-starter ramp-up and return-from-leave ramp.
- Public holiday and working-calendar treatment.
- System outage, laptop failure, network failure, and office closure.
- Insufficient or poor-quality campaign data.
- Campaign paused or exhausted before target period end.
- Transfer during a day or period.
- Multiple campaigns contributing to one target.
- Connected versus qualified, conversion, and sale definitions.
- Rounding and minimum target rules.
- Overlapping or duplicate exemptions.
- Retroactive requests and late evidence.
- Appeal, correction, reversal, and closed-period reopening.
- Team-wide or campaign-wide business adjustments.
- Reporting provisional values separately from closed values.

### 29.4 Exemption governance and privacy

- Who may request, endorse, approve, escalate, reverse, and appeal.
- Maximum ordinary approval scope for a Team Leader.
- Cases requiring Manager approval.
- No self-approval.
- Minimal reason categories without diagnoses.
- Restricted evidence access and retention.
- Notification content that does not expose private details.
- Exemption versus availability. An exempt Agent may still work.
- Exemption versus leave. Leave may also end work eligibility for a period.
- Actual output remains unchanged.
- Exempt status is not displayed as failure or 0%.

### 29.5 Bulk workforce operations

- Immutable external workforce ID.
- Duplicate identity, changed email, and name changes.
- Rehire versus new person.
- Unknown or circular supervisor.
- Missing team, campaign, role, policy, or scope.
- Future and overlapping assignments.
- Explicit create, update, reactivate, transfer, end, and deactivate actions.
- No implicit deletion when a row is absent.
- No passwords or reset tokens in files.
- High-risk role elevation and bulk deactivation approval.
- File-version compatibility and template expiry.
- Dry-run, row-level errors, warnings, and high-risk summary.
- Atomic transaction groups and idempotent retry.
- Compensating reversal after commit.
- Notifications and activation delivery.
- Audit trace from every affected record to import, uploader, and approver.

### 29.6 Performance reporting

- Base, prorated, adjusted, and effective targets.
- Actual output and campaign attribution.
- Exempt, adjusted, not applicable, provisional, closed, reopened, and under-review states.
- Policy and formula version.
- Historical results that do not move when the Agent changes campaign or supervisor.
- Privacy thresholds for small groups.
- Comparisons adjusted for lead quality, campaign difficulty, schedule, and opportunity.
- Appeals and corrections visible without silently replacing prior values.
- Manager, Team Leader, Team Captain, Agent, and Viewer report scopes.

### 29.7 Operations and integration

- Whether an HR or payroll system will become the workforce source of truth later.
- Stable team, campaign, employee, and policy codes for integration.
- Notification channels and fallback when email is unavailable.
- Approval-service-level expectations and escalation.
- Time synchronization for effective dates and period close.
- Audit and retention for workforce and performance records.
- Support ownership for identity mismatch, assignment conflict, and target dispute.
- Training and user guidance for each business role.
- Feature flags for bulk upload, campaign transfer, target management, and exemption approval.
- Disaster recovery of role, assignment, target, and approval state.

## 30. Final recommendation

Use this plan to produce an approved implementation backlog only after Phase 0 decisions are complete. Do not begin with AI, dashboards, or visual polish while auth, team scope, DNC, queue concurrency, import transactions, HTTPS, and recovery remain unresolved.

The safest delivery path is a narrow secure vertical slice. Prove the core call-work loop first:

1. One team.
2. One approved campaign import.
3. One Manager, one Team Leader, and one Team Captain, created manually rather than by bulk import.
4. A small Agent pilot.
5. One-record leasing.
6. Idempotent outcomes.
7. Immediate DNC.
8. Callbacks with masked references.
9. Verified backup and restore.

Only after that slice passes its gates, add the heavier subsystems as a second slice:

10. One staged workforce import.
11. One effective-dated campaign assignment and one controlled transfer.
12. One target policy and one approved exemption scenario.

Scale teams, reports, and analytics only after that slice passes its security, privacy, concurrency, usability, and operational gates.

