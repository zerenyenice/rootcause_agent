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


# --- T009 amendment: deny-by-default tool outputs ---

ROWS_TEXT = json.dumps([["ROWVALUE-Ayse-Yilmaz", 41]])


def _redacted(name: str) -> str:
    return f"<redacted tool output: {name}>"


def _dump(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


@pytest.mark.parametrize(
    "name", ["read_file", "grep", "ls", "glob", "task", "export_csv", "profile_table"]
)
def test_unlisted_tool_output_is_replaced_by_a_marker(mask: Callable[..., Any], name: str) -> None:
    message = ToolMessage(content=f"line 1\n{ROWS_TEXT}", tool_call_id="c1", name=name)

    masked = _dump(mask(data=message))

    assert "ROWVALUE-Ayse-Yilmaz" not in masked
    assert _redacted(name) in masked


def test_tool_output_of_unknown_name_is_still_denied(mask: Callable[..., Any]) -> None:
    message = ToolMessage(content=f"x {ROWS_TEXT}", tool_call_id="c1")

    assert "ROWVALUE-Ayse-Yilmaz" not in _dump(mask(data=message))


def test_allowlisted_tool_output_is_kept(mask: Callable[..., Any]) -> None:
    content = json.dumps({"estimated_rows": 12, "estimated_cost": 1.5, "note": "ok"})
    message = ToolMessage(content=content, tool_call_id="c1", name="explain_sql")

    masked = _dump(mask(data=message))

    assert "estimated_rows" in masked
    assert "<redacted tool output" not in masked


def test_write_todos_output_is_no_longer_kept(mask: Callable[..., Any]) -> None:
    message = ToolMessage(
        content="Updated todo list to rowsentineltodo", tool_call_id="c1", name="write_todos"
    )

    masked = _dump(mask(data=message))

    assert "rowsentineltodo" not in masked
    assert _redacted("write_todos") in masked


def test_run_sql_summary_is_kept_with_columns_count_truncated_and_sql(
    mask: Callable[..., Any],
) -> None:
    summary = {
        "columns": ["email"],
        "row_count": 2,
        "truncated": False,
        "sql": "SELECT email FROM users",
        "path": "/query_results/c1.json",
    }
    message = ToolMessage(content=json.dumps(summary), tool_call_id="c1", name="run_sql")

    masked = _dump(mask(data=message))

    for expected in ("email", "row_count", "truncated", "SELECT email FROM users"):
        assert expected in masked
    assert "<redacted tool output" not in masked


def test_run_sql_message_that_still_carries_rows_loses_them(mask: Callable[..., Any]) -> None:
    message = ToolMessage(content=json.dumps(_run_sql_payload()), tool_call_id="c1", name="run_sql")

    masked = _dump(mask(data=message))

    assert ROW_VALUE not in masked
    assert "other@example.com" not in masked


@pytest.mark.parametrize(
    "tool_message",
    [
        {"role": "tool", "name": "read_file", "content": f"1\t{ROWS_TEXT}", "tool_call_id": "c1"},
        {"type": "tool", "name": "grep", "content": f"/f:1:{ROWS_TEXT}", "tool_call_id": "c1"},
    ],
    ids=["role-tool", "type-tool"],
)
def test_tool_message_echoed_in_generation_input_is_redacted(
    mask: Callable[..., Any], tool_message: dict[str, Any]
) -> None:
    generation_input = [
        {"role": "user", "content": "why did orders drop?"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "read_file", "id": "c1"}]},
        tool_message,
    ]

    masked = _dump(mask(data=generation_input))

    assert "ROWVALUE-Ayse-Yilmaz" not in masked
    assert _redacted(str(tool_message["name"])) in masked
    assert "why did orders drop?" in masked


def test_tool_message_objects_in_message_history_are_redacted(mask: Callable[..., Any]) -> None:
    history = {
        "messages": [
            AIMessage(content="", tool_calls=[{"name": "ls", "args": {}, "id": "c1"}]),
            ToolMessage(
                content=f"/large_tool_results/c9 {ROWS_TEXT}", tool_call_id="c1", name="ls"
            ),
        ]
    }

    masked = _dump(mask(data=history))

    assert "ROWVALUE-Ayse-Yilmaz" not in masked
    assert _redacted("ls") in masked


def test_human_and_assistant_text_is_not_redacted_as_tool_output(
    mask: Callable[..., Any],
) -> None:
    data = [{"role": "user", "content": "orders dropped 18%"}, AIMessage(content="checking")]

    masked = _dump(mask(data=data))

    assert "orders dropped 18%" in masked
    assert "checking" in masked
    assert "<redacted tool output" not in masked


@pytest.mark.parametrize("prefix", ["/query_results/", "/large_tool_results/"])
def test_agent_filesystem_state_under_result_paths_is_dropped(
    mask: Callable[..., Any], prefix: str
) -> None:
    files = {
        f"{prefix}c1.json": {"content": ROWS_TEXT, "encoding": "utf-8"},
        "/memories/notes.md": {"content": "keep me", "encoding": "utf-8"},
    }

    masked = _dump(mask(data={"files": files}))

    assert "ROWVALUE-Ayse-Yilmaz" not in masked
    assert f"{prefix}c1.json" not in masked


def test_result_paths_are_dropped_from_file_listings_in_state_updates(
    mask: Callable[..., Any],
) -> None:
    update = {"files": {"/query_results/c1.json": {"content": ROWS_TEXT}}, "todos": []}

    masked = mask(data=update)

    assert "ROWVALUE-Ayse-Yilmaz" not in _dump(masked)
    assert "todos" in masked


def test_denied_output_still_does_not_mutate_the_input(mask: Callable[..., Any]) -> None:
    message = ToolMessage(content=ROWS_TEXT, tool_call_id="c1", name="read_file")

    mask(data=message)

    assert message.content == ROWS_TEXT


# --- T009 amendment: secrets ---


@pytest.mark.parametrize(
    "text",
    [
        "postgresql://u:p@ss@h/db",
        "postgresql://u:p@ss:w0rd@h:5432/db?sslmode=require",
        "postgres://svc:pa%40ss@h/db",
    ],
)
def test_passwords_containing_at_signs_are_fully_redacted(
    mask: Callable[..., Any], text: str
) -> None:
    masked = mask(data=f"connect {text} now")

    for leaked in ("p@ss", "ss@h", "w0rd", "pa%40ss", "ss:w0rd"):
        assert leaked not in masked
    assert "@h" in masked


@pytest.mark.parametrize(
    "text",
    [
        "host=h user=u password=KVPASSWORD dbname=d",
        "host=h user=u password='KV PASSWORD' dbname=d",
        'host=h password="KVPASSWORD" user=u',
        "PASSWORD=KVPASSWORD host=h",
    ],
)
def test_libpq_password_pairs_are_redacted(mask: Callable[..., Any], text: str) -> None:
    masked = mask(data=text)

    assert "KVPASSWORD" not in masked
    assert "KV PASSWORD" not in masked
    assert "host=h" in masked


def test_libpq_password_redaction_also_applies_to_the_log_redactor() -> None:
    from rootcause_core.logging import make_text_redactor

    redacted = make_text_redactor(())("host=h password=KVPASSWORD user=u postgresql://u:p@ss@h/db")

    assert "KVPASSWORD" not in redacted
    assert "p@ss" not in redacted


# --- T009 amendment: PII pass ---

PII_SAMPLES = {
    "email": ("write to jane.doe+tag@example.co.uk today", "jane.doe+tag@example.co.uk"),
    "phone-tr": ("call +90 532 123 45 67 now", "532 123 45 67"),
    "phone-tr-local": ("call 0532 123 45 67 now", "532 123 45 67"),
    "phone-us": ("call +1 (415) 555-0132 now", "555-0132"),
    "iban-spaced": ("pay TR33 0006 1005 1978 6457 8413 26 today", "0006 1005 1978 6457 8413 26"),
    "iban-compact": ("pay DE89370400440532013000 today", "DE89370400440532013000"),
    "national-id": ("customer 10000000146 complained", "10000000146"),
    "national-id-arbitrary": ("tckn: 12345678901.", "12345678901"),
}


@pytest.fixture
def pii_mask(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> Callable[..., Any]:
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings(langfuse_mask_pii=True))
    return langfuse_capture.init_kwargs[-1]["mask"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("kind", PII_SAMPLES)
@pytest.mark.parametrize("carrier", ["string", "human", "assistant", "generation-output"])
def test_pii_in_generation_text_is_replaced(
    pii_mask: Callable[..., Any], kind: str, carrier: str
) -> None:
    text, pii = PII_SAMPLES[kind]
    data: Any = {
        "string": text,
        "human": [{"role": "user", "content": text}],
        "assistant": AIMessage(content=text),
        "generation-output": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }[carrier]

    masked = _dump(pii_mask(data=data))

    assert pii not in masked
    assert text.split()[0] in masked  # surrounding text survives


@pytest.mark.parametrize(
    "text",
    [
        "orders dropped 18% on 2026-10-14",
        "row_count 20000, estimated cost 1234.5",
        "order 123456 shipped; 3 of 7 failed",
        "SELECT count(*) FROM orders WHERE id = 42",
        "version 4.70.0 build 20261014",
    ],
)
def test_pii_pass_leaves_ordinary_numbers_and_text_alone(
    pii_mask: Callable[..., Any], text: str
) -> None:
    assert pii_mask(data=text) == text


@pytest.mark.parametrize("kind", PII_SAMPLES)
def test_pii_pass_can_be_switched_off(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings], kind: str
) -> None:
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings(langfuse_mask_pii=False))
    mask = langfuse_capture.init_kwargs[-1]["mask"]
    text, pii = PII_SAMPLES[kind]

    assert pii in _dump(mask(data=[{"role": "user", "content": text}]))


def test_pii_off_still_redacts_credentials(
    langfuse_capture: Any, make_langfuse_settings: Callable[..., Settings]
) -> None:
    from rootcause_core.clients.langfuse import langfuse_client

    langfuse_client(make_langfuse_settings(langfuse_mask_pii=False))
    mask = langfuse_capture.init_kwargs[-1]["mask"]

    assert "secret" not in mask(data=f"dsn {URL}")
