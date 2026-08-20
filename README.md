# CipherContact (OmniDB)

Secure internal database DLP management and distribution system for call campaigns. Desktop-first web application for managed work laptops on a private network.

Status: pre-implementation. This repository currently holds the plan, decisions, and scaffold only. No production code or data yet.

## What this is

- Controlled distribution of campaign contact data to agents, one record at a time.
- Server-enforced authorization by organization, team, role, and object scope.
- Immediate, global do-not-call suppression.
- Staged, validated, transaction-safe campaign imports.
- Append-only audit history with no raw personal data in logs.
- Private access only: office LAN HTTPS for agents, Tailscale HTTPS for approved admins. No public ports.

## What this is not (MVP)

No AI, no sentiment analysis, no automated routing, no dialer, no public internet exposure, no mobile-first UI, no general raw-number export, no in-application call audio. See the plan non-goals.

## Source of truth

- Plan: `docs/architecture/CipherContact - Detailed Implementation Plan v0.3.md`
- Change log: `docs/CipherContact - v0.3 Change Log.md`
- Decisions: `docs/decisions/decision-log.md` and `docs/decisions/adr/`
- Structure: `docs/architecture/overview.md`
- Build progress: `BUILD-LOG.md`

## Build sequence

Phase 0 (decisions and governance) must complete before Phase 1 (secure skeleton). See `docs/PHASE-0-CHECKLIST.md`.

## Security posture

Default deny. HTTPS everywhere. Opaque server-side sessions with PostgreSQL authoritative for validity and revocation. Argon2id password hashing. Keyed-HMAC phone fingerprints. Quarantined, isolated upload parsing. Secrets outside the repository. Encrypted off-device backups with a tested restore.
