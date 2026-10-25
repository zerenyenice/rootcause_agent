# MVP plan

Status: approved by the user on 2026-10-25. Replaces the phase-by-phase
order of the backlog until the MVP gate passes.

## Why

After the foundation tasks, nothing answers a "why did X change?" question
end to end. Most effort went into scaffolding, hardening and review rounds.
The MVP is one thin vertical slice through the system. Everything else waits
until that slice works and is measured.

## MVP definition

An analyst asks "why did X change?" about the demo manufacturing warehouse in
a minimal chat UI. The DeepAgents orchestrator frames the problem and
delegates to the 5 Whys root-cause subagent. The subagent inspects the schema,
runs validated read-only SQL and returns a WhyChain report. Every why level
cites an executed query.

**MVP gate:** the evaluation runner scores the easy cases E1 to E3 and all
three pass (PROJECT.md target for easy cases is 90%). This also meets the
Phase 0 gate: question-to-SQL works on the demo warehouse.

## Slice tasks (GitHub label `mvp`, milestone "MVP")

| # | Task | Scope for the MVP |
|---|------|-------------------|
| 0 | Single-package repo layout | `src/rootcause`, one `tests/` tree, `web/`; stubs removed |
| 1 | #21 run_sql executor | DuckDB read-only executor over the eval warehouse, SQL validator, row cap, results to the agent filesystem. Audit table, Arrow and Postgres later |
| 2 | #22 schema tools | `list_tables`, `describe_table`, `sample_rows` from the warehouse plus the generator's table docs. No catalog |
| 3 | #56 5 Whys skill | SKILL.md port with query-backed evidence rules |
| 4 | #35 + #60 WhyChain | Pydantic WhyChain model and markdown rendering |
| 5 | #61 + #37 root-cause subagent | SubAgent spec wired into the orchestrator, framing in the orchestrator prompt, a simple step budget |
| 6 | #45 evaluation runner | Run the agent over E1 to E3, score with keywords and chain validity (every level has executed SQL). LLM judge later |
| 7 | #63 + #64 minimal chat UI | Next.js page streaming from LangGraph Server; messages and the final report as markdown |

Order: 0, then 1 and 2 in parallel, then 3 and 4, then 5, then 6 and 7 in
parallel.

## After the MVP (label `later`)

In rough order: medium and difficult incidents, LLM judge, anomaly agent,
catalog, memory and lessons, management UI, auth and tenancy, extra
dialects, performance and pilot hardening. Everything currently on the board
that is not `mvp` is `later` and is re-planned when the MVP gate passes.

## Process

The process tiers in CLAUDE.md apply. Most slice tasks are Standard tier.
The run_sql executor is Sensitive tier (SQL execution).
