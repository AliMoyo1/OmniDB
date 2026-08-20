# Architecture overview

Authoritative detail is in `CipherContact - Detailed Implementation Plan v0.3.md` in this folder, as amended by `../decisions/decision-log.md` and the ADRs. Where the plan still describes Tailscale, remote access, or the earlier DNC and retention models, the decisions supersede it.

## Topology (decided 2026-08-20)

- On site only. Every role reaches the app over LAN HTTPS at https://server-LAN-IP. No remote path, no Tailscale, no public ports.
- Laptops are not centrally managed. The server is the managed component. Certificate trust is a one-time internal-CA-root install per device (recommended) or a first-use exception.
- Caddy terminates TLS on the LAN. The web app, PostgreSQL, and Redis are on private container networks with no host-published ports.
- A background worker and a singleton scheduler run jobs. Encrypted backups go off-device.

## Stores

- PostgreSQL: source of truth for all business state, and authoritative for session validity and revocation.
- Redis: non-authoritative session cache, rate limits, and the background-job broker.

## Package layout

~~~text
OmniDB/
  app/
    api/            HTTP routes (server-rendered HTML plus HTMX, and /api/v1 JSON)
    auth/           sessions, login, TOTP 2FA, CSRF, step-up, authorization helpers
    campaigns/      campaign drafts, assignments, transfers
    imports/        quarantine, parse, classify, preview, atomic commit
    work/           leasing, completion, callbacks, skips, DNC
    suppressions/   do-not-call labeling, skip, and audited override
    reporting/      aggregate reports (no raw contacts) and completed-campaign export
    audit/          append-only audit events
    templates/      Jinja2 templates
    static/         self-hosted CSS, JS, icons, fonts
  migrations/       Alembic
  tests/            unit, integration, authorization, concurrency, e2e, security, performance
  deploy/           compose, caddy, backup, monitoring
  docs/             architecture, decisions, operations, privacy, testing
  scripts/
~~~

## Load-bearing invariants

See plan section 5, as amended by the decisions. Critical ones: HTTPS everywhere, no public ports, one record per agent, one active lease per work item, idempotent completion, DNC labeled and auto-skipped (Team Captain override is justified and audited), no raw personal data in logs, PostgreSQL-authoritative sessions, and keyed-HMAC phone fingerprints.
