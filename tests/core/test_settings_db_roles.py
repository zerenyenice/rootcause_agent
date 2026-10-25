"""Settings for the three-role database model."""

import pytest

from rootcause.settings import Settings, get_settings

pytestmark = pytest.mark.unit

MIGRATION_URL = "postgresql://migrator:mig-pw@localhost:5432/rootcause"
ADMIN_URL = "postgresql://admin:adm-pw@localhost:5432/rootcause"


@pytest.mark.usefixtures("base_env")
def test_role_names_and_extra_urls_have_defaults() -> None:
    settings = get_settings()

    assert settings.db_owner_role == "rootcause_owner"
    assert settings.db_migrator_role == "rootcause_migrator"
    assert settings.db_app_role == "rootcause_app"
    assert settings.migration_database_url is None
    assert settings.admin_database_url is None


@pytest.mark.usefixtures("base_env")
def test_urls_and_roles_load_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_MIGRATION_DATABASE_URL", MIGRATION_URL)
    monkeypatch.setenv("ROOTCAUSE_ADMIN_DATABASE_URL", ADMIN_URL)
    monkeypatch.setenv("ROOTCAUSE_DB_OWNER_ROLE", "own")
    monkeypatch.setenv("ROOTCAUSE_DB_MIGRATOR_ROLE", "mig")
    monkeypatch.setenv("ROOTCAUSE_DB_APP_ROLE", "app")

    settings = get_settings()

    assert settings.migration_database_url is not None
    assert settings.migration_database_url.get_secret_value() == MIGRATION_URL
    assert settings.admin_database_url is not None
    assert settings.admin_database_url.get_secret_value() == ADMIN_URL
    assert (settings.db_owner_role, settings.db_migrator_role, settings.db_app_role) == (
        "own",
        "mig",
        "app",
    )


@pytest.mark.usefixtures("base_env")
def test_passwords_never_appear_in_repr_or_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_MIGRATION_DATABASE_URL", MIGRATION_URL)
    monkeypatch.setenv("ROOTCAUSE_ADMIN_DATABASE_URL", ADMIN_URL)

    settings = get_settings()

    rendered = repr(settings) + str(settings) + str(settings.model_dump())
    assert "mig-pw" not in rendered
    assert "adm-pw" not in rendered


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize(
    "variable",
    ["ROOTCAUSE_DB_OWNER_ROLE", "ROOTCAUSE_DB_MIGRATOR_ROLE", "ROOTCAUSE_DB_APP_ROLE"],
)
def test_blank_role_names_fall_back_to_the_defaults(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "")

    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.db_owner_role and settings.db_migrator_role and settings.db_app_role
