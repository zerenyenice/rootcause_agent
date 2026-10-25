# Architecture Decision Records

One file per decision, numbered, never edited after acceptance except to change
`Status` (Proposed, Accepted, Deprecated, Superseded by ADR-NNNN). New decisions
that reverse an old one get a new number and link back.

Template: [0000-template.md](0000-template.md). Index:

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-deepagents-orchestrator.md) | DeepAgents orchestrator with isolated subagents, no custom LangGraph graph in v1 | Accepted |
| [0002](0002-read-only-enforced-twice.md) | Read-only database access enforced at the role and at the SQL validator | Accepted |
| [0003](0003-pgvector-catalog-openviking-memory.md) | Postgres with pgvector for the catalog, OpenViking for memory, no second vector store | Accepted |
| [0004](0004-five-whys-method.md) | 5 Whys as the v1 root-cause method; other methods are later skills | Accepted |
| [0005](0005-evaluation-suite-first.md) | Seeded evaluation suite built in phase 0, before agent tuning | Accepted |
| [0007](0007-three-database-roles-append-only-audit.md) | Three database roles for the application database; append-only audit log | Proposed |
