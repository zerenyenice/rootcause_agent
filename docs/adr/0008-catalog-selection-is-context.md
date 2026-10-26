# ADR-0008: Catalog selection is agent context, not an access-control boundary

- Status: Accepted
- Date: 2026-10-26
- Deciders: user (product owner), lead
- Related: PROJECT.md section 7, ADR-0002, issues #145, #163

## Context

The management UI lets an analyst select the tables and columns of a
connection and describe them. The catalog branch first treated that selection
as an access-control boundary: `SelectionExecutor` parses each agent query and
rejects references to unselected tables or columns.

Three review rounds each found a new way to read unselected columns through
SQL features (opaque sources such as LATERAL and VALUES, bare names resolving
to another schema, a table alias that becomes a whole-row reference). Each was
fixed, but enforcing column access by parsing SQL is open-ended: every dialect
feature is a potential path around it.

## Decision

Selection decides what the agent is told about and searches: the schema tools
list only selected tables and columns, descriptions come from the catalog, and
the search index holds only selected entries. The strict SQL check stays as
defence in depth and must block every leak found so far, but it is not
promised to be complete.

The data-access boundary is the database login, per ADR-0002: the agent only
queries connections whose login passed the read-only verification. Data the
agent must never read is protected in the source database, for example with
column-level `GRANT SELECT (...)` on Postgres or a dedicated schema of views,
not by leaving it unselected.

## Consequences

- The UI and docs describe selection as "what the agent uses", not as a
  security control. `deploy/README.md` says how to protect sensitive columns.
- Further SQL-level hardening (#163) is defence in depth, not a release
  blocker.
- A structural barrier (per-connection roles or view-only databases) remains
  possible later if a customer needs selection to be a hard boundary.
