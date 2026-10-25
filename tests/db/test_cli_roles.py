"""CLI contract for the three-role model: which URL each command reads, and error output."""

import pytest

pytestmark = pytest.mark.unit

APP_URL = "postgresql://rootcause_app:app-pw@app.invalid:5432/rootcause"
MIGRATION_URL = "postgresql://rootcause_migrator:mig-pw@migration.invalid:5432/rootcause"
ADMIN_PASSWORD = "adm1n-pw-7"
ADMIN_URL = f"postgresql://pgadmin:{ADMIN_PASSWORD}@admin.invalid:5432/rootcause"


@pytest.fixture(autouse=True)
def base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", APP_URL)
    monkeypatch.setenv("ROOTCAUSE_MIGRATION_DATABASE_URL", MIGRATION_URL)
    monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", "dev")


def _run(argv: list[str], capsys):
    from rootcause.db.cli import main

    with pytest.raises(SystemExit) as raised:
        main(argv)
    out = capsys.readouterr()
    return raised.value.code, out.err + out.out


@pytest.mark.parametrize("command", [["upgrade"], ["current"], ["downgrade", "base"]])
def test_commands_use_the_migration_url_not_the_runtime_url(command, monkeypatch):
    from rootcause.db import cli, migrate

    seen: list[str] = []
    for name in ("upgrade", "downgrade", "current", "revision"):
        monkeypatch.setattr(migrate, name, lambda url, *a, **k: seen.append(url))

    cli.main(command)

    assert seen == [MIGRATION_URL]


def test_revision_uses_the_migration_url(monkeypatch):
    from rootcause.db import cli, migrate

    seen: list[str] = []
    monkeypatch.setattr(migrate, "revision", lambda url, *a, **k: seen.append(url))

    cli.main(["revision", "-m", "x"])

    assert seen == [MIGRATION_URL]


def test_admin_url_with_a_password_is_rejected_and_points_to_the_safe_channels(capsys):
    code, output = _run(["bootstrap", "--admin-url", ADMIN_URL], capsys)

    assert code not in (0, None)
    assert "ROOTCAUSE_ADMIN_DATABASE_URL" in output
    assert "PGPASSWORD" in output
    assert ADMIN_PASSWORD not in output
    assert "admin.invalid" not in output, "no connection attempt, no host in the message"


@pytest.mark.parametrize(
    "query", ["password=x", "passfile=/tmp/x", "options=-c%20x%3Dy", "user=root", "host=evil"]
)
def test_admin_url_with_disallowed_query_keys_is_rejected(query, capsys):
    url = f"postgresql://pgadmin@admin.invalid:5432/rootcause?{query}"

    code, output = _run(["bootstrap", "--admin-url", url], capsys)

    assert code not in (0, None)
    assert "database operation failed" not in output, "rejected before any connection attempt"
    assert "admin.invalid" not in output


def test_admin_url_password_in_the_query_is_rejected_like_an_inline_password(capsys):
    url = "postgresql://u@h/db?password=x"

    code, output = _run(["bootstrap", "--admin-url", url], capsys)

    assert code not in (0, None)
    assert "ROOTCAUSE_ADMIN_DATABASE_URL" in output
    assert "PGPASSWORD" in output


@pytest.mark.parametrize(
    "query",
    ["sslmode=require", "sslrootcert=/etc/ca.pem", "connect_timeout=5", "application_name=x"],
)
def test_admin_url_with_allowed_query_keys_reaches_the_connection_attempt(query, capsys):
    url = f"postgresql://pgadmin@admin.invalid:5432/rootcause?{query}"

    code, output = _run(["bootstrap", "--admin-url", url], capsys)

    assert code not in (0, None)
    assert "database operation failed" in output


def test_invalid_admin_url_in_the_environment_names_the_admin_variable(monkeypatch, capsys):
    monkeypatch.setenv("ROOTCAUSE_ADMIN_DATABASE_URL", "http://pgadmin:zz-secret@host/x")

    code, output = _run(["bootstrap"], capsys)

    assert code not in (0, None)
    assert "ROOTCAUSE_ADMIN_DATABASE_URL" in output
    assert "ROOTCAUSE_DATABASE_URL" not in output
    assert "zz-secret" not in output


def test_invalid_admin_url_argument_names_the_option(capsys):
    code, output = _run(["bootstrap", "--admin-url", "http://pgadmin@host/x"], capsys)

    assert code not in (0, None)
    assert "--admin-url" in output
    assert "ROOTCAUSE_DATABASE_URL" not in output


def test_bootstrap_without_any_admin_url_names_the_variable(capsys):
    code, output = _run(["bootstrap"], capsys)

    assert code not in (0, None)
    assert "ROOTCAUSE_ADMIN_DATABASE_URL" in output


def test_bootstrap_reads_the_admin_url_from_the_environment(monkeypatch, capsys):
    """Reaches the connection attempt (unresolvable host) rather than a settings error."""
    monkeypatch.setenv("ROOTCAUSE_ADMIN_DATABASE_URL", ADMIN_URL)

    code, output = _run(["bootstrap"], capsys)

    assert code not in (0, None)
    assert "database operation failed" in output
    assert ADMIN_PASSWORD not in output
    assert "admin.invalid" not in output


SECRET_DETAIL = "internal-detail-xyz"


@pytest.fixture
def exploding_upgrade(monkeypatch):
    from rootcause.db import migrate

    def boom(*args, **kwargs):
        raise TypeError(SECRET_DETAIL)

    monkeypatch.setattr(migrate, "upgrade", boom)


@pytest.mark.usefixtures("exploding_upgrade")
def test_unexpected_exception_prints_a_one_line_summary_without_traceback(capsys):
    code, output = _run(["upgrade"], capsys)

    assert code not in (0, None)
    assert "unexpected error (TypeError)" in output
    assert "Traceback" not in output
    assert SECRET_DETAIL not in output


@pytest.mark.usefixtures("exploding_upgrade")
def test_debug_flag_shows_the_traceback(capsys):
    from rootcause.db.cli import main

    reraised = False
    try:
        main(["--debug", "upgrade"])
    except SystemExit:
        pass
    except TypeError:
        reraised = True

    output = capsys.readouterr()
    assert reraised or "Traceback" in output.err + output.out
    assert reraised or "TypeError" in output.err + output.out
