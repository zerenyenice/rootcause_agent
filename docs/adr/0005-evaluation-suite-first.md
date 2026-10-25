# ADR-0005: Seeded evaluation suite built in phase 0, before agent tuning

- Status: Accepted
- Date: 2026-10-25
- Deciders: project owner, lead
- Related: PRD section 11, epic EP4

## Context

Agent quality cannot be judged from anecdotes. Prompt and skill changes have
non-local effects, and the phase gates are defined as pass rates on known cases.

## Decision

Build the synthetic manufacturing warehouse (deterministic baseline plus an
incident layer with 20 cases at three difficulty levels, a ground-truth
manifest and a scoring rubric) in phase 0, and run it in CI before any agent is
tuned. Phase gates read from this suite.

## Consequences

- Early investment before user-visible features.
- Every prompt or skill change is measured; regressions are visible per commit.
- The suite is itself code with tests; its generator and manifest are versioned
  with the product.
