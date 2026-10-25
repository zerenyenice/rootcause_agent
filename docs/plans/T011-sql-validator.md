# Plan: T011 SQL validator with sqlglot

Backlog T011 (epic EP1). Branch `feat/sql-validator` off `dev`. Gate 1 approved 2026-10-25 (batch approval).

## Goal
`rootcause_connections.validation.validate_sql` is the single gate every agent query passes before execution: read-only allow list, dialect transpilation, LIMIT injection, referenced-object extraction and optional catalog check.

## Design
- `validate_sql(sql: str, dialect: str, *, catalog: CatalogSnapshot | None = None, default_limit: int = 10_000) -> ValidationResult`.
- `ValidationResult` frozen dataclass: `ok`, `sql` (normalised, transpiled to `dialect`), `referenced_tables: tuple[str, ...]`, `referenced_columns: tuple[str, ...]`, `limit_injected: bool`, `errors: tuple[ValidationIssue, ...]`, `warnings: tuple[ValidationIssue, ...]`. `ValidationIssue` has `code` (enum: MULTIPLE_STATEMENTS, NOT_READ_ONLY, FORBIDDEN_FUNCTION, PARSE_ERROR, UNKNOWN_TABLE, UNKNOWN_COLUMN, UNSUPPORTED_DIALECT) and `message`.
- Rules: exactly one statement; top-level expression must be `Select`, `Union`/`Intersect`/`Except`, or a `With` whose body is one of those and whose CTEs contain no DML; any `Insert/Update/Delete/Merge/Create/Drop/Alter/Truncate/Command/Set/Transaction/Call/Grant/Use/Copy` anywhere in the tree fails; `pg_sleep`, `COPY`, `INTO OUTFILE`, `LOAD_FILE` style functions fail; comments are stripped before analysis; a `LIMIT` is injected (outermost query) when absent and the outermost select is not aggregate-only (has no GROUP BY and no aggregate functions without GROUP BY); existing LIMIT above `default_limit` is capped with a warning; `CatalogSnapshot` is a frozen mapping of table name (schema-qualified or bare) to column names, used case-insensitively; unknown tables are errors, unknown columns are errors, ambiguous bare names are warnings.
- Supported dialects in v1: `postgres`, `duckdb`, `mysql`, `tsql`, `snowflake`, `bigquery`, `clickhouse`; anything else returns `UNSUPPORTED_DIALECT`.
- Dependency: `sqlglot>=25`. No I/O, pure function, no mutation.

## Tests (tester-owned, RED first)
Parametrised matrix of at least 40 statements for postgres and duckdb: allowed SELECT/CTE/UNION/subquery/window, forbidden DDL/DML (including a CTE wrapping an INSERT, `SELECT ... INTO`, `;`-chained statements, a comment hiding a second statement, `pg_sleep`), LIMIT injection and capping, aggregate-only queries not limited, referenced tables and columns extraction (including aliases and schema-qualified names), catalog checks (unknown table/column, case-insensitive match), unsupported dialect, transpile postgres `ILIKE` to duckdb. Coverage >= 80%.

## Acceptance (backlog)
Matrix passes; unknown columns flagged when a catalog snapshot is supplied.
