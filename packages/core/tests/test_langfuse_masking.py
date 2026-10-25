"""T009: the mask handed to the Langfuse client redacts credentials and drops raw SQL rows."""

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from rootcause_core.settings import Settings

pytestmark = pytest.mark.unit

URL = "postgresql://u:secret@h/db"
ROW_VALUE = "ROWVALUE-jane.doe@example.com"


@pytest.fixture
def mask(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> Callable[..., Any]:
    """The exact ``mask`` the production client factory gives to ``Langfuse(mask=...)``."""
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings())
    [kwargs] = langfuse_capture.init_kwargs
    return kwargs["mask"]  # type: ignore[no-any-return]


def _run_sql_payload(rows: list[list[Any]] | None = None) -> dict[str, Any]:
    rows = rows if rows is not None else [[ROW_VALUE], ["other@example.com"]]
    return {
        "columns": ["email"],
        "rows": rows,
        "row_count": len(rows),
        "truncated": False,
        "sql": "SELECT email FROM users",
    }


def test_removes_url_password_from_strings(mask: Callable[..., Any]) -> None:
    masked = mask(data=f"connecting to {URL} now")

    assert "secret" not in masked
    assert "u:" in masked
    assert "@h/db" in masked


def test_redacts_strings_nested_in_dicts_and_lists(mask: Callable[..., Any]) -> None:
    data = {"input": [{"content": f"dsn={URL}"}, "ok"], "meta": {"url": URL}}

    masked = mask(data=data)

    assert "secret" not in json.dumps(masked)
    assert masked["input"][1] == "ok"


def test_leaves_plain_values_untouched(mask: Callable[..., Any]) -> None:
    assert mask(data=42) == 42
    assert mask(data=None) is None
    assert mask(data=True) is True
    assert mask(data="orders dropped 18%") == "orders dropped 18%"
    assert mask(data=[]) == []
    assert mask(data={}) == {}


def test_keeps_unicode_and_sql_metacharacters(mask: Callable[..., Any]) -> None:
    text = "SELECT 'x'; -- drop table users \N{GRINNING FACE} café"

    assert mask(data=text) == text


def test_run_sql_payload_keeps_columns_and_counts_but_not_rows(mask: Callable[..., Any]) -> None:
    masked = mask(data=_run_sql_payload())

    assert masked["columns"] == ["email"]
    assert masked["row_count"] == 2
    assert masked["sql"] == "SELECT email FROM users"
    assert ROW_VALUE not in json.dumps(masked)
    assert "other@example.com" not in json.dumps(masked)


def test_run_sql_payload_with_no_rows(mask: Callable[..., Any]) -> None:
    masked = mask(data=_run_sql_payload(rows=[]))

    assert masked["columns"] == ["email"]
    assert masked["row_count"] == 0


def test_run_sql_payload_serialised_as_json_text_is_masked(mask: Callable[..., Any]) -> None:
    masked = mask(data=json.dumps(_run_sql_payload()))

    assert ROW_VALUE not in masked
    assert "email" in masked
    assert "row_count" in masked


def test_tool_message_carrying_rows_is_masked(mask: Callable[..., Any]) -> None:
    message = ToolMessage(content=json.dumps(_run_sql_payload()), tool_call_id="c1")

    masked = mask(data=message)

    assert ROW_VALUE not in json.dumps(masked, default=str)


def test_rows_nested_in_a_message_list_are_masked(mask: Callable[..., Any]) -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"sql": "x"}, "id": "c1"}]),
        ToolMessage(content=json.dumps(_run_sql_payload()), tool_call_id="c1"),
    ]

    masked = mask(data={"messages": messages})

    assert ROW_VALUE not in json.dumps(masked, default=str)


def test_result_is_json_serialisable(mask: Callable[..., Any]) -> None:
    json.dumps(mask(data={"a": [_run_sql_payload(), URL, 1, None]}))


def test_does_not_mutate_its_input(mask: Callable[..., Any]) -> None:
    data = {"a": [_run_sql_payload(), URL]}
    before = copy.deepcopy(data)

    mask(data=data)

    assert data == before


def test_accepts_extra_keyword_arguments(mask: Callable[..., Any]) -> None:
    assert mask(data="plain", field="input") == "plain"


def test_large_payload_is_reduced_to_a_count(mask: Callable[..., Any]) -> None:
    rows = [[f"{ROW_VALUE}-{i}"] for i in range(5000)]

    masked = mask(data=_run_sql_payload(rows=rows))

    assert masked["row_count"] == 5000
    assert len(json.dumps(masked)) < 2000
