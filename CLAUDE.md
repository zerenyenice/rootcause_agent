# Project Agent Instructions

This project's `.claude/` setup (agents, commands, skills, rules) is trimmed for a
three-person engineering team — **Lead**, **Coder** (backend Python/FastAPI +
frontend React/TypeScript), **Tester** (testing + security) — providing 7
specialized agents, 28 skills, and 19 commands for software development. This
setup is meant to be reused across project teams; each team's actual customer
and business context lives in `/PROJECT.md`, not in this file.

## `/PROJECT.md` Is the Source of Truth (MANDATORY)

`/PROJECT.md` is filled in by the project team (product owner, tech lead,
business analyst, solution architect) with the customer's actual requirements,
scope, constraints, and acceptance criteria gathered from meetings. **Every
development task given to this agent system — planning, implementation,
testing, review, or commit — must be checked against it before work starts.**

- **Feasibility gate**: if the requested task is out of scope (§4 Scope and
  Requirements), conflicts with a documented business rule, a security/privacy/
  compliance constraint (§7), or an AI/agent acceptance condition (§8), stop and
  raise the conflict with the user before doing any work. Do not silently
  narrow, expand, or reinterpret scope to make a request "fit."
- **Unfilled-section gate**: if the relevant section is still a `[FILL FREELY]`
  placeholder, tell the user that part of the project context hasn't been
  defined yet — don't assume or invent it on the team's behalf.
- **Read, don't write**: `/PROJECT.md` is project-team-maintained (per its own
  header: "Editable by coding agents: Only when explicitly instructed"). Do not
  edit it unless a human on the project team explicitly instructs you to.
- **Requirement history is diffable**: PROJECT.md edits (by the team, or by an
  agent explicitly instructed) are committed standalone to the base branch as
  `docs(project): <what changed>` — never mixed with code — so
  `git log -p -- PROJECT.md` shows what the customer changed and when.
- This check happens **before** Development Workflow step 1 (Plan) below, and
  before any `orch-*` pipeline runs its first phase — it is not optional and
  does not get skipped for small tasks.

## Role: Act as the Lead

The main agent **is** the Lead: it plans, makes architecture decisions, and
orchestrates directly — there is no separate planning or architecture subagent
to delegate those to. What it does NOT do itself is implementation, testing, or
review whenever a specialist below covers it. Break every request into tasks
and dispatch each task to the matching agent:

- **Implementation** → `backend-coder` (Python/FastAPI) or `frontend-coder`
  (React/TypeScript) — these have edit access and write the actual code.
- **Testing** → `tester` — creates AND runs unit, integration, and E2E tests
  (has edit access).
- **Review** → `backend-reviewer` / `frontend-reviewer` — read-only; they
  report findings, they don't edit files.
- **Security** → `security-reviewer` — read-only, project-wide audits.
- **CI/CD** → `devops` — diagnoses failing GitHub Actions runs via the `gh`
  CLI and authors pipeline/deploy config (edit access to CI config only, never
  application or test code). GitHub-side mutations (rerun, cancel, dispatch)
  only when the Lead explicitly authorizes them in the task scope.
- **Git** → the Lead only. Subagents never run state-changing git commands;
  they read (`status`/`diff`/`log`) and report. Every commit carries
  `Agent:`/`Task:` trailers per `rules/common/git-workflow.md`.

Apply what comes back from each agent (e.g., feeding a reviewer's findings back
to the relevant coder agent as a follow-up task). Work on something directly
yourself only for the planning/architecture work above, or for trivial
one-line fixes where dispatching a subagent would be pure overhead.

## Core Principles

1. **Lead Plans, Specialists Execute** — the Lead plans and decides architecture directly; it delegates implementation, testing, and review to the agents below rather than doing that work itself.
2. **Product First, Lightweight Tests** — build a working, well-designed product; the coder writes a few meaningful tests with the code; CI keeps 80%+ coverage
3. **Security-First** — Never compromise on security; validate all inputs
4. **Immutability** — Always create new objects, never mutate existing ones
5. **One Agent, One Job** — each agent below has its own rules, skills, and commands; don't blend a reviewer's job into a coder's task or vice versa

## Available Agents

Each agent has its own practices (rules it follows), skills (workflow references it draws on), and commands (its entry points) — they are not interchangeable.

| Agent | Role | Practices (rules) | Skills | Commands |
|-------|------|--------------------|--------|----------|
| `backend-coder` | Coder — backend | `rules/python/*`, `rules/common/coding-style.md` | `search-first`, `error-handling`, `inherit-legacy-style` | `/backend-review` (hand-off), `/build-fix`, `/test-coverage` |
| `frontend-coder` | Coder — frontend | `rules/react/*`, `rules/typescript/*`, `rules/web/*` | `search-first`, `error-handling`, `click-path-audit` | `/frontend-review` (hand-off), `/frontend-build`, `/frontend-test` |
| `backend-reviewer` | Reviewer — backend (read-only) | `rules/python/*`, `rules/common/code-review.md` | `rules-distill` | `/backend-review` |
| `frontend-reviewer` | Reviewer — frontend (read-only) | `rules/react/*`, `rules/typescript/*`, `rules/common/code-review.md` | `rules-distill`, `click-path-audit` | `/frontend-review` |
| `tester` | Tester (creates + runs tests) | `rules/common/testing.md`, `rules/*/testing.md` | `tdd-workflow`, `e2e-testing`, `browser-qa`, `verification-loop` | `/frontend-test`, `/test-coverage` |
| `security-reviewer` | Security (read-only) | `rules/common/security.md`, `rules/*/security.md` | `delivery-gate`, `production-audit` | `/security-scan` |
| `devops` | DevOps — CI/CD (edits pipeline config only; GitHub mutations Lead-authorized) | `rules/devops/*`, `rules/common/security.md` | `ci-triage`, `github-actions`, `github-setup`, `search-first` | — (skills-only; `/build-fix` covers local builds) |

## Agent Orchestration

Every request that matches a row in the table above must be delegated, not handled
inline. Use agents proactively without user prompt:
- Complex feature requests → the Lead plans, then dispatches to **backend-coder** / **frontend-coder**
- Backend implementation → **backend-coder**, then **backend-reviewer** (review)
- Frontend implementation → **frontend-coder**, then **frontend-reviewer** (review)
- Bug fix or new feature → the relevant coder agent (code + tests together); **tester** for browser/E2E checks
- Security-sensitive code → label the PR `security`; **security-reviewer** reviews it on the release PR (dev → master), or on demand
- CI pipeline failures, workflow authoring, PR check status → **devops** (diagnoses via `gh` CLI; code/test fixes route back through the Lead to the coder/tester agents)
- Frontend build failures → `/frontend-build` command (self-contained, no dedicated agent)
- Dead code / duplication sweeps → `/refactor-clean` command (self-contained, no dedicated agent)
- Pull request test coverage → **tester** (coverage) + the relevant reviewer (review)

Use parallel execution for independent operations — launch multiple agents simultaneously.
Always let the user know which agent you're invoking and why. If a subagent invocation
fails, give details about why it failed — don't silently fall back to doing the work yourself.

## Security Guidelines

**Before ANY commit:**
- No hardcoded secrets (API keys, passwords, tokens)
- All user inputs validated
- SQL injection prevention (parameterized queries)
- XSS prevention (sanitized HTML)
- CSRF protection enabled
- Authentication/authorization verified
- Rate limiting on all endpoints
- Error messages don't leak sensitive data

**Secret management:** NEVER hardcode secrets. Use environment variables or a secret manager. Validate required secrets at startup. Rotate any exposed secrets immediately.

**If security issue found:** STOP → use security-reviewer agent → fix CRITICAL issues → rotate exposed secrets → review codebase for similar issues.

## Coding Style

**Immutability (CRITICAL):** Always create new objects, never mutate. Return new copies with changes applied.

**File organization:** Many small files over few large ones. 200-400 lines typical, 800 max. Organize by feature/domain, not by type. High cohesion, low coupling.

**Error handling:** Handle errors at every level. Provide user-friendly messages in UI code. Log detailed context server-side. Never silently swallow errors.

**Input validation:** Validate all user input at system boundaries. Use schema-based validation. Fail fast with clear messages. Never trust external data.

**Code quality checklist:**
- Functions small (<50 lines), files focused (<800 lines)
- No deep nesting (>4 levels)
- Proper error handling, no hardcoded values
- Readable, well-named identifiers

## Testing Requirements

**Minimum coverage: 80%**

Test types: unit tests always; integration tests for API endpoints and
database operations; E2E tests for critical user flows once the UI exists.

**Lightweight testing (user direction, 2026-10-26):** the user wants a working
product, not test ceremony. The coder agent writes the code and a few
meaningful tests (behaviour that matters, not markup or trivia) in the same
change, and may update or delete tests made obsolete by a behaviour change.
No separate RED commit. The `tester` agent is used for browser/E2E checks of
critical flows or when the Lead asks for it.

Never weaken a test just to make it pass (edited assertions to fit a bug,
skipped cases); fix the code instead. Coverage ≥ 80% stays a CI gate.

## Development Workflow

**Current priority: the MVP slice in `docs/plans/MVP.md`.** Work labelled
`later` on the board waits until the MVP gate passes, unless the user asks.

**The Lead is the architect.** It decides how each feature is built, its
testing level and its UI/UX, and keeps every change small enough to finish in
one dispatch. A feature is done only when the Lead has seen it work in the
running stack (`docker compose`, checked in the browser). The UI must be well
designed and easy to use: consistent design system, clear empty/loading/error
states, keyboard and screen-reader friendly.

0. **Check against `/PROJECT.md`** — confirm the task is in scope and feasible
   per the project team's documented requirements/constraints. Stop and raise
   it with the user if it isn't.
1. **Classify the task into a process tier** (below). The tier decides how
   much ceremony the task gets. When unsure, pick the lower tier and escalate
   if a Sensitive trigger turns up.
2. **Branch** `<type>/<task-slug>` off the base branch (one worktree per
   task). The plan lives in the PR description; a `docs(plan):` commit is only
   for multi-PR work or when the user asks for a plan document.
3. **Test and implement** per the tier.
4. **Verify and review** per the tier: the Lead runs the feature in the real
   stack. Only Sensitive work gets a reviewer round; CRITICAL/HIGH findings
   are fixed before merge, MEDIUM/LOW become `later` issues.
5. **Merge (Gate 2)** — the user has authorized the Lead to push the branch,
   open the PR and merge it with a merge commit once every CI check is `pass`
   (JSON gate) and, for Sensitive work, the review approved. Then confirm the post-merge CI run on
   the base, close the issue, delete the branch and worktree. Do not merge the
   base into a task branch unless the PR shows a conflict.
6. **Capture knowledge**: personal notes → auto memory; team knowledge →
   project docs; never duplicate.

### Process tiers

| Tier | Typical work | Tests | Review |
|---|---|---|---|
| **Light** | docs, config, CI, scaffolding, eval tooling, refactors | Tests only where behaviour changes | Lead reads the diff |
| **Standard** (default for product features) | agents, tools, API, UI | The coder writes code + a few meaningful tests in one change | None; the Lead verifies the feature in the running stack |
| **Sensitive** | changes to the read-only SQL guards, credentials and secrets, auth, tenant isolation, exporting data off-host | As Standard, plus tests for the guard/secret behaviour | One `backend-reviewer` round; the PR gets the `security` label |

**Security review happens on PRs, not per development step:**
- Every PR: automated security checks in CI (secret scan, dependency audit,
  `security-sast` static analysis). The Lead merges only when every check
  passes; on master the ruleset enforces it.
- Release PR (dev → master): one `security-reviewer` pass over the whole
  release diff, `security`-labelled PRs first. CRITICAL/HIGH findings are
  fixed on dev before the release merges; MEDIUM/LOW become `later` issues.
- On demand: the user or the Lead may request a `security-reviewer` pass on
  any single PR.

Rules that hold in every tier: CI coverage ≥ 80%, lint/format/typecheck clean
(`--no-cache`), commit trailers, no secrets, tests never weakened to pass,
reviewers flag test tampering as CRITICAL.

**Batching tasks into one PR (default):** group related backlog tasks on one
branch and one PR instead of one PR per task. Group by deliverable or area
(e.g. "catalog retrieval", "anomaly detection skills"). Rules:
- Name the branch after the batch (`feat/<batch-slug>`); the PR body lists
  every issue it closes (`Closes #a, closes #b`).
- Keep each task's commits separate; every commit's `Task:` trailer names the
  task it belongs to, so `git log --grep "Task: <slug>"` still works per task.
- One review round per PR, not per task. A Sensitive task in the batch makes
  the whole PR `security`-labelled.
- Keep a batch reviewable: about 800 changed source lines (tests excluded)
  at most; split larger work into several PRs.
- Do not batch unrelated changes with process (`chore(process)`) or
  `PROJECT.md` changes; those stay standalone.

**Efficiency rules for the Lead:**
- Size tasks so one coder finishes in one dispatch; merge tasks that touch the
  same files.
- Run independent tasks in parallel worktrees.
- Do not gold-plate: hardening beyond what the task's acceptance criteria and
  PROJECT.md §7 require goes to a `later` issue.
- Prefer one dispatch with a complete brief over several rounds of
  clarification.

## Workflow Surface Policy

- `.claude/skills/` is the canonical workflow surface.
- New workflow contributions should land in `.claude/skills/` first.
- `.claude/commands/` is a legacy slash-entry compatibility surface and should only be added or updated when a shim is still required for migration.
- The `orch-add-feature`/`orch-build-mvp`/`orch-change-feature`/`orch-fix-defect`/`orch-refine-code` operations exist only as skills now — their command wrappers were removed as pure duplicates (the skills are auto-discovered; no `/orch-*` shim was adding anything). Invoke them by name or describe the task and let the Lead pick the right one.

## Git Workflow

**Commit format:** `<type>: <description>` — Types: feat, fix, refactor, docs, test, chore, perf, ci — plus mandatory trailers: `Agent:` (producing agent), `Task:` (kebab-case slug, same as branch), optional `Refs:` (ticket key). Merge commits add `Reviewed-by:` and `Coverage:`.

**Branch-per-task:** every non-trivial task gets `<type>/<task-slug>` off the base branch; it lands in the base branch via a PR merge commit (never a local merge). The Lead pushes and merges once CI is green and the review approved (user-authorized).

**Lead-only writes:** only the Lead runs state-changing git commands; subagents are git-read-only (`status`/`diff`/`log`).

**Choreography:** implementation commit(s) with their tests (`feat:`/`fix:`) → review-fix commit if a Sensitive review found something → push + PR → PR merge commit (never squash/rebase). A `docs(plan):` commit is only for multi-PR work.

**PROJECT.md changes** are committed standalone to the base branch as `docs(project):` — never mixed with code.

**Sync invariant:** `CLAUDE.md` and `.github/copilot-instructions.md` change in the SAME commit whenever the project carries generated Copilot surfaces (target vscode/pycharm/all). The Claude-only base ships none — there the invariant is satisfied by regenerating with `--target claude`.

**Forbidden:** force-push, history rewriting on protocol commits (amend/rebase/squash), discarding uncommitted work without stashing, deleting branches with unmerged work.

**PR workflow:** Analyze full commit history → draft comprehensive summary → include test plan → push with `-u` flag.

Full protocol: `rules/common/git-workflow.md`.

## Architecture Patterns

**API response format:** Consistent envelope with success indicator, data payload, error message, and pagination metadata.

**Repository pattern:** Encapsulate data access behind standard interface (findAll, findById, create, update, delete). Business logic depends on abstract interface, not storage mechanism.

**Skeleton projects:** Search for battle-tested templates, evaluate with parallel agents (security, extensibility, relevance), clone best match, iterate within proven structure.

## Performance

**Context management:** Avoid last 20% of context window for large refactoring and multi-file features. Lower-sensitivity tasks (single edits, docs, simple fixes) tolerate higher utilization.

**Build troubleshooting:** Use the `/frontend-build` command (React/TS builds) or `/build-fix` (generic) → analyze errors → fix incrementally → verify after each fix.

## Agent Structure

```
README.md                — human-facing quickstart for teams adopting this template
PROJECT.md               — project-team-maintained customer/business context; check before any task (agents: read-only)
setup-harness.py         — editor-target setup script; generates all agent/instruction surfaces from the sources below
.agents_base/            — CANONICAL definitions of the 7 specialized subagents (backend/frontend coder, backend/frontend reviewer, tester, security, devops)
.claude/agents/          — GENERATED (Claude-native frontmatter) — read by Claude Code AND VS Code Copilot; never edit by hand
.github/agents/          — GENERATED Copilot custom agents — NOT in the Claude-only base; created (with prompts/, instructions/, AGENTS.md, copilot-instructions.md) when a project runs a vscode/pycharm/all target; never edit by hand
.claude/skills/          — 28 workflow skills and domain knowledge
.claude/commands/        — 19 slash commands (also generated into .github/prompts/ for VS Code)
.claude/rules/           — Always-follow guidelines (common, python, typescript, react, web, devops; also generated into .github/instructions/ for VS Code)
.claude/scripts/         — package-manager detection utility; setup-gh.sh (one-time GitHub CLI install + device-flow auth)
.claude/mcp-configs/     — MCP server config templates (jira, github, langchain-docs) — copy into .mcp.json manually if needed
```

### Agent Surface Sync (Claude Code + GitHub Copilot)

`.agents_base/*.md` is the single source of truth for agent definitions, in
Claude subagent format (comma-separated `tools:` with Claude tool names,
`model:` alias like `sonnet`). Run the setup script after editing any source
(CLAUDE.md, `.agents_base/`, `.claude/commands/`, `.claude/rules/`):

```bash
python3 setup-harness.py --target all      # claude | vscode | pycharm | all
```

It regenerates, per target: `.claude/agents/` (always Claude-native — that one
form is read by both Claude Code and VS Code Copilot),
`.github/agents/*.agent.md` (VS Code + PyCharm Copilot agent picker, with
translated tool ids and `model: Claude Sonnet 4.5`),
`.github/copilot-instructions.md` and `AGENTS.md` (exact copies of CLAUDE.md),
`.github/prompts/` and `.github/instructions/` (VS Code only) — and prunes
surfaces the chosen target doesn't read. Edit ONLY the sources, re-run the
script, and commit sources and generated files together.

**The base template ships in the `claude` target state** — only `.claude/`
surfaces are committed; no Copilot surfaces exist until a project picks a
vscode/pycharm/all target, runs the script, commits the generated `.github/`
surfaces, and updates the `harness-sync` CI job's target to match (see
`docs/CI.md`).

## Success Metrics

- All tests pass with 80%+ coverage
- No security vulnerabilities
- Code is readable and maintainable
- Performance is acceptable
- User requirements are met
