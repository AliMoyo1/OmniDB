# CipherContact Plan - v0.2 to v0.3 Change Log

Date: 2026-08-20
Applies to: CipherContact - Detailed Implementation Plan (v0.2 to v0.3)
Build repository: https://github.com/AliMoyo1/OmniDB

## Corrections applied from the v0.2 review

- [x] Session-store authority: PostgreSQL is authoritative for session validity and revocation; Redis is a non-authoritative cache. (Invariant 21, section 9.3, services tables 8.3 and 14.1, ADR-018)
- [x] Phone fingerprint hardened to keyed HMAC-SHA256 with separated, versioned key custody. (Invariant 22, section 9.5, contacts and suppression_entries, ADR-019)
- [x] Agent callback list masked: returns references and times only, raw number revealed by leasing. (Invariant 3, section 11.12, API 12.2)
- [x] Notification and approval channel: in-application inbox as primary, email optional. (D-23, section 11.15, ADR-017)
- [x] Phase 4 split into 4A, 4B, 4C with re-estimate; target and exemption subsystem flagged as deferrable. (section 20)
- [x] First pilot slice re-sliced: core call-work loop first; workforce import, transfers, targets, and exemptions moved to a second slice. (section 30)

## Repository

- [x] Build repository set to https://github.com/AliMoyo1/OmniDB. Product name kept as CipherContact pending confirmation of a rename. (header revision note, section 2.1, D-01)

## Reverted

- [x] Quality monitoring and agent evaluation were briefly added, then fully removed on 2026-08-20 after the requester clarified it was a typo. Removed: section 31, objective 15, the in-app-audio non-goal, decision D-24, the two quality invariants, one risk row, one ADR, and the Phase 4 and pilot-slice references. Incidental pre-existing "quality review" wording was neutralized to plain "review". "Data quality" and "lead quality" wording, and all infrastructure monitoring (section 18), were kept because they are unrelated. Scope is strictly database DLP management and distribution.

## Open question flagged to the owner

- Product naming: keep CipherContact as the product inside the OmniDB repo, or rename the product to OmniDB?
