# ADR-0007: Three database roles for the application database; append-only audit log

- Status: Accepted
- Date: 2026-10-25
- Deciders: lead (architecture decision during T006 review; project owner may override)
- Related: PROJECT.md section 7, ADR-0003, task T006 (plan amendment 2)

## Context

PROJECT.md section 7 requires an immutable audit log of every query an agent
runs. A trigger that rejects UPDATE and DELETE (migration 0002) is not enough
when the role the API connects as also owns the table: an owner can disable or
drop the trigger, replace its function, TRUNCATE, or rewrite rows with
`ALTER COLUMN ... USING`. The backend and security reviews of T006 reproduced
each of these as the runtime role.

## Decision

Three roles, names configurable through `ROOTCAUSE_DB_OWNER_ROLE`,
`ROOTCAUSE_DB_MIGRATOR_ROLE` and `ROOTCAUSE_DB_APP_ROLE`:

| Role | Login | Purpose |
| --- | --- | --- |
| `rootcause_owner` | NOLOGIN | Owns the `app`, `catalog` and `audit` schemas and every migrated object |
| `rootcause_migrator` | LOGIN, member of the owner role | Runs `rootcause-db upgrade`; `env.py` does `SET LOCAL ROLE <owner>` so new objects belong to the owner |
| `rootcause_app` | LOGIN | Runtime role of the API (`ROOTCAUSE_DATABASE_URL`) |

- `rootcause-db bootstrap` (superuser, from `ROOTCAUSE_ADMIN_DATABASE_URL`, or
  `--admin-url` without a password) verifies the roles, creates the `vector`
  extension and the schemas owned by the owner role, and sets
  `ALTER DEFAULT PRIVILEGES FOR ROLE <owner>`: app and catalog tables
  `SELECT, INSERT, UPDATE, DELETE`; audit tables `SELECT, INSERT`; sequences
  `USAGE, SELECT`. The app role gets `USAGE` without `CREATE` on the schemas;
  `CREATE` on `public` is revoked from `PUBLIC`. Bootstrap refuses superuser,
  `BYPASSRLS`, `pg_*` and admin-user roles; any of the three roles that is a
  member (directly or indirectly) of a superuser or `BYPASSRLS` role or of
  `pg_write_all_data`, `pg_execute_server_program`, `pg_write_server_files` or
  `pg_read_server_files`; an owner that can log in; an app role with
  `CREATEROLE` or `CREATEDB` or that is a member of the owner or migrator role;
  and an app or migrator role that owns the database, before changing anything.
  `--admin-url` must carry no password (neither inline nor as a `password`
  query parameter), and the admin URL may only carry the `sslmode`,
  `sslrootcert`, `connect_timeout` and `application_name` query parameters.
  `deploy/postgres/init.sql` does the same for the compose stack.
- Migrations use `ROOTCAUSE_MIGRATION_DATABASE_URL`; in prod they refuse to run
  as a superuser or as a member of a superuser role.
- Migration 0003 adds a statement-level `BEFORE TRUNCATE` trigger on
  `audit.query_log` (also covering `TRUNCATE app.connections CASCADE` and the
  owner role) and revokes every privilege on `app.alembic_version` from all
  roles but the owner.

## Consequences

- The runtime role cannot update, delete, truncate, alter or drop the audit
  log, its trigger or its function, cannot create objects, and cannot read or
  change the migration state. Integration tests replay the reviewers' tamper
  statements as the app role.
- Deployments manage three roles and two passwords instead of one superuser
  connection; the migration job and the API need different URLs.
- **Residual risk.** The owner role (and so the migrator, by `SET ROLE`) and
  any superuser can still disable the triggers or alter the audit table. The
  database alone cannot give strict tamper evidence against them; that needs
  off-host shipping of the audit trail (WAL archiving or pgaudit to a separate
  store). That work is a backlog item, not part of T006.
- **Residual risk: the migrator inherits the owner's rights.** The migrator is
  granted the owner role with the default `INHERIT` (`inherit_option = t`), so
  any migrator session can alter or drop owner-owned objects, including the
  audit table and its triggers, without `SET ROLE`. Optional hardening on
  PostgreSQL 16+: `GRANT <owner> TO <migrator> WITH INHERIT FALSE, SET TRUE`,
  so the owner's rights are only usable after the explicit `SET LOCAL ROLE` in
  `env.py`. This narrows accidental use; it does not stop a deliberate
  `SET ROLE` by whoever holds the migrator credentials.
- **Residual risk: the role checks run once.** Bootstrap and the prod
  superuser refusal check the roles when they run; a membership, attribute or
  grant added later (e.g. `GRANT pg_write_all_data TO <app>`) is not detected.
  Recommended follow-up: the API re-checks its own role at startup and refuses
  to serve when it is privileged (tracked for T035).
- **Residual risk: the app role can forge history.** `INSERT` on the audit
  table lets the runtime role write rows with an id and timestamp it chooses,
  including backdated ones. Follow-up: make the id `GENERATED ALWAYS AS
  IDENTITY`, set `recorded_at` server-side, and grant `INSERT` on the other
  columns only (column-level grants).
