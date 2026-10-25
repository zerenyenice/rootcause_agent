# Development Workflow

> **Process tiers:** CLAUDE.md "Process tiers" (Light / Standard / Sensitive)
> decide how much of this file applies to a task. Where this file is
> stricter than the task's tier, the tier wins. Non-negotiables in every
> tier: coverage >= 80% in CI, trailers, no secrets, no test weakening.

> This file extends [common/git-workflow.md](git-workflow.md) with the full feature development process that happens before git operations.

The Feature Implementation Workflow describes the development pipeline: research, planning, TDD, code review, and then committing to git.

## Feature Implementation Workflow

0. **Project Alignment Check** _(mandatory, before anything else — not skippable)_
   - Check the request against `/PROJECT.md` — the project team's filled-in
     customer/business context (scope §4, security/compliance §7, AI/agent
     acceptance criteria §8).
   - If the task is out of scope, conflicts with a documented constraint, or the
     relevant section is still `[FILL FREELY]`, stop and raise it with the user
     before doing any research or planning.
   - `/PROJECT.md` is project-team-maintained — read it, don't edit it, unless a
     human on the project team explicitly instructs otherwise.

1. **Research & Reuse** _(when adding a new external dependency or a new subsystem; skip for work inside existing modules)_
   - **GitHub code search first:** Run `gh search repos` and `gh search code` to find existing implementations, templates, and patterns before writing anything new.
   - **Library docs second:** Use Context7 or primary vendor docs to confirm API behavior, package usage, and version-specific details before implementing.
   - **Exa only when the first two are insufficient:** Use Exa for broader web research or discovery after GitHub search and primary docs.
   - **Check package registries:** Search npm, PyPI, crates.io, and other registries before writing utility code. Prefer battle-tested libraries over hand-rolled solutions.
   - **Search for adaptable implementations:** Look for open-source projects that solve 80%+ of the problem and can be forked, ported, or wrapped.
   - Prefer adopting or porting a proven approach over writing net-new code when it meets the requirement.

2. **Plan First**
   - The Lead plans directly — there is no dedicated planning/architecture subagent
   - Write the plan in the PR description; separate planning docs only for multi-PR work
   - Identify dependencies and risks
   - Break down into phases
   - After the user approves the plan (Gate 1): create the task branch
     `<type>/<task-slug>` off the currently checked-out base branch (stash any
     dirty tree first) and commit the approved plan as its first commit
     (`docs(plan):`, `Agent: lead`)

3. **TDD Approach**
   - Use **tester** agent to write the tests
   - Write tests first (RED) — the Lead commits them on the task branch
     (`test: <desc> (RED)`, `Agent: tester`) before any implementation
   - Dispatch to **backend-coder** / **frontend-coder** to implement to pass tests (GREEN) — implementation commits must not touch test files
   - Refactor (IMPROVE)
   - Verify 80%+ coverage
   - Task-branch commits are protocol-internal: the Lead makes them at these
     choreography points automatically, without asking

4. **Code Review**
   - Use **backend-reviewer** / **frontend-reviewer** (whichever applies) immediately after writing code
   - Address CRITICAL and HIGH issues — as separate `fix: address review findings` commits
   - Fix MEDIUM issues when possible

5. **Hand-off & PR (Gate 2, two user-controlled steps)**
   - **Step 1 — completion report:** the task ends on its local task branch —
     nothing pushed, no PR, no local merge. Present the branch name, commit
     list, diff summary, and the tester's measured coverage; below 80% blocks
     step 2 unless the user explicitly overrides with a stated reason. The
     user inspects the branch at their own pace.
   - **Step 2 — PR on instruction:** when the user says to open the PR, push
     the branch (`-u`) and create a PR targeting the base branch with a
     comprehensive body (`Reviewed-by:` verdicts + coverage). CI checks run on
     the PR; the user merges on GitHub (or explicitly instructs the Lead to
     `gh pr merge --merge`) — merge commit only, never squash/rebase
   - After the merge: pull the base branch, confirm the post-merge CI run is
     green, delete remote + local task branches
   - Detailed commit messages, conventional commits format, `Agent:`/`Task:` trailers
   - See [git-workflow.md](git-workflow.md) for the full git protocol and PR process

6. **Pre-Review Checks**
   - Verify all automated checks (CI/CD) are passing
   - Resolve any merge conflicts (by merging the base branch *into* the task branch — never rebase)
   - Ensure branch is up to date with target branch
   - Only request review after these checks pass
