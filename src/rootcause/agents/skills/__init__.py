"""Agent skills shipped as package data (DeepAgents SKILL.md directories)."""

import re
from functools import cache
from pathlib import Path

SKILL_FILE = "SKILL.md"
REFERENCES_DIR = "references"
_SKILLS_DIR = Path(__file__).resolve().parent
# YAML frontmatter delimited by ``---`` lines (LF or CRLF) at the very start of a SKILL.md.
_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", flags=re.DOTALL)


@cache
def skill_paths() -> tuple[Path, ...]:
    """Return every packaged skill directory (one holding a SKILL.md), sorted by name."""
    return tuple(
        sorted(
            child
            for child in _SKILLS_DIR.iterdir()
            if child.is_dir() and (child / SKILL_FILE).is_file()
        )
    )


@cache
def load_skill_body(name: str) -> str:
    """Return the SKILL.md text of the packaged skill ``name`` as a system prompt.

    The frontmatter is dropped and every ``references/*.md`` file is appended, so
    an agent that has the body as its prompt (and no file access to the skill
    directory) still gets the references the body points to.

    Packaged files do not change at runtime, so the result is cached per process
    (the graph factory runs inside the server event loop, where file I/O is blocked).

    Raises:
        FileNotFoundError: no packaged skill has that name.
    """
    skill = next((path for path in skill_paths() if path.name == name), None)
    if skill is None:
        raise FileNotFoundError(f"no packaged skill named {name!r}")
    text = (skill / SKILL_FILE).read_text(encoding="utf-8")
    body = _FRONTMATTER.sub("", text, count=1).strip()
    return "\n\n".join([body, *_references(skill)])


def _references(skill: Path) -> list[str]:
    references = skill / REFERENCES_DIR
    if not references.is_dir():
        return []
    return [
        f"## Reference: {REFERENCES_DIR}/{path.name}\n\n{path.read_text(encoding='utf-8').strip()}"
        for path in sorted(references.glob("*.md"))
    ]
