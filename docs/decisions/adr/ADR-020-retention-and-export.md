# ADR-020: Campaign-completion retention and export

- Status: Accepted
- Date: 2026-08-20
- Owner: Product owner (privacy review, D-09)
- Related decision: D-09

## Context

Each campaign uses a primary database of contact numbers. The business wants completed campaign data exported for its records and then removed, to minimize retained personal data.

## Decision

- A campaign database is complete when every number has a final disposition, the calling agent recorded, and a usage record showing the number was used in the campaign.
- On completion, a visible countdown of 60 days begins, shown prominently in the app.
- A Team Captain may export the completed database to Excel and delete it from the system before the countdown ends.
- If not deleted manually, the system auto-deletes the completed database 60 days after completion.

## Alternatives considered

- The plan's per-category retention schedule. This decision replaces it for campaign contact data with a concrete completion-triggered rule.

## Consequences

Strong data minimization: completed contact data does not linger. A clear operational handoff (export then delete) with a hard 60-day backstop.

## Security and privacy effect

Flag: the Excel export contains all raw numbers, dispositions, and agent names. It is an authorized, audited exception to the no-raw-export rule and is restricted to a Team Captain. Once exported, the file is outside the app's controls and is the exporter's responsibility. Auto-delete removes only the in-system copy. Audit records who exported and who deleted. Immutable audit events and DNC-suppression evidence are retained separately and are not removed by this rule.

## Migration or rollback effect

Changes the retention model. The plan retention section will be updated in a v0.4 pass.
