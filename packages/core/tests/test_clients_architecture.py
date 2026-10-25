"""T008 architecture guard: provider SDK clients are built only in rootcause_core.clients."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_ROOTS = ("apps", "packages")
EXCLUDED_PARTS = {".venv", "node_modules", "__pycache__", "tests", "build", "dist"}
CLIENTS_DIR = ("rootcause_core", "clients")

FORBIDDEN = {
    "ChatAnthropic": re.compile(r"\bChatAnthropic\b"),
    "AzureChatOpenAI": re.compile(r"\bAzureChatOpenAI\b"),
    "ChatOpenAI": re.compile(r"\bChatOpenAI\b"),
    "AzureOpenAIEmbeddings": re.compile(r"\bAzureOpenAIEmbeddings\b"),
    "OpenAIEmbeddings": re.compile(r"\bOpenAIEmbeddings\b"),
    "from anthropic import": re.compile(r"^\s*from\s+anthropic(\.\w+)*\s+import\b", re.M),
    "import anthropic": re.compile(r"^\s*import\s+anthropic\b", re.M),
    "from openai import": re.compile(r"^\s*from\s+openai(\.\w+)*\s+import\b", re.M),
    "import openai": re.compile(r"^\s*import\s+openai\b", re.M),
    "from langfuse import Langfuse": re.compile(
        r"^\s*from\s+langfuse(\.\w+)*\s+import\b[^\n]*\bLangfuse\b", re.M
    ),
    "init_chat_model": re.compile(r"\binit_chat_model\b"),
}


def _is_clients_layer(path: Path) -> bool:
    parts = path.parts
    return any(parts[i : i + 2] == CLIENTS_DIR for i in range(len(parts) - 1))


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            relative = path.relative_to(REPO_ROOT)
            if EXCLUDED_PARTS & set(relative.parts) or path.name.startswith("test_"):
                continue
            if not _is_clients_layer(relative):
                files.append(path)
    return files


def test_scan_covers_the_source_tree() -> None:
    names = {path.name for path in _source_files()}

    assert "orchestrator.py" in names
    assert "settings.py" in names


def test_clients_layer_exists() -> None:
    assert (REPO_ROOT / "packages/core/src/rootcause_core/clients/__init__.py").is_file()


def test_no_direct_provider_client_use_outside_clients_layer() -> None:
    violations = [
        f"{path.relative_to(REPO_ROOT)}: {label}"
        for path in _source_files()
        for label, pattern in FORBIDDEN.items()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert violations == [], "Use rootcause_core.clients instead:\n" + "\n".join(violations)
