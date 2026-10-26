# Team Development Template — Multi-Agent System (Claude Code + GitHub Copilot)

A base repository for starting new projects. It ships a pre-configured Claude
Code multi-agent setup for a three-person team model — **Lead** (planning +
orchestration), **Coder** (Python/FastAPI backend + React/TypeScript frontend),
**Tester** (testing + security) — with a test-driven, review-gated development
cycle built in.

You describe tasks in plain language; the Lead (the main Claude agent) checks
them against your `PROJECT.md`, plans, and dispatches work to specialist
subagents. You approve at two gates: the plan, and the merge.

The template works with **Claude Code** and with **GitHub Copilot in VS Code
and PyCharm/JetBrains** — the setup script below generates the right files for
whichever your team uses.

## Setup — do this first

Run the setup script from the repo root and pick your editor target:

```bash
python3 setup-harness.py                # interactive
python3 setup-harness.py --target all   # mixed team (recommended)
```

It generates every editor-specific surface from the canonical sources
(`CLAUDE.md`, `.agents_base/`, `.claude/commands/`, `.claude/rules/`):

| Target | Generates |
|---|---|
| `claude` | `.claude/agents/` (Claude-native form) — Copilot surfaces pruned |
| `vscode` | above + `.github/agents/*.agent.md`, `.github/copilot-instructions.md`, `AGENTS.md`, `.github/prompts/`, `.github/instructions/` |
| `pycharm` | above minus the VS Code-only files (`AGENTS.md`, prompts, instructions) |
| `all` | everything — the same clone works in Claude Code, VS Code, and PyCharm |

The script is idempotent. Re-run it with the same target whenever you edit a
source file, and commit sources and generated files together. **Never
hand-edit generated files** (`.claude/agents/`, `.github/agents/`,
`.github/copilot-instructions.md`, `AGENTS.md`) — edit `.agents_base/` and the
other sources instead. After running it, reload your editor and the seven
agents appear in Copilot Chat's agents dropdown.

> **The template ships Claude-only** — out of the box only `.claude/` surfaces
> are committed. Picking `vscode`, `pycharm`, or `all` generates the Copilot
> surfaces under `.github/` (+ `AGENTS.md`); commit them in your project copy
> and update the `harness-sync` job's target in `.github/workflows/ci.yml` to
> match (see `docs/CI.md`).

The script ends with a **GitHub access check**: the devops agent and the
`/pr` workflow talk to GitHub's API through the `gh` CLI, which needs a
one-time, per-machine login. If the check reports `gh` missing or
unauthenticated, run:

```bash
bash .claude/scripts/setup-gh.sh
```

It installs `gh` if needed and starts the OAuth device flow (approve in your
browser — your SSH keys stay the git push mechanism). Details, agent-driven
and headless variants: `.claude/skills/github-setup/SKILL.md`.

## Quickstart — starting a new project

1. **Copy this template** into your new project's repository (or clone and
   re-point the remote).
2. **Run the setup script** (see [Setup](#setup--do-this-first) above) so the
   agent surfaces match your team's editor(s) before the bootstrap commit.
3. **Initialize git and commit the template first** — the workflow depends on
   git history (gates, test-integrity proof, PR flow). A `.gitignore` covering
   env files, caches, and build output ships with the template:
   ```bash
   git init
   git add -A
   git commit -m "chore: bootstrap from team development template"
   ```
4. **Fill in `PROJECT.md`.** This is the single source of truth for what you're
   building — customer expectations, scope, business rules, security/compliance
   constraints, acceptance criteria. It is filled by the project team (product
   owner, tech lead, business analyst), not by agents. **Agents check every
   task against it and will refuse out-of-scope work**, and will tell you when
   a section they need is still an unfilled `[FILL FREELY]` placeholder.
5. **Set up GitHub access** (one-time per machine) so the devops agent and
   the `/pr` workflow can reach GitHub's API:
   ```bash
   bash .claude/scripts/setup-gh.sh
   ```
   Skips instantly if `gh` is already authenticated; otherwise installs it
   and walks you through the browser device-flow login.
6. **Open Claude Code** (or Copilot Chat in VS Code/PyCharm) in the repo and
   describe your first task, e.g. *"add a user registration endpoint"*. The
   Lead takes it from there.
7. *(Optional)* Enable the Jira integration by copying
   `.claude/mcp-configs/mcp-servers.json` into your `.mcp.json` and filling in
   credentials.

## How the development cycle works

Every task flows through the same gated pipeline:

```
PROJECT.md check  →  Plan  →  [GATE 1: you approve the plan]
  →  task branch created; approved plan committed first
  →  tester writes a failing test (RED — committed before any implementation)
  →  coder implements until green (never touches test files)
  →  reviewers report findings; Lead feeds fixes back to the coder
  →  [GATE 2: you approve the merge — coverage ≥80% required]
```

- **Gate 0** — the task must be feasible and in scope per `PROJECT.md`.
- **Gate 1** — no implementation code is written until you approve the plan;
  approval creates the task branch.
- **Gate 2** — nothing merges back (or gets pushed) until you confirm; you see
  the diff summary, the commit list, and the tester's measured coverage —
  below 80% blocks unless you explicitly override. Commits *on* the task
  branch are automatic; the branch is cheap to discard if you reject the merge.

### How git is used

- **Branch-per-task**: every non-trivial task runs on its own
  `<type>/<task-slug>` branch and merges back (`--no-ff`) only after you
  approve at Gate 2.
- **Only the Lead commits** — subagents are git-read-only, so history stays
  linear and attributable.
- **Every commit is tagged** with the producing agent and task via `Agent:` /
  `Task:` trailers (plus `Refs:` for ticket keys, and `Reviewed-by:` /
  `Coverage:` on merge commits).
- **Requirement changes are diffable**: `PROJECT.md` edits land as standalone
  `docs(project):` commits — `git log -p -- PROJECT.md` shows what the
  customer changed and when.
- **Traceability from `git log` alone**, e.g.:
  ```bash
  git log --grep "Agent: tester"     # everything the tester produced
  git log --grep "Refs: PROJ-123"    # all commits for a Jira ticket
  git log --grep "Task: user-registration"
  ```

## The agents

| Agent | Job | Access |
|---|---|---|
| Lead (main agent) | Plans, decides architecture, orchestrates, handles git | full |
| `backend-coder` | Implements Python/FastAPI code | edit |
| `frontend-coder` | Implements React/TypeScript code | edit |
| `tester` | Writes AND runs unit/integration/E2E tests | edit |
| `backend-reviewer` | Reviews backend code, reports findings | read-only |
| `frontend-reviewer` | Reviews frontend code, reports findings | read-only |
| `security-reviewer` | OWASP/security audits on sensitive changes | read-only |

Separation of duties is enforced by rules and git history: tests are
tester-owned (coders may not modify them — the failing test is committed before
implementation, so tampering shows up in the diff), reviewers can't edit files,
and only the Lead commits.

## Repository layout

```
README.md          — this file
PROJECT.md         — YOUR project's requirements & constraints (fill this!)
DEVELOPMENT.md     — full reference for the development & testing cycle
DEVOPS.md          — DevOps integration guide (Azure DevOps PR flow, Docker/Helm, releases)
AGENT-SYSTEM.md    — how the machinery works (agents, rules, skills, commands, MCP, scripts)
CLAUDE.md          — agent operating instructions (edit only to change team process)
setup-harness.py   — editor setup script (run first — see Setup above)
.agents_base/      — CANONICAL agent definitions; all agent surfaces are generated from these
.github/
├── copilot-instructions.md — GENERATED exact copy of CLAUDE.md for GitHub Copilot
├── agents/        — GENERATED Copilot custom agents (*.agent.md, VS Code + PyCharm)
├── prompts/       — GENERATED VS Code prompt files (from .claude/commands/)
└── instructions/  — GENERATED VS Code scoped instructions (from .claude/rules/)
AGENTS.md          — GENERATED exact copy of CLAUDE.md (VS Code AGENTS.md support)
.claude/
├── agents/        — GENERATED Claude-native copies of the 6 specialist subagents
├── skills/        — workflow knowledge (TDD, E2E, git, security, orchestration…)
├── commands/      — slash commands you can type (e.g. /plan, /security-scan, /pr)
├── rules/         — coding/testing/security standards (common, python, typescript, react, web)
├── scripts/       — package-manager detection utility
└── mcp-configs/   — Jira MCP server template
```

## Useful commands

| Command | What it does |
|---|---|
| `/plan <task>` | Plan a feature without writing code; waits for your approval |
| `/backend-review`, `/frontend-review` | On-demand code review |
| `/security-scan` | Project-wide security audit |
| `/frontend-build`, `/build-fix` | Incrementally fix build failures |
| `/test-coverage` | Analyze coverage and generate missing tests |
| `/pr` | Create a GitHub PR from the current branch |
| `/jira get <KEY>` | Pull a Jira ticket and extract requirements |

Most workflows don't need a command at all — describe the task and the Lead
picks the right skill (add-feature, fix-defect, change-feature, refactor,
build-mvp) automatically.

## House rules (the short version)

- **Test-driven, always**: failing test first, 80%+ coverage, coverage gates the commit.
- **Security-first**: no hardcoded secrets, validated inputs, parameterized queries; sensitive diffs get a security review automatically.
- **`PROJECT.md` wins**: if a task conflicts with it, agents stop and raise it rather than reinterpreting scope.
- **Conventional commits**: `feat:` / `fix:` / `refactor:` / `test:` / `docs:` / `chore:`.

## Development

The repository is one installable Python package, `rootcause` (src layout,
managed with [`uv`](https://docs.astral.sh/uv/)). Python 3.12 is pinned in
`.python-version`; `uv` installs it on demand if it is missing.

```bash
uv sync                 # create .venv and install the rootcause package (editable) + dev tools
uv run pytest           # run all tests with coverage (fails under 80%)
uv run pytest --no-cov -k <expr>   # focused run; the 80% coverage gate applies to full runs only
uv run ruff check .     # lint
uv run ruff format --check .
uv run mypy             # strict type-check of src/ and tests/
uv run uvicorn --factory rootcause.api.main:create_app --reload   # serve the API (needs ROOTCAUSE_DATABASE_URL)
uv run python -m rootcause.agent_service.persistence   # once: create the agent's checkpoint/store tables (migrator role)
uv run uvicorn --factory rootcause.agent_service.main:create_app --port 8100   # agent service (chat UI backend)
uv run --with "langgraph-cli[inmem]" langgraph dev   # LangGraph Server (root langgraph.json), "rootcause" graph
```

### Running the MVP end to end

1. Generate the synthetic manufacturing warehouse (DuckDB file plus the planted
   cases in `cases.json`); `data/` is gitignored:

   ```bash
   uv run python -m rootcause.evals.manufacturing generate --out data/warehouse --incidents E1,E2,E3 --days 120
   ```

2. Point the agent at it and give it a model key (in `.env` or the shell):

   ```bash
   ROOTCAUSE_DATABASE_URL=postgresql://...            # required by the settings
   ROOTCAUSE_WAREHOUSE_PATH=data/warehouse/warehouse.duckdb
   ROOTCAUSE_ANTHROPIC_API_KEY=...                    # or the Azure OpenAI variables
   ```

   Without `ROOTCAUSE_WAREHOUSE_PATH` the graph still starts, but with no SQL
   tools and no `root-cause` subagent it can only frame the problem.

3. Start the agent service and the chat UI (two terminals). The agent keeps
   threads in Postgres: after `rootcause-db bootstrap` and `rootcause-db upgrade`,
   create its checkpoint and store tables once with the migrator role
   (`ROOTCAUSE_MIGRATION_DATABASE_URL`); the service itself runs as the app role
   (`ROOTCAUSE_DATABASE_URL`) and refuses to start until the tables exist:

   ```bash
   uv run python -m rootcause.agent_service.persistence
   uv run uvicorn --factory rootcause.agent_service.main:create_app --port 8100
   cd web && npm run dev
   ```

   The chat UI calls the agent at `NEXT_PUBLIC_AGENT_URL` (default
   `http://localhost:8100`); the agent accepts browser requests from
   `ROOTCAUSE_AGENT_ALLOWED_ORIGINS` (JSON list, default `["http://localhost:3000"]`).
   The API accepts browser requests from `ROOTCAUSE_API_ALLOWED_ORIGINS` (same format).
   No authentication in the MVP: bind the services to localhost.
   `langgraph dev` (root `langgraph.json`) still serves the same graph for LangGraph Studio.

   The orchestrator frames the problem (what, when, where, impact), delegates
   the why-questions to the `root-cause` subagent and presents its WhyChain report.

4. Run the evaluation unattended. The runner hands the agent only the question;
   `investigate()` tells the orchestrator no user is available, runs on a fresh
   thread, stops after `ROOTCAUSE_INVESTIGATION_MAX_STEPS` graph steps (default 120)
   or `ROOTCAUSE_INVESTIGATION_TIMEOUT_SECONDS` (default 600, checked between
   approval rounds) and rejects every large-scan approval. The root-cause subagent
   may make `ROOTCAUSE_INVESTIGATION_MAX_TOOL_CALLS` tool calls per run (default 60):

   ```bash
   uv run python -m rootcause.evals.run --cases data/warehouse/cases.json --levels easy --agent rootcause.agents.investigate:investigate
   ```

The API exposes an application factory, so always start it with `--factory`.
Rate limiting keys on the peer address (`X-Forwarded-For` is never read). Behind
a reverse proxy, start uvicorn with `--proxy-headers --forwarded-allow-ips <proxy-ip>`
so the real client address is taken from trusted proxies only.

Layout:

```
src/rootcause/  — the single installable package (py.typed)
├── settings.py, logging.py, telemetry.py, errors.py, prompts.py — shared foundation
├── clients/        — the only place provider SDKs and HTTP clients are built
├── observability/  — Langfuse tracing, masking and export
├── db/             — declarative base, engines, Alembic migrations, bootstrap, rootcause-db CLI
├── connections/    — data source connections and the SQL validator
├── catalog/        — catalog version models
├── agents/         — orchestrator, backends, prompts and agent tools
├── api/            — the FastAPI service (/health, /version) and the LangGraph `rootcause` graph factory
└── evals/          — evaluation harness (manufacturing warehouse generator)
tests/          — one tree mirrored by area (core, db, connections, catalog, agents, api, evals)
web/            — Next.js chat UI (streams from LangGraph Server; see web/README.md)
docs/           — PRD, backlog, plans and process documentation
deploy/         — Docker Compose stack, Postgres bootstrap and dev-only alembic.ini
langgraph.json  — LangGraph Server config registering the `rootcause` graph
```

`uv sync` installs `rootcause` in editable mode, so changes are importable without a
reinstall. Shared tooling (ruff, mypy, pytest, coverage) is configured once in the
root `pyproject.toml`.
