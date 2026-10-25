"""Packaged root-cause skill (issue #56): DeepAgents SKILL.md format and content."""

import re
from importlib import resources
from pathlib import Path

import pytest

from rootcause.agents.skills import skill_paths

SKILL_REL = "skills/root-cause/SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return (resources.files("rootcause.agents") / SKILL_REL).read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter"
    pairs = (ln.split(":", 1) for ln in match.group(1).splitlines() if ":" in ln)
    return {k.strip(): v.strip() for k, v in pairs}


def test_skill_file_is_reachable_as_package_resource() -> None:
    assert (resources.files("rootcause.agents") / SKILL_REL).is_file()


def test_frontmatter_has_name_and_description(skill_text: str) -> None:
    meta = _frontmatter(skill_text)
    assert meta["name"] == "root-cause"
    assert meta["description"].strip()


@pytest.mark.parametrize(
    "phrase",
    ["define the problem", "why", "root cause", "validate", "countermeasure"],
)
def test_five_phases_are_present(skill_text: str, phrase: str) -> None:
    lowered = skill_text.lower()
    assert phrase in lowered
    for n in range(1, 6):
        assert f"phase {n}" in lowered


def test_query_backed_evidence_rule(skill_text: str) -> None:
    lowered = skill_text.lower()
    assert "every why" in lowered
    assert "executed query" in lowered


def test_no_individuals_rule(skill_text: str) -> None:
    lowered = skill_text.lower()
    assert "individual" in lowered
    assert "blame" in lowered or "never name" in lowered


def test_branching_rule_at_25_percent(skill_text: str) -> None:
    assert "25%" in skill_text
    assert "branch" in skill_text.lower()


def test_depth_3_to_7(skill_text: str) -> None:
    assert re.search(r"\b3\b\s*(?:-|to|–)\s*\b7\b", skill_text)


def test_mit_attribution_to_upstream(skill_text: str) -> None:
    assert "MIT" in skill_text
    assert "awesome-skills/5-whys-skill" in skill_text


def test_skill_paths_contains_root_cause_dir() -> None:
    paths = skill_paths()
    assert isinstance(paths, tuple)
    assert all(isinstance(p, Path) and (p / "SKILL.md").is_file() for p in paths)
    assert any(p.name == "root-cause" for p in paths)


def test_skill_paths_are_unique_directories() -> None:
    paths = skill_paths()
    assert len(set(paths)) == len(paths)
    assert all(p.is_dir() for p in paths)
