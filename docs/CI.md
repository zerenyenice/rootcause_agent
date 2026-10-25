# CI/CD

This repo is both the agent-harness template and the RootCause Python
monorepo — a `uv` workspace (`apps/api` + `packages/*`, see `pyproject.toml`
and `uv.lock`) with `ruff`, `mypy` and `pytest` configured at the root. CI
therefore has two concerns:

1. **Product quality** — `python-quality` runs the same lint → format →
   typecheck → test commands a developer runs locally and fails the job
   under 80% coverage (the Gate 2 bar from `rules/common/testing.md`);
   `dependency-audit` checks the locked runtime dependency set for known
   vulnerabilities.
2. **Harness integrity** — generated agent surfaces must match their
   sources, tracked shell/workflow/JSON files must lint and parse clean, no
   secret ever lands in history, and every commit carries this project's
   git-protocol trailers.
3. **Deploy config** — `compose-config` keeps `deploy/docker-compose.yml`
   (the local/pilot stack from T002) parsing and interpolating against the
   committed placeholder env file.

Owner agent: `devops`. Governing rules: `.claude/rules/devops/ci-cd.md`,
`.claude/rules/devops/github-actions.md`, `.claude/rules/devops/security.md`.

## `.github/workflows/ci.yml`

### `python-quality`

**What it checks:** the Python workspace, in fail-fast order (cheap checks
before expensive ones, per `rules/devops/ci-cd.md`):

1. `uv sync --locked` — install exactly what `uv.lock` pins; the job fails
   if `pyproject.toml` and `uv.lock` have drifted apart.
2. `uv run ruff check .` — lint (rule set from `[tool.ruff.lint]`).
3. `uv run ruff format --check .` — formatting, no rewrites.
4. `uv run mypy` — strict typecheck over the paths in `[tool.mypy].files`
   (the workspace `src/` trees plus the test directories; tests get the
   relaxed `[[tool.mypy.overrides]]` for untyped defs, everything else is
   `strict = true`).
5. `uv run pytest -q --cov --cov-report=xml --cov-report=term-missing
   --cov-fail-under=80` — the full suite with coverage; **under 80% the
   step fails**. The threshold is repeated on the command line on purpose so
   the gate is visible in the workflow diff, not only in `pyproject.toml`.

`coverage.xml` is uploaded as the `coverage-xml` artifact with `if:
always()` (so a red run still yields evidence) and 7-day retention (it is a
debugging aid, not storage).

**Why:** this is the machine-enforced half of Gate 2 — "CI mirrors the merge
gate". The commands are the ones from `pyproject.toml`/T001, so a run that is
green here and red locally (or vice versa) is a pipeline defect to fix, never
a reason to loosen the gate. Lowering `--cov-fail-under`, adding
`continue-on-error`, or deleting a step is a gate override only the user can
grant (`rules/devops/ci-cd.md` § Never weaken to go green).

**Toolchain pinning:** `astral-sh/setup-uv` is pinned to a commit SHA and
installs a pinned `uv` version (the same minor the team runs locally) with
its cache keyed on `uv.lock`; `uv python install 3.12` matches
`.python-version`. Bumping the `uv` version is a deliberate `ci:` change,
not something the runner decides.

**Reproduce locally** (from the repo root, after `uv sync --locked`):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy \
  && uv run pytest -q --cov --cov-report=xml --cov-report=term-missing --cov-fail-under=80
```

### `dependency-audit`

**What it checks:** the locked *runtime* dependency set of every workspace
member, audited against the PyPI advisory database with
[`pip-audit`](https://github.com/pypa/pip-audit):

```bash
uv export --locked --no-dev --no-emit-workspace --format requirements.txt -o requirements-audit.txt
uv tool run pip-audit@2.10.1 -r requirements-audit.txt --strict --progress-spinner off
```

`--no-dev` drops the `dev` tooling group (ruff/mypy/pytest are not shipped),
`--no-emit-workspace` drops the workspace members themselves (they are
first-party, not auditable on PyPI) while keeping their transitive
third-party dependencies, and `--locked` refuses to run if `uv.lock` is
stale. Any known vulnerability fails the job (`pip-audit` exits non-zero);
`--strict` additionally fails the run if a dependency cannot be resolved for
auditing, instead of silently skipping it.

**Empty audit set:** while the workspace carries no third-party runtime
dependencies, the exported file is comment-only. `pip-audit` handles that
(prints `No known vulnerabilities found`, exit 0) — the job just emits a
`::notice::` so the empty set is visible in the run rather than mistaken
for a clean audit of a real dependency tree.

**Why:** `rules/devops/security.md` requires a dependency audit in CI; new
HIGH/CRITICAL advisories are review findings to act on (bump, pin, or an
explicitly justified `--ignore-vuln`), not noise to silence.

### `harness-sync`

**What it checks:** `.claude/agents/`, `.github/agents/*.agent.md`,
`.github/copilot-instructions.md`, `AGENTS.md`, `.github/prompts/`, and
`.github/instructions/` are all *generated* from `CLAUDE.md`,
`.agents_base/*.md`, and `.claude/{commands,rules}` by `setup-harness.py`.
The job re-runs the generator and fails if that produces any diff (modified
**or** untracked file) — a non-empty `git status --porcelain` means a
generated file was hand-edited, or a source changed without the generator
being re-run afterward.

**Why:** this is the single most important check in the repo. If generated
surfaces can drift from sources undetected, the multi-editor promise
(Claude Code, VS Code Copilot, JetBrains Copilot all reading consistent
instructions) silently breaks.

**Note:** `setup-harness.py` ends with a non-fatal GitHub-access check that
prints a warning when `gh` is unauthenticated on the runner. That's expected
in CI (the runner has no `gh auth login`) and does not fail the job — only
`git status --porcelain` being non-empty does.

**Reproduce locally:**

```bash
python3 setup-harness.py --target claude   # or your project's chosen target
git status --porcelain    # must print nothing
```

If it prints something, commit the sources and the regenerated files
together in the same commit (`chore(process): ...`, per
`rules/common/git-workflow.md` B11).

**Target note:** the base template ships Claude-only surfaces (`.claude/`).
When a project copy adopts VS Code/PyCharm Copilot, run the script with that
target, commit the generated `.github/` surfaces, and change the target in
this job to match — the job must regenerate the same target the repo
commits, or every run reports false drift. With a Copilot target, the B11
sync invariant (`CLAUDE.md` ≡ `.github/copilot-instructions.md`) applies and
this job enforces it mechanically.

### `lint-shell`

**What it checks:** `shellcheck` against every tracked `*.sh` file
(currently `.claude/scripts/setup-gh.sh`,
`.claude/skills/rules-distill/scripts/scan-rules.sh`,
`.claude/skills/rules-distill/scripts/scan-skills.sh`). `shellcheck` is
preinstalled on `ubuntu-latest` runners — no download step needed.

**Why:** shell scripts are executable config the harness depends on
(`setup-gh.sh` is the documented first-time `gh` auth path); a quoting or
globbing bug in one is a real defect, not style noise.

**Reproduce locally:**

```bash
shellcheck $(git ls-files '*.sh')
```

### `lint-workflows`

**What it checks:** `actionlint` against every file in
`.github/workflows/`. Catches schema errors, unknown/misspelled contexts,
and shell-injection patterns in `run:` blocks that a plain YAML parser would
miss. The binary is downloaded from the `rhysd/actionlint` GitHub Release
and SHA-256-verified before use, rather than trusted from a third-party
wrapper action.

**Reproduce locally** (with `actionlint` installed, e.g. `brew install actionlint`):

```bash
actionlint .github/workflows/*.yml
```

### `validate-json`

**What it checks:** every tracked `*.json` file
(`.mcp.json`, `.claude/mcp-configs/mcp-servers.json`, and any settings file a
contributor adds) parses with `python3 -m json.tool`.

**Reproduce locally:**

```bash
for f in $(git ls-files '*.json'); do python3 -m json.tool "$f" > /dev/null && echo "OK $f"; done
```

### `compose-config`

**What it checks:** `deploy/docker-compose.yml` (Postgres + pgvector and
OpenViking, task T002; `api`/`web` join under the `app` profile in
T005/T070) against the committed placeholder environment:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example config -q
```

`config -q` parses the YAML, validates it against the Compose schema and
resolves every `${VAR}` / `${VAR:?required}` reference, without pulling
images or starting containers — `docker compose` is preinstalled on
`ubuntu-latest`, so no download step and no secrets are involved. A second
step reproduces the `${VAR}` expansion OpenViking applies to
`deploy/openviking/ov.conf` at load time (`os.path.expandvars` + `json.loads`)
and fails if the expanded template is not valid JSON or misses a required
section — the template is deliberately only valid JSON *after* expansion
(the embedding dimension must expand to a number), so a plain JSON parse
would not do.

**Why:** the stack definition is the first thing every later task's
integration tests depend on; a typo in it should fail a PR, not a
developer's first `docker compose up`. Keeping `deploy/.env.example` as the
interpolation source also forces the example to stay complete: a new
`${VAR:?}` in the compose file without a placeholder in the example fails
this job. Image tags stay exact (never `latest`) per
`rules/devops/security.md`; bumps are deliberate `chore(deploy):` commits.

**Reproduce locally** (with Docker installed):

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example config -q
```

### `commit-protocol` (pull_request only)

**What it checks:** every non-merge commit in the PR's range
(`merge-base(base, head)..head`) has (1) a conventional-commit type prefix
— `feat|fix|refactor|docs|test|chore|perf|ci`, optional `(scope)`, e.g.
`chore(process):` — and (2) `Agent:` and `Task:` trailers in the commit
body, per `rules/common/git-workflow.md`. Offending commit SHAs and subjects
are printed as `::error::` annotations.

**Why:** this repo's git history is meant to be a reconstructable audit
trail (who/what produced each commit, which task it belongs to). This job is
the machine-enforced version of that expectation for PRs, mentioned as a
possible hardening step in `docs/DEVOPS.md` §10.

**Implementation note:** the PR's base/head SHAs are read from
`github.event.pull_request.{base,head}.sha` via `env:`, never interpolated
directly into the `run:` script — they're passed as `BASE_SHA`/`HEAD_SHA`
environment variables and the script reads commit subjects/bodies with
`git show`, never with an inline `${{ }}` expression, per
`rules/devops/github-actions.md`'s injection-safety rule.

**Reproduce locally** (compare against the branch's actual base):

```bash
merge_base=$(git merge-base origin/dev HEAD)   # or origin/master
for sha in $(git rev-list --no-merges "${merge_base}..HEAD"); do
  git show -s --format='%H%n%s%n%n%b%n---' "$sha"
done
```

## `.github/workflows/security.yml`

### `secret-scan`

**What it checks:** `gitleaks detect` over the full git history
(`fetch-depth: 0`, `--log-opts="--all"` so every ref is scanned, not just
the current branch). Runs on `pull_request`, on `push` to `dev`/
`master`, and on a **weekly cron** (Mondays 03:00 UTC) — the cron exists
because gitleaks' detection rules are updated upstream between the version
bumps Dependabot brings in here, so a secret pattern only classified as
sensitive *after* it was committed only surfaces on a re-scan of full
history, not on a quiet week's diff. Owner: `devops`.

The binary is downloaded from the `gitleaks/gitleaks` GitHub Release and
SHA-256-verified before use. No `GITLEAKS_LICENSE` is referenced — this is a
personal repo and the OSS CLI covers full-history scanning without a
license.

**Reproduce locally** (with the `gitleaks` binary for your platform):

```bash
gitleaks detect --source . --log-opts="--all" --redact -v
```

## Repository governance

The checks above are **required checks** — enforced via a branch ruleset,
not left to convention. This section documents the ruleset the Lead applies
via `gh api` (or the GitHub UI); **settings are applied by the Lead/a human,
never by a subagent** — the `devops` agent's `gh` usage is read-only
(diagnosis, `gh run`/`gh pr checks`), and it never mutates repo settings.

The rules are split into **three rulesets**. Two reasons for the split:
(1) GitHub bypass actors bypass an *entire* ruleset — bypass cannot be
scoped to a single rule inside one, so a combined ruleset with an admin
bypass would silently let the bypass skip the CI and secret-scan gates too,
not just the review requirement (security-reviewer finding, MEDIUM);
(2) `dev` is the Lead's integration branch — the git protocol has the Lead
pushing protocol commits (plans, process changes, task merges) directly to
it, so `dev` gets integrity protection but not PR/check gates.

**Ruleset 1 — `branch-integrity` (`dev`, `master`; NO
bypass actors):**
- Block force-pushes (non-fast-forward) and branch deletion — mirrors the
  protocol's forbidden-operations list
  (`rules/common/git-workflow.md` § Forbidden Operations).

**Ruleset 2 — `required-checks` (`master`; NO bypass
actors):**
- Require these status checks to pass (names must match the job `name:`
  values above exactly): `python-quality`, `dependency-audit`,
  `harness-sync`, `lint-shell`, `lint-workflows`, `validate-json`,
  `compose-config`, `secret-scan`, `commit-protocol` (the last enforces the
  git-audit-trail guarantee the whole protocol depends on). `python-quality`
  and `dependency-audit` were added in T003 and `compose-config` in T002;
  the Lead adds each to the ruleset after its PR merges (a check cannot be
  required before it exists on the base branch).
- Require branches to be up to date before merging (strict policy).
- Effect: a **direct push to these branches is rejected** — an un-merged
  commit has no passing checks, so only PRs whose checks are green can land.

**Ruleset 3 — `review-gates` (`master`; bypass: repo
admin):**
- Require a pull request with at least 1 approving review, and require
  review from a Code Owner (pairs with `.github/CODEOWNERS` — see its
  header comment).
- The repo-admin bypass exists for solo-maintainer periods (a PR author
  cannot approve their own PR); remove or narrow it once collaborators
  join. It never weakens Rulesets 1–2 — CI must pass regardless.

None of the above is applied by this change — it is a specification for the
Lead/user to apply once, via `gh api repos/<owner>/<repo>/rulesets` (or
Settings → Rules → Rulesets in the GitHub UI).
