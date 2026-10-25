# Plan: T005 FastAPI app skeleton beside LangGraph Server

Backlog T005 (epic EP0). Branch `feat/api-skeleton` off `dev`. Gate 1 approved 2026-10-25 (batch approval). Depends on T004 (merged).

## Goal
`apps/api` serves a FastAPI application with the project's response envelope, health and version endpoints, error handlers, rate limiting, logging and tracing from `rootcause_core`, and a `langgraph.json` registering a placeholder graph so `langgraph dev` starts.

## Design
- `rootcause_api/main.py`: `create_app(settings: Settings | None = None) -> FastAPI`; module-level `app = create_app()` for uvicorn. Startup configures logging and telemetry via `rootcause_core`; FastAPI is instrumented with `opentelemetry-instrumentation-fastapi`.
- `rootcause_api/envelope.py`: `Envelope[T]` pydantic generic: `success: bool`, `data: T | None`, `error: ErrorBody | None` (`code`, `message`, `details: dict | None`), `meta: Meta | None` (`total`, `page`, `limit`, `request_id`). Helpers `ok(data, meta=None)` and `fail(code, message, status)` returning `JSONResponse`.
- `rootcause_api/errors.py`: handlers mapping `ConfigurationError` -> 500 (`configuration_error`, generic message), `RootCauseError` -> 400, `RequestValidationError` -> 422 with field details, `HTTPException` -> its status, unhandled `Exception` -> 500 `internal_error` without leaking the message; every error response is an `Envelope` and is logged with the request id.
- `rootcause_api/middleware.py`: request-id middleware (`X-Request-ID` in and out, bound into structlog context); rate limiting with `slowapi` keyed by client IP, default `120/minute`, configurable via `Settings.rate_limit` (new optional field in `rootcause_core.settings`, default "120/minute"), 429 responses in the envelope.
- `rootcause_api/routes/health.py`: `GET /health` returns 200 `Envelope[HealthReport]` with `status: ok | degraded`, `checks: {postgres: ok|fail|skipped, openviking: ok|fail|skipped}`, `version`. Postgres check: `SELECT 1` through an async SQLAlchemy engine built from `Settings.database_url` with a 2 s timeout; OpenViking check: HTTP GET of its health URL via httpx with a 2 s timeout, `skipped` when `openviking_url` is unset. `GET /version` returns `Envelope[VersionInfo]` (`version`, `git_sha` from env `ROOTCAUSE_GIT_SHA` or "unknown").
- `apps/api/langgraph.json`: `{"graphs": {"placeholder": "./src/rootcause_api/graphs.py:graph"}, "env": ".env"}` with `graphs.py` building a one-node LangGraph `StateGraph` that echoes input (replaced by the orchestrator in T030).
- Dependencies (apps/api): `fastapi>=0.115`, `uvicorn[standard]`, `slowapi`, `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `httpx`, `opentelemetry-instrumentation-fastapi`, `langgraph>=0.4`, workspace `rootcause-core`. Dev: `pytest-asyncio` or `anyio` plugin, `httpx` test client.
- `uvicorn rootcause_api.main:app` documented in README; `deploy/docker-compose.yml` gets an `api` service under profile `app` (Dockerfile at `apps/api/Dockerfile`, non-root, uv-based) only if T002 has merged by then; otherwise a follow-up.

## Tests (tester-owned, RED first)
`create_app` returns a FastAPI app; `/health` with no database reachable returns 200, `status == "degraded"`, `checks.postgres == "fail"`, `checks.openviking == "skipped"`; `/health` with a stubbed working engine and a stubbed OpenViking 200 response returns `status == "ok"`; `/version` envelope; validation error on a test route returns 422 envelope with field details; `RootCauseError` raised in a test route returns 400 envelope with the error message; unhandled exception returns 500 envelope with generic message and no stack text; `X-Request-ID` echoed and generated; rate limit: with `rate_limit="3/minute"` the fourth request within a minute returns 429 envelope; OpenAPI document contains `/health` and `/version`; `langgraph.json` parses and the referenced graph compiles and runs with an input. Coverage >= 80%.

## Acceptance (backlog)
`uvicorn` serves `/health` with dependency status; `langgraph dev` starts with the placeholder graph (verified with `uv run --with langgraph-cli langgraph dev --no-browser` smoke, or documented if the CLI cannot run offline); integration tests cover the envelope and error handlers.
