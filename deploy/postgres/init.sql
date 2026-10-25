-- RootCause Postgres bootstrap (tasks T002, T006; docs/adr/0007).
--
-- Mounted into /docker-entrypoint-initdb.d/ and executed by the official
-- postgres entrypoint exactly once: on the FIRST start against an empty data
-- directory, as POSTGRES_USER (a superuser) inside POSTGRES_DB. It never runs
-- again while the `postgres_data` volume exists; `docker compose down -v`
-- wipes the volume so the next `up` re-runs it.
--
-- Three roles (names match the ROOTCAUSE_DB_*_ROLE defaults):
--   rootcause_owner     NOLOGIN; owns the schemas and every migrated object
--   rootcause_migrator  LOGIN, member of rootcause_owner; runs `rootcause-db upgrade`
--   rootcause_app       LOGIN; the API's runtime role, least privilege
-- The database itself stays owned by POSTGRES_USER, never by a login role.
--
-- Passwords are read from the container environment with psql's \getenv
-- (POSTGRES_MIGRATOR_PASSWORD, POSTGRES_APP_PASSWORD, set in deploy/.env) and
-- interpolated as quoted literals (:'var'); none is stored in this file.
-- This file mirrors `rootcause-db bootstrap`; on any other server run that
-- command instead. Schema evolution belongs to the migrations.

\getenv migrator_password POSTGRES_MIGRATOR_PASSWORD
\getenv app_password POSTGRES_APP_PASSWORD

CREATE ROLE rootcause_owner NOLOGIN;
CREATE ROLE rootcause_migrator LOGIN PASSWORD :'migrator_password' IN ROLE rootcause_owner;
CREATE ROLE rootcause_app LOGIN PASSWORD :'app_password';

CREATE EXTENSION IF NOT EXISTS vector;

-- ADR-0003: app state, the catalog and the audit log share one Postgres 16
-- instance, separated by schema. All three are owned by the owner role.
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION rootcause_owner;
CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION rootcause_owner;
CREATE SCHEMA IF NOT EXISTS audit AUTHORIZATION rootcause_owner;

-- Runtime role: USAGE without CREATE on the schemas.
GRANT USAGE ON SCHEMA app, catalog, audit TO rootcause_app;

-- Objects the migrations create (as rootcause_owner) get these grants.
-- The audit log is append-only for the app role (PROJECT.md section 7);
-- migration 0003 revokes everything on app.alembic_version again.
ALTER DEFAULT PRIVILEGES FOR ROLE rootcause_owner IN SCHEMA app, catalog
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rootcause_app;
ALTER DEFAULT PRIVILEGES FOR ROLE rootcause_owner IN SCHEMA audit
    GRANT SELECT, INSERT ON TABLES TO rootcause_app;
ALTER DEFAULT PRIVILEGES FOR ROLE rootcause_owner IN SCHEMA app, catalog, audit
    GRANT USAGE, SELECT ON SEQUENCES TO rootcause_app;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
