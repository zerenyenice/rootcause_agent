# ADR-0001: DeepAgents orchestrator with isolated subagents, no custom LangGraph graph in v1

- Status: Accepted
- Date: 2026-10-24
- Deciders: project owner, lead
- Related: PRD section 3, PROJECT.md section 6, epic EP3

## Context

The investigation flow is long-running and multi-step: frame the problem, plan,
confirm the metric, detect and decompose the anomaly, iterate why-questions with
queries, validate, report. It needs planning state, a place for large query
results that must not enter the model context, delegation to specialists with
their own prompts and tools, and persistence across restarts. Alternatives were
a hand-built LangGraph state machine, a single large agent with many tools, or
the LangChain DeepAgents harness.

## Decision

Use `create_deep_agent` as the orchestrator with three isolated subagents
(anomaly, root cause, metadata) declared as SubAgent specs. Rely on the harness
for `write_todos` planning, the `task` tool, the virtual filesystem, Agent Skills
loading, and backends. A CompositeBackend routes `/memories/` and `/skills/` to a
persistent StoreBackend and keeps everything else per thread. The LangGraph
checkpointer and store live on Postgres. `interrupt_on` guards expensive
`run_sql` calls. No custom LangGraph graph is written in v1.

## Consequences

- Less code and faster delivery; subagent context isolation is built in.
- The harness's conventions (filesystem, skills directory layout, report shape)
  constrain our design; we follow them rather than fight them.
- Structured subagent reports (Pydantic models) are our own convention on top.
- If a flow needs branching the harness cannot express, the escape hatch is a
  custom graph for that subagent only, recorded in a new ADR.
