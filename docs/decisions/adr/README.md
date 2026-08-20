# Architecture Decision Records

Each significant decision gets one ADR. Keep them short and durable. Record context, the decision, alternatives, and consequences, including security and privacy effects.

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
- ADR-003: Access topology and certificate trust, LAN HTTPS only, no Tailscale (Accepted)
- ADR-004: Opaque server-side browser sessions, PostgreSQL authoritative
- ADR-004A: Authentication, local accounts with TOTP 2FA and Super Admin reset (Accepted)
- ADR-005: Team membership and object-level authorization
- ADR-005A: Role capability matrix
- ADR-005B: Effective-dated role, reporting, acting, and delegation assignments
- ADR-005C: Workforce identity key, login-email username (Accepted)
- ADR-006: Canonical contact and campaign-contact separation
- ADR-006A: Campaign assignment, concurrency, transfer, and callback handoff
- ADR-007: Transactional work leases and idempotent completion
- ADR-008: Staged import with commit-time revalidation
- ADR-009: Explicit DNC, label and auto-skip with Team Captain override (Accepted)
- ADR-010: One background-job and scheduler stack
- ADR-011: Self-hosted frontend assets
- ADR-012: Backup, recovery objectives, and key custody
- ADR-013: AI excluded from the MVP (Accepted)
- ADR-014: Target metric and policy (deferred from first pilot)
- ADR-015: Exemption request and approval (deferred from first pilot)
- ADR-016: Staged bulk-workforce import (deferred from first pilot)
- ADR-017: Notifications, in-app inbox primary, email dormant (Accepted)
- ADR-018: Session validity authoritative in PostgreSQL, Redis cache only
- ADR-019: Keyed HMAC phone fingerprints in separated key custody
- ADR-020: Campaign-completion retention and export (Accepted)
