# ADR-0006: Langfuse for agent tracing and prompt management

- Status: Accepted
- Date: 2026-10-25
- Deciders: project owner, lead
- Related: PROJECT.md sections 6 and 7, backlog task T009, ADR-0001

## Context

The DeepAgents orchestrator (ADR-0001) delegates to subagents, plans with
todos and calls SQL tools. Debugging a wrong conclusion needs the full run:
every model call with its prompt, every tool call and its result, per thread.
OpenTelemetry spans from `rootcause_core.telemetry` cover the API but not the
LLM-level detail. Prompts are versioned files today; the project owner asked
for the option to edit and roll back prompts without a release.

Constraints: customer data may not leave the deployment (PROJECT.md section
7), so the tool must be self-hostable; raw query results must not be stored
in traces; the agents stay on LangChain DeepAgents, so the integration has to
be a LangChain callback rather than a framework change.

Alternatives considered:

- **LangSmith**: first-party LangChain tracing and prompt hub, but a
  proprietary SaaS; self-hosting needs an enterprise licence.
- **Plain OpenTelemetry + a generic backend (Jaeger/Tempo)**: self-hosted and
  already wired, but has no LLM-aware views (generations, token usage,
  sessions) and no prompt management.
- **Langfuse**: open source (MIT core), self-hostable with Docker Compose,
  LangChain `CallbackHandler`, sessions/users/tags, prompt management with
  labels and versions, data masking hook in the SDK, OpenTelemetry-based SDK.

## Decision

Use Langfuse (Python SDK 5.x, server v4) for agent tracing and optional prompt
management.

- `rootcause_core.clients.langfuse` is the only module that imports the SDK
  (architecture guard). It builds one client per settings on a dedicated
  OpenTelemetry `TracerProvider`, so Langfuse never takes over the global
  provider owned by `rootcause_core.telemetry`.
- Every agent invocation uses `run_config(thread_id, ...)`
  (`OrchestratorHandle.config_for`): one trace per run, session id = thread
  id, callbacks propagated to subagents, model and tool calls.
- A mask runs on every traced value: credentials are redacted with the log
  redactor, SQL result rows are dropped (columns, counts and SQL kept).
- Prompts resolve through `PromptRegistry`: files by default; with
  `ROOTCAUSE_PROMPT_SOURCE=langfuse` the labelled version is fetched and any
  Langfuse failure falls back to the shipped file. `scripts/push_prompts.py`
  uploads the files as new versions.
- Disabled by default; a self-hosted server is available as the optional
  `observability` compose profile.

## Consequences

- Runs are debuggable per thread in a UI that understands LLM calls; with
  tracing off the system behaves exactly as before.
- Six more containers for a self-hosted server (web, worker, Postgres,
  ClickHouse, Redis, MinIO); Langfuse Cloud is an option only where data
  residency allows it.
- SDK 5.x needs a v4.59.0+ server; server upgrades follow the SDK.
- Remote prompts can diverge from the files in git; the registry falls back to
  the files and the push script keeps Langfuse seeded from git, which stays
  the reviewed source.
- Follow-ups: link prompt versions to generations in traces; evaluation runs
  (EP4) may report scores to Langfuse.
