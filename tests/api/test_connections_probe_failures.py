"""``POST /connections/{id}/test`` failure modes found in review (RED).

MEDIUM: when opening the Postgres executor fails after the login (the catalog
load times out or the server rejects it), ``/test`` still answers 200 with the
four probe fields, ``read_only`` false and a generic ``error``; nothing from the
driver reaches the body, and an earlier ``read_only_verified_at`` is cleared.

MEDIUM (race): when the connection's login changes (PATCH) while a probe runs,
the probe's outcome belongs to the old login and must NOT set
``read_only_verified_at`` on the changed connection.
"""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

PATH = "/api/v1/connections"
PROBE_FIELDS = {"reachable", "read_only", "server_version", "error"}
LEAKY_DETAIL = "relation SENTINEL_hidden_table does not exist"
NEW_PASSWORD = "SENTINEL-new-pw-456"  # gitleaks:allow


def _create(client: TestClient, body: dict[str, Any]) -> str:
    response = client.post(PATH, json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def _verified_at(repository: Any, connection_id: str) -> datetime | None:
    record = asyncio.run(repository.get(uuid.UUID(connection_id)))
    assert record is not None
    verified: datetime | None = record.read_only_verified_at
    return verified


def _mark_verified(repository: Any, connection_id: str) -> None:
    asyncio.run(
        repository.update(uuid.UUID(connection_id), {"read_only_verified_at": datetime.now(UTC)})
    )


def _open_failures() -> list[Any]:
    from rootcause.connections.postgres_executor import QueryFailed, QueryTimeout

    return [
        pytest.param(lambda: QueryTimeout(15.0), id="catalog-timeout"),
        pytest.param(lambda: QueryFailed("UndefinedTable", LEAKY_DETAIL), id="catalog-failed"),
        pytest.param(lambda: QueryFailed("InsufficientPrivilege"), id="catalog-denied"),
    ]


@pytest.mark.parametrize("make_error", _open_failures())
def test_test_reports_a_generic_not_read_only_outcome_when_opening_the_executor_fails(
    connections_client: TestClient,
    connection_repository: Any,
    postgres_body: Callable[..., dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_error: Callable[[], Exception],
) -> None:
    import rootcause.connections.probe as probe_module

    calls: list[dict[str, Any]] = []

    def failing_executor(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise make_error()

    monkeypatch.setattr(probe_module, "PostgresExecutor", failing_executor)
    connection_id = _create(connections_client, postgres_body())
    _mark_verified(connection_repository, connection_id)
    capsys.readouterr()

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert calls, "the probe did not open the executor through PostgresExecutor"
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data) == PROBE_FIELDS
    assert data["read_only"] is False
    assert isinstance(data["error"], str)
    assert data["error"]
    captured = capsys.readouterr()
    for text in (response.text, captured.out + captured.err):
        assert "SENTINEL" not in text
    assert _verified_at(connection_repository, connection_id) is None


def test_probe_result_is_not_recorded_when_the_login_changes_mid_probe(
    connections_client: TestClient,
    connection_repository: Any,
    postgres_body: Callable[..., dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rootcause.api.routes.connections as routes
    from rootcause.connections.probe import ProbeOutcome

    connection_id = _create(connections_client, postgres_body())
    patches: list[int] = []

    def probe_racing_a_patch(record: Any, cipher: Any) -> ProbeOutcome:
        # Runs in the worker thread: the login changes while the old one is probed.
        response = connections_client.patch(
            f"{PATH}/{connection_id}", json={"password": NEW_PASSWORD}
        )
        patches.append(response.status_code)
        return ProbeOutcome(reachable=True, read_only=True, server_version="16.4", error=None)

    monkeypatch.setattr(routes, "probe", probe_racing_a_patch)

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert patches == [200], "the fake probe did not run or the PATCH failed"
    assert response.status_code == 200, response.text
    assert _verified_at(connection_repository, connection_id) is None


def test_probe_result_is_recorded_when_nothing_changes_mid_probe(
    connections_client: TestClient,
    connection_repository: Any,
    postgres_body: Callable[..., dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rootcause.api.routes.connections as routes
    from rootcause.connections.probe import ProbeOutcome

    connection_id = _create(connections_client, postgres_body())
    probed: list[str] = []

    def read_only_probe(record: Any, cipher: Any) -> ProbeOutcome:
        probed.append(str(record.id))
        return ProbeOutcome(reachable=True, read_only=True, server_version="16.4", error=None)

    monkeypatch.setattr(routes, "probe", read_only_probe)

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert probed == [connection_id]
    assert response.status_code == 200, response.text
    assert _verified_at(connection_repository, connection_id) is not None


def test_renaming_mid_probe_does_not_discard_the_probe_result(
    connections_client: TestClient,
    connection_repository: Any,
    postgres_body: Callable[..., dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rootcause.api.routes.connections as routes
    from rootcause.connections.probe import ProbeOutcome

    connection_id = _create(connections_client, postgres_body())
    patches: list[int] = []

    def probe_racing_a_rename(record: Any, cipher: Any) -> ProbeOutcome:
        response = connections_client.patch(f"{PATH}/{connection_id}", json={"name": "renamed"})
        patches.append(response.status_code)
        return ProbeOutcome(reachable=True, read_only=True, server_version="16.4", error=None)

    monkeypatch.setattr(routes, "probe", probe_racing_a_rename)

    response = connections_client.post(f"{PATH}/{connection_id}/test")

    assert patches == [200]
    assert response.status_code == 200, response.text
    assert _verified_at(connection_repository, connection_id) is not None
