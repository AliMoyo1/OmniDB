# OmniDB Build Log

Product: CipherContact. Build repository: OmniDB (https://github.com/AliMoyo1/OmniDB).
Authoritative plan: `docs/architecture/CipherContact - Detailed Implementation Plan v0.3.md`.

This file tracks scaffolding and build steps. Update it as work proceeds.

## 2026-08-20: Repository scaffold (Phase 0 and Phase 1 step 1)

Created the repository shell and the Phase 0 governance pack. No application logic yet, in line with the plan gate that Phase 0 decisions D-01 through D-08 must be resolved before Phase 1 feature work begins.

Done:
- [x] Directory structure per plan section 14.2 (app, migrations, tests, deploy, docs, scripts).
- [x] README, pyproject tooling stub, .gitignore, .env.example.
- [x] docs/architecture/overview.md with topology and structure.
- [x] docs/decisions/decision-log.md tracking D-01 through D-23.
- [x] docs/decisions/adr with process, template, index, and seeded ADR-001 and ADR-013.
- [x] docs/PHASE-0-CHECKLIST.md derived from plan section 20.
- [x] Moved the authoritative plan and change log into docs.

Open:
- [x] D-01 product naming: DECIDED as CipherContact (product); build repo remains OmniDB.
- [x] Scaffold pushed to https://github.com/AliMoyo1/OmniDB (main).
- [x] Phase 0 decisions D-01 through D-08 resolved 2026-08-20 (see decision-log.md and ADRs).

## 2026-08-20: Phase 0 decisions resolved

All blocking decisions recorded in docs/decisions/decision-log.md, with ADRs for the significant ones (003 access, 004A auth, 009 DNC, 005C workforce ID, 017 notifications, 020 retention). Architecture changes from the plan: Tailscale removed (LAN-only, IP access, unmanaged laptops); TOTP 2FA local auth; DNC label-and-skip with Team Captain override; 60-day completion-triggered retention with Team Captain Excel export. Target policy, exemptions, and bulk import deferred from the first pilot. Two flags stand: DNC-override legal review (D-02) and the export DLP note (D-09). Phase 1 is now unblocked.

Next (Phase 1, after Phase 0 sign-off):
- [ ] Locked dependency file with hashes.
- [ ] FastAPI app, opaque sessions, CSRF, default-deny authorization helpers.
- [ ] Initial PostgreSQL schema and Alembic baseline.
- [ ] Docker Compose, Caddy LAN HTTPS, Tailscale Serve, health checks.
- [ ] CI checks, and encrypted backup with a tested restore.
