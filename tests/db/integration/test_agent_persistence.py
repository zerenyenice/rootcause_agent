"""Agent persistence against real Postgres: setup as migrator/owner, run as the app role."""

import asyncio
from typing import Any

import psycopg
import pytest
from langgraph.checkpoint.base import empty_checkpoint

from rootcause.agent_service.persistence import open_persistence, setup_persistence
from rootcause.errors import ConfigurationError
from rootcause.settings import Settings, clear_settings_cache

pytestmark = pytest.mark.integration

LANGGRAPH_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "store")


def _settings(migrated_roles: Any) -> Settings:
    clear_settings_cache()
    return Settings()  # ROOTCAUSE_* set by the db_roles fixture


def test_open_without_setup_fails_naming_the_setup_command(migrated_roles: Any) -> None:
    async def scenario() -> None:
        async with open_persistence(_settings(migrated_roles)):
            pytest.fail("tables were never created")

    with pytest.raises(ConfigurationError, match="python -m rootcause.agent_service.persistence"):
        asyncio.run(scenario())


def test_setup_as_owner_then_app_role_writes_and_reads_a_checkpoint(migrated_roles: Any) -> None:
    settings = _settings(migrated_roles)
    config: Any = {"configurable": {"thread_id": "t-1", "checkpoint_ns": ""}}

    async def scenario() -> tuple[Any, Any]:
        await setup_persistence(settings)
        await setup_persistence(settings)  # idempotent
        async with open_persistence(settings) as persistence:
            checkpoint = empty_checkpoint()
            await persistence.checkpointer.aput(
                config, checkpoint, {"source": "input", "step": 0, "parents": {}}, {}
            )
            await persistence.store.aput(("memories", "u1"), "k", {"note": "kök neden 🔧"})
            return (
                await persistence.checkpointer.aget_tuple(config),
                await persistence.store.aget(("memories", "u1"), "k"),
            )

    stored, item = asyncio.run(scenario())

    assert stored is not None
    assert stored.config["configurable"]["thread_id"] == "t-1"
    assert item is not None and item.value == {"note": "kök neden 🔧"}


def test_tables_are_owned_by_the_owner_role_in_the_app_schema(migrated_roles: Any) -> None:
    asyncio.run(setup_persistence(_settings(migrated_roles)))

    with psycopg.connect(migrated_roles.admin_url) as connection:
        rows = connection.execute(
            "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'app' "
            "AND tablename = ANY(%s)",
            (list(LANGGRAPH_TABLES),),
        ).fetchall()

    assert {name for name, _ in rows} == set(LANGGRAPH_TABLES)
    assert {owner for _, owner in rows} == {migrated_roles.owner}


def test_app_role_cannot_create_the_tables_itself(migrated_roles: Any) -> None:
    with (
        psycopg.connect(migrated_roles.app_url, autocommit=True) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("CREATE TABLE app.rogue (id int)")
