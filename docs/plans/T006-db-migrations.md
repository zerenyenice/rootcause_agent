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
