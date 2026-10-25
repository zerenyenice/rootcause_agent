# MVP plan

Status: approved by the user on 2026-10-25; extended the same day from a thin
agent slice to a runnable platform (PROJECT.md §4). Work outside the MVP is
labelled `later` on the board and waits for the gate.

## MVP definition

The platform runs from Docker Compose. In the web UI an analyst:

1. adds a database connection (Postgres or DuckDB) and tests it;
2. manages its metadata: selects the tables and columns the agent may use,
   adds table and column descriptions, and builds the search index;
3. picks the connection in chat and asks "why did X change?". The answer
   streams in. The orchestrator delegates to the 5 Whys root-cause subagent,
   which runs validated read-only SQL on that connection and returns a
   WhyChain report where every level cites an executed query.

Every agent run is traced in Langfuse.

**MVP gate:** `docker compose up` brings up every service; the flow above
works end to end on the demo warehouse; the easy evaluation cases E1 to E3
all pass (3 runs per case, measured on the configured model).

Already done (slice v1): DuckDB read-only executor, schema tools, 5 Whys
skill, WhyChain, root-cause subagent, investigate(), evaluation runner,
minimal chat UI. Measured on Azure gpt-5.1: E2 6/6, E1 0/6, E3 0/6.

## Services

| Service | Code | Port | Role |
|---|---|---|---|
| `web` | `web/` (Next.js) | 3000 | chat, connections and catalog pages |
| `api` | `rootcause.api.main:app` (FastAPI) | 8000 | backend for the UI: connections, catalog, index |
| `agent` | `rootcause.agent_service.main:app` (FastAPI) | 8100 | deep-agent service: threads, streaming runs, approvals |
| `migrate` | `python -m rootcause.db.cli migrate` | — | one-shot Alembic upgrade before `api` and `agent` |
| `postgres` | pgvector image | 5432 | app DB, catalog, embeddings, LangGraph checkpoints |
| Langfuse | compose profile `langfuse` | 3001 | tracing (or a Langfuse Cloud host via env) |

`api` and `agent` share the `rootcause` package and one image with different
commands. Both read settings from env. No authentication in the MVP: services
bind to localhost and the README says so (auth is `later`).

## API contracts

`api` uses the existing response envelope (`rootcause.api.envelope`).

- `GET/POST /api/v1/connections`, `GET/PATCH/DELETE /api/v1/connections/{id}`.
  Body: `name`, `kind` (`postgres` | `duckdb`), `postgres`: host, port,
  database, user, password, sslmode; `duckdb`: path. Passwords are encrypted
  at rest and never returned.
- `POST /api/v1/connections/{id}/test` → reachable, read-only verified,
  server version.
- `POST /api/v1/catalog/{connection_id}/import` → introspects schemas, tables,
  columns (types, row counts) into the catalog; keeps existing descriptions.
- `GET /api/v1/catalog/{connection_id}/tables` (with columns);
  `PATCH /api/v1/catalog/tables/{id}` (`selected`, `description`);
  `PATCH /api/v1/catalog/columns/{id}` (`selected`, `description`).
- `POST /api/v1/catalog/{connection_id}/index` → embeds selected tables and
  columns (name, type, description, categorical values) into pgvector;
  `GET .../index` → status and counts.

`agent` speaks the LangGraph streaming protocol, so the web keeps `useStream`
through the SDK's `FetchStreamTransport`:

- `POST /threads` → `{thread_id}`; `GET /threads/{id}/state` → messages and
  pending interrupts.
- `POST /threads/{id}/runs/stream`, body `{input, context: {connection_id},
  command}` → SSE events `metadata`, `values`, `messages`, `updates` (with
  `__interrupt__`), `error`. `command.resume` answers an approval.
- The graph's executor and schema tools come from the thread's connection
  and its selected catalog entries; a Postgres checkpointer persists threads.

## Work (each row is one PR batch)

| Wave | Branch | Tier | Issues | Content |
|---|---|---|---|---|
| A | `fix/agent-final-answer` | Standard | #138, #139 | always return a WhyChain (partial when a budget ends the run); the orchestrator never reports findings it did not receive; no orphan tool messages; per-role `reasoning_effort` setting |
| A | `feat/agent-service` | Standard | new | agent service per the contract, Postgres checkpointer, Langfuse callback per run, chat UI on `FetchStreamTransport` |
| A | `feat/connections-api` | Sensitive | new | connection CRUD with encrypted credentials, test (verifies read-only), introspection, Postgres read-only executor (read-only transaction, statement timeout, validated SQL) |
| A | `ci/compose-app-services` | Light | new | Dockerfiles and compose services `web`, `api`, `agent`, `migrate`; Langfuse profile wired to the agent; `.env.example` |
| B | `feat/catalog` | Standard | new | catalog models and migration, import, selection and descriptions, pgvector index, agent schema tools and a `search_catalog` tool reading the selected catalog, per-thread connection |
| B | `feat/admin-ui` | Standard | new | Connections and Catalog pages, connection picker in chat |
| C | gate | — | — | seed the demo warehouse as a connection with its catalog; run E1 to E3 three times each; fix what blocks |

Wave A runs in parallel. Wave B starts when `feat/connections-api` merges;
the UI can build against the contracts above with mocked responses.

## After the MVP (label `later`)

Medium and difficult incidents, LLM judge, anomaly agent, memory and lessons,
auth and tenancy, extra dialects, catalog versions and glossary, performance
and pilot hardening.

## Process

The process tiers in CLAUDE.md apply; related tasks share one PR.
