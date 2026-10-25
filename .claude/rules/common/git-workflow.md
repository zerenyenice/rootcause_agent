# Git Workflow

> **Process tiers:** CLAUDE.md "Process tiers" (Light / Standard / Sensitive)
> decide how much of this file applies to a task. Where this file is
> stricter than the task's tier, the tier wins. Non-negotiables in every
> tier: coverage >= 80% in CI, trailers, no secrets, no test weakening.
>
> **Batched PRs:** a branch may carry several related tasks (CLAUDE.md
> "Batching tasks into one PR"); each commit's `Task:` trailer names its own
> task, and the PR closes every task's issue.

Git is this system's audit trail. Anyone must be able to reconstruct from
`git log` alone: what was asked, what was planned, which agent produced what,
in what order, what the reviewers said, and what the user approved. History is
evidence, not convention.

## Who May Run Git (single committer: the Lead)

Only the **Lead** (main agent) runs state-changing git commands: `add`,
`commit`, `branch`, `checkout -b`, `merge`, `push`, `stash`, `tag`. Subagents
are **git-read-only**: they may run `git status`, `git diff`, `git log`,
`git show` to inspect and verify, but never write to git.

Rationale: parallel subagents share one working tree; a single writer keeps
history linear and attributable.

## Commit Message Format

```
<type>: <description>

<optional body>

Agent: <agent-name>
Task: <task-slug>
Refs: <ticket-key>
```

Types: feat, fix, refactor, docs, test, chore, perf, ci

### Trailers (mandatory attribution and linkage)

Every commit carries:

- `Agent:` — one line per contributing agent (`tester`, `backend-coder`,
  `frontend-coder`, `lead` for the Lead's own planning/doc/config commits).
- `Task:` — kebab-case task slug (same slug as the branch name).
- `Refs:` — optional issue/ticket key (Jira etc.) when the task came from one;
  gives ticket↔git traceability both directions.

Example:

```
feat: add user registration endpoint

Agent: backend-coder
Task: user-registration
Refs: PROJ-123
```

Merge commits additionally carry `Reviewed-by:` (which reviewer agents
approved) and `Coverage:` (the tester's measured number presented at Gate 2).
Under the PR flow these always appear in the PR description; when the Lead
merges via `gh pr merge --merge` they are also written into the merge commit
body (a UI merge keeps them in the PR only — still durable and linked):

```
Task: user-registration
Reviewed-by: backend-reviewer
Reviewed-by: security-reviewer
Coverage: 87%
```

Never skip trailers — not even on trivial-tier tasks.

Note: To disable co-author attribution on commits, set `"includeCoAuthoredBy": false` in `~/.claude/settings.json` (Claude Code appends `Co-Authored-By` by default; ECC does not ship this setting).

## Branching

**Branch-per-task, based on the current branch.** Every non-trivial task gets
a branch `<type>/<task-slug>` (`feat/user-registration`,
`fix/empty-poller-crash`, `refactor/extract-http-client`, `docs/api-runbook`),
created when the user approves the plan (Gate 1).

- **The base branch is whatever branch is checked out when the task is
  accepted** — typically `dev`, but a team's `develop` or a long-lived
  feature branch works the same. The task branch forks from it, the task
  **completes on that branch locally**, and — only when the user, having
  checked the result, says so — the branch is pushed and a **PR targeting
  that same base branch** is opened. Do not hardcode a branch name as the
  merge target, and never merge the task branch into the base locally.
- **Dirty-tree rule:** if the working tree has uncommitted changes when a task
  is accepted, the Lead must NOT carry them onto the task branch —
  `git stash -u` them (restoring after returning to the base branch) or ask
  the user to commit first. The task branch starts from a clean tree.
- Nothing is committed directly to the base branch except: the bootstrap
  commit, PROJECT.md updates (B1 below), and template/process config changes
  the user explicitly directs (B11 below).

**Parallel tasks.** Multiple task branches may exist, but the shared working
tree holds ONE checked-out branch — so the Lead serializes actual file work:
finish (or stash + commit clean) one task's working-tree changes before
switching branches. Parallel *agents* on the SAME task are fine (their work
lands as separate commits on the same branch).

**History is append-only; merges preserve the story.**

- Never `commit --amend`, `rebase`, or squash commits the protocol depends on
  (RED commits, plan commits, review-fix commits) — rewriting history destroys
  the evidence.
- Task branches land in the base branch via **PR merge commits** (GitHub's
  "Create a merge commit" method — always non-fast-forward), so each task's
  internal sequence stays visible from the base branch. **Squash-merge and
  rebase-merge are forbidden for protocol branches** — they are history
  rewriting by another name and destroy the RED→GREEN evidence.
- To update a stale task branch, **merge the base branch into it** — never
  rebase the task branch onto the base.

**Base-branch drift and post-merge verification.** If the base branch gained
new commits while the task branch was in flight (the user or another merged
task moved it), the Lead first merges the base branch *into* the task branch
(never rebase), resolves conflicts there, re-runs the full test suite on the
task branch, and only then opens or refreshes the PR. After the PR merges,
CI re-runs the suite on the base branch via the push trigger; the Lead pulls
the merged base and confirms that run is green before declaring the task
done — two individually green branches can still merge into a broken state.

**Branch hygiene.** The repo setting "Automatically delete head branches"
(`delete_branch_on_merge`, enabled at bootstrap) removes the remote task
branch when its PR merges — the protected base branches are never affected
(deletion-blocked by ruleset). The Lead then pulls the base branch and
deletes the local task branch (`git branch -d <branch>` — `-d`, never
`-D`). The base branch plus live task branches is the steady state.

## The Gates

- **Commits on a task branch are protocol-internal** — the Lead makes them
  automatically at the choreography points below, without asking. A task
  branch is cheap to discard, so this is safe.
- **GATE 2 is the hand-off gate, in two user-controlled steps:**
  1. **Completion report** — the task ENDS on its local task branch: nothing
     pushed, no PR, no local merge. The Lead presents the branch name, the
     commit list, the diff summary, and the tester's measured coverage, then
     stops. The user inspects the branch at their own pace (checkout, run,
     read).
  2. **PR on instruction** — only when the user says to open the PR does the
     Lead push the branch (`-u`) and create the PR targeting the base branch
     (see Pull Request Workflow below). Required CI checks run on the PR; the
     user merges it on GitHub — or explicitly tells the Lead to merge — using
     a **merge commit** (never squash/rebase).

  **Standing authorization (this project):** the user has authorized the
  Lead to skip step 1's wait and to push, open the PR and merge it with a
  merge commit once every CI check is `pass` and the review approved. The
  completion report goes in the PR body instead.

  Coverage <80% blocks step 2 unless the user explicitly overrides with a
  stated reason (record the override in the PR description and merge commit
  body).

**Trivial-tier exception.** For trivial tasks (per the orch-pipeline size
classifier), the Lead MAY commit to a short-lived task branch and merge
immediately after Gate 2, or ask the user whether committing directly to the
base branch is acceptable — but trailers and the coverage gate always apply.
Never skip trailers.

## Lifecycle Choreographies

- **B0. Project bootstrap** — template copy → `git init` → `.gitignore` →
  initial `chore: bootstrap from team development template` commit (see
  Bootstrap below).
- **B1. Requirements: PROJECT.md changes** — `PROJECT.md` is project-team-
  maintained; agents don't edit it. When the team updates it (directly or by
  explicitly instructing an agent), that change is committed to the base
  branch as its own commit: `docs(project): <what changed>` with `Agent: lead`
  (if agent-typed) — never mixed with code. `git log -p -- PROJECT.md` answers
  "what did the customer change and when." When PROJECT.md changes, the Lead
  checks whether merged features are affected and proposes
  `orch-change-feature` tasks for the deltas.
- **B2. Planning artifacts** — when Gate 1 produces a plan document
  (task_list, PRD, architecture notes, ADRs), the Lead commits the approved
  artifact as the FIRST commit on the new task branch:
  `docs(plan): <task> implementation plan` (`Agent: lead`). The approved plan
  is frozen in history — scope drift during implementation becomes visible as
  a diff against it. ADRs produced mid-task are committed alongside the change
  they explain (`docs(adr):`).
- **B3. Feature implementation (TDD choreography)** — on the task branch:

  | Step | Commit | Type | Agent trailer | Rule |
  |---|---|---|---|---|
  | 1 | Approved plan (B2) | `docs(plan):` | `lead` | first commit on branch |
  | 2 | RED: failing tests | `test: <desc> (RED)` | `tester` | MUST precede any implementation |
  | 3 | GREEN: implementation | `feat:` | coder agent | diff MUST NOT touch test files |
  | 4 | Refactor (optional) | `refactor:` | coder agent | tests stay green |
  | 5 | Review fixes (if findings) | `fix: address review findings` | coder agent | see B7 |
  | 6 | Task ends on the branch | — | — | GATE 2 step 1: completion report, wait for user |
  | 7 | Push + PR on user instruction | PR merge commit | — | GATE 2 step 2: `Reviewed-by:`/`Coverage:` in PR body (and merge commit body when the Lead merges via `gh pr merge --merge`) |

- **B4. Bug fix** — same as B3 with: the RED commit is the *regression test*
  reproducing the bug (`test: reproduce <bug> (RED)`); implementation commit
  is `fix:`; `Refs:` trailer links the ticket. The regression test's commit is
  the permanent proof the bug existed and is covered.
- **B5. Change existing feature** — the tests-updated-to-new-spec commit comes
  first (`test: update <feature> tests to new spec`), then the implementation
  change. Same ordering guarantee as RED-first, adapted to changed behavior.
- **B6. Refactor (behavior-preserving)** — before touching code, the Lead
  records the green baseline: run the suite, then note the baseline SHA in the
  plan commit (B2). Characterization tests (if coverage was thin) are
  committed first (`test: characterize <area>`). Each restructure step is its
  own `refactor:` commit with tests green — small commits are the safety net;
  a broken step is reverted by dropping one commit, not by archaeology.
- **B7. Review** — reviewers are read-only; their findings return to the Lead.
  Git touchpoints: (1) fixes for CRITICAL/HIGH findings are separate
  `fix: address review findings` commits — never folded into the original
  implementation commit — so finding→fix is traceable; (2) the final verdict
  is recorded as `Reviewed-by:` trailers on the merge commit; (3) reviewers
  verify protocol order via `git log` (RED precedes GREEN; implementation
  commits' `--stat` shows no test files).
- **B8. Security** — security-reviewer runs on trigger (auth, input handling,
  DB, crypto, secrets…); its approval is recorded as
  `Reviewed-by: security-reviewer` on the merge commit. Security fixes commit
  as `fix(security): <desc>`. Secret-in-history incidents follow the incident
  protocol below. The staged-diff secret scan runs before every commit.
- **B9. MVP / vertical slices** — each slice is its own task branch and its
  own Gate 2 PR (`feat/mvp-slice-1-<slug>` …). The spec ingestion may
  produce a `docs(plan): mvp slice map` commit on the base branch
  (user-approved) so the slice plan itself is versioned. One slice merged =
  one working increment on the base branch.
- **B10. Documentation & knowledge capture** — team/project knowledge that the
  workflow says belongs in the project's docs gets committed as `docs:`
  commits, in the task branch when produced by a task, directly on the base
  branch (user-approved) when standalone. Personal/session notes go to auto
  memory, NOT to git.
- **B11. Process/template changes (meta)** — edits to `CLAUDE.md`,
  `.github/copilot-instructions.md`, `.claude/**` (agents, rules, skills,
  commands) commit as `chore(process): <desc>`. **Sync invariant:** in
  projects that carry generated Copilot surfaces (harness target
  vscode/pycharm/all), `CLAUDE.md` and `.github/copilot-instructions.md` must
  change in the SAME commit — a commit touching one without the other is a
  protocol violation the Lead must refuse to make. (The Claude-only base
  ships no Copilot surfaces; there the invariant is discharged by
  regenerating with `--target claude` before committing.)
- **B12. Releases / milestones (optional, lightweight)** — when the user
  declares a milestone, the Lead may create an annotated tag
  (`git tag -a v0.1.0 -m "<summary>"`). No mandated versioning scheme —
  team-specific; the protocol only says tags are Lead-created and annotated.

## Forbidden Operations

Forbidden for agents in all cases: `push --force` (any variant),
`reset --hard` / `checkout` / `restore` that discards uncommitted work (stash
first — `git stash -u` for untracked — or get explicit user confirmation),
deleting branches with unmerged work, editing the base branch's or `main`'s
history.

## Secret Safety

- `.env` and credential files are gitignored from day one (bootstrap
  `.gitignore`); only `.env.example` with placeholder values is committed.
- Before EVERY commit the Lead scans the staged diff for secrets
  (`git diff --staged`) — this extends the pre-commit security checklist in
  `rules/common/security.md`.
- **Incident protocol** if a secret is ever found already committed:
  1. Rotate the secret immediately — treat it as leaked no matter how fast the
     removal.
  2. Remove it from the working tree and commit.
  3. History purging (`git filter-repo` etc.) only with explicit user
     direction, since it rewrites history and requires coordination if the
     repo was pushed.

## Bootstrap (repo-existence check)

If the working directory is not a git repository, the Lead stops before any
Implement phase and runs the bootstrap (with user confirmation): `git init` →
create `.gitignore` → `git add -A` →
`chore: bootstrap from team development template`. The integrity protocol
cannot work without git; do not proceed without it.

## Traceability Queries

```bash
git log --grep "Agent: tester"       # everything the tester produced
git log --grep "Task: <slug>"        # a task's full commit story
git log --grep "Refs: PROJ-123"      # all commits for a ticket
git log --grep "Reviewed-by:"        # every reviewed merge
git log -p -- PROJECT.md             # requirement evolution over time
```

## Pull Request Workflow

The PR is Gate 2 step 2 — created only on the user's instruction, after they
have checked the completed task branch. When creating PRs:
1. Analyze full commit history (not just latest commit)
2. Use `git diff [base-branch]...HEAD` to see all changes
3. Draft comprehensive PR summary (include `Reviewed-by:` verdicts and the
   measured coverage — the PR body is the durable Gate 2 record)
4. Include test plan with TODOs
5. Push the task branch with `-u` flag; the PR targets the task's base branch
6. The user merges on GitHub (or explicitly instructs the Lead to
   `gh pr merge --merge`) — merge commit only, never squash/rebase — then
   branch hygiene applies (see Branching above)

> For the full development process (planning, TDD, code review) before git operations,
> see [development-workflow.md](development-workflow.md).
