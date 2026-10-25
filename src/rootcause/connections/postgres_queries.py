"""Fixed catalog queries of the Postgres executor; caller input only ever arrives as parameters.

"User" relations are tables, partitioned tables, views, materialized views and
foreign tables outside the system schemas (``pg_*`` and ``information_schema``)
that the connected role may SELECT. Partitions are hidden behind their parent.
The filter is spelled out in full in each query (plain literals, no string building).
"""

from typing import Final

CATALOG_SQL: Final = (
    "SELECT n.nspname, c.relname, a.attname "
    "FROM pg_catalog.pg_class AS c "
    "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
    "JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition "
    "AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema' "
    "AND has_table_privilege(c.oid, 'SELECT') "
    "AND a.attnum > 0 AND NOT a.attisdropped "
    "ORDER BY n.nspname, c.relname, a.attnum"
)
TABLES_SQL: Final = (
    "SELECT n.nspname, c.relname, greatest(c.reltuples, 0)::bigint "
    "FROM pg_catalog.pg_class AS c "
    "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition "
    "AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema' "
    "AND has_table_privilege(c.oid, 'SELECT') "
    "ORDER BY n.nspname, c.relname"
)
# Every relation a query can name outside the system schemas, readable or not, partitions
# included: bare-name uniqueness checks must see every schema a search_path may reach.
RELATIONS_SQL: Final = (
    "SELECT n.nspname, c.relname "
    "FROM pg_catalog.pg_class AS c "
    "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') "
    "AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema' "
    "ORDER BY n.nspname, c.relname"
)
TABLE_LOOKUP_SQL: Final = (
    "SELECT c.oid, n.nspname, c.relname "
    "FROM pg_catalog.pg_class AS c "
    "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition "
    "AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema' "
    "AND has_table_privilege(c.oid, 'SELECT') "
    "AND c.relname = %(table)s "
    "AND (%(schema)s::text IS NULL OR n.nspname = %(schema)s::text)"
)
COLUMNS_SQL: Final = (
    "SELECT a.attname, format_type(a.atttypid, NULL), NOT a.attnotnull "
    "FROM pg_catalog.pg_attribute AS a "
    "WHERE a.attrelid = %(oid)s AND a.attnum > 0 AND NOT a.attisdropped "
    "ORDER BY a.attnum"
)
# The first ``limit`` user relations in (schema, table) code-point order with their
# columns, one row per column (a relation without columns yields one row of NULLs).
# One statement however many relations there are; callers ask for one more relation
# than they keep to learn whether the list was cut.
INTROSPECT_SQL: Final = (
    "WITH relations AS ("
    "SELECT c.oid, n.nspname, c.relname, greatest(c.reltuples, 0)::bigint AS row_estimate "
    "FROM pg_catalog.pg_class AS c "
    "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition "
    "AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema' "
    "AND has_table_privilege(c.oid, 'SELECT') "
    'ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C" '
    "LIMIT %(limit)s) "
    "SELECT r.nspname, r.relname, r.row_estimate, "
    "a.attname, format_type(a.atttypid, NULL), NOT a.attnotnull "
    "FROM relations AS r "
    "LEFT JOIN pg_catalog.pg_attribute AS a "
    "ON a.attrelid = r.oid AND a.attnum > 0 AND NOT a.attisdropped "
    'ORDER BY r.nspname COLLATE "C", r.relname COLLATE "C", a.attnum'
)
STATEMENT_TIMEOUT_SQL: Final = "SELECT set_config('statement_timeout', %(timeout)s, true)"
SERVER_VERSION_SQL: Final = "SELECT current_setting('server_version')"

# Built-in roles whose members may touch the server or its data outside SELECT: server
# files and programs, signalling or checkpointing the server, writing every table,
# subscriptions and maintenance (VACUUM, REFRESH MATERIALIZED VIEW, ...). Names that do
# not exist on an older server simply match nothing.
RISKY_BUILTIN_ROLES: Final = (
    "pg_write_server_files",
    "pg_execute_server_program",
    "pg_read_server_files",
    "pg_signal_backend",
    "pg_checkpoint",
    "pg_write_all_data",
    "pg_create_subscription",
    "pg_maintain",
)

# True when the role could change anything (fail closed). ``roles`` is the login role
# plus every role it is a member of, directly or transitively (``pg_has_role`` MEMBER,
# regardless of INHERIT: a member may SET ROLE). The role is NOT read-only when one of
# those roles:
# - is a superuser or has CREATEDB, CREATEROLE or REPLICATION;
# - is one of ``RISKY_BUILTIN_ROLES``;
# - may INSERT/UPDATE (any column)/DELETE/TRUNCATE a non-system relation, or advance a
#   non-system sequence;
# - owns a non-system relation of any kind, a non-system schema, or the current
#   database (an owner can re-grant itself anything, or DROP);
# - may CREATE in a non-system schema, or in any database on the server.
# TEMP on the database is tolerated: PUBLIC holds it by default, temp objects are
# session-local and the executor's READ ONLY transactions refuse CREATE TEMP TABLE.
# ``default_transaction_read_only`` is deliberately ignored: a session can override it.
# Known gap: EXECUTE on a SECURITY DEFINER function that writes is not detected (the
# validator only lets allow-listed functions through, so the executor cannot call one).
CAN_WRITE_SQL: Final = """
WITH roles AS (
    SELECT r.oid, r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication
    FROM pg_catalog.pg_roles AS r
    WHERE pg_has_role(current_user, r.oid, 'MEMBER')
), relations AS (
    SELECT c.oid, c.relkind, c.relowner
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
), schemas AS (
    SELECT n.oid, n.nspowner FROM pg_catalog.pg_namespace AS n
    WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
)
SELECT
    EXISTS (
        SELECT 1 FROM roles
        WHERE rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication
        OR rolname = ANY (%(risky_roles)s::text[])
    )
    OR EXISTS (
        SELECT 1 FROM roles AS r JOIN relations AS c ON c.relkind IN ('r', 'p', 'v', 'f')
        WHERE has_table_privilege(r.oid, c.oid, 'DELETE, TRUNCATE')
        OR has_any_column_privilege(r.oid, c.oid, 'INSERT, UPDATE')
    )
    OR EXISTS (
        SELECT 1 FROM roles AS r JOIN relations AS c ON c.relkind = 'S'
        WHERE has_sequence_privilege(r.oid, c.oid, 'USAGE, UPDATE')
    )
    OR EXISTS (
        SELECT 1 FROM relations AS c WHERE pg_has_role(current_user, c.relowner, 'MEMBER')
    )
    OR EXISTS (
        SELECT 1 FROM schemas AS s WHERE pg_has_role(current_user, s.nspowner, 'MEMBER')
    )
    OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_database AS d
        WHERE d.datname = current_database() AND pg_has_role(current_user, d.datdba, 'MEMBER')
    )
    OR EXISTS (
        SELECT 1 FROM roles AS r CROSS JOIN schemas AS s
        WHERE has_schema_privilege(r.oid, s.oid, 'CREATE')
    )
    OR EXISTS (
        SELECT 1 FROM roles AS r CROSS JOIN pg_catalog.pg_database AS d
        WHERE has_database_privilege(r.oid, d.oid, 'CREATE')
    )
"""
