"""One lock per resolved DuckDB file around the short-lived opens of the probe module."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from rootcause.connections import probe as probe_module
from rootcause.connections.probe import introspect, probe
from rootcause.connections.repository import ConnectionRecord

pytestmark = pytest.mark.unit

THREADS = 8


@pytest.fixture(autouse=True)
def clean_lock_map() -> Iterator[None]:
    probe_module._FILE_LOCKS.clear()  # noqa: SLF001
    yield
    probe_module._FILE_LOCKS.clear()  # noqa: SLF001


def _warehouse(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE plants (plant_id VARCHAR, plant_name VARCHAR)")
        con.execute("INSERT INTO plants VALUES ('P01','Alpha')")
    finally:
        con.close()
    return path


def _record(path: str) -> ConnectionRecord:
    import uuid

    now = datetime.now(UTC)
    return ConnectionRecord(
        id=uuid.uuid4(),
        name="d",
        kind="duckdb",
        host=None,
        port=None,
        database=None,
        user=None,
        sslmode=None,
        path=path,
        encrypted_secret=None,
        read_only_verified_at=None,
        created_at=now,
        updated_at=now,
    )


def _in_threads(work: list[object]) -> list[object]:
    """Run each callable in its own thread released together; collect results or errors."""
    barrier = threading.Barrier(len(work))
    results: list[object] = [None] * len(work)

    def runner(index: int, job: object) -> None:
        barrier.wait()
        try:
            results[index] = job()  # type: ignore[operator]
        except BaseException as error:  # noqa: BLE001
            results[index] = error

    threads = [threading.Thread(target=runner, args=(i, j)) for i, j in enumerate(work)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    return results


def test_concurrent_probes_of_one_file_all_succeed(tmp_path: Path) -> None:
    record = _record(str(_warehouse(tmp_path / "w.duckdb")))

    outcomes = _in_threads([lambda: probe(record, None)] * THREADS)

    assert all(getattr(o, "reachable", False) and getattr(o, "read_only", False) for o in outcomes)


def test_concurrent_probe_and_introspect_of_one_file_both_succeed(tmp_path: Path) -> None:
    record = _record(str(_warehouse(tmp_path / "w.duckdb")))

    outcomes = _in_threads(
        [
            lambda: probe(record, None),
            lambda: introspect(record, None, max_tables=10),
            lambda: probe(record, None),
            lambda: introspect(record, None, max_tables=10),
        ]
    )

    assert not any(isinstance(o, BaseException) for o in outcomes), outcomes
    assert getattr(outcomes[0], "reachable", False)
    assert [t.name for t in outcomes[1].schemas[0].tables] == ["plants"]  # type: ignore[attr-defined]


def test_the_same_file_under_different_spellings_shares_one_lock(tmp_path: Path) -> None:
    path = _warehouse(tmp_path / "w.duckdb")
    (tmp_path / "sub").mkdir()
    indirect = tmp_path / "sub" / ".." / "w.duckdb"

    assert probe_module._file_lock(_record(str(path))) is probe_module._file_lock(  # noqa: SLF001
        _record(str(indirect))
    )
    assert len(probe_module._FILE_LOCKS) == 1  # noqa: SLF001


def test_different_files_get_different_locks(tmp_path: Path) -> None:
    one = _record(str(_warehouse(tmp_path / "a.duckdb")))
    two = _record(str(_warehouse(tmp_path / "b.duckdb")))

    lock_one = probe_module._file_lock(one)  # noqa: SLF001
    lock_two = probe_module._file_lock(two)

    assert lock_one is not lock_two
    assert len(probe_module._FILE_LOCKS) == 2  # noqa: SLF001
    with lock_one:  # holding one file's lock does not block the other's
        assert lock_two.acquire(blocking=False)
        lock_two.release()


def test_probing_registers_a_lock_per_resolved_path_and_reuses_it(tmp_path: Path) -> None:
    one = _record(str(_warehouse(tmp_path / "a.duckdb")))
    two = _record(str(_warehouse(tmp_path / "b.duckdb")))

    probe(one, None)
    probe(one, None)
    probe(two, None)

    assert sorted(probe_module._FILE_LOCKS) == sorted(  # noqa: SLF001
        str(Path(r.path or "").resolve()) for r in (one, two)
    )


def test_the_lock_is_released_after_a_failed_open(tmp_path: Path) -> None:
    missing = _record(str(tmp_path / "missing.duckdb"))

    outcome = probe(missing, None)

    assert outcome.reachable is False
    lock = probe_module._file_lock(missing)  # noqa: SLF001
    assert lock.acquire(blocking=False)
    lock.release()
