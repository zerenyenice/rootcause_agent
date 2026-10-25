# Plan: T002 Docker Compose for the local stack

Backlog T002 (epic EP0). Branch `feat/docker-compose-stack` off `dev`. Gate 1 approved 2026-10-25 (batch approval).

## Goal
`deploy/docker-compose.yml` brings up the stateful services every later task needs: Postgres 16 with pgvector and an OpenViking server. The API and web services are added to the same file by T005 and T070 under a `app` profile; this task leaves documented placeholders for them.

## Changes
1. `deploy/docker-compose.yml`: service `postgres` (image `pgvector/pgvector:pg16` pinned by digest or exact tag), healthcheck `pg_isready`, named volume, init script `deploy/postgres/init.sql` that runs `CREATE EXTENSION IF NOT EXISTS vector;` and creates schemas `app`, `catalog`, `audit`; service `openviking` (official image from github.com/volcengine/OpenViking docs, pinned to a 0.3.x tag; if no official image exists, a `deploy/openviking/Dockerfile` installing the pinned PyPI package and running the server), healthcheck on its HTTP port, volume for its data; ports bound to localhost only; `restart: unless-stopped`.
2. `deploy/.env.example` with placeholders only (POSTGRES_PASSWORD, ROOTCAUSE_DATABASE_URL, ROOTCAUSE_OPENVIKING_URL, OPENVIKING embedding provider variables), and `.env` already gitignored.
3. `deploy/README.md`: how to start, how to verify (`docker compose ps`, `psql -c '\dx'`), how to reset volumes, and the note that api/web services arrive with T005/T070.
4. `.github/workflows/ci.yml`: a `compose-config` step in an existing lightweight job (or a tiny new job) running `docker compose -f deploy/docker-compose.yml config -q` so the file always parses.

## Validation
`docker compose config`; if Docker is available locally, `docker compose up -d postgres openviking`, wait for healthy, `psql` shows the `vector` extension and the three schemas, OpenViking health endpoint answers; `docker compose down -v`.

## Acceptance (backlog)
`docker compose up` brings up all services with passing healthchecks; Postgres has `vector` on first start; no secrets in the repo; required variables validated at API startup (covered by T004 settings).
