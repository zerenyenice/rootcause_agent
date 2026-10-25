"""GET /version reports the package version and git sha."""

from collections.abc import Callable
from typing import Any

import pytest


@pytest.mark.unit
def test_version_defaults_git_sha_to_unknown(make_client: Callable[..., Any]) -> None:
    import rootcause_api

    client = make_client()

    response = client.get("/version")

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["version"] == rootcause_api.__version__
    assert body["data"]["git_sha"] == "unknown"


@pytest.mark.unit
def test_version_uses_git_sha_from_environment(
    make_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROOTCAUSE_GIT_SHA", "abc1234")
    client = make_client()

    body = client.get("/version").json()

    assert body["data"]["git_sha"] == "abc1234"
