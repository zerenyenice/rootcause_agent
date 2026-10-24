# Project Context

<!--
Classification: PROJECT-TEAM-MAINTAINED
Owner: Project team / Product owner / Tech lead / Business analyst / Solution architect
Editable by coding agents: Only when explicitly instructed by the project team
Purpose: Provide project-specific customer, business, and technical context so agents can develop the right system
-->

> Populated from `docs/PRD.md` on 2026-10-25 at the project owner's instruction.
> The PRD is the detailed reference; this file is the summary the agents gate on.
> Living PRD: https://claude.ai/artifact/CwPHDcFKQf5vnBrowK54NB

## 1. Project Summary

```text
RootCause is a multi-agent system that answers "why did this metric move?"
against a customer's own SQL database and gets better with every investigation.

A LangChain DeepAgents orchestrator delegates to three subagents: an anomaly
detection agent (finds and ranks what changed), a 5 Whys root-cause agent
(traces each anomaly to an evidence-backed root cause), and a metadata agent
(turns questions into correct SQL using a semantic catalog). All agents read a
shared metadata catalog of the database and write what they learn into an
OpenViking context store, so solved cases become lessons and skills.

Success means an analyst can type "orders dropped 18% last Tuesday, why?" and
receive a validated why-chain where every step links to a query a human can
re-run, within about ten minutes, and the system reuses past lessons on
similar cases. See docs/PRD.md section 1.
```

## 2. Customer Expectations and Business Outcomes

```text
Expected outcomes (v1 targets, PRD section 1):
- Root-cause acceptance rate: 70% of investigations marked correct by analysts.
- Median time from question to validated why-chain: under 10 minutes.
- 100% of why-chain steps backed by an executed query.
- 40% of investigations reuse at least one stored lesson after 50 solved cases.
- SQL generation error rate under 5%.

Business problem: analysts spend hours manually slicing data to explain KPI
movements, conclusions are rarely evidence-linked, and knowledge from past
investigations is lost. The customer judges success by trust (evidence on
every claim), speed, and the system visibly learning from feedback.

Non-goals for v1: writing to customer databases, real-time streaming
detection, replacing BI dashboards, application code debugging.
```

## 3. Users and Workflows

```text
Roles (PRD section 2):
- Business or data analyst: asks why a KPI changed, reviews evidence, accepts
  or rejects conclusions, triggers scans. Surface: Investigate (chat) UI.
- Data engineer: registers databases, runs metadata indexing, reviews and
  corrects tables, columns, categorical values, joins, metrics, glossary.
  Surface: Catalog and Connections UI.
- Platform admin: credentials, model keys, memory retention, roles and
  per-connection access, audit. Surface: Settings UI and config.

Core flow: analyst asks a why-question -> orchestrator frames the problem in
the 5 Whys template and plans -> metadata agent confirms metric, table, grain
-> anomaly agent confirms and bounds the change and decomposes by segment ->
root-cause agent iterates why-questions with queries, validates backwards,
proposes countermeasures -> orchestrator writes the report -> analyst gives a
verdict -> memory service commits the case and extracts a lesson.

Approval steps: expensive queries (above a per-connection scan threshold)
pause for human approval. Catalog machine-generated enrichments and inferred
joins wait in a review queue. Verdicts (accept, reject, partial) are the
feedback signal that drives lesson extraction.
```

## 4. Scope and Requirements

```text
In scope (v1): anomaly detection on connected SQL databases (confirm and scan
modes); 5 Whys root-cause subagent with query-backed evidence, branching,
backward validation and countermeasures; database connection service with a
read-only, audited tool surface; metadata catalog (tables, columns,
categorical values, joins, metrics, glossary, events) with hybrid search and
versions; OpenViking memory with per-agent namespaces, lessons and skill
promotion; web UI for chat, catalog management and connections; evaluation
dataset (synthetic manufacturing warehouse, 20 seeded incidents at easy,
medium, difficult levels) with a scoring harness in CI.

Out of scope (v1): write access to customer databases; streaming detection;
BI dashboards; code-level debugging; root-cause methods other than 5 Whys
(fishbone, fault tree are later skills); Kubernetes (Docker Compose for
pilots).

Non-functional: first token under 3 s; confirm-mode check under 60 s;
why-chain under 10 min; catalog search under 300 ms; agent runs resume from
the checkpointer; per-investigation token budget with hard stop; English and
Turkish in chat and reports. Coding standards per CLAUDE.md: TDD with 80%+
coverage, immutability, small files, validated inputs.

Business rules: nothing enters a report without an executed query; a why
level that restates the symptom at another grain is not a cause; conclusions
name processes, pipelines or decisions, never individuals; anomalies need
agreement from two detection skills or one at very high confidence; lessons
are promoted to skills only after three supporting accepted cases or a human
playbook flag.

Delivery phases and gates: Phase 0 Foundations (question-to-SQL works on the
demo warehouse); Phase 1 Anomaly agent (scan finds 90% of seeded anomalies);
Phase 2 Root-cause agent (70% accepted on 20 seeded cases); Phase 3 Memory
(a repeated case is solved using a lesson); Phase 4 Catalog UI and hardening
(pilot on one real customer database). Backlog: docs/backlog/backlog.yaml and
the GitHub project "RootCause".
```

## 5. Data, Documents, and Integrations

```text
Customer databases: Postgres, MySQL, SQL Server, Snowflake, BigQuery,
ClickHouse, DuckDB via SQLAlchemy dialects; Postgres and DuckDB are the
reference test targets. Access is read-only, enforced by database role and
by a sqlglot-based validator (single SELECT/WITH only, LIMIT injection,
timeouts, row caps). Every query is audit-logged.

Owned stores: Postgres 16 with pgvector for the app, catalog, audit, anomaly
records and LangGraph checkpointer/store; OpenViking server (viking://
namespaces per tenant, user and agent) for memories, lessons and skills.

Source-of-truth rules: the published catalog version is what agents read; a
human edit in the catalog overrides machine enrichment and becomes a memory;
PROJECT.md and docs/PRD.md define requirements; docs/backlog/backlog.yaml
defines the task list; the evaluation manifest cases.json defines ground
truth for scoring.

Evaluation data: synthetic discrete-manufacturing warehouse (2 plants, 8
lines, 40 machines, one year hourly) generated by packages/evals with a
baseline layer and an incident layer, outputs DuckDB, Postgres dump, CSVs,
cases.json and a data dictionary. See docs/PRD.md section 11.

External references: awesome-skills/5-whys-skill (method ported into the
root-cause agent), LangChain DeepAgents docs, volcengine/OpenViking.

Repository layout: apps/api (FastAPI + LangGraph Server), apps/web
(Next.js), packages/{agents,tools,catalog,connections,anomaly,memory,evals},
docs/, deploy/.
```

## 6. Technology and Architecture Direction

```text
Agents: deepagents, langchain, langgraph on Python 3.12 with uv. One
DeepAgents orchestrator (planning via write_todos, task tool for isolated
subagents, virtual filesystem, CompositeBackend routing /memories/ and
/skills/ to a persistent StoreBackend, Postgres checkpointer, interrupt_on
for expensive run_sql). Three subagents declared via SubAgent specs with
their own prompts, tool subsets and models. Structured Pydantic reports
(AnomalyRecord, WhyChain, MetadataReport). Skills as SKILL.md with
progressive disclosure.

Models: Claude Sonnet 5 for anomaly and metadata agents; Claude Opus 5 or
Fable 5.1 for root cause and lesson extraction; provider abstracted via
init_chat_model; prompt caching on; per-role configuration.

Services: FastAPI mounted beside LangGraph Server; SQLAlchemy 2, sqlglot,
pyarrow; pandas, numpy, scipy, statsmodels, ruptures, scikit-learn,
statsforecast for detection; Postgres 16 + pgvector with hybrid search;
OpenViking 0.3.x server in Docker behind a thin MemoryStore adapter.

UI: Next.js 15, React, LangGraph JS SDK streaming, Tailwind, Recharts.
Observability: OpenTelemetry spans, structured logs, optional LangSmith.
Quality: pytest, ruff, mypy, Playwright, golden-case evaluation suite in CI.
Deployment: Docker Compose single host for pilots; Kubernetes later.

Architectural constraints: the orchestrator never queries a database
directly; all data access goes through the tool layer; query results above
200 rows go to the virtual filesystem; catalog lookups return L0 summaries
first; subagents run isolated and return compressed reports.

Decisions taken (see docs/adr/ when created): DeepAgents, no custom LangGraph
graph in v1; read-only enforced twice; pgvector for catalog and OpenViking
for memory, no second vector database; 5 Whys as the method; evaluation
suite built in phase 0 before agent tuning.
```

## 7. Security, Privacy, and Risk Context

```text
Security: read-only database roles plus a SQL validator; credentials
encrypted at rest (Fernet, key from a secrets backend), never returned by
any API, agents receive connection ids only; audit log immutable; OIDC login
with roles analyst, data engineer, admin and per-connection access; tenant
isolation on catalog, memory, threads and audit; rate limiting on endpoints;
CSRF protection; no secrets in the repo (.env.example only).

Privacy: only aggregates and SQL leave the customer database for detection;
sample rows may be shown in the UI but are never persisted to memory;
configurable column masking in the catalog; users can view, edit and delete
memories about them; memories scoped per tenant and connection unless
marked generic.

Compliance and licensing: OpenViking server is AGPLv3 and is run unmodified
as a separate service; legal review before pilot. Python client use only.

Key risks (PRD section 13): wrong SQL presented confidently; plausible but
false causes (confounders, Simpson's paradox); OpenViking maturity and API
churn; indexing cost on large warehouses; token cost; anomaly false
positives; lessons that encode one-offs; dialect edge cases. Mitigations
include catalog-approved joins only, counterfactual validation per link,
thin adapters, sampling and caps, budgets, ensemble agreement, promotion
thresholds, and a per-dialect test matrix.

Assumptions: pilot customer can provide a read-only replica; deployment is a
single host with Docker Compose; English and Turkish are the required
languages.
```

## 8. AI / Agentic Behavior and Acceptance Criteria

```text
Agents must: ground every claim in an executed query and link it; restate
vague problems in the what/when/where/impact template and ask the user to
quantify when needed; branch when more than one cause explains over 25% of a
delta; validate why-chains backwards with counterfactual checks; report
partial results honestly when budgets are exhausted; cite the memories,
lessons and skills that informed an answer; write lessons only after a human
verdict.

Agents must not: write to customer databases; run anything but single
SELECT/WITH statements; present an unverified hypothesis as a root cause;
name individuals as causes; store raw rows with personal data in memory;
retrieve one tenant's or connection's memories for another.

Human approval: queries above the per-connection scan threshold pause via
interrupt_on; machine-generated catalog enrichments and low-confidence joins
require review; skill promotion can be forced or retired by an admin.

Explainability: WhyChain levels show question, answer, SQL, row summaries and
confidence; AnomalyRecords record techniques fired with parameters so runs
are reproducible; the UI shows "what informed this".

Evaluation and acceptance: the seeded manufacturing suite (20 cases) scores
detection (window overlap >= 50%, correct segment), root cause (LLM judge >= 4
of 5 plus type match), chain validity (every level has executed SQL), decoy
resistance (naming a decoy fails), efficiency (reported), false positives
(at most 1 benign event per dev-set scan). Pass targets: easy 90%, medium
70%, difficult 50% in v1. Phase gates: phase 0 question-to-SQL works on the
demo warehouse; phase 1 scan finds 90% of seeded anomalies; phase 2 70%
accepted on 20 seeded cases; phase 3 a repeated case is solved using a
lesson; phase 4 pilot on one real customer database. Final acceptance for v1
is the phase 4 pilot with the success metrics in section 2 measured.
```
