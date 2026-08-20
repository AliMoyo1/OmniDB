# ADR-017: Notifications and approvals

- Status: Accepted
- Date: 2026-08-20
- Owner: IT and product owner
- Related decision: D-23

## Context

Activations and approvals need to reach people reliably. No email service is configured yet; a Microsoft service account is expected later.

## Decision

- An in-application inbox is the primary notification and approval channel. A logged-in user sees pending items without email.
- Email-notification capability is built but left unconfigured (dormant) behind configuration, to be enabled later when a Microsoft service account (SMTP or Graph) is available.
- Notifications never contain raw numbers, notes, or other sensitive detail.

## Consequences

The pilot works with no mail server. Turning on email later is a configuration step, not new development.

## Security and privacy effect

Keeps sensitive detail out of email. In-app delivery stays inside the authorization boundary.

## Migration or rollback effect

Email is additive configuration.
