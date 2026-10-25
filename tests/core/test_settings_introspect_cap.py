"""``introspect_max_tables``: cap on the tables one ``/introspect`` response lists (RED)."""

import pytest

from rootcause.errors import ConfigurationError
from rootcause.settings import get_settings

pytestmark = pytest.mark.unit

DEFAULT_INTROSPECT_MAX_TABLES = 1000


@pytest.mark.usefixtures("base_env")
def test_introspect_max_tables_defaults_to_one_thousand() -> None:
    assert get_settings().introspect_max_tables == DEFAULT_INTROSPECT_MAX_TABLES


@pytest.mark.usefixtures("base_env")
def test_introspect_max_tables_loads_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROOTCAUSE_INTROSPECT_MAX_TABLES", "25")

    assert get_settings().introspect_max_tables == 25


@pytest.mark.usefixtures("base_env")
@pytest.mark.parametrize("value", ["0", "-1", "many"])
def test_introspect_max_tables_rejects_non_positive_or_non_integer_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ROOTCAUSE_INTROSPECT_MAX_TABLES", value)

    with pytest.raises(ConfigurationError) as excinfo:
        get_settings()

    assert "ROOTCAUSE_INTROSPECT_MAX_TABLES" in str(excinfo.value)
