# ADR-013: AI excluded from the MVP

- Status: Accepted
- Date: 2026-08-20
- Owner: Product owner
- Related decision: scope (plan section 3.2 and Phase 7)

## Context

The original brief mentioned AI lead scoring, sentiment analysis, and automated routing. These carry fairness, privacy, and feedback-loop risks and are not needed to prove the core system.

## Decision

No AI in the MVP. Lead scoring, sentiment analysis, and automated assignment stay out until a separate approval gate (plan Phase 7) is passed. The feature flag `ai_enabled` is permanently false for the MVP.

## Alternatives considered

- Include a simple scoring model early. Rejected: routing high-scoring leads to historically successful agents can create a self-reinforcing loop, and there is no validated baseline.

## Consequences

A simpler, safer MVP. Analytics remain deterministic and reproducible from immutable events.

## Security and privacy effect

Avoids automated decisions about people, and avoids inference of consent or DNC.

## Migration or rollback effect

None. AI would be additive behind its own gate.
