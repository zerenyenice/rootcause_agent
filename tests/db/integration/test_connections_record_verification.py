"""``SqlConnectionRepository.record_verification``: a probe result is only recorded while
the connection's login is unchanged (conditional UPDATE on the real table)."""

import asyncio
import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from rootcause.connections.repository import NewConnection, SqlConnectionRepository

pytestmark = pytest.mark.integration

VERIFIED_AT = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def repository(migrated_roles: Any) -> Iterator[SqlConnectionRepository]:
    url = migrated_roles.app_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    repo = SqlConnectionRepository(create_async_engine(url))
    yield repo
    asyncio.run(repo.dispose())


def _new() -> NewConnection:
    return NewConnection(
        name="orders",
        kind="postgres",
        host="db.internal",
        port=5432,
        database="orders",
        user="reader",
        sslmode="require",
        encrypted_secret=b"cipher-1",
    )


def test_unchanged_login_is_recorded_without_touching_updated_at(
    repository: SqlConnectionRepository,
) -> None:
    async def scenario() -> tuple[bool, Any]:
        probed = await repository.create(_new())
        recorded = await repository.record_verification(probed, VERIFIED_AT)
        return recorded, (probed, await repository.get(probed.id))

    recorded, (probed, stored) = asyncio.run(scenario())

    assert recorded is True
    assert stored is not None
    assert stored.read_only_verified_at == VERIFIED_AT
    assert stored.updated_at == probed.updated_at


def test_rename_only_change_still_records(repository: SqlConnectionRepository) -> None:
    async def scenario() -> tuple[bool, Any]:
        probed = await repository.create(_new())
        await repository.update(probed.id, {"name": "renamed"})
        recorded = await repository.record_verification(probed, VERIFIED_AT)
        return recorded, await repository.get(probed.id)

    recorded, stored = asyncio.run(scenario())

    assert recorded is True
    assert stored is not None
    assert stored.name == "renamed"
    assert stored.read_only_verified_at == VERIFIED_AT


@pytest.mark.parametrize("change", [{"encrypted_secret": b"cipher-2"}, {"host": "other.internal"}])
def test_changed_login_is_not_recorded(
    repository: SqlConnectionRepository, change: dict[str, Any]
) -> None:
    async def scenario() -> tuple[bool, Any]:
        probed = await repository.create(_new())
        await repository.update(probed.id, change)
        recorded = await repository.record_verification(probed, VERIFIED_AT)
        return recorded, await repository.get(probed.id)

    recorded, stored = asyncio.run(scenario())

    assert recorded is False
    assert stored is not None
    assert stored.read_only_verified_at is None


def test_missing_connection_is_not_recorded(repository: SqlConnectionRepository) -> None:
    async def scenario() -> bool:
        probed = await repository.create(_new())
        await repository.delete(probed.id)
        return await repository.record_verification(dataclasses.replace(probed), VERIFIED_AT)

    assert asyncio.run(scenario()) is False
