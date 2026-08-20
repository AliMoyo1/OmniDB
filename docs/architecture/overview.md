# Architecture overview

Authoritative detail is in `CipherContact - Detailed Implementation Plan v0.3.md` in this folder. This is a short orientation.

## Topology

- Agent work laptops reach Caddy over office LAN HTTPS (443) only.
- Manager and admin devices reach the app over Tailscale Serve HTTPS only.
- Caddy is the only LAN listener. The web app, PostgreSQL, and Redis are on private container networks with no host-published ports.
- A background worker and a singleton scheduler run jobs. Encrypted backups go off-device. Monitoring is admin-only.

## Stores

- PostgreSQL: source of truth for all transactional business state, and authoritative for session validity and revocation.
- Redis: non-authoritative session cache, rate limits, and the background-job broker.

## Package layout

~~~text
OmniDB/
  app/
    api/            HTTP routes (server-rendered HTML plus HTMX, and /api/v1 JSON)
    auth/           sessions, login, CSRF, step-up, authorization helpers
    campaigns/      campaign drafts, assignments, transfers
    imports/        quarantine, parse, classify, preview, atomic commit
    work/           leasing, completion, callbacks, skips, DNC
    suppressions/   do-not-call suppression
    reporting/      aggregate reports (no raw contacts)
    audit/          append-only audit events
    templates/      Jinja2 templates
    static/         self-hosted CSS, JS, icons, fonts
  migrations/       Alembic
  tests/            unit, integration, authorization, concurrency, e2e, security, performance
  deploy/           compose, caddy, tailscale, backup, monitoring
  docs/             architecture, decisions, operations, privacy, testing
  scripts/
~~~

## Load-bearing invariants

See plan section 5. The critical ones: HTTPS everywhere, no public ports, one record per agent, one active lease per work item, idempotent completion, immediate global DNC, no raw personal data in logs, PostgreSQL-authoritative sessions, and keyed-HMAC phone fingerprints.
