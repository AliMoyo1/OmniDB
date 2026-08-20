# ADR-003: Access topology and certificate trust

- Status: Accepted
- Date: 2026-08-20
- Owner: IT and technical lead
- Related decisions: D-04, D-05, D-11 (host OS D-03 is Linux LTS)

## Context

CipherContact is used on site by agents and supervisors on office laptops. The laptops are not centrally managed. The organization already exposes other internal systems to staff by IP address on the office network. There is no requirement for off-site access.

## Decision

- Access is LAN HTTPS only, for every role, on site. Tailscale and any remote-access path are removed.
- Users reach the app at https://server-LAN-IP. The server, not the laptops, is the managed component.
- HTTPS remains mandatory (plan invariant 1). The server holds a certificate covering its LAN IP.
- Because laptops are unmanaged, the recommended way to keep HTTPS trusted is a one-time install of an internal CA root on each device, documented as a short step. A self-signed certificate with a first-use browser exception is the fallback. The exact certificate mechanism is finalized during Phase 1 setup.
- Caddy terminates TLS on the LAN. PostgreSQL and Redis stay on private container networks with no host-published ports.

## Alternatives considered

- Tailscale for remote privileged access (previous plan). Rejected: no off-site requirement, and it adds a third-party control plane and another credential to manage.
- A public domain with DNS-01 certificates. Not chosen: access is by IP and there is no requirement for a public domain.

## Consequences

Simpler topology and one fewer dependency. Access is strictly on site. Certificate trust is a one-time per-laptop step or a first-use exception.

## Security and privacy effect

No public or remote exposure. Residual risk: if a user skips the CA install and clicks through a first-use warning, that first connection is not protected against a local man-in-the-middle. Installing the CA root removes this.

## Migration or rollback effect

Removes Tailscale from the deployment. No data effect.
