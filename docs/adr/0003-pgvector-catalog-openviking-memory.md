# ADR-0003: Postgres with pgvector for the catalog, OpenViking for memory, no second vector store

- Status: Accepted
- Date: 2026-10-24
- Deciders: project owner, lead
- Related: PRD sections 7 and 8, epics EP2 and EP8

## Context

The system needs hybrid search over catalog objects (tables, columns, values,
metrics, glossary) and long-term agent memory with lessons that become skills.
The project requires OpenViking for memory. Adding a dedicated vector database
for the catalog would mean a third stateful service for pilots.

## Decision

The catalog, audit log, anomaly records, app state and the LangGraph
checkpointer and store live in one Postgres 16 instance with pgvector; catalog
search is hybrid (tsvector plus vector similarity). Agent memories, lessons and
skills live in an OpenViking server (0.3.x, run unmodified in Docker) behind a
thin `MemoryStore` adapter. No other vector store is introduced in v1.

## Consequences

- Two stateful services for a pilot: Postgres and OpenViking.
- OpenViking is AGPLv3; it is run as a separate, unmodified service and only its
  Python client is used from our code. Legal review before pilot.
- The adapter isolates us from OpenViking API churn; swapping the memory backend
  later is a contained change.
- Catalog search quality depends on pgvector; if it proves insufficient at scale
  a new ADR will revisit this.
