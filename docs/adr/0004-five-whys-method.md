# ADR-0004: 5 Whys as the v1 root-cause method; other methods are later skills

- Status: Accepted
- Date: 2026-10-24
- Deciders: project owner, lead
- Related: PRD section 4, epic EP6, awesome-skills/5-whys-skill

## Context

Several root-cause methods exist (5 Whys, fishbone/Ishikawa, fault tree,
Kepner-Tregoe). The product promise is an evidence-linked why-chain an analyst
can follow, and the team has a well-specified 5 Whys skill to port.

## Decision

The root-cause subagent implements 5 Whys, adapted so every level is answered by
an executed query: problem template, candidate generation and query testing per
level, branching when more than one cause explains over 25% of the delta,
stop conditions, backward counterfactual validation, countermeasures, and the
skill's pitfalls as guardrails. Other methods are added later as additional
skills loaded by the same agent, not as separate agents.

## Consequences

- One method to evaluate and tune in v1; the WhyChain contract is stable.
- Problems that are better served by a causal tree may be forced into chains;
  branching mitigates this for v1.
- Later methods must produce the same WhyChain contract to reuse the UI and
  the evaluation harness.
