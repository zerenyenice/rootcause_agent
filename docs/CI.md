# CI/CD

This repo ships no application (no `package.json`/`pyproject.toml`, no
tests) — it is the agent-harness template itself. CI protects the template's
own integrity: generated surfaces must match their sources, tracked
shell/workflow/JSON files must lint and parse clean, no secret ever lands in
history, and every commit carries this project's git-protocol trailers.

Owner agent: `devops`. Governing rules: `.claude/rules/devops/ci-cd.md`,
`.claude/rules/devops/github-actions.md`, `.claude/rules/devops/security.md`.

## `.github/workflows/ci.yml`

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
  values above exactly): `harness-sync`, `lint-shell`, `lint-workflows`,
  `validate-json`, `secret-scan`, `commit-protocol` (the last enforces the
  git-audit-trail guarantee the whole protocol depends on).
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
