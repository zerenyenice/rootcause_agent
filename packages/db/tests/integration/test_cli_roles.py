"""CLI against a real server: env-driven bootstrap and the prod superuser refusal."""

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration


def _sync(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def test_cli_bootstrap_reads_admin_url_from_the_environment_then_upgrade_uses_migrator(
    db_roles,
):
    from rootcause_db.cli import main

    main(["bootstrap"])
    main(["upgrade"])

    engine = sa.create_engine(_sync(db_roles.admin_url))
    with engine.connect() as connection:
        owner = connection.execute(
            sa.text(
                "SELECT pg_get_userbyid(relowner) FROM pg_class "
                "WHERE oid = 'audit.query_log'::regclass"
            )
        ).scalar_one()
        version_rows = connection.execute(
            sa.text("SELECT count(*) FROM app.alembic_version")
        ).scalar_one()
    engine.dispose()
    assert owner == db_roles.owner
    assert version_rows == 1


@pytest.mark.parametrize("slot", ["ROOTCAUSE_DB_APP_ROLE", "ROOTCAUSE_DB_MIGRATOR_ROLE"])
def test_cli_bootstrap_refuses_the_admin_user_as_a_role_and_grants_nothing(
    db_roles, monkeypatch, capsys, slot
):
    from rootcause_core.settings import clear_settings_cache
    from rootcause_db.cli import main

    monkeypatch.setenv(slot, db_roles.admin_user)
    clear_settings_cache()

    with pytest.raises(SystemExit) as raised:
        main(["bootstrap"])

    assert raised.value.code not in (0, None)
    assert capsys.readouterr().err.strip(), "a message must explain the refusal"
    engine = sa.create_engine(_sync(db_roles.admin_url))
    with engine.connect() as connection:
        acls = connection.execute(sa.text("SELECT count(*) FROM pg_default_acl")).scalar_one()
    engine.dispose()
    assert acls == 0


def test_upgrade_in_prod_as_a_superuser_is_refused(db_roles, monkeypatch, capsys):
    from rootcause_core.settings import clear_settings_cache
    from rootcause_db.cli import main

    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "prod")
    monkeypatch.setenv("ROOTCAUSE_MIGRATION_DATABASE_URL", db_roles.admin_url)
    clear_settings_cache()

    with pytest.raises(SystemExit) as raised:
        main(["upgrade"])

    assert raised.value.code not in (0, None)
    err = capsys.readouterr().err.lower()
    assert "superuser" in err
    engine = sa.create_engine(_sync(db_roles.admin_url))
    with engine.connect() as connection:
        tables = connection.execute(
            sa.text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'app'")
        ).scalar_one()
    engine.dispose()
    assert tables == 0, "nothing may be migrated"
