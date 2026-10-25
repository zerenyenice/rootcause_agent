# deploy

Docker Compose stack for local development and single-host pilots. Task
T002 (`docs/plans/T002-docker-compose-stack.md`); owner agent `devops`.

| File | Purpose |
|------|---------|
| `docker-compose.yml` | `postgres` (Postgres 16 + pgvector) and `openviking` (OpenViking 0.3.x server); placeholders for `api`/`web` |
| `.env.example` | Every variable the stack needs, placeholder values only. Copy to `deploy/.env` (gitignored) |
| `postgres/init.sql` | First-start bootstrap: `CREATE EXTENSION vector`, schemas `app`, `catalog`, `audit` |
| `openviking/ov.conf` | Secret-free OpenViking config template; `${VAR}` references are expanded by the server from the container environment |
| `docker-compose.yml` profile `observability` | Optional self-hosted Langfuse v4 (web, worker, Postgres, ClickHouse, Redis, MinIO) for agent traces and prompt management (T009, ADR-0006) |

Both services publish their ports on `127.0.0.1` only, restart
`unless-stopped`, persist to named volumes (`rootcause_postgres_data`,
`rootcause_openviking_data`) and have healthchecks, so `depends_on:
condition: service_healthy` works for the application services.

## Start

```bash
cp deploy/.env.example deploy/.env          # once; then replace EVERY placeholder
openssl rand -hex 32                         # for POSTGRES_PASSWORD / OPENVIKING_ROOT_API_KEY
docker compose -f deploy/docker-compose.yml up -d
```

Compose loads `deploy/.env` automatically (the project directory is the
directory of the compose file), so `cd deploy && docker compose up -d` is
equivalent. The compose file uses `${VAR:?}` for every secret, so a missing
value fails before any container starts instead of running with a default.

What you need in `deploy/.env`:

- `POSTGRES_PASSWORD` (user/db default to `rootcause`).
- `OPENVIKING_ROOT_API_KEY`: OpenViking refuses to start on `0.0.0.0`
  (required for Docker port mapping) without a root key. The root key is
  admin-only; application code should use a per-account **user key** (see
  "Create the application key" below).
- `OPENVIKING_EMBEDDING_API_KEY` plus provider/model/dimension (defaults:
  OpenAI, `text-embedding-3-small`, 1536) and `OPENVIKING_VLM_MODEL`. The
  VLM provider/base URL/key default to the embedding ones. Provider ids and
  model/dimension pairs: upstream
  [configuration guide (v0.3.24)](https://github.com/volcengine/OpenViking/blob/v0.3.24/docs/en/guides/01-configuration.md).
  For air-gapped setups use `OPENVIKING_EMBEDDING_PROVIDER=ollama` with a
  local `OPENVIKING_EMBEDDING_API_BASE`.

## Verify

```bash
docker compose -f deploy/docker-compose.yml ps            # both services "healthy"

# Postgres: pgvector installed, three schemas present
docker compose -f deploy/docker-compose.yml exec postgres \
  psql -U rootcause -d rootcause -c '\dx' -c '\dn'

# OpenViking: liveness (no auth) and readiness (also checks the embedding provider)
curl -fsS http://127.0.0.1:1933/health     # {"status":"ok"}
curl -fsS http://127.0.0.1:1933/ready      # {"status":"ready","checks":{...,"embedding":"ok",...}}
```

If `/health` answers `503 {"status": "pending_initialization", ...}` the
server could not see its config file: check that
`deploy/openviking/ov.conf` is mounted (`docker compose config` shows the
bind mount) and read `docker compose logs openviking`. The upstream Web
Studio UI is served at `http://127.0.0.1:1933/studio`.

### Create the application key

OpenViking runs in `api_key` mode. Create an account and its first admin
user once; the returned `user_key` is what the API (T080 memory adapter)
will use. Keep it in `deploy/.env` / the API's `.env`, never in the repo.

```bash
curl -fsS -X POST http://127.0.0.1:1933/api/v1/admin/accounts \
  -H "X-API-Key: $OPENVIKING_ROOT_API_KEY" -H 'Content-Type: application/json' \
  -d '{"account_id": "rootcause", "admin_user_id": "api"}'
# -> {"result": {"account_id": "rootcause", "admin_user_id": "api", "user_key": "..."}}
```

## Reset

```bash
docker compose -f deploy/docker-compose.yml down        # stop, keep data
docker compose -f deploy/docker-compose.yml down -v     # stop AND delete both volumes
```

`postgres/init.sql` runs only against an empty data directory, so a change
to it needs `down -v`. OpenViking's config is a template outside its data
volume: editing `deploy/.env` or `openviking/ov.conf` takes effect on
`docker compose restart openviking`, without a reset.

## Pins and upgrades

- `pgvector/pgvector:0.8.7-pg16` (official pgvector build on the official
  postgres 16 image).
- `ghcr.io/volcengine/openviking:v0.3.24`, the last 0.3.x release.
  PROJECT.md section 6 and ADR-0003 pin OpenViking to 0.3.x; upstream has
  moved on to 0.5.x, so moving off 0.3.x is an ADR/PROJECT.md decision, not
  a compose bump.

Both are exact tags; the multi-arch manifest digests observed at pin time are
recorded as comments in `docker-compose.yml`. Bump either via a `chore(deploy):`
commit after reading the upstream release notes; CI (`compose-config` job)
re-validates the file on every PR.

## Langfuse (optional `observability` profile)

Agent runs (orchestrator, subagents, model and tool calls) can be traced in
Langfuse, and prompts can be managed there (T009,
[ADR-0006](../docs/adr/0006-langfuse-observability.md)). Use Langfuse Cloud
or the self-hosted server in this compose file; the profile is not started by
a plain `docker compose up`.

```bash
# in deploy/.env: replace every LANGFUSE_* placeholder (`openssl rand -hex 32`)
docker compose -f deploy/docker-compose.yml --profile observability up -d
docker compose -f deploy/docker-compose.yml --profile observability ps   # all "healthy"
# Langfuse UI: http://localhost:3000
```

- Server **v4** (`langfuse/langfuse:4.70.0`, `langfuse/langfuse-worker:4.70.0`).
  The Python SDK 5.x pinned in `packages/core` supports self-hosted
  Langfuse v4.59.0 or later only; v3 servers are not supported.
- Six services: `langfuse-web` (UI + API, `127.0.0.1:${LANGFUSE_PORT:-3000}`),
  `langfuse-worker`, `langfuse-postgres` (its own database, separate from
  the RootCause one), `langfuse-clickhouse`, `langfuse-redis`,
  `langfuse-minio` (S3, `127.0.0.1:${LANGFUSE_MINIO_PORT:-9090}` for media
  uploads). ClickHouse, Redis and Postgres are not published on the host.
  Usage telemetry to Langfuse is turned off.
- Secrets: `LANGFUSE_NEXTAUTH_SECRET`, `LANGFUSE_SALT`,
  `LANGFUSE_ENCRYPTION_KEY` (exactly 64 hex characters),
  `LANGFUSE_DB_PASSWORD`, `LANGFUSE_CLICKHOUSE_PASSWORD`,
  `LANGFUSE_REDIS_PASSWORD`, `LANGFUSE_MINIO_PASSWORD`. Compose interpolates
  the whole file whatever the active profile, so these must be present in
  `deploy/.env` even when the profile is not used (any placeholder value is
  enough then). Use hex values: the database password is embedded in a URL.
- First user and API keys: either sign up in the UI and create project keys
  under Project settings > API keys, or set `LANGFUSE_INIT_ORG_ID`,
  `LANGFUSE_INIT_PROJECT_ID`, `LANGFUSE_INIT_USER_EMAIL` and
  `LANGFUSE_INIT_USER_PASSWORD` before the first start; the project is then
  created with `ROOTCAUSE_LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` as its keys.

Point RootCause at it (the API's `.env` or process environment):

```bash
ROOTCAUSE_LANGFUSE_ENABLED=true
ROOTCAUSE_LANGFUSE_HOST=http://localhost:3000
ROOTCAUSE_LANGFUSE_PUBLIC_KEY=pk-lf-...
ROOTCAUSE_LANGFUSE_SECRET_KEY=sk-lf-...
```

Each run is one trace whose session id is the LangGraph thread id. Before
export, URL passwords are redacted and SQL result rows are dropped (column
names, row counts and the SQL text are kept), per PROJECT.md section 7.

Prompts: `ROOTCAUSE_PROMPT_SOURCE=langfuse` serves the version labelled
`ROOTCAUSE_PROMPT_LABEL` (default `production`) and falls back to the shipped
file on any Langfuse error. Upload the shipped prompts as new versions with
`uv run python scripts/push_prompts.py --apply` (dry run without `--apply`;
add `--label production` to promote the uploaded versions immediately).

Reset only the Langfuse data:

```bash
docker compose -f deploy/docker-compose.yml --profile observability down
docker volume rm rootcause_langfuse_postgres_data rootcause_langfuse_clickhouse_data \
  rootcause_langfuse_clickhouse_logs rootcause_langfuse_redis_data rootcause_langfuse_minio_data
```

## api / web services

The `api` (T005, FastAPI beside LangGraph Server) and `web` (T070, Next.js)
services are added to this same compose file under the `app` profile, so a
plain `docker compose up` keeps starting only the stateful services. Until
then run the API on the host against `localhost:5432` /
`http://localhost:1933` (see the `ROOTCAUSE_*` block in `.env.example`);
inside the compose network the hosts are `postgres` and `openviking`.
