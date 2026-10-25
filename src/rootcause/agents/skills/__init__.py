"""Agent skills shipped as package data (DeepAgents SKILL.md directories)."""

import re
from pathlib import Path

SKILL_FILE = "SKILL.md"
_SKILLS_DIR = Path(__file__).resolve().parent
# YAML frontmatter delimited by ``---`` lines at the very start of a SKILL.md.
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", flags=re.DOTALL)


def skill_paths() -> tuple[Path, ...]:
    """Return every packaged skill directory (one holding a SKILL.md), sorted by name."""
    return tuple(
        sorted(
            child
            for child in _SKILLS_DIR.iterdir()
            if child.is_dir() and (child / SKILL_FILE).is_file()
        )
    )


def load_skill_body(name: str) -> str:
    """Return the SKILL.md text of the packaged skill ``name`` without its frontmatter.

    Raises:
        FileNotFoundError: no packaged skill has that name.
    """
    skill = next((path for path in skill_paths() if path.name == name), None)
    if skill is None:
        raise FileNotFoundError(f"no packaged skill named {name!r}")
    text = (skill / SKILL_FILE).read_text(encoding="utf-8")
    return _FRONTMATTER.sub("", text, count=1).strip()
