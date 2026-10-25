-- RootCause Postgres bootstrap (task T002).
--
-- Mounted into /docker-entrypoint-initdb.d/ and executed by the official
-- postgres entrypoint exactly once: on the FIRST start against an empty data
-- directory, as POSTGRES_USER (a superuser) inside POSTGRES_DB. It never runs
-- again while the `postgres_data` volume exists; `docker compose down -v`
-- wipes the volume so the next `up` re-runs it.
--
-- Schema evolution beyond this bootstrap belongs to migrations (later tasks),
-- not to this file.

CREATE EXTENSION IF NOT EXISTS vector;

-- ADR-0003: app state, the catalog and the audit log share one Postgres 16
-- instance, separated by schema.
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS audit;
