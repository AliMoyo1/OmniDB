# ADR-001: Dedicated project boundary and repository

- Status: Accepted
- Date: 2026-08-20
- Owner: Product owner and technical lead
- Related decision: D-01

## Context

CipherContact handles sensitive contact data and access control. It must be isolated from unrelated systems, in particular the ThemisIQ repository, and needs its own secrets, environments, backups, and release process.

## Decision

Build in the dedicated repository OmniDB (https://github.com/AliMoyo1/OmniDB), outside ThemisIQ. Keep production secrets, backup keys, and imported data out of the repository. The code package uses the neutral name "app".

Naming (decided 2026-08-20): the product is CipherContact. The build repository remains OmniDB (https://github.com/AliMoyo1/OmniDB). The code package uses the neutral name "app".

## Alternatives considered

- Placing the code inside ThemisIQ. Rejected: this violates isolation and mixes unrelated secrets and release processes.

## Consequences

Clean isolation and an independent lifecycle. A separate CI, backup, and deployment path must be maintained.

## Security and privacy effect

Reduces blast radius and prevents accidental cross-project secret or data exposure.

## Migration or rollback effect

None yet. No code or data exists.
