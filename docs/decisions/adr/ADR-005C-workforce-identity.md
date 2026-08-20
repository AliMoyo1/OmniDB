# ADR-005C: Workforce identity key

- Status: Accepted
- Date: 2026-08-20
- Owner: IT and product owner
- Related decision: D-22

## Context

Users need a stable identity key that is not a display name.

## Decision

The workforce ID is the username (the local part) of the user's login email. It is treated as immutable once assigned: changing a user's email later does not change their stored workforce ID.

## Consequences

Simple and unique per user in a single organization. Login identifier and workforce ID align.

## Security and privacy effect

No display-name matching. The stored workforce ID stays stable for audit and history even if contact details change.

## Migration or rollback effect

None yet.
