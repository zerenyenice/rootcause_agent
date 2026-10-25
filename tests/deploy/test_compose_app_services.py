"""Text-level checks of the MVP application services in deploy/ and their images.

Covers deploy/docker-compose.yml (web, api, agent, migrate), the repo-root and
web Dockerfiles, both .dockerignore files and deploy/.env.example. Starting the
stack is not tested here; CI's compose-config job runs `docker compose config`.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
COMPOSE = DEPLOY / "docker-compose.yml"
ENV_EXAMPLE = DEPLOY / ".env.example"
PY_DOCKERFILE = ROOT / "Dockerfile"
WEB_DOCKERFILE = ROOT / "web" / "Dockerfile"

APP_SERVICES = ("web", "api", "agent", "migrate")
PYTHON_SERVICES = ("api", "agent", "migrate")
LONG_RUNNING = ("web", "api", "agent")
STACK_SECRETS = (
    "POSTGRES_PASSWORD",
    "POSTGRES_MIGRATOR_PASSWORD",
    "POSTGRES_APP_PASSWORD",
    "OPENVIKING_ROOT_API_KEY",
    "OPENVIKING_EMBEDDING_API_KEY",
    "OPENVIKING_VLM_API_KEY",
)
MIGRATION_URL = "ROOTCAUSE_MIGRATION_DATABASE_URL"
# FROM <image>:<exact tag>@sha256:<64 hex> [AS <stage>]
PINNED_FROM = re.compile(r"^FROM\s+\S+:[\w.-]+@sha256:[0-9a-f]{64}(\s+AS\s+\w+)?$", re.IGNORECASE)


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    loaded = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    result: dict[str, Any] = loaded["services"]
    return result


def _environment(service: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in (service.get("environment") or {}).items()}


def _from_lines(dockerfile: Path) -> list[str]:
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip().upper().startswith("FROM ")]


def _env_example_value(name: str) -> str:
    match = re.search(rf"^{name}=(.*)$", ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{name} must be set (not only commented) in deploy/.env.example"
    return match.group(1)


@pytest.mark.parametrize("name", APP_SERVICES)
def test_application_service_is_defined_without_a_profile(
    services: dict[str, Any], name: str
) -> None:
    assert name in services
    # MVP gate: a plain `docker compose up` starts every application service.
    assert not services[name].get("profiles")


def test_every_published_port_binds_to_loopback_only(services: dict[str, Any]) -> None:
    for name, service in services.items():
        for port in service.get("ports") or []:
            assert str(port).startswith("127.0.0.1:"), (name, port)


def test_python_services_share_one_image_built_from_the_repo_root(
    services: dict[str, Any],
) -> None:
    images = {services[name]["image"] for name in PYTHON_SERVICES}
    builds = [services[name]["build"] for name in PYTHON_SERVICES]

    assert len(images) == 1
    for build in builds:
        assert build["context"] == ".."
        assert build["dockerfile"] == "Dockerfile"


def test_migrate_runs_the_upgrade_once_after_postgres_is_healthy(
    services: dict[str, Any],
) -> None:
    migrate = services["migrate"]
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]

    assert migrate["command"] == ["rootcause-db", "upgrade"]
    assert "rootcause-db" in scripts
    assert migrate["restart"] == "no"
    assert migrate["depends_on"]["postgres"]["condition"] == "service_healthy"


@pytest.mark.parametrize("name", ["api", "agent"])
def test_api_and_agent_wait_for_a_successful_migration(services: dict[str, Any], name: str) -> None:
    depends_on = services[name]["depends_on"]

    assert depends_on["migrate"]["condition"] == "service_completed_successfully"
    assert depends_on["postgres"]["condition"] == "service_healthy"


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("api", "rootcause.api.main:create_app"),
        ("agent", "rootcause.agent_service.main:create_app"),
    ],
)
def test_uvicorn_serves_the_app_factory(services: dict[str, Any], name: str, target: str) -> None:
    command = services[name]["command"]

    assert command[:3] == ["uvicorn", "--factory", target]


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_long_running_services_have_a_healthcheck(services: dict[str, Any], name: str) -> None:
    assert services[name]["healthcheck"]["test"]


@pytest.mark.parametrize("name", ["api", "agent"])
def test_demo_data_is_mounted_read_only_at_data(services: dict[str, Any], name: str) -> None:
    mounts = [str(volume) for volume in services[name]["volumes"]]

    assert any(mount.endswith(":/data:ro") for mount in mounts), mounts


@pytest.mark.parametrize("name", PYTHON_SERVICES)
def test_runtime_url_uses_the_app_role_inside_the_network(
    services: dict[str, Any], name: str
) -> None:
    url = _environment(services[name])["ROOTCAUSE_DATABASE_URL"]

    assert url.startswith("postgresql://rootcause_app:")
    assert "@postgres:5432/" in url


@pytest.mark.parametrize("name", PYTHON_SERVICES)
def test_stack_secrets_are_blanked_for_the_application(services: dict[str, Any], name: str) -> None:
    env = _environment(services[name])

    for secret in STACK_SECRETS:
        assert env.get(secret) == "", (name, secret)


def test_only_migrate_gets_the_migrator_url(services: dict[str, Any]) -> None:
    assert _environment(services["migrate"])[MIGRATION_URL].startswith(
        "postgresql://rootcause_migrator:"
    )
    for name in ("api", "agent"):
        assert _environment(services[name])[MIGRATION_URL] == "", name


def test_web_receives_its_public_urls_as_build_args(services: dict[str, Any]) -> None:
    args = services["web"]["build"]["args"]

    assert {"NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_AGENT_URL"} <= set(args)


def test_langfuse_and_web_default_to_different_host_ports(services: dict[str, Any]) -> None:
    web_ports = [str(port) for port in services["web"]["ports"]]
    langfuse_ports = [str(port) for port in services["langfuse-web"]["ports"]]

    assert any(":-3000}" in port for port in web_ports)
    assert not any(":-3000}" in port for port in langfuse_ports)


@pytest.mark.parametrize("dockerfile", [PY_DOCKERFILE, WEB_DOCKERFILE], ids=["python", "web"])
def test_base_images_are_pinned_by_tag_and_digest(dockerfile: Path) -> None:
    lines = _from_lines(dockerfile)

    assert lines
    for line in lines:
        assert ":latest" not in line, line
        assert PINNED_FROM.match(line), line


@pytest.mark.parametrize(
    ("dockerfile", "user"), [(PY_DOCKERFILE, "rootcause"), (WEB_DOCKERFILE, "node")]
)
def test_runtime_stage_runs_as_a_non_root_user(dockerfile: Path, user: str) -> None:
    text = dockerfile.read_text(encoding="utf-8")
    final_stage = text[text.rindex("\nFROM ") :]

    assert re.search(rf"^USER\s+{user}\s*$", final_stage, re.MULTILINE)


def test_python_image_installs_only_locked_runtime_dependencies() -> None:
    lines = PY_DOCKERFILE.read_text(encoding="utf-8").splitlines()
    syncs = [line for line in lines if "uv sync" in line and not line.lstrip().startswith("#")]

    assert syncs
    for sync in syncs:
        assert "--locked" in sync
        assert "--no-dev" in sync


@pytest.mark.parametrize(
    ("ignore_file", "entries"),
    [
        (ROOT / ".dockerignore", ("**/.env", "**/.venv", "**/node_modules", "**/data", "**/.git")),
        (ROOT / "web" / ".dockerignore", (".env", ".env.*", "node_modules", "data", ".git")),
    ],
    ids=["root", "web"],
)
def test_dockerignore_keeps_secrets_and_local_state_out(
    ignore_file: Path, entries: tuple[str, ...]
) -> None:
    lines = {line.strip() for line in ignore_file.read_text(encoding="utf-8").splitlines()}

    assert set(entries) <= lines


def test_next_builds_a_standalone_server() -> None:
    config = (ROOT / "web" / "next.config.ts").read_text(encoding="utf-8")

    assert re.search(r"""output:\s*["']standalone["']""", config)


@pytest.mark.parametrize(
    "name",
    [
        "WEB_PORT",
        "API_PORT",
        "AGENT_PORT",
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_AGENT_URL",
        "DATA_DIR",
        "ROOTCAUSE_SECRET_KEY",
    ],
)
def test_env_example_sets_the_application_variables(name: str) -> None:
    assert _env_example_value(name)


def test_env_example_secret_key_is_a_placeholder() -> None:
    assert "change-me" in _env_example_value("ROOTCAUSE_SECRET_KEY")
