# Plan: T004 Shared settings, logging and OpenTelemetry bootstrap

Backlog T004 (epic EP0). Branch `feat/core-settings` off `dev`. Gate 1 approved 2026-10-25 (batch approval).

## Goal
`rootcause_core` provides the configuration, structured logging and tracing bootstrap every service and package will use.

## Design
- `rootcause_core/settings.py`: `Settings(BaseSettings)` frozen, env prefix `ROOTCAUSE_`, `.env` support. Fields: `environment` (dev|test|prod, default dev), `log_level` (default INFO), `log_format` (json|console, default json), `database_url` (SecretStr, required), `openviking_url` (optional AnyUrl), `otel_exporter` (none|console|otlp, default none), `otel_endpoint` (optional), `service_name` (default "rootcause"). `get_settings()` cached; `clear_settings_cache()` for tests. Validation errors are re-raised as `ConfigurationError` listing the missing variable names (never values).
- `rootcause_core/logging.py`: `configure_logging(settings)` using `structlog` with stdlib integration; JSON renderer in json mode, console renderer otherwise; processors add timestamp, level, logger, and `trace_id`/`span_id` from the current OpenTelemetry span when one is active; `get_logger(name)`.
- `rootcause_core/telemetry.py`: `configure_telemetry(settings)` builds a `TracerProvider` with resource `service.name`, exporter per setting (console, OTLP HTTP, or none); idempotent; `get_tracer(name)`; `shutdown_telemetry()` flushes.
- `rootcause_core/errors.py`: `RootCauseError` base and `ConfigurationError`.
- Dependencies (packages/core/pyproject.toml): `pydantic-settings>=2.6`, `structlog>=24`, `opentelemetry-sdk>=1.27`, `opentelemetry-exporter-otlp-proto-http>=1.27`.

## Tests (tester-owned, RED first)
Settings load from env; missing `ROOTCAUSE_DATABASE_URL` raises `ConfigurationError` naming the variable and not leaking values; secret repr is masked; invalid enum values rejected; `get_settings` caching and cache clear; JSON log line parses and carries level, event, timestamp; log line inside an active span carries matching `trace_id`; console exporter emits a span; `configure_telemetry` idempotent; coverage >= 80% for the package.

## Acceptance (backlog)
Missing required settings fail fast with a clear message; spans carry a trace id that appears in logs.
