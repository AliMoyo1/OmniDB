# Decision log

Blocking decisions from plan section 4. Resolved 2026-08-20 unless noted. Full trade-offs are in `decision-analysis.md`; the significant decisions also have ADRs.

Status values: Open, In progress, Decided, Deferred.

| ID | Decision | Resolution (2026-08-20) | Status | ADR |
|---|---|---|---|---|
| D-01 | Repository and product | Product CipherContact, built in the OmniDB repo, outside ThemisIQ | Decided | ADR-001 |
| D-02 | Jurisdiction | Zimbabwe DPA as the legal baseline, kept configurable so the app can run under another country's rules later | Decided | see notes |
| D-03 | Production host OS | Linux LTS on an x86 mini PC | Decided | |
| D-04 | Access topology | LAN HTTPS for everyone, on site only. Tailscale removed. No remote path | Decided | ADR-003 |
| D-05 | Certificate trust | Server-managed certificate reached at https://LAN-IP. Laptops unmanaged; recommended one-time internal-CA-root install per device, self-signed first-use exception as fallback. HTTPS stays mandatory | Decided | ADR-003 |
| D-06 | Authentication | Local accounts with TOTP 2FA (Microsoft Authenticator or any authenticator app). Super Admin can reset a user's password and 2FA enrollment | Decided | ADR-004A |
| D-07 | Team structure | Single organization, no multi-tenant | Decided | ADR-005 |
| D-08 | Explicit DNC | DNC numbers are labeled and auto-skipped. A Team Captain may override to allow calling, only after entering a justification, with an audit event | Decided | ADR-009 |
| D-09 | Data retention | A campaign database is complete when every number has a disposition, a recorded calling agent, and a usage record. Completion starts a visible 60-day countdown; a Team Captain may export the completed database to Excel and delete it; auto-delete at 60 days | Decided | ADR-020 |
| D-10 | Backups | Encrypted off-device backups, key held separately, monthly restore test | Decided | ADR-012 |
| D-11 | Laptops and browsers | NOT centrally managed. Users reach the app by IP over LAN HTTPS, as done for other internal systems. Current Edge and Chrome, 1366x768 and 1920x1080 | Decided | ADR-003 |
| D-12 | Volume | Plan defaults accepted (50 agents, 50k contacts, 10k-row imports). Confirm real numbers before capacity test | Decided | |
| D-13 | Provenance | Mandatory provenance, blocking import gate | Decided | |
| D-14 | Support owner | Accountable owner plus backup, name pending | Accepted | |
| D-15 | Hierarchy | Manager, Team Leader, Team Captain, Agent; Super Admin separate; Viewer optional. Confirm against reality | Decided | ADR-005A |
| D-16 | Role scope | Effective-dated scoped assignments | Decided | ADR-005B |
| D-17 | Concurrency | One primary campaign per shift for MVP | Decided | ADR-006A |
| D-18 | Transfers | Adopt transfer preflight, ops picks defaults | Decided | ADR-006A |
| D-19 | Target policy | DEFERRED from the first pilot | Deferred | |
| D-20 | Exemptions | DEFERRED with targets | Deferred | |
| D-21 | Bulk user import | DEFERRED. Create pilot users manually | Deferred | |
| D-22 | Workforce ID | The username (local part) of the user's login email, immutable once assigned | Decided | ADR-005C |
| D-23 | Notifications | In-app inbox primary. Email-notification capability built but left unconfigured until a Microsoft service account is available | Decided | ADR-017 |

## Notes and flags

- D-02: keep jurisdiction rules behind configuration. Do not hard-code Zimbabwe-only assumptions.
- D-08 legal flag: allowing a Team Captain to override DNC and call anyway carries direct-marketing risk if the number is an explicit customer opt-out. Recommend legal review (D-02) on whether explicit opt-outs may be overridden at all, and consider restricting override to imported-list DNC while making explicit customer opt-outs non-overridable or higher-approval. Recorded as decided per the product owner; the flag stands.
- D-09 DLP flag: the Team Captain Excel export contains all raw numbers, dispositions, and agent names. It is an authorized, audited exception to the no-raw-export rule. Once exported, the file lives outside the app's controls and is the exporter's responsibility. The 60-day auto-delete removes the in-system copy only. Immutable audit and DNC-suppression evidence are retained separately.
- D-22 stability flag: treat the username as immutable once assigned. A later change to a user's email does not change their stored workforce ID.
