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
STATEMENT_TIMEOUT_SQL: Final = "SELECT set_config('statement_timeout', %(timeout)s, true)"
SERVER_VERSION_SQL: Final = "SELECT current_setting('server_version')"

# True when the role could change anything: it (or any role it may SET ROLE to) is a
# superuser, may INSERT/UPDATE (any column)/DELETE/TRUNCATE a non-system relation, may
# advance a sequence, or may CREATE in a non-system schema or in the database.
# ``default_transaction_read_only`` is deliberately ignored: a session can override it.
CAN_WRITE_SQL: Final = """
WITH roles AS (
    SELECT r.oid, r.rolsuper FROM pg_catalog.pg_roles AS r
    WHERE pg_has_role(current_user, r.oid, 'MEMBER')
), relations AS (
    SELECT c.oid, c.relkind
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'f', 'S')
    AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
), schemas AS (
    SELECT n.oid FROM pg_catalog.pg_namespace AS n
    WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
)
SELECT
    EXISTS (SELECT 1 FROM roles WHERE rolsuper)
    OR EXISTS (
        SELECT 1 FROM roles AS r JOIN relations AS c ON c.relkind <> 'S'
        WHERE has_table_privilege(r.oid, c.oid, 'DELETE, TRUNCATE')
        OR has_any_column_privilege(r.oid, c.oid, 'INSERT, UPDATE')
    )
    OR EXISTS (
        SELECT 1 FROM roles AS r JOIN relations AS c ON c.relkind = 'S'
        WHERE has_sequence_privilege(r.oid, c.oid, 'USAGE, UPDATE')
    )
    OR EXISTS (
        SELECT 1 FROM roles AS r CROSS JOIN schemas AS s
        WHERE has_schema_privilege(r.oid, s.oid, 'CREATE')
    )
    OR EXISTS (
        SELECT 1 FROM roles AS r WHERE has_database_privilege(r.oid, current_database(), 'CREATE')
    )
"""
