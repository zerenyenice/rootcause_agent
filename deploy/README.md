# deploy

Docker Compose stack for local development and single-host pilots. Task
T002 (`docs/plans/T002-docker-compose-stack.md`); owner agent `devops`.

| File | Purpose |
|------|---------|
| `docker-compose.yml` | `postgres` (Postgres 16 + pgvector) and `openviking` (OpenViking 0.3.x server); placeholders for `api`/`web` |
| `.env.example` | Every variable the stack needs, placeholder values only. Copy to `deploy/.env` (gitignored) |
| `postgres/init.sql` | First-start bootstrap: roles `rootcause_owner`/`rootcause_migrator`/`rootcause_app`, `CREATE EXTENSION vector`, schemas `app`, `catalog`, `audit` owned by `rootcause_owner`, runtime grants |
| `alembic.ini` | Dev-only config for plain `alembic`; use the `rootcause-db` CLI everywhere else |
| `openviking/ov.conf` | Secret-free OpenViking config template; `${VAR}` references are expanded by the server from the container environment |

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
- `POSTGRES_MIGRATOR_PASSWORD` and `POSTGRES_APP_PASSWORD`: login passwords
  of the migrator and runtime roles `init.sql` creates (see "Database roles").
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

# Postgres: pgvector installed, three schemas owned by rootcause_owner, three roles
docker compose -f deploy/docker-compose.yml exec postgres \
  psql -U rootcause -d rootcause -c '\dx' -c '\dn' -c '\du rootcause_*'

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

## Database roles

The application database uses three roles ([ADR-0007](../docs/adr/0007-three-database-roles-append-only-audit.md)):

| Role | Used by | Can |
|------|---------|-----|
| `rootcause_owner` (NOLOGIN) | nobody directly | owns the schemas and every migrated object |
| `rootcause_migrator` | `rootcause-db upgrade` (`ROOTCAUSE_MIGRATION_DATABASE_URL`) | `SET ROLE rootcause_owner`, so it can run DDL |
| `rootcause_app` | the API (`ROOTCAUSE_DATABASE_URL`) | DML on app/catalog tables, only `SELECT, INSERT` on the audit log |

The compose stack creates them in `init.sql`. On any other server create the
roles yourself (owner `NOLOGIN`, migrator `IN ROLE rootcause_owner`), then:

```bash
export ROOTCAUSE_ADMIN_DATABASE_URL=postgresql://<superuser>:<password>@host:5432/rootcause
uv run rootcause-db bootstrap          # idempotent; or --admin-url without a password + PGPASSWORD
uv run rootcause-db upgrade            # as the migrator, ROOTCAUSE_MIGRATION_DATABASE_URL
```

Residual risk: the owner role (and so the migrator) and superusers can still
alter the audit table and its triggers. Strict tamper evidence needs off-host
shipping of the audit trail (WAL archive or pgaudit), tracked as a backlog item.

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

## api / web services

The `api` (T005, FastAPI beside LangGraph Server) and `web` (T070, Next.js)
services are added to this same compose file under the `app` profile, so a
plain `docker compose up` keeps starting only the stateful services. Until
then run the API on the host against `localhost:5432` /
`http://localhost:1933` (see the `ROOTCAUSE_*` block in `.env.example`);
inside the compose network the hosts are `postgres` and `openviking`.
