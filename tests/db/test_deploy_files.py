"""Text-level checks of the deploy files: three roles, app never connects as the superuser."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
INIT_SQL = (DEPLOY / "postgres" / "init.sql").read_text(encoding="utf-8")
ENV_EXAMPLE = (DEPLOY / ".env.example").read_text(encoding="utf-8")


def _env_value(name: str) -> str:
    match = re.search(rf"^{name}=(.*)$", ENV_EXAMPLE, re.MULTILINE)
    assert match, f"{name} must be set (not only commented) in .env.example"
    return match.group(1)


@pytest.mark.parametrize("role", ["rootcause_owner", "rootcause_migrator", "rootcause_app"])
def test_init_sql_creates_each_role(role):
    assert re.search(rf"CREATE\s+ROLE\s+{role}\b", INIT_SQL, re.IGNORECASE)


def test_owner_role_cannot_log_in_and_the_others_can():
    assert re.search(r"CREATE\s+ROLE\s+rootcause_owner\b[^;]*NOLOGIN", INIT_SQL, re.I | re.S)
    for role in ("rootcause_migrator", "rootcause_app"):
        assert re.search(rf"CREATE\s+ROLE\s+{role}\b[^;]*\bLOGIN\b", INIT_SQL, re.I | re.S)


def test_migrator_is_a_member_of_the_owner_role():
    assert re.search(
        r"(CREATE\s+ROLE\s+rootcause_migrator\b[^;]*IN\s+ROLE\s+rootcause_owner"
        r"|GRANT\s+rootcause_owner\s+TO\s+rootcause_migrator)",
        INIT_SQL,
        re.I | re.S,
    )


def test_init_sql_does_not_hardcode_real_passwords():
    assert "change-me" in INIT_SQL.lower() or ":'" in INIT_SQL or "current_setting" in INIT_SQL


def test_runtime_url_uses_the_app_role():
    assert "//rootcause_app:" in _env_value("ROOTCAUSE_DATABASE_URL")


def test_migration_and_admin_urls_are_documented_with_their_roles():
    assert "//rootcause_migrator:" in _env_value("ROOTCAUSE_MIGRATION_DATABASE_URL")
    assert "ROOTCAUSE_ADMIN_DATABASE_URL" in ENV_EXAMPLE


def test_runtime_url_is_not_the_postgres_superuser():
    value = _env_value("ROOTCAUSE_DATABASE_URL")

    assert "//rootcause:" not in value
    assert "//postgres:" not in value
