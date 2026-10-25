#!/usr/bin/env python3
"""Sync docs/backlog/backlog.yaml to GitHub issues and a GitHub Projects (v2) board.

Idempotent: issue numbers are recorded in docs/backlog/.github-sync.json and
existing issues are matched by exact title before anything is created.

Usage:
    python3 scripts/backlog_to_github.py --repo OWNER/REPO --project RootCause [--dry-run]

Requires the gh CLI authenticated with the `project` scope
(`gh auth refresh -s project,read:project`).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKLOG_PATH = ROOT / "docs" / "backlog" / "backlog.yaml"
STATE_PATH = ROOT / "docs" / "backlog" / ".github-sync.json"

PHASE_NAMES = {
    0: "Phase 0 - Foundations",
    1: "Phase 1 - Anomaly agent",
    2: "Phase 2 - Root-cause agent",
    3: "Phase 3 - Memory",
    4: "Phase 4 - Catalog UI and hardening",
}


@dataclass(frozen=True)
class Options:
    repo: str
    project: str
    dry_run: bool


@dataclass
class SyncState:
    issues: dict[str, int] = field(default_factory=dict)  # backlog id -> issue number
    project_items: dict[str, str] = field(default_factory=dict)  # backlog id -> project item id

    @classmethod
    def load(cls) -> SyncState:
        if STATE_PATH.exists():
            raw = json.loads(STATE_PATH.read_text())
            return cls(issues=raw.get("issues", {}), project_items=raw.get("project_items", {}))
        return cls()

    def save(self) -> None:
        STATE_PATH.write_text(
            json.dumps({"issues": self.issues, "project_items": self.project_items}, indent=2)
            + "\n"
        )


def gh(args: list[str], *, dry_run: bool = False, input_text: str | None = None) -> str:
    cmd = ["gh", *args]
    if dry_run:
        print("DRY RUN:", " ".join(cmd))
        return ""
    result = subprocess.run(cmd, capture_output=True, text=True, input=input_text)
    if result.returncode != 0:
        raise RuntimeError(f"gh failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def gh_json(args: list[str]) -> Any:
    out = gh(args)
    return json.loads(out) if out else None


# ---------------------------------------------------------------- labels / milestones


def ensure_labels(opts: Options, labels: list[dict[str, str]]) -> None:
    for label in labels:
        gh(
            [
                "label",
                "create",
                label["name"],
                "--repo",
                opts.repo,
                "--color",
                label["color"],
                "--description",
                label.get("description", ""),
                "--force",
            ],
            dry_run=opts.dry_run,
        )
    print(f"labels ensured: {len(labels)}")


def ensure_milestones(opts: Options, milestones: list[dict[str, str]]) -> dict[str, int]:
    existing = (
        {}
        if opts.dry_run
        else {
            m["title"]: m["number"]
            for m in gh_json(["api", f"repos/{opts.repo}/milestones?state=all&per_page=100"]) or []
        }
    )
    numbers: dict[str, int] = dict(existing)
    for ms in milestones:
        if ms["name"] in numbers:
            continue
        created = gh(
            [
                "api",
                f"repos/{opts.repo}/milestones",
                "-f",
                f"title={ms['name']}",
                "-f",
                f"description={ms['description']}",
            ],
            dry_run=opts.dry_run,
        )
        if created:
            numbers[ms["name"]] = json.loads(created)["number"]
    print(f"milestones ensured: {len(milestones)}")
    return numbers


# ---------------------------------------------------------------- issues


def existing_issue_numbers_by_title(opts: Options) -> dict[str, int]:
    if opts.dry_run:
        return {}
    issues = (
        gh_json(
            [
                "issue",
                "list",
                "--repo",
                opts.repo,
                "--state",
                "all",
                "--limit",
                "1000",
                "--json",
                "number,title",
            ]
        )
        or []
    )
    return {i["title"]: i["number"] for i in issues}


def issue_body_for_task(
    task: dict[str, Any], epics_by_id: dict[str, dict[str, Any]], state: SyncState
) -> str:
    epic = epics_by_id[task["epic"]]
    epic_ref = f"#{state.issues[epic['id']]}" if epic["id"] in state.issues else epic["title"]
    deps = [f"#{state.issues[d]}" if d in state.issues else d for d in task.get("depends_on", [])]
    header = [
        f"**Backlog id:** `{task['id']}`  ",
        f"**Epic:** {epic_ref}  ",
        f"**Phase:** {PHASE_NAMES[task['phase']]}  ",
        f"**Size:** {task['size']}  ",
        f"**Depends on:** {', '.join(deps) if deps else 'none'}",
        "",
    ]
    return (
        "\n".join(header)
        + task["body"].rstrip()
        + "\n\n_Source: docs/backlog/backlog.yaml (PRD: docs/PRD.md)_\n"
    )


def issue_body_for_epic(epic: dict[str, Any], tasks: list[dict[str, Any]], state: SyncState) -> str:
    lines = [
        f"**Backlog id:** `{epic['id']}`  ",
        f"**Phase:** {PHASE_NAMES[epic['phase']]}",
        "",
        epic["body"].rstrip(),
        "",
        "## Tasks",
        "",
    ]
    for t in tasks:
        if t["epic"] != epic["id"]:
            continue
        ref = f"#{state.issues[t['id']]}" if t["id"] in state.issues else f"`{t['id']}`"
        lines.append(f"- [ ] {ref} {t['title']} (phase {t['phase']}, {t['size']})")
    return "\n".join(lines) + "\n"


def create_or_get_issue(
    opts: Options,
    state: SyncState,
    by_title: dict[str, int],
    item: dict[str, Any],
    body: str,
    labels: list[str],
    milestone: str,
) -> int:
    if item["id"] in state.issues:
        return state.issues[item["id"]]
    if item["title"] in by_title:
        state.issues[item["id"]] = by_title[item["title"]]
        return by_title[item["title"]]
    args = [
        "issue",
        "create",
        "--repo",
        opts.repo,
        "--title",
        item["title"],
        "--body-file",
        "-",
        "--milestone",
        milestone,
    ]
    for label in labels:
        args += ["--label", label]
    url = gh(args, dry_run=opts.dry_run, input_text=body)
    if opts.dry_run:
        return -1
    number = int(url.rstrip("/").split("/")[-1])
    state.issues[item["id"]] = number
    state.save()
    print(f"created {item['id']} -> #{number} {item['title']}")
    return number


def update_issue_body(opts: Options, number: int, body: str) -> None:
    gh(
        ["issue", "edit", str(number), "--repo", opts.repo, "--body-file", "-"],
        dry_run=opts.dry_run,
        input_text=body,
    )


# ---------------------------------------------------------------- project v2


def owner_of(repo: str) -> str:
    return repo.split("/")[0]


def ensure_project(opts: Options) -> dict[str, Any]:
    owner = owner_of(opts.repo)
    projects = (
        gh_json(["project", "list", "--owner", owner, "--format", "json", "--limit", "100"]) or {}
    )
    for p in projects.get("projects", []):
        if p["title"] == opts.project:
            return p
    created = gh_json(
        ["project", "create", "--owner", owner, "--title", opts.project, "--format", "json"]
    )
    print(f"created project {opts.project} (#{created['number']})")
    return created


def ensure_single_select_field(
    opts: Options, project_number: int, name: str, options: list[str]
) -> dict[str, Any]:
    owner = owner_of(opts.repo)
    fields = (
        gh_json(
            [
                "project",
                "field-list",
                str(project_number),
                "--owner",
                owner,
                "--format",
                "json",
                "--limit",
                "100",
            ]
        )
        or {}
    )
    for f in fields.get("fields", []):
        if f["name"] == name:
            return f
    gh(
        [
            "project",
            "field-create",
            str(project_number),
            "--owner",
            owner,
            "--name",
            name,
            "--data-type",
            "SINGLE_SELECT",
            "--single-select-options",
            ",".join(options),
        ]
    )
    fields = (
        gh_json(
            [
                "project",
                "field-list",
                str(project_number),
                "--owner",
                owner,
                "--format",
                "json",
                "--limit",
                "100",
            ]
        )
        or {}
    )
    return next(f for f in fields["fields"] if f["name"] == name)


def add_issue_to_project(
    opts: Options, state: SyncState, project: dict[str, Any], backlog_id: str, issue_number: int
) -> str:
    if backlog_id in state.project_items:
        return state.project_items[backlog_id]
    owner = owner_of(opts.repo)
    url = f"https://github.com/{opts.repo}/issues/{issue_number}"
    item = gh_json(
        [
            "project",
            "item-add",
            str(project["number"]),
            "--owner",
            owner,
            "--url",
            url,
            "--format",
            "json",
        ]
    )
    state.project_items[backlog_id] = item["id"]
    state.save()
    return item["id"]


def set_single_select(
    opts: Options, project: dict[str, Any], item_id: str, fld: dict[str, Any], value: str
) -> None:
    option = next((o for o in fld["options"] if o["name"] == value), None)
    if option is None:
        print(f"warning: option {value!r} not found on field {fld['name']}")
        return
    gh(
        [
            "project",
            "item-edit",
            "--project-id",
            project["id"],
            "--id",
            item_id,
            "--field-id",
            fld["id"],
            "--single-select-option-id",
            option["id"],
        ]
    )


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", required=True, help="OWNER/REPO that will hold the issues")
    parser.add_argument("--project", default="RootCause", help="GitHub Projects (v2) title")
    parser.add_argument("--dry-run", action="store_true")
    ns = parser.parse_args()
    opts = Options(repo=ns.repo, project=ns.project, dry_run=ns.dry_run)

    backlog = yaml.safe_load(BACKLOG_PATH.read_text())
    epics: list[dict[str, Any]] = backlog["epics"]
    tasks: list[dict[str, Any]] = backlog["tasks"]
    epics_by_id = {e["id"]: e for e in epics}
    state = SyncState.load()

    ensure_labels(opts, backlog["labels"])
    ensure_milestones(opts, backlog["milestones"])
    by_title = existing_issue_numbers_by_title(opts)

    # Pass 1: epics, then tasks (bodies may still reference backlog ids).
    for epic in epics:
        create_or_get_issue(
            opts,
            state,
            by_title,
            epic,
            issue_body_for_epic(epic, tasks, state),
            ["epic", f"phase:{epic['phase']}"],
            PHASE_NAMES[epic["phase"]],
        )
    for task in tasks:
        labels = [*task["labels"], f"phase:{task['phase']}", f"size:{task['size']}"]
        create_or_get_issue(
            opts,
            state,
            by_title,
            task,
            issue_body_for_task(task, epics_by_id, state),
            labels,
            PHASE_NAMES[task["phase"]],
        )

    # Pass 2: rewrite bodies now that every id maps to an issue number.
    if not opts.dry_run:
        for epic in epics:
            update_issue_body(
                opts, state.issues[epic["id"]], issue_body_for_epic(epic, tasks, state)
            )
        for task in tasks:
            update_issue_body(
                opts, state.issues[task["id"]], issue_body_for_task(task, epics_by_id, state)
            )
        print("issue bodies cross-linked")

    # Pass 3: project board with Phase and Size fields.
    if opts.dry_run:
        print("dry run complete; project sync skipped")
        return 0
    project = ensure_project(opts)
    phase_field = ensure_single_select_field(
        opts, project["number"], "Phase", list(PHASE_NAMES.values())
    )
    size_field = ensure_single_select_field(opts, project["number"], "Size", ["S", "M", "L", "XL"])
    kind_field = ensure_single_select_field(opts, project["number"], "Kind", ["Epic", "Task"])
    status_field = ensure_single_select_field(
        opts, project["number"], "Status", ["Backlog", "Ready", "In progress", "In review", "Done"]
    )
    for epic in epics:
        item_id = add_issue_to_project(opts, state, project, epic["id"], state.issues[epic["id"]])
        set_single_select(opts, project, item_id, phase_field, PHASE_NAMES[epic["phase"]])
        set_single_select(opts, project, item_id, kind_field, "Epic")
        set_single_select(opts, project, item_id, status_field, "Backlog")
    for task in tasks:
        item_id = add_issue_to_project(opts, state, project, task["id"], state.issues[task["id"]])
        set_single_select(opts, project, item_id, phase_field, PHASE_NAMES[task["phase"]])
        set_single_select(opts, project, item_id, size_field, task["size"])
        set_single_select(opts, project, item_id, kind_field, "Task")
        set_single_select(opts, project, item_id, status_field, "Backlog")
    print(f"project synced: {project['url']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
