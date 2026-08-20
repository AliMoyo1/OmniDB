# Decision log

Tracks the blocking decisions from plan section 4. No production implementation begins until D-01 through D-08 are resolved with a recorded owner. D-09 through D-14 and D-23 must be complete before the pilot.

Status values: Open, In progress, Decided.

| ID | Decision | Recommended starting position | Owner | Status | Notes / ADR |
|---|---|---|---|---|---|
| D-01 | Repository and project path | Product CipherContact in the OmniDB repo, outside ThemisIQ | Product owner, tech lead | Decided | Product CipherContact, repo OmniDB. ADR-001 |
| D-02 | Jurisdiction and direct-marketing rules | Confirm Zimbabwe and client obligations with qualified counsel | Product owner, privacy | Open | |
| D-03 | Production host OS | Supported Linux LTS on x86 mini PC preferred | IT, tech lead | Open | ADR-003 area |
| D-04 | Client access topology | LAN HTTPS for agents, Tailscale HTTPS for admins | IT, security | Open | ADR-003 |
| D-05 | Server certificate trust | Managed private CA on work laptops | IT | Open | |
| D-06 | Identity source | Corporate OIDC if available, else local accounts with privileged MFA | Security, product owner | Open | ADR-004 area |
| D-07 | Team structure | One organization with explicit scoped teams | Product owner | Open | ADR-005 |
| D-08 | Explicit DNC authority | Immediate global suppression, privileged audited correction only | Legal, privacy, product owner | Open | ADR-009 area |
| D-09 | Data retention | Define periods per category | Privacy, legal | Open | |
| D-10 | Backup destination and key custody | Encrypted off-device, separate key custody | IT, security | Open | ADR-012 area |
| D-11 | Supported laptops and browsers | Managed Windows, current Edge or Chrome | IT, UX | Open | |
| D-12 | Expected volume | Confirm contacts, campaigns, concurrency, retention | Product owner | Open | |
| D-13 | Campaign data provenance | Require source, date, purpose, lawful basis, vendor | Privacy, campaign ops | Open | |
| D-14 | On-call and support ownership | Named incident, backup, patching, revocation owner | Product owner, IT | Open | |
| D-15 | Business hierarchy | Manager, Team Leader, Team Captain, Agent; Super Admin separate; Viewer optional | Product owner | Open | ADR-005A |
| D-16 | Role scope | Effective-dated scoped assignments, not a mutable role on users | Product owner, security | Open | ADR-005B |
| D-17 | Campaign concurrency | One primary campaign per shift to start | Operations | Open | ADR-006A |
| D-18 | Campaign transfer cutoff | Define lease, callback, target, and effective-time treatment | Operations | Open | ADR-006A |
| D-19 | Target policy | Define metric, period, proration, rounding, calendars, ramp, attribution | Manager, operations | Open | ADR-014 |
| D-20 | Exemption approval | Requester, endorser, approver, escalation path | Product owner, HR/ops | Open | ADR-015 |
| D-21 | Bulk-user authority | Who may stage and commit; elevation and deactivation approvals | Security, product owner | Open | ADR-016 |
| D-22 | Workforce identity key | Immutable external workforce ID; never match by name | IT/HR ops | Open | ADR-005C |
| D-23 | Notification and approval channel | In-app inbox primary, email optional | IT, product owner | Open | ADR-017 |

## Product naming (decided 2026-08-20)

The product is CipherContact. The build repository remains OmniDB (https://github.com/AliMoyo1/OmniDB). The code package uses the neutral name "app". Recorded in ADR-001.
