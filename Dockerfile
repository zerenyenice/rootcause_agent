# RootCause Python image: one image, three compose services with different
# commands (deploy/docker-compose.yml):
#   api      uvicorn --factory rootcause.api.main:create_app            :8000
#   agent    uvicorn --factory rootcause.agent_service.main:create_app  :8100
#   migrate  rootcause-db upgrade (one-shot, as the migrator role)
#
# Build from the repo root (the allow-list in .dockerignore keeps the context
# to pyproject.toml, uv.lock, README.md and src/):
#   docker build -t rootcause-app:dev .
#
# Dependencies come only from uv.lock (`uv sync --locked`; the build fails if
# the lockfile is out of date with pyproject.toml), without the dev group.
# The project is installed as a regular (non-editable) wheel into /app/.venv,
# so the runtime stage needs neither uv nor the source tree. Runs as an
# unprivileged user. No secret is read at build time; every setting comes
# from the ROOTCAUSE_* environment at run time.
#
# Base images: exact tags plus the multi-arch index digest observed
# 2026-10-25. Bump both together in a `chore(deploy):` commit.

# uv 0.13.5 (matches the version the lockfile is maintained with).
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# ---------------------------------------------------------------------------
# Builder: resolve and install the locked dependencies, then the project.
# ---------------------------------------------------------------------------
FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first (cached layer): only the lockfile and project metadata.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project --no-editable

# Then the project itself (packaged data: prompts, skills, Alembic scripts).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------------------
# Runtime: the virtualenv only, as a non-root user.
# ---------------------------------------------------------------------------
FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS runtime

ARG APP_UID=10001
RUN groupadd --system --gid "${APP_UID}" rootcause \
    && useradd --system --uid "${APP_UID}" --gid rootcause \
        --create-home --home-dir /home/rootcause --shell /usr/sbin/nologin rootcause

# Same path as in the builder, so the console scripts' shebangs stay valid.
# Owned by root and read-only for the service user.
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The working directory holds no .env: settings come from the process
# environment only (compose passes deploy/.env via env_file).
WORKDIR /home/rootcause
USER rootcause

EXPOSE 8000 8100

# Default command = the api service; compose sets the agent/migrate commands.
CMD ["uvicorn", "--factory", "rootcause.api.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
