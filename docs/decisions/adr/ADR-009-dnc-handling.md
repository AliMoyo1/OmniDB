# ADR-009: Explicit DNC handling

- Status: Accepted
- Date: 2026-08-20
- Owner: Product owner (legal review pending, D-02)
- Related decision: D-08

## Context

Numbers that must not be called are labeled do-not-call. The business wants the app to prevent calling them by default, while allowing a supervised exception.

## Decision

- DNC numbers are labeled and automatically skipped by the app. They are not served to agents as normal work.
- A Team Captain may override the skip to allow calling a specific DNC number, only after entering a justification. The override and its justification are recorded as an audit event.
- The number keeps its DNC label; the override is a logged, per-case exception, not a removal of the label.

## Alternatives considered

- Immediate global suppression with correction only by a higher privileged role (previous plan position). Changed at the product owner's direction to allow Team Captain override with justification.

## Consequences

Supervisors can handle edge cases without an administrator, at the cost of a lower bar for calling a DNC number.

## Security, privacy, and legal effect

Flag: if a DNC label represents an explicit customer opt-out, calling anyway may breach direct-marketing rules under the governing law (D-02). Recommended follow-up: legal review on whether explicit opt-outs may be overridden at all, and consider distinguishing imported-list DNC (Team Captain may override) from explicit customer opt-outs (non-overridable, or higher approval). Every override is audited so misuse is visible.

## Migration or rollback effect

Changes DNC behavior from the earlier immediate-global-suppression model. The plan text will be updated in a v0.4 pass.
