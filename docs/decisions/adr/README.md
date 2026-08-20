# Architecture Decision Records

Each significant decision gets one ADR. Keep them short and durable. Record context, the decision, alternatives, and consequences, including security and privacy effects.

The recommended list is in plan section 26. Seeded here: ADR-001 and ADR-013. Create the rest as their decisions are resolved.

## Status values

Proposed, Accepted, Superseded, Deprecated.

## Template

~~~markdown
# ADR-XXX: Title

- Status: Proposed
- Date: YYYY-MM-DD
- Owner:
- Related decision: D-XX

## Context

## Decision

## Alternatives considered

## Consequences

## Security and privacy effect

## Migration or rollback effect
~~~

## Index

- ADR-001: Dedicated project boundary and repository (Accepted)
- ADR-002: Desktop-first responsive UX
- ADR-003: LAN HTTPS plus Tailscale private privileged access
- ADR-004: Opaque server-side browser sessions, PostgreSQL authoritative
- ADR-005: Team membership and object-level authorization
- ADR-005A: Role capability matrix
- ADR-005B: Effective-dated role, reporting, acting, and delegation assignments
- ADR-005C: Workforce identity matching and lifecycle
- ADR-006: Canonical contact and campaign-contact separation
- ADR-006A: Campaign assignment, concurrency, transfer, and callback handoff
- ADR-007: Transactional work leases and idempotent completion
- ADR-008: Staged import with commit-time revalidation
- ADR-009: Immediate global explicit-DNC suppression
- ADR-010: One background-job and scheduler stack
- ADR-011: Self-hosted frontend assets
- ADR-012: Backup, recovery objectives, and key custody
- ADR-013: AI excluded from the MVP (Accepted)
- ADR-014: Target metric, period, calendar, proration, rounding, ramp, attribution
- ADR-015: Exemption request, approval, privacy, mass adjustment, appeal, reopen
- ADR-016: Staged bulk-workforce import, high-risk approval, reversal
- ADR-017: Notification and approval delivery, in-app inbox primary
- ADR-018: Session validity authoritative in PostgreSQL, Redis cache only
- ADR-019: Keyed HMAC phone fingerprints in separated key custody
