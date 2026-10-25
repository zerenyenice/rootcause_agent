"""``rootcause-db`` refuses destructive commands in prod and never prints the URL."""

import pytest
from sqlalchemy.exc import OperationalError

from rootcause_core.errors import ConfigurationError

PASSWORD = "s3cr3t-pw"
URL = f"postgresql://user:{PASSWORD}@db.invalid:5432/rootcause"
MIGRATE_FUNCTIONS = ("upgrade", "downgrade", "current", "revision")


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from rootcause_db import migrate

    recorded: list[str] = []
    for name in MIGRATE_FUNCTIONS:
        monkeypatch.setattr(migrate, name, lambda *a, _n=name, **k: recorded.append(_n))
    return recorded


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    def configure(environment: str) -> None:
        monkeypatch.setenv("ROOTCAUSE_DATABASE_URL", URL)
        monkeypatch.setenv("ROOTCAUSE_ENVIRONMENT", environment)

    return configure


def _exit_code(argv: list[str]):
    from rootcause_db.cli import main

    with pytest.raises(SystemExit) as raised:
        main(argv)
    return raised.value.code


def _failed(code) -> bool:
    return code not in (0, None)


@pytest.mark.parametrize(
    "argv", [["downgrade", "base"], ["downgrade", "-1"], ["revision", "-m", "x"]]
)
def test_destructive_commands_are_refused_in_prod(argv, calls, env, capsys):
    env("prod")

    code = _exit_code(argv)

    assert _failed(code)
    assert calls == [], "nothing may run in prod"
    output = capsys.readouterr()
    assert (output.err + output.out + str(code)).strip(), "a message must explain the refusal"
    assert PASSWORD not in output.err + output.out + str(code)


def test_prod_refusal_message_mentions_the_environment(calls, env, capsys):
    env("prod")

    code = _exit_code(["downgrade", "base"])

    text = (capsys.readouterr().err + str(code)).lower()
    assert "prod" in text


@pytest.mark.parametrize("environment", ["dev", "test"])
def test_downgrade_and_revision_run_outside_prod(environment, calls, env):
    from rootcause_db.cli import main

    env(environment)

    main(["downgrade", "base"])
    main(["revision", "-m", "x"])

    assert calls == ["downgrade", "revision"]


def test_upgrade_is_allowed_in_prod(calls, env):
    from rootcause_db.cli import main

    env("prod")

    main(["upgrade"])

    assert calls == ["upgrade"]


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception(f"could not connect {URL} password={PASSWORD}")),
        ConfigurationError("bad setting", variables=("ROOTCAUSE_DATABASE_URL",)),
    ],
    ids=["operational", "configuration"],
)
def test_errors_print_a_message_without_the_url_and_exit_non_zero(error, monkeypatch, env, capsys):
    from rootcause_db import migrate

    env("dev")

    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(migrate, "upgrade", boom)

    code = _exit_code(["upgrade"])

    assert _failed(code)
    output = capsys.readouterr()
    combined = output.err + output.out + str(code)
    assert combined.strip()
    assert PASSWORD not in combined
    assert "db.invalid" not in combined
    assert "Traceback" not in combined


def test_missing_database_url_exits_non_zero_with_a_message(monkeypatch, capsys):
    monkeypatch.delenv("ROOTCAUSE_DATABASE_URL", raising=False)

    code = _exit_code(["upgrade"])

    assert _failed(code)
    output = capsys.readouterr()
    assert "ROOTCAUSE_DATABASE_URL" in output.err + output.out + str(code)
