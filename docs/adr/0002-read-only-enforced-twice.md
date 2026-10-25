# ADR-0002: Read-only database access enforced at the role and at the SQL validator

- Status: Accepted
- Date: 2026-10-24
- Deciders: project owner, lead
- Related: PRD section 6, PROJECT.md sections 7 and 8, tasks T010, T011, T012

## Context

Agents generate SQL from natural language and execute it against customer
databases. A single control is one bug or one misconfiguration away from a
write. Customers will not accept "the model is instructed not to write".

## Decision

Two independent controls, both mandatory:

1. The database role used by every connection is read-only, verified at
   registration (a writable role is refused) and re-checked on health checks.
2. Every statement passes `validate_sql` before execution: exactly one statement,
   read-only shape (SELECT, set operations, or WITH wrapping those), forbidden
   expression and function sets, LIMIT injection and capping, parse failures
   rejected, unknown constructs fail closed. Execution additionally enforces
   statement timeouts, row caps and concurrency caps and audit-logs every call.

## Consequences

- A validator bug cannot write because the role cannot; a role misconfiguration
  cannot write because the validator rejects it.
- Some legitimate read-only syntax may be rejected (for example EXPLAIN ANALYZE,
  which executes); such cases are added explicitly after review.
- The validator is security-critical code: changes require security review.
