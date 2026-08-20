# Decision analysis: pros, cons, and recommendations

Companion to `decision-log.md`. For each blocking decision from plan section 4, this gives the realistic options, the trade-offs, and a recommendation. Nothing here is final until recorded in `decision-log.md` and, where relevant, an ADR.

Tiers:
- Tier 1, hard blockers (D-01 to D-08): must be resolved before any Phase 1 code.
- Tier 2, pre-pilot (D-09 to D-14, D-23): may finalize during Phase 0, must be done before pilot.
- Tier 3, design decisions the plan already settles (D-15 to D-22): recommendation is to adopt the plan unless a real business reason says otherwise.

Cross-cutting recommendation: keep the first pilot to the core call-work loop. Defer the target and exemption subsystem (D-19, D-20) and bulk-workforce import (D-21) to a second slice. This shrinks Tier 3 risk substantially.

---

## Tier 1: hard blockers

### D-01: Repository and project path (DECIDED)

Decided 2026-08-20: product CipherContact, built in the OmniDB repository (https://github.com/AliMoyo1/OmniDB), outside ThemisIQ. Package name is the neutral `app`. See ADR-001.

### D-02: Operating jurisdiction and direct-marketing rules

- Decision: which law governs, and what it requires for direct marketing, consent, and opt-out.
- Options: (A) treat the Zimbabwe Cyber and Data Protection Act (2021) and POTRAZ rules as governing, confirmed by qualified counsel; (B) additionally map client or vendor contractual obligations where campaign data comes from third parties.
- Pros of doing this properly: a defensible lawful basis for calling, clear DNC and consent handling, and reduced regulatory and reputational risk.
- Cons and watch-outs: needs a qualified data-protection lawyer (cost and time); the answer may restrict which campaign sources are usable and may require documented consent references.
- Recommendation: adopt the Zimbabwe DPA as the baseline and engage counsel to confirm direct-marketing consent and opt-out obligations and the lawful basis. Treat per-campaign provenance (D-13) as the practical enforcement of this. This cannot be self-resolved; document assumptions now, get counsel sign-off before the pilot.

### D-03: Production host operating system

- Decision: Linux or Windows on the on-premise host.
- Options: (A) supported Linux LTS (for example Ubuntu Server LTS) on an x86 mini PC; (B) Windows host with Docker Desktop or WSL2.
- Pros of Linux: cleanest fit for Docker and Compose, low overhead, reliable unattended operation, no BitLocker or WSL2 startup fragility, no surprise Windows Update reboots, lower cost.
- Cons of Linux: the operator must be comfortable administering Linux.
- Pros of Windows: familiar to a Windows-first team.
- Cons of Windows: Docker Desktop or WSL2 auto-start fragility, BitLocker recovery-key custody, Windows Update reboots interrupting service, sleep and hibernation issues, and recovery after an interrupted update. The plan calls out all of these as extra work.
- Recommendation: Linux LTS on an x86 mini PC. You already run Linux servers, so the operational capability exists. This is the lower-risk, lower-cost choice.

### D-04: Client access topology

- Decision: how agents and admins reach the app privately.
- Options: (A) Caddy LAN HTTPS for agents plus Tailscale Serve HTTPS for admins (the plan); (B) a self-managed VPN (WireGuard) instead of Tailscale; (C) public exposure (rejected).
- Pros of the plan: no public ports, simple LAN TLS via Caddy, and an easy private mesh for remote admins via Tailscale.
- Cons and watch-outs: Tailscale is a third-party control plane; LAN HTTPS needs certificate trust on laptops (D-05).
- Recommendation: adopt the plan. If you want to avoid Tailscale's hosted control plane, self-host Headscale, but for a small internal tool Tailscale is fine. Never enable Tailscale Funnel.

### D-05: Server certificate trust

- Decision: how agent browsers trust the LAN HTTPS certificate.
- Options: (A) an internal private CA distributed to managed laptops (via GPO or MDM); (B) a real domain name with Caddy using DNS-01 to obtain publicly trusted certificates, resolved internally to the LAN IP; (C) per-device manual trust (does not scale).
- Pros of a private CA: works fully offline on the LAN and is centrally controlled.
- Cons of a private CA: you must deploy and rotate the CA on every laptop, which realistically needs a managed fleet.
- Pros of the public-domain plus DNS-01 route: browsers trust the certificate automatically with no CA distribution.
- Cons of that route: you need a domain you control and DNS access, plus internal name resolution to the LAN IP.
- Recommendation: this depends on D-11. If the laptops are centrally managed, use an internal private CA pushed by GPO. If they are not centrally managed, and you own a domain, use a real domain with Caddy DNS-01 so certificates are publicly trusted without touching each laptop. Lean toward the domain plus DNS-01 route to avoid CA-distribution pain. Never train users to click through TLS warnings.

### D-06: Identity source

- Decision: how users authenticate.
- Options: (A) corporate OIDC single sign-on if an identity provider exists; (B) local accounts with Argon2id password hashing and MFA for privileged roles.
- Pros of OIDC: centralized joiner and leaver lifecycle, existing MFA, and no password storage in this app.
- Cons of OIDC: requires an identity provider the organization actually has and can integrate, and adds an external dependency on the LAN.
- Pros of local accounts: self-contained, no external dependency, works on an isolated LAN.
- Cons of local accounts: this app must own account lifecycle, MFA, and reset flows, and store password hashes.
- Recommendation: for the first pilot on an isolated LAN, start with local accounts using Argon2id and mandatory MFA for Super Admin, Manager, and Team Captain. Design the auth layer so OIDC can be added later if the organization has a corporate IdP. This avoids blocking the pilot on an IdP integration.

### D-07: Team structure

- Decision: one organization or multi-organization.
- Options: (A) a single organization with explicit scoped teams; (B) multi-organization (multi-tenant) isolation.
- Pros of single-org: much simpler model, matches one company.
- Cons of single-org: if multiple legal entities or isolated clients are needed later, that is a retrofit.
- Recommendation: single organization for the MVP. Do not build multi-organization behavior until there is a confirmed need. The plan explicitly warns against half-built multi-org.

### D-08: Explicit DNC authority

- Decision: how a do-not-call request takes effect.
- Options: (A) immediate global suppression, with a privileged audited correction path (the plan); (B) supervisor-approved suppression before it takes effect (rejected).
- Pros of immediate global: honors the opt-out instantly, is legally defensible, and is simple to reason about.
- Cons and watch-outs: a mistaken DNC needs a privileged correction path, which the plan already includes.
- Recommendation: immediate global suppression. A Team Captain does not gate whether it becomes effective. Correction requires a privileged role, a reason, and an audit event. This is effectively non-negotiable for compliance.

---

## Tier 2: pre-pilot

### D-09: Data retention

- Decision: how long each data category is kept and what ends it.
- Options: (A) defined per-category retention with automated enforcement (the plan); (B) keep everything (rejected: privacy and storage risk).
- Pros of defined retention: DPA alignment, controlled storage, and reduced exposure if breached.
- Cons and watch-outs: needs privacy and legal input per category and automation to enforce.
- Recommendation: adopt the plan's candidate schedule (section 16.2) as a draft and have privacy and legal confirm the periods. Key points: delete raw uploads shortly after a verified commit, keep audit per security needs, and keep only the minimal DNC evidence needed to honor suppression. Automate retention jobs.

### D-10: Backup destination and key custody

- Decision: where encrypted backups go and who holds the keys.
- Options: (A) encrypted off-device backups with the encryption key held separately (the plan); (B) local-only backups (rejected: single point of failure).
- Pros of off-device encrypted: survives host loss and ransomware, and key separation limits exposure.
- Cons and watch-outs: needs a real destination and disciplined key custody.
- Recommendation: encrypted logical PostgreSQL backups (pg_dump plus age or gpg) to an off-device destination. Given local connectivity realities, an offsite external-drive rotation is acceptable if cloud egress is unreliable; otherwise S3-compatible object storage. Store the encryption key separately from the backup and the server. Test a restore monthly and before each high-risk release.

### D-11: Supported laptops and browsers

- Decision: which laptops and browsers, and whether the fleet is centrally managed.
- Recommendation: confirm the actual fleet and whether it is centrally managed, because that answer drives D-05 and the DLP posture. Target current Edge and Chrome on Windows at 1366x768 and 1920x1080, at 100, 125, and 150 percent scaling. If laptops are not centrally managed, plan for weaker endpoint DLP and pick the domain plus DNS-01 certificate route.

### D-12: Expected volume

- Decision: real numbers for contacts, campaigns, concurrency, and audit retention.
- Recommendation: gather actual figures. The plan targets 50 concurrent agents, 50,000 active contacts, and 10,000-row imports, which is a safe default for a first pilot. If real numbers are much larger, revisit the host baseline (D-03) and indexing before the capacity test.

### D-13: Campaign data provenance

- Decision: what must be recorded before a campaign can launch.
- Options: (A) mandatory provenance as a blocking import gate (the plan); (B) optional provenance (rejected).
- Pros of mandatory: lawful-basis traceability and DNC and consent accountability.
- Cons and watch-outs: campaign owners must supply source, date, purpose, lawful basis or consent reference, and vendor details.
- Recommendation: make provenance mandatory and block import when it is missing. This is the practical enforcement of D-02 and is non-negotiable for lawful direct marketing.

### D-14: On-call and support ownership

- Decision: who owns production incidents, backups, patching, and access revocation.
- Recommendation: name one accountable owner plus a backup person. On a small team this may be the builder, but it must be written down with an escalation path. This is a go-live blocker.

### D-23: Notification and approval channel

- Decision: how activations and approvals reach people.
- Options: (A) an in-application inbox as primary, email optional (the plan v0.3); (B) email as primary (risky if SMTP is unreliable).
- Pros of in-app inbox: works without a mail server and keeps sensitive detail out of email.
- Cons and watch-outs: users must log in to see notifications, which is acceptable for a work tool.
- Recommendation: in-app inbox primary. Wire email only if a reliable SMTP relay exists. Confirm whether any SMTP is available in the environment.

---

## Tier 3: design decisions the plan already settles

For these the recommendation is to adopt the plan's position. The main action is to confirm it matches the real organization.

### D-15: Business hierarchy

Adopt Manager, Team Leader, Team Captain, Agent, with Super Admin separate and Viewer optional. Action: confirm these four tiers match the real call-center structure. If the structure is flatter (for example no Team Captain), collapse a tier rather than invent one.

### D-16: Role scope

Adopt effective-dated scoped assignments, not a mutable role column on users. This is the core modeling decision and is non-negotiable for history and audit. See plan section 28 Option A.

### D-17: Campaign concurrency

Adopt one primary campaign per agent per shift for the MVP, plus callback-only obligations. Pros: simpler queue, simpler targets, easier to reason about. Defer weighted multi-campaign allocation until operations defines priorities and attribution.

### D-18: Campaign transfer cutoff

Adopt the plan's transfer preflight: choose a lease treatment (complete before cutoff, return to source queue, or captain resolves) and a callback treatment (retain, transfer to a named agent, or handoff queue), and prorate targets. Action: operations picks the default treatments.

### D-19: Target policy (recommend defer)

The whole target subsystem is a candidate to defer out of the first pilot. If kept, define exactly one primary metric (do not conflate connected, conversion, and sale), one period (daily or weekly), a working calendar, and proration. Recommendation: defer to post-pilot; if needed early, start with a single simple metric.

### D-20: Exemption approval (recommend defer with D-19)

Adopt the plan's chain (agent or captain requests, Team Leader approves, Manager escalates, no self-approval) if targets are in scope. Since targets are recommended for deferral, exemptions defer with them.

### D-21: Bulk-user authority (recommend defer bulk import)

Adopt the plan's authority model (Manager or scoped Team Leader stage; role elevation and bulk deactivation need higher approval). But bulk-workforce import is Phase 4B and deferrable. For the pilot, create the handful of users manually. Recommendation: defer bulk import; manual user creation for the pilot.

### D-22: Workforce identity key

Adopt an immutable external workforce ID, never matching users by display name. Action: confirm a stable employee ID exists to use. If not, generate an internal immutable ID at onboarding. Decide the ID source now, because it is hard to change later.

---

## Recommended decisions summary

| ID | Recommendation | Blocker |
|---|---|---|
| D-01 | Decided: CipherContact product, OmniDB repo | Resolved |
| D-02 | Zimbabwe DPA baseline, confirm direct-marketing rules with counsel | Yes |
| D-03 | Linux LTS on x86 mini PC | Yes |
| D-04 | Caddy LAN HTTPS plus Tailscale for admins | Yes |
| D-05 | Domain plus DNS-01 if fleet unmanaged, else internal private CA | Yes |
| D-06 | Local accounts plus privileged MFA for pilot, OIDC-ready | Yes |
| D-07 | Single organization | Yes |
| D-08 | Immediate global DNC, privileged audited correction | Yes |
| D-09 | Adopt plan retention draft, confirm with privacy and legal | Pre-pilot |
| D-10 | Encrypted off-device backups, separate key custody, monthly restore test | Pre-pilot |
| D-11 | Confirm fleet and central management (drives D-05) | Pre-pilot |
| D-12 | Gather real volume numbers, plan defaults are safe | Pre-pilot |
| D-13 | Mandatory provenance, blocking import gate | Pre-pilot |
| D-14 | Name an accountable owner plus backup | Go-live |
| D-15 | Adopt four-tier hierarchy, confirm against reality | Design |
| D-16 | Effective-dated scoped assignments | Design |
| D-17 | One primary campaign per shift for MVP | Design |
| D-18 | Adopt transfer preflight, ops picks defaults | Design |
| D-19 | Defer target policy from first pilot | Design |
| D-20 | Defer exemptions with targets | Design |
| D-21 | Defer bulk import, manual user creation for pilot | Design |
| D-22 | Immutable workforce ID, decide the ID source | Design |
| D-23 | In-app inbox primary, email optional | Pre-pilot |
