# Plan: T003 CI pipeline — lint, typecheck, tests with coverage gate, security audit

Backlog T003 (issue #14), epic EP0. Branch `feat/ci-python-pipeline` off `dev`. Gate 1 approved 2026-10-25 (batch approval).

## Goal
Add a Python quality job to `.github/workflows/ci.yml` that mirrors the local commands from T001 and fails under 80% coverage, plus a dependency audit, following `.claude/rules/devops/*`.

## Changes
1. New job `python-quality` in `ci.yml` (fail-fast order): checkout (pinned SHA) → `astral-sh/setup-uv` (pinned SHA, cache on `uv.lock`) → `uv python install 3.12` → `uv sync --locked` → `uv run ruff check .` → `uv run ruff format --check .` → `uv run mypy` → `uv run pytest -q --cov --cov-report=xml --cov-report=term --cov-fail-under=80` → upload `coverage.xml` artifact with `if: always()` and 7-day retention. `timeout-minutes: 15`, `permissions: contents: read`.
2. New job `dependency-audit`: `uv export --locked --no-dev --format requirements.txt | uv tool run pip-audit -r /dev/stdin` (or equivalent), HIGH/CRITICAL fail the job.
3. Header comments in `ci.yml` and the intro of `docs/CI.md` updated: the repo now ships a Python workspace; document the new jobs.
4. Review follow-ups from T001 (issue #14 comment): relaxed mypy override for `tests`/`*/tests` (`disallow_untyped_defs=false`), README note on `uv run pytest --no-cov -k ...` for focused runs, lint-fix `scripts/backlog_to_github.py` and remove it from `extend-exclude`, raise `ruff`/`mypy` lower bounds to the locked minors.
5. After merge the Lead adds `python-quality` and `dependency-audit` to the `protect-master` ruleset required checks.

## Validation
`actionlint` on the workflow; run the job's commands locally; a dry run via `act` is optional.

## Acceptance (backlog)
Pinned SHAs, least-privilege permissions, timeouts, concurrency; coverage below 80% fails; actionlint passes.
