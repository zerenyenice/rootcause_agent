"""Architecture guard: provider SDKs and HTTP clients are built only in rootcause.clients."""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src", "scripts")
EXCLUDED_PARTS = {".venv", "node_modules", "__pycache__", "tests", "build", "dist"}
CLIENTS_DIR = ("rootcause", "clients")
# The only non-clients file that may construct an httpx client (readiness probes).
HTTPX_ALLOWED_FILES = (("src", "rootcause", "api", "routes", "health.py"),)
LANGFUSE_MODULE = ("src", "rootcause", "clients", "langfuse.py")

FORBIDDEN_MODULES = {"anthropic", "openai", "langfuse"}
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
DYNAMIC_IMPORTERS = {"import_module", "__import__"}
MODEL_KEYWORD_CALLS = {"create_deep_agent", "SubAgent"}


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


def _httpx_module_aliases(tree: ast.AST) -> set[str]:
    """Names bound to the httpx module (``import httpx`` / ``import httpx as h``)."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "httpx"
    }


def _httpx_class_names(tree: ast.AST) -> set[str]:
    """Local names bound to httpx client classes (``from httpx import Client as C``)."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "httpx"
        for alias in node.names
        if alias.name in FORBIDDEN_HTTPX_CLASSES
    }


def _is_httpx_client_call(node: ast.Call, modules: set[str], classes: set[str]) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in classes
    return (
        isinstance(func, ast.Attribute)
        and func.attr in FORBIDDEN_HTTPX_CLASSES
        and isinstance(func.value, ast.Name)
        and func.value.id in modules
    )


def _dynamic_import_target(node: ast.Call) -> str | None:
    """The module string passed to ``import_module``/``__import__`` when it is a literal."""
    if _call_name(node) not in DYNAMIC_IMPORTERS:
        return None
    candidates = [*node.args[:1], *(kw.value for kw in node.keywords if kw.arg == "name")]
    for candidate in candidates:
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value
    return None


def _model_literal_violations(node: ast.Call) -> list[str]:
    name = _call_name(node)
    if name not in MODEL_KEYWORD_CALLS:
        return []
    return [
        f"{name}(model=<str literal>)"
        for kw in node.keywords
        if kw.arg == "model" and _is_str_constant(kw.value)
    ]


def _call_violations(
    node: ast.Call, *, allow_httpx: bool, modules: set[str], classes: set[str]
) -> list[str]:
    found = _model_literal_violations(node)
    target = _dynamic_import_target(node)
    if target is not None and _is_forbidden_module(target):
        found.append(f"dynamic import of {target}")
    if not allow_httpx and _is_httpx_client_call(node, modules, classes):
        found.append("httpx client constructed outside clients layer")
    return found


def find_violations(tree: ast.AST, *, allow_httpx: bool = False) -> list[str]:
    """Return human-readable violations found in a parsed module."""
    found: list[str] = []
    modules = _httpx_module_aliases(tree)
    classes = _httpx_class_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(f"import {a.name}" for a in node.names if _is_forbidden_module(a.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and _is_forbidden_module(module):
                found.append(f"from {module} import ...")
            if any(a.name == "init_chat_model" for a in node.names):
                found.append("init_chat_model import")
        elif (isinstance(node, ast.Name) and node.id == "init_chat_model") or (
            isinstance(node, ast.Attribute) and node.attr == "init_chat_model"
        ):
            found.append("init_chat_model")
        elif isinstance(node, ast.Call):
            found.extend(
                _call_violations(node, allow_httpx=allow_httpx, modules=modules, classes=classes)
            )
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
    return relative.parts in HTTPX_ALLOWED_FILES


def test_scan_covers_the_source_tree() -> None:
    names = {path.name for path in _source_files()}

    assert "orchestrator.py" in names
    assert "settings.py" in names


def test_scan_includes_scripts_directory() -> None:
    relatives = {path.relative_to(REPO_ROOT).parts[0] for path in _source_files()}

    assert "scripts" in relatives


def test_clients_layer_exists() -> None:
    assert (REPO_ROOT / "src/rootcause/clients/__init__.py").is_file()


def test_no_provider_sdk_or_raw_http_client_outside_clients_layer() -> None:
    violations: list[str] = []
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT)
        tree = _parse(path.read_text(encoding="utf-8"))
        violations.extend(
            f"{relative}: {label}"
            for label in find_violations(tree, allow_httpx=_is_httpx_allowed(relative))
        )

    assert violations == [], "Use rootcause.clients instead:\n" + "\n".join(violations)


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
        # T008 carry-over: httpx import aliases
        "import httpx as h\nh.AsyncClient()",
        "import httpx as h\nh.Client(timeout=1)",
        "from httpx import AsyncClient as AC\nAC()",
        "from httpx import Client as C\nC()",
        "def f():\n    import httpx as h\n    return h.Client()",
        "x = h.Client()\nimport httpx as h",
        # T008 carry-over: dynamic imports with a forbidden module string
        "import importlib\nimportlib.import_module('anthropic')",
        "import importlib\nimportlib.import_module('openai.types')",
        "import importlib as il\nil.import_module('langchain_openai')",
        "from importlib import import_module\nimport_module('langchain_anthropic.chat_models')",
        "__import__('anthropic')",
        "__import__('openai')",
        "import importlib\nimportlib.import_module(name='openai')",
        # T008 carry-over: SubAgent keyword model
        "SubAgent(name='a', description='d', system_prompt='p', model='openai:gpt')",
        "SubAgent(model='anthropic:claude')",
        "agents.SubAgent(model='x')",
        # T009: the langfuse SDK is only imported by the clients layer
        "import langfuse",
        "import langfuse.langchain",
        "from langfuse import Langfuse, get_client",
        "from langfuse.langchain import CallbackHandler",
        "import importlib\nimportlib.import_module('langfuse')",
        "__import__('langfuse.langchain')",
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
        "import httpx as h\nh.get('http://x')\nh.Timeout(1)",
        "from httpx import Timeout, Response",
        "import importlib\nimportlib.import_module('json')",
        "import importlib\nimportlib.import_module(name)",
        "import importlib\nimportlib.import_module('rootcause.clients')",
        "__import__(name)",
        "SubAgent(name='a', model=model)",
        "SubAgent(name='a', model=chat_model('fast'))",
        "SubAgent(name='a', description='openai:gpt')",
        "from langfuse_tools import helper",
        "x = 'langfuse'  # import langfuse",
        "from rootcause.clients.langfuse import langfuse_client",
    ],
)
def test_guard_allows_legitimate_code(source: str) -> None:
    assert find_violations(_parse(source)) == []


def test_httpx_client_allowed_only_in_api_health_route() -> None:
    tree = _parse("import httpx\nhttpx.AsyncClient()")

    assert find_violations(tree, allow_httpx=True) == []
    assert _is_httpx_allowed(Path("src/rootcause/api/routes/health.py"))


@pytest.mark.parametrize(
    "path",
    [
        "src/rootcause/api/main.py",
        "src/rootcause/api/health.py",
        "src/rootcause/api/routes/other.py",
        "src/rootcause/api/routes/health_extra.py",
        "src/rootcause/worker/routes/health.py",
        "src/rootcause/agents/x.py",
        "scripts/probe.py",
    ],
)
def test_httpx_is_not_allowed_elsewhere_in_apps(path: str) -> None:
    assert not _is_httpx_allowed(Path(path))


def test_allowed_httpx_file_exists() -> None:
    assert (REPO_ROOT.joinpath(*HTTPX_ALLOWED_FILES[0])).is_file()


def _imports_langfuse(path: Path) -> bool:
    tree = _parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "langfuse" for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".")[0] == "langfuse"
        ):
            return True
    return False


def test_only_the_langfuse_client_module_imports_the_langfuse_sdk() -> None:
    importers = {
        path.relative_to(REPO_ROOT).parts
        for root in SCAN_ROOTS
        for path in (REPO_ROOT / root).rglob("*.py")
        if not EXCLUDED_PARTS & set(path.relative_to(REPO_ROOT).parts)
        and not path.name.startswith("test_")
        and _imports_langfuse(path)
    }

    assert importers == {LANGFUSE_MODULE}


def test_langfuse_client_module_exists() -> None:
    assert REPO_ROOT.joinpath(*LANGFUSE_MODULE).is_file()
