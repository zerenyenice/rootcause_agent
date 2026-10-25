"""T009 hardening: PII pass performance, unicode e-mails, tool-id spoofing, SDK contract."""

import re
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from rootcause_core.observability.masking import build_mask
from rootcause_core.observability.pii import redact_pii

pytestmark = pytest.mark.unit

MAX_SECONDS = 0.1
LONG_TEXT_LIMIT = 32 * 1024
EMAIL_CHARS = string.ascii_letters + string.digits + "._%+-"
SENTINEL = "rowsentinelvalue"


def _blob(length: int) -> str:
    return (EMAIL_CHARS * (length // len(EMAIL_CHARS) + 1))[:length]


@pytest.fixture
def mask() -> Any:
    return build_mask()


# --- A: performance and long text ---


def test_masking_a_200k_email_alphabet_string_without_at_sign_is_fast(mask: Any) -> None:
    text = _blob(200_000)

    started = time.perf_counter()
    mask(data=text)

    assert time.perf_counter() - started < MAX_SECONDS


def test_pii_pass_on_a_string_below_the_long_text_limit_is_fast() -> None:
    text = _blob(LONG_TEXT_LIMIT - 1)

    started = time.perf_counter()
    redact_pii(text)

    assert time.perf_counter() - started < MAX_SECONDS


def test_strings_longer_than_32_kib_are_replaced_by_a_length_marker(mask: Any) -> None:
    text = "a" * (LONG_TEXT_LIMIT + 1)

    assert mask(data=text) == f"<redacted long text: {len(text)} chars>"


def test_a_long_string_nested_in_a_message_is_replaced(mask: Any) -> None:
    text = "word " * 20_000

    masked = mask(data=[{"role": "user", "content": text}])

    assert masked[0]["content"] == f"<redacted long text: {len(text)} chars>"


def test_a_string_of_exactly_32_kib_is_kept(mask: Any) -> None:
    text = "a" * LONG_TEXT_LIMIT

    assert mask(data=text) == text


# --- C: Turkish e-mails ---


@pytest.mark.parametrize(
    ("email", "fragments"),
    [
        ("ayse@örnek.com.tr", ("ayse", "örnek", "com.tr")),
        ("şule.öztürk@firma.com.tr", ("şule", "öztürk", "firma", "ule.")),
        ("ÇAĞRI@FIRMA.COM.TR", ("ÇAĞRI", "AĞRI", "FIRMA")),
    ],
)
def test_turkish_emails_are_fully_masked(email: str, fragments: tuple[str, ...], mask: Any) -> None:
    for out in (redact_pii(f"contact {email} now"), mask(data=f"contact {email} now")):
        assert out.startswith("contact ")
        assert out.endswith(" now")
        assert "<email>" in out
        assert all(fragment not in out for fragment in fragments)
        assert "@" not in out


# --- E: tool call id spoofing ---


def _calls(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": cid, "name": name, "args": {}} for cid, name in pairs],
    }


def _kept_payload() -> str:
    return '{"estimated_rows": "' + SENTINEL + '"}'


@pytest.mark.parametrize(
    "pairs",
    [
        (("d", "read_file"), ("d", "explain_sql")),
        (("d", "explain_sql"), ("d", "read_file")),
    ],
    ids=["unsafe-first", "safe-first"],
)
def test_a_tool_call_id_mapped_to_two_names_is_redacted(
    mask: Any, pairs: tuple[tuple[str, str], ...]
) -> None:
    messages = [
        _calls(*pairs),
        {"role": "tool", "tool_call_id": "d", "content": _kept_payload()},
    ]

    assert SENTINEL not in str(mask(data=messages))


def test_a_tool_call_id_mapped_to_two_names_across_messages_is_redacted(mask: Any) -> None:
    messages = [
        _calls(("d", "read_file")),
        _calls(("d", "explain_sql")),
        {"role": "tool", "tool_call_id": "d", "content": _kept_payload()},
    ]

    assert SENTINEL not in str(mask(data=messages))


def test_a_message_name_disagreeing_with_its_tool_calls_name_is_redacted(mask: Any) -> None:
    messages = [
        _calls(("d", "read_file")),
        {"role": "tool", "tool_call_id": "d", "name": "explain_sql", "content": _kept_payload()},
    ]

    assert SENTINEL not in str(mask(data=messages))


def test_a_consistent_safe_tool_name_is_still_kept(mask: Any) -> None:
    messages = [
        _calls(("d", "explain_sql")),
        {"role": "tool", "tool_call_id": "d", "name": "explain_sql", "content": _kept_payload()},
    ]

    assert SENTINEL in str(mask(data=messages))


# --- D: SDK contract ---


def test_langfuse_sdk_exposes_the_private_hooks_the_client_patches() -> None:
    from langfuse._client import propagation
    from langfuse.langchain import CallbackHandler

    assert hasattr(CallbackHandler, "_get_error_level_and_status_message")
    assert callable(getattr(propagation, "_detach_context_token_safely", None))


def test_core_pins_the_exact_langfuse_version() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"

    assert re.search(r'"langfuse==5\.0\.4"', pyproject.read_text())


def test_importing_clients_survives_a_missing_detach_helper() -> None:
    script = (
        "import langfuse._client.propagation as p\n"
        "del p._detach_context_token_safely\n"
        "import rootcause_core.clients\n"
        "print('import-ok')\n"
    )

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )

    assert result.returncode == 0, result.stderr[-800:]
    assert "import-ok" in result.stdout
