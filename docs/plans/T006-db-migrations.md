# Plan: T006 Database migrations with Alembic for app, catalog and audit schemas

Backlog T006 (epic EP0). Branch `feat/db-migrations` off `dev`. Gate 1 approved 2026-10-25 (batch approval). Depends on T002 (merged). Local Postgres 16 and pgvector are installed via Homebrew (binaries only); tests start throwaway instances.

## Goal
A single Alembic environment for the application's own Postgres with three schemas (`app`, `catalog`, `audit`), a first migration creating the connections, audit log and catalog version tables, and a CI job that migrates up and down against a service Postgres.

## Design
- `packages/core/src/rootcause_core/db/`: `engine.py` (`make_engine(settings) -> Engine` sync SQLAlchemy 2 engine for migrations and admin, `make_async_engine(settings)` for the API; both from `Settings.database_url`, psycopg3 driver for sync, asyncpg for async; `pool_pre_ping`), `base.py` (`Base = DeclarativeBase` with naming conventions for constraints), `schemas.py` (constants `APP_SCHEMA`, `CATALOG_SCHEMA`, `AUDIT_SCHEMA`, `ensure_schemas(connection)`).
- Models (SQLAlchemy 2 typed, `Mapped[...]`), in the owning packages:
  - `rootcause_connections.models.Connection` (schema `app`, table `connections`): id UUID pk, display_name, dialect, host, port, database, username, encrypted_secret (bytea), limits JSONB (timeout_s, max_rows, concurrency, scan_row_threshold), read_only_verified_at, created_at, updated_at, tenant_id.
  - `rootcause_connections.models.QueryAuditLog` (schema `audit`, table `query_log`): id bigint identity pk, connection_id fk, thread_id, agent, purpose, sql text, started_at, duration_ms, row_count, status enum(ok, error, rejected), error_code, request_id, tenant_id; append-only (no updated_at), index on (connection_id, started_at).
  - `rootcause_catalog.models.CatalogVersion` (schema `catalog`, table `versions`): id, connection_id fk, version int, published_at nullable, created_by, notes; unique (connection_id, version).
- `deploy/alembic.ini` and `packages/core/src/rootcause_core/db/alembic/` (env.py loading `Settings`, `script.py.mako`, `versions/0001_initial.py`); `env.py` runs `CREATE SCHEMA IF NOT EXISTS` for the three schemas and `CREATE EXTENSION IF NOT EXISTS vector` before migrating; `alembic_version` table lives in schema `app`. Autogenerate wired through `target_metadata = Base.metadata` with `include_schemas=True`.
- CLI entry `rootcause-db` (console script in core): `upgrade`, `downgrade`, `current`, `revision --autogenerate -m`.
- CI: a `db-migrations` job in `ci.yml` with a `pgvector/pgvector:0.8.7-pg16` service container, running `uv run rootcause-db upgrade head`, `downgrade base`, `upgrade head`, then `pytest -m integration packages/core packages/connections packages/catalog`.
- Tests use `pytest-postgresql` (factories using local `pg_ctl`/`initdb` from Homebrew when `PG_CTL` is discoverable; on CI the service container via `postgresql_noproc`). Dev dependency: `pytest-postgresql>=6`, `psycopg[binary]>=3.2`.

## Tests (tester-owned, RED first)
Unit: schema constants, naming convention on `Base.metadata` (e.g. fk names `fk_<table>_<col>_<ref>`), model columns and constraints present in metadata, `make_engine` builds a psycopg URL and `make_async_engine` an asyncpg URL from any `postgres*://` input, migration scripts import and have `revision`/`down_revision`. Integration (`@pytest.mark.integration`, skipped with a clear reason when no Postgres is available): `upgrade head` on a fresh database creates the three schemas, the `vector` extension and the tables; `downgrade base` drops them and leaves no tables in the three schemas; `upgrade head` twice is idempotent; autogenerate against the migrated database produces no diff (`alembic.autogenerate.compare_metadata` is empty); inserting an audit row and a connection row round-trips; `query_log` rejects updates via a trigger or a revoke? (keep simple: no trigger in v1; test append-only by convention only).

## Acceptance (backlog)
`alembic upgrade head` on a fresh database succeeds and `downgrade` reverses it; CI runs migrations against a service Postgres before tests.

## Amendment 2026-10-25 (after backend review)

- **Package layout.** New workspace package `packages/db` (`rootcause_db`, project `rootcause-db`) owns the Alembic environment, `versions/`, `migrate.py`, `cli.py` and the `rootcause-db` console script; it depends on `rootcause-core`, `rootcause-connections` and `rootcause-catalog`. `rootcause_core.db` keeps `Base`, schema constants and engine factories only. `rootcause-catalog` depends on `rootcause-connections` (its FK targets `app.connections`). `deploy/alembic.ini` points at the new script location.
- **Privileges.** Extension and schema creation move to `rootcause-db bootstrap --admin-url <superuser url>` (idempotent: `CREATE EXTENSION IF NOT EXISTS vector`, the three schemas, grants to the app role) and the compose init SQL. `env.py` no longer creates them; it fails fast with a clear message if `vector` or a schema is missing. An integration test migrates as a non-superuser owner role after bootstrap.
- **CLI safety.** `downgrade` and `revision` refuse to run when the environment is prod; errors print a message without the URL and exit non-zero.
- **Audit immutability (PROJECT.md section 7).** Migration `0002` adds a trigger rejecting UPDATE and DELETE on `audit.query_log`; `downgrade` past it requires non-prod.
- **Model hardening.** `connections.id` server default `gen_random_uuid()`; `updated_at` onupdate; `QueryStatus` StrEnum mapped to the `query_status` enum; port check 1 to 65535; malformed URLs raise `ConfigurationError`; engine pool and connect timeouts from Settings; `pg_advisory_xact_lock` around migrations; `NullPool` for migration engines; `compare_server_default=True`.
