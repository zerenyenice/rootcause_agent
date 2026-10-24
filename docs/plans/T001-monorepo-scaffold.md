# Plan: T001 Scaffold monorepo layout and uv workspace

Backlog: T001 (GitHub issue in epic EP0). Branch: `feat/monorepo-scaffold` off `dev`.
Gate 1 approved by the project owner on 2026-10-25.

## Goal

Create the repository layout from PRD section 10 as an installable `uv`
workspace with shared quality tooling, so that every later task has a place
to land and `uv run pytest` works from a clean clone.

## Layout to create

```
pyproject.toml                 # workspace root, dev deps, ruff/mypy/pytest config
uv.lock
apps/api/pyproject.toml        # rootcause_api  (FastAPI app lands in T005)
apps/api/src/rootcause_api/__init__.py
apps/web/README.md             # placeholder; Next.js app lands in T070
packages/core/                 # rootcause_core  (settings/logging land in T004)
packages/agents/               # rootcause_agents
packages/tools/                # rootcause_tools
packages/catalog/              # rootcause_catalog
packages/connections/          # rootcause_connections
packages/anomaly/              # rootcause_anomaly
packages/memory/               # rootcause_memory
packages/evals/                # rootcause_evals
deploy/README.md               # placeholder; compose lands in T002
tests/test_workspace.py        # workspace-level tests (tester-owned)
```

Each package: `pyproject.toml` (hatchling build, `src/` layout, Python >=3.12),
`src/<dist>/__init__.py` exposing `__version__ = "0.1.0"`, `py.typed`, and
`tests/test_smoke.py`.

## Decisions

- Build backend: hatchling. Workspace members: `apps/api`, `packages/*`.
- Python 3.12 pinned in `.python-version`; `requires-python = ">=3.12"`.
- Tooling at the root: `ruff` (line length 100, rules E,F,I,UP,B,SIM), `mypy`
  strict on `src`, `pytest` with `--cov --cov-fail-under=80`, markers `unit`,
  `integration`, `e2e`.
- No runtime dependencies added in this task (YAGNI); each later task adds its own.
- Node/Next.js is not set up here; `apps/web` is a README placeholder.

## Steps (TDD choreography)

1. Plan commit (this file) — `docs(plan):`, Agent: lead.
2. RED — tester writes `tests/test_workspace.py` and per-package smoke tests
   that fail because packages do not exist. Commit `test: … (RED)`, Agent: tester.
3. GREEN — backend-coder creates the layout and configuration; `uv sync`,
   `uv run pytest`, `uv run ruff check .`, `uv run mypy` all pass. Commit `feat:`.
4. Review — backend-reviewer checks configuration and layout; fixes as
   `fix: address review findings`.
5. Gate 2 step 1 — report branch, commits, diff summary, coverage.

## Risks

- Coverage gate on an almost-empty codebase: smoke tests import every package
  so measured coverage of `__init__` modules is 100%.
- `uv` and Python 3.12 were not installed on the dev machine; installed via
  Homebrew and `uv python install 3.12` before this task started.

## Acceptance criteria (from the backlog)

- `uv sync` installs all packages in editable mode from a clean clone.
- `uv run pytest` runs and passes a smoke test in every package.
- `ruff check` and `mypy` pass on the scaffold.
- README documents how to run the API, the UI and the tests.
