"""Architecture guard: provider SDKs and HTTP clients are built only in rootcause_core.clients."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_ROOTS = ("apps", "packages", "scripts")
EXCLUDED_PARTS = {".venv", "node_modules", "__pycache__", "tests", "build", "dist"}
CLIENTS_DIR = ("rootcause_core", "clients")
HTTPX_ALLOWED_PREFIXES = (("apps", "api"),)

FORBIDDEN_MODULES = {"anthropic", "openai"}
PROVIDER_PACKAGE = re.compile(r"^langchain_[a-z0-9_]+$")
# langchain_* packages that are not model-provider SDKs.
NON_PROVIDER_PACKAGES = {
    "langchain_core",
    "langchain_classic",
    "langchain_text_splitters",
    "langchain_community",
    "langchain_tests",
}
FORBIDDEN_HTTPX_CLASSES = {"AsyncClient", "Client"}


def _is_clients_layer(relative: Path) -> bool:
    parts = relative.parts
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


def _is_forbidden_module(name: str) -> bool:
    top = name.split(".")[0]
    if top in FORBIDDEN_MODULES:
        return True
    return bool(PROVIDER_PACKAGE.fullmatch(top)) and top not in NON_PROVIDER_PACKAGES


def _is_str_constant(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_httpx_client_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_HTTPX_CLASSES:
        return isinstance(func.value, ast.Name) and func.value.id == "httpx"
    return False


def find_violations(tree: ast.AST, *, allow_httpx: bool = False) -> list[str]:
    """Return human-readable violations found in a parsed module."""
    found: list[str] = []
    httpx_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(f"import {a.name}" for a in node.names if _is_forbidden_module(a.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and _is_forbidden_module(module):
                found.append(f"from {module} import ...")
            if module == "httpx":
                httpx_names.update(
                    a.asname or a.name for a in node.names if a.name in FORBIDDEN_HTTPX_CLASSES
                )
            if any(a.name == "init_chat_model" for a in node.names):
                found.append("init_chat_model import")
        elif (isinstance(node, ast.Name) and node.id == "init_chat_model") or (
            isinstance(node, ast.Attribute) and node.attr == "init_chat_model"
        ):
            found.append("init_chat_model")
        elif isinstance(node, ast.Call):
            if _call_name(node) == "create_deep_agent":
                found.extend(
                    "create_deep_agent(model=<str literal>)"
                    for kw in node.keywords
                    if kw.arg == "model" and _is_str_constant(kw.value)
                )
            if not allow_httpx and (
                _is_httpx_client_call(node)
                or (isinstance(node.func, ast.Name) and node.func.id in httpx_names)
            ):
                found.append("httpx client constructed outside clients layer")
        elif isinstance(node, ast.Dict):
            found.extend(
                'subagent dict "model": <str literal>'
                for key, value in zip(node.keys, node.values, strict=True)
                if _is_str_constant(key)
                and isinstance(key, ast.Constant)
                and key.value == "model"
                and _is_str_constant(value)
            )
    return found


def _parse(source: str) -> ast.AST:
    return ast.parse(source)


def _is_httpx_allowed(relative: Path) -> bool:
    return any(relative.parts[: len(prefix)] == prefix for prefix in HTTPX_ALLOWED_PREFIXES)


def test_scan_covers_the_source_tree() -> None:
    names = {path.name for path in _source_files()}

    assert "orchestrator.py" in names
    assert "settings.py" in names


def test_scan_includes_scripts_directory() -> None:
    relatives = {path.relative_to(REPO_ROOT).parts[0] for path in _source_files()}

    assert "scripts" in relatives


def test_clients_layer_exists() -> None:
    assert (REPO_ROOT / "packages/core/src/rootcause_core/clients/__init__.py").is_file()


def test_no_provider_sdk_or_raw_http_client_outside_clients_layer() -> None:
    violations: list[str] = []
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT)
        tree = _parse(path.read_text(encoding="utf-8"))
        violations.extend(
            f"{relative}: {label}"
            for label in find_violations(tree, allow_httpx=_is_httpx_allowed(relative))
        )

    assert violations == [], "Use rootcause_core.clients instead:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "import anthropic",
        "import openai.types",
        "from anthropic import Anthropic",
        "from openai import AsyncOpenAI",
        "from langchain_anthropic import ChatAnthropic",
        "from langchain_openai import AzureChatOpenAI",
        "from langchain_aws import ChatBedrock",
        "from langchain_ollama import ChatOllama",
        "from langchain_google_genai import ChatGoogleGenerativeAI",
        "from langchain_mistralai import ChatMistralAI",
        "import langchain_groq",
        "from langchain.chat_models import init_chat_model",
        "import langchain.chat_models as cm\ncm.init_chat_model('x')",
        "create_deep_agent(model='anthropic:claude')",
        "create_deep_agent(tools=[], model='x')",
        "agent = {'name': 'a', 'model': 'openai:gpt'}",
        "import httpx\nhttpx.AsyncClient()",
        "import httpx\nhttpx.Client(timeout=1)",
        "from httpx import AsyncClient\nAsyncClient()",
    ],
)
def test_guard_detects_violation(source: str) -> None:
    assert find_violations(_parse(source)) != []


@pytest.mark.parametrize(
    "source",
    [
        "from langchain_core.messages import HumanMessage",
        "from langchain_text_splitters import RecursiveCharacterTextSplitter",
        "create_deep_agent(model=chat_model('fast'))",
        "create_deep_agent(model=model)",
        "agent = {'name': 'a', 'model': model}",
        "x = 'ChatAnthropic is mentioned only in a string'  # import anthropic",
        "import httpx\nhttpx.get('http://x')",
    ],
)
def test_guard_allows_legitimate_code(source: str) -> None:
    assert find_violations(_parse(source)) == []


def test_httpx_client_allowed_only_in_api_health_probes() -> None:
    tree = _parse("import httpx\nhttpx.AsyncClient()")

    assert find_violations(tree, allow_httpx=True) == []
    assert _is_httpx_allowed(Path("apps/api/src/rootcause_api/health.py"))
    assert not _is_httpx_allowed(Path("packages/agents/src/rootcause_agents/x.py"))
