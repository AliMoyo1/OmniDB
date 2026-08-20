# ADR-004A: Authentication method

- Status: Accepted
- Date: 2026-08-20
- Owner: Security and product owner
- Related decision: D-06

## Context

There is no corporate identity provider to integrate for the pilot. Users need a simple, secure login on the LAN.

## Decision

- Local accounts, with passwords hashed using Argon2id (plan 9.2).
- Second factor is TOTP (RFC 6238), compatible with Microsoft Authenticator, Google Authenticator, and similar apps. The user enrolls once and enters the shown code at login.
- Super Admin can reset a user's password and reset (re-enroll) their 2FA, for lockout recovery.
- The auth layer is built so corporate OIDC can be added later without reworking sessions.

## Alternatives considered

- Corporate OIDC single sign-on. Deferred: no identity provider available for the pilot; kept as a future option.

## Consequences

Self-contained authentication that works on an isolated LAN. This app owns account lifecycle, 2FA enrollment, and reset.

## Security and privacy effect

Argon2id plus TOTP is strong for an internal tool. Super Admin reset is a powerful capability; every reset is audited and invalidates the user's active sessions.

## Migration or rollback effect

OIDC would be additive.
