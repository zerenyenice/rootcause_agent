"""Agent skills shipped as package data (DeepAgents SKILL.md directories)."""

from pathlib import Path

SKILL_FILE = "SKILL.md"
_SKILLS_DIR = Path(__file__).resolve().parent


def skill_paths() -> tuple[Path, ...]:
    """Return every packaged skill directory (one holding a SKILL.md), sorted by name."""
    return tuple(
        sorted(
            child
            for child in _SKILLS_DIR.iterdir()
            if child.is_dir() and (child / SKILL_FILE).is_file()
        )
    )
