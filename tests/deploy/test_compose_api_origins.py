"""The API service's CORS origins are configurable from deploy/.env."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / "deploy" / ".env.example"


def test_env_example_documents_api_allowed_origins() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    match = re.search(r"^ROOTCAUSE_API_ALLOWED_ORIGINS=(.*)$", text, re.MULTILINE)

    assert match, "ROOTCAUSE_API_ALLOWED_ORIGINS must be set in deploy/.env.example"
    assert match.group(1) == '["http://localhost:3000"]'
