"""Concurrent migration runs are serialised, in one process and across processes."""

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

SUBPROCESS_TIMEOUT_S = 180


def _versions(url: str) -> list[str]:
    engine = sa.create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(sa.text("SELECT version_num FROM app.alembic_version")).scalars()
            )
    finally:
        engine.dispose()


def test_concurrent_threads_both_succeed_and_leave_one_version_row(bootstrapped_url):
    from rootcause_db.migrate import upgrade

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def run() -> None:
        try:
            barrier.wait(timeout=30)
            upgrade(bootstrapped_url, "head")
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert len(_versions(bootstrapped_url)) == 1


def _cli() -> str:
    found = shutil.which("rootcause-db") or str(Path(sys.executable).parent / "rootcause-db")
    if not Path(found).exists():
        pytest.skip("rootcause-db console script is not installed")
    return found


def test_two_cli_processes_upgrading_at_once_both_succeed(bootstrapped_url):
    command = [_cli(), "upgrade", "head"]
    environment = dict(os.environ)  # db_roles already put ROOTCAUSE_* settings in os.environ

    processes = [
        subprocess.Popen(  # noqa: S603
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [p.communicate(timeout=SUBPROCESS_TIMEOUT_S) + (p.returncode,) for p in processes]

    for stdout, stderr, code in results:
        assert code == 0, stdout + stderr
    assert len(_versions(bootstrapped_url)) == 1
