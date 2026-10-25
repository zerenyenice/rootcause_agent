"""``bootstrap`` (superuser) prepares the database; migrations then run as a plain owner role."""

import threading

import psycopg
import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

SCHEMAS = {"app", "catalog", "audit"}


def _sync_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _state(url: str) -> tuple[set[str], set[str]]:
    engine = sa.create_engine(_sync_url(url))
    try:
        with engine.connect() as connection:
            extensions = {
                r for (r,) in connection.execute(sa.text("SELECT extname FROM pg_extension"))
            }
            schemas = {
                r
                for (r,) in connection.execute(
                    sa.text("SELECT schema_name FROM information_schema.schemata")
                )
            }
    finally:
        engine.dispose()
    return extensions, schemas


def test_bootstrap_creates_extension_and_schemas(fresh_database_url):
    from rootcause_db.bootstrap import bootstrap

    bootstrap(fresh_database_url)

    extensions, schemas = _state(fresh_database_url)
    assert "vector" in extensions
    assert schemas >= SCHEMAS


def test_bootstrap_is_idempotent(fresh_database_url):
    from rootcause_db.bootstrap import bootstrap

    bootstrap(fresh_database_url)
    bootstrap(fresh_database_url)

    extensions, schemas = _state(fresh_database_url)
    assert "vector" in extensions
    assert schemas >= SCHEMAS


def test_upgrade_as_non_superuser_owner_succeeds_after_bootstrap(app_role):
    from rootcause_db.bootstrap import bootstrap
    from rootcause_db.migrate import upgrade

    bootstrap(app_role.admin_url, app_role=app_role.name)

    upgrade(app_role.url, "head")

    engine = sa.create_engine(_sync_url(app_role.url))
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(sa.text("SELECT count(*) FROM app.alembic_version")).scalar_one()
                == 1
            )
            is_super = connection.execute(
                sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
    finally:
        engine.dispose()
    assert is_super is False


def test_upgrade_as_app_role_without_bootstrap_explains_bootstrap(app_role):
    from rootcause_db.migrate import upgrade

    with pytest.raises(Exception) as raised:  # noqa: PT011
        upgrade(app_role.url, "head")

    assert "bootstrap" in str(raised.value).lower()
    assert not isinstance(raised.value, (sa.exc.DBAPIError, psycopg.Error)), (
        "must fail fast with a clear message, not a raw permission error"
    )
    assert "s3cr3t" not in str(raised.value) and app_role.password not in str(raised.value)


def test_concurrent_upgrades_both_succeed_and_leave_one_version_row(bootstrapped_url):
    from rootcause_db.migrate import upgrade

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        try:
            barrier.wait(timeout=30)
            upgrade(bootstrapped_url, "head")
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    engine = sa.create_engine(_sync_url(bootstrapped_url))
    try:
        with engine.connect() as connection:
            rows = connection.execute(sa.text("SELECT version_num FROM app.alembic_version")).all()
    finally:
        engine.dispose()
    assert len(rows) == 1


def test_cli_bootstrap_infers_app_role_from_the_database_url(app_role, monkeypatch):
    """CI runs ``rootcause-db bootstrap --admin-url ...`` with ROOTCAUSE_DATABASE_URL = app URL."""
    from rootcause_db.cli import main
    from rootcause_db.migrate import upgrade

    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", app_role.url)

    main(["bootstrap", "--admin-url", app_role.admin_url])

    upgrade(app_role.url, "head")
    engine = sa.create_engine(_sync_url(app_role.url))
    try:
        with engine.connect() as connection:
            count = connection.execute(
                sa.text("SELECT count(*) FROM app.alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()
    assert count == 1


def test_bootstrap_function_without_app_role_still_works_for_a_superuser_only_setup(
    fresh_database_url,
):
    from rootcause_db.bootstrap import bootstrap

    bootstrap(fresh_database_url, app_role=None)

    assert "vector" in _state(fresh_database_url)[0]
