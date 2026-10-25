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
- Trace data protection (amended 2026-10-25 after backend and security
  review; PROJECT.md section 7):
  - **Tool outputs are denied by default.** The mask keeps tool output only
    for `write_todos`, `explain_sql` and the `run_sql` summary (columns, row
    count, truncation flag, SQL, result path); every other tool output,
    including `read_file`/`grep`/`ls` and subagent `task` results, is
    replaced by `<redacted tool output: NAME>`, also where generation inputs
    echo it.
  - **`run_sql` never puts rows in message text.** Rows go to the agent
    filesystem under `/query_results/<tool_call_id>.json`; the mask drops
    anything under `/query_results/` and `/large_tool_results/`.
  - **Exporter-level scrubbing.** The OTLP exporter is wrapped so span
    attributes, status descriptions, events and the resource are scrubbed
    too; errors are recorded by exception type only.
  - **Secrets.** Every `SecretStr` setting (and the database password inside
    the DSN) feeds both the log and the trace redactor; URL passwords
    containing `@` and libpq `password=` pairs are redacted by pattern.
  - **PII pass** (`ROOTCAUSE_LANGFUSE_MASK_PII`, on by default): e-mails,
    Turkish and US phone numbers, IBANs and 11-digit Turkish national ids in
    trace text become markers.
  - **Isolation.** Each run is a fresh trace started from an empty
    OpenTelemetry context (never a child of a FastAPI span) and carries the
    opaque `tenant_id` / `connection_id`.
  - **Transport.** `ROOTCAUSE_LANGFUSE_HOST` must be https unless it is a
    loopback address or a single-label compose service name.
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
- **Langfuse UI access exposes every tenant's questions and SQL.** Langfuse
  project membership is admin-only; grant it like database access, not like
  a dashboard.
- **Prompt source defaults to `files` in prod.** Switching to `langfuse` is a
  team decision; `scripts/push_prompts.py --apply` runs with an operator's
  key, never the API's runtime key. Prompts resolve when the orchestrator is
  built (process start), so a newly labelled version is picked up on restart
  (and after the cache TTL for new builds), not mid-run.
- **Langfuse Cloud** is allowed only with the customer's written data
  residency sign-off; the default is the self-hosted profile.
- **`LANGFUSE_DEBUG` must never be set in production**: SDK debug logging
  prints full, unmasked span contents to the process log.
- **Residual risk: paraphrased personal data.** Masking removes tool outputs,
  rows, credentials and pattern-shaped PII, but the model may restate a
  customer value in its own words (a name, an address) in its reasoning or
  answer; no regex catches that. Trace access control (above) is the
  remaining control.
- Follow-ups: link prompt versions to generations in traces; evaluation runs
  (EP4) may report scores to Langfuse; see the T009 plan for the
  data-protection follow-ups.
