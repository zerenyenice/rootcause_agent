"""``check_selection`` fails closed (``unresolvable_reference``) when sqlglot cannot decide."""

from dataclasses import dataclass
from typing import Any

import pytest
import sqlglot

from rootcause.catalog import selection_check as module
from rootcause.catalog.selection_check import (
    UNRESOLVABLE_REFERENCE_CODE,
    SelectionPolicy,
    SelectionRejected,
    check_selection,
)
from rootcause.connections.duckdb_executor import RelationName

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Table:
    schema: str
    name: str
    columns: tuple[str, ...]
    hides_columns: bool


def _policy() -> SelectionPolicy:
    return SelectionPolicy.build(
        "duckdb",
        [_Table("main", "t", ("id", "visible"), True)],
        [RelationName("main", "t")],
    )


def _boom(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("sqlglot blew up")


def test_a_selected_column_passes() -> None:
    assert check_selection("SELECT visible FROM t", _policy()) == {("main", "t")}


@pytest.mark.parametrize("sql", ["SELECT FROM WHERE (", "", "SELECT 1 FROM t WHERE ((("])
def test_unparseable_sql_is_rejected(sql: str) -> None:
    with pytest.raises(SelectionRejected) as caught:
        check_selection(sql, _policy())
    assert caught.value.code == UNRESOLVABLE_REFERENCE_CODE


def test_a_qualify_error_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "qualify", _boom)
    with pytest.raises(SelectionRejected) as caught:
        check_selection("SELECT visible FROM t", _policy())
    assert caught.value.code == UNRESOLVABLE_REFERENCE_CODE


def test_a_scope_traversal_error_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "traverse_scope", _boom)
    with pytest.raises(SelectionRejected) as caught:
        check_selection("SELECT visible FROM t", _policy())
    assert caught.value.code == UNRESOLVABLE_REFERENCE_CODE


def test_a_parse_error_from_sqlglot_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sqlglot, "parse_one", _boom)
    with pytest.raises(SelectionRejected) as caught:
        check_selection("SELECT visible FROM t", _policy())
    assert caught.value.code == UNRESOLVABLE_REFERENCE_CODE


def test_a_hidden_column_is_rejected() -> None:
    with pytest.raises(SelectionRejected):
        check_selection("SELECT secret FROM t", _policy())
