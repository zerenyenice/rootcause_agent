"""T009 amendment: deploy/docker-compose.yml hardening (observability profile)."""

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

pytestmark = pytest.mark.unit

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
COMPOSE = DEPLOY / "docker-compose.yml"
LANGFUSE_ENV_EXAMPLE = DEPLOY / "langfuse.env.example"
INIT_SECRET = "LANGFUSE_INIT_PROJECT_SECRET_KEY"
ROOT_USERS = {"", "root", "0", "0:0", "root:root"}
GUARD = re.compile(r"\$\{(\w+):\?")


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    loaded = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _environment(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env)
    return {str(key): str(value) for key, value in env.items()}


def test_langfuse_redis_runs_as_a_non_root_user(compose: dict[str, Any]) -> None:
    user = str(compose["services"]["langfuse-redis"].get("user", "")).strip()

    assert user not in ROOT_USERS
    assert not user.startswith("0:")


def test_init_project_secret_key_is_not_in_any_environment_block(compose: dict[str, Any]) -> None:
    for name, service in compose["services"].items():
        assert INIT_SECRET not in _environment(service), name
    for key, value in compose.items():
        if key.startswith("x-") and isinstance(value, dict):
            assert INIT_SECRET not in value, key


def test_init_project_secret_key_is_not_interpolated_anywhere_in_the_compose_file() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    # Comments may point at langfuse.env; no `KEY: value` or `KEY=value` assignment may remain.
    assert not re.search(rf"^\s*{INIT_SECRET}\s*[:=]", text, re.MULTILINE)


def test_init_project_secret_key_is_documented_in_langfuse_env_example() -> None:
    assert INIT_SECRET in LANGFUSE_ENV_EXAMPLE.read_text(encoding="utf-8")


def _guard_scripts(compose: dict[str, Any]) -> dict[str, tuple[str, list[str]]]:
    """service -> (shell script, guarded variable names) for entrypoint/command guards."""
    scripts: dict[str, tuple[str, list[str]]] = {}
    for name, service in compose["services"].items():
        for key in ("entrypoint", "command"):
            parts = service.get(key)
            if not isinstance(parts, list):
                continue
            script = next((part for part in parts if GUARD.search(str(part))), None)
            if script is not None:
                scripts[name] = (str(script), GUARD.findall(str(script)))
    return scripts


def _run_guard(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the guard with the final ``exec`` and side effects replaced by an echo marker."""
    shell = script.replace("$$", "$")
    shell = shell.replace("mkdir -p /data/langfuse", "true")
    shell = re.sub(r"\bexec\b", "echo STARTED", shell)
    return subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", shell],  # noqa: S607
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_the_langfuse_services_carry_startup_guards(compose: dict[str, Any]) -> None:
    guards = _guard_scripts(compose)

    assert {"langfuse-redis", "langfuse-clickhouse", "langfuse-minio"} <= set(guards)


@pytest.mark.parametrize("service", ["langfuse-redis", "langfuse-clickhouse", "langfuse-minio"])
def test_guards_refuse_placeholder_secrets(compose: dict[str, Any], service: str) -> None:
    script, variables = _guard_scripts(compose)[service]
    good = {name: "0a1b2c3d4e5f60718293a4b5c6d7e8f9" for name in variables}

    accepted = _run_guard(script, good)
    assert accepted.returncode == 0, accepted.stderr
    assert "STARTED" in accepted.stdout

    for variable in variables:
        refused = _run_guard(script, {**good, variable: "change-me-langfuse-password"})
        assert refused.returncode != 0, variable
        assert "STARTED" not in refused.stdout, variable


@pytest.mark.parametrize("service", ["langfuse-redis", "langfuse-clickhouse", "langfuse-minio"])
def test_guards_still_refuse_empty_secrets(compose: dict[str, Any], service: str) -> None:
    script, variables = _guard_scripts(compose)[service]

    refused = _run_guard(script, {name: "" for name in variables})

    assert refused.returncode != 0
    assert "STARTED" not in refused.stdout
