"""Spawned-process evidence for exclusive data-root ownership."""

from __future__ import annotations

import multiprocessing
from multiprocessing.connection import Connection
from pathlib import Path


def _application_worker(
    data_root: str,
    legacy_root: str,
    connection: Connection,
    fail_startup: bool,
) -> None:
    from fastapi.testclient import TestClient

    import app.server as server
    from app.config import LocalDataConfig
    from app.runtime_mode import RuntimeMode

    if fail_startup:
        target = Path(data_root).resolve()
        original_mkdir = Path.mkdir

        def fail_target_root(path: Path, *args, **kwargs) -> None:
            if path.resolve() == target:
                raise RuntimeError("deliberate startup failure")
            original_mkdir(path, *args, **kwargs)

        Path.mkdir = fail_target_root

    application = server.create_app(
        Path(legacy_root),
        mode=RuntimeMode.TEST,
        data_config=LocalDataConfig(Path(data_root)),
    )
    try:
        with TestClient(application):
            connection.send(("acquired", ""))
            connection.recv()
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _root_lock_worker(data_root: str, connection: Connection) -> None:
    from app.data_root_lock import DataRootLock

    lock = DataRootLock(data_root)
    try:
        lock.acquire()
        connection.send(("acquired", ""))
        connection.recv()
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        lock.release()
        connection.close()


def _spawn(root: Path, legacy: Path, *, fail_startup: bool = False):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_application_worker,
        args=(str(root), str(legacy), child, fail_startup),
    )
    process.start()
    child.close()
    return process, parent


def _spawn_lock(root: Path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_root_lock_worker,
        args=(str(root), child),
    )
    process.start()
    child.close()
    return process, parent


def _receive(connection: Connection) -> tuple[str, str]:
    assert connection.poll(15), "spawned application did not report startup"
    return connection.recv()


def _stop(process, connection: Connection, *, request_stop: bool = True) -> None:
    if request_stop and process.is_alive():
        try:
            connection.send("stop")
        except (BrokenPipeError, EOFError, OSError):
            pass
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
    connection.close()
    assert process.exitcode == 0


def test_spawned_process_exclusivity_different_roots_and_reacquire(tmp_path):
    shared = tmp_path / "shared"
    other = tmp_path / "other"

    owner, owner_connection = _spawn_lock(shared)
    assert _receive(owner_connection)[0] == "acquired"
    assert not shared.exists()
    rejected_absent, rejected_absent_connection = _spawn(
        shared, tmp_path / "legacy-rejected-absent"
    )
    status, detail = _receive(rejected_absent_connection)
    assert status == "error"
    assert "already in use" in detail
    _stop(rejected_absent, rejected_absent_connection, request_stop=False)
    assert not shared.exists()
    _stop(owner, owner_connection)

    first, first_connection = _spawn(shared, tmp_path / "legacy-first")
    assert _receive(first_connection)[0] == "acquired"
    database = shared / "sim-intent.sqlite3"
    database_before = database.read_bytes()
    blobs_before = list((shared / "blobs").rglob("*")) if (shared / "blobs").exists() else []

    rejected, rejected_connection = _spawn(shared, tmp_path / "legacy-rejected")
    status, detail = _receive(rejected_connection)
    assert status == "error"
    assert "already in use" in detail
    _stop(rejected, rejected_connection, request_stop=False)
    assert database.read_bytes() == database_before
    assert (
        list((shared / "blobs").rglob("*")) if (shared / "blobs").exists() else []
    ) == blobs_before

    canonical_alias = shared / ".." / shared.name
    alias, alias_connection = _spawn(
        canonical_alias, tmp_path / "legacy-alias"
    )
    status, detail = _receive(alias_connection)
    assert status == "error"
    assert "already in use" in detail
    _stop(alias, alias_connection, request_stop=False)

    independent, independent_connection = _spawn(
        other, tmp_path / "legacy-independent"
    )
    assert _receive(independent_connection)[0] == "acquired"
    _stop(independent, independent_connection)
    _stop(first, first_connection)

    # File existence is not ownership. Simulate stale crash metadata in the
    # external lock directory even where normal release removes the file.
    from app.data_root_lock import DataRootLock

    stale_lock = DataRootLock(shared).path
    stale_lock.parent.mkdir(parents=True, exist_ok=True)
    stale_lock.write_text("stale metadata", encoding="utf-8")
    reacquired, reacquired_connection = _spawn(
        shared, tmp_path / "legacy-reacquired"
    )
    assert _receive(reacquired_connection)[0] == "acquired"
    _stop(reacquired, reacquired_connection)


def test_spawned_failed_startup_releases_root_lock(tmp_path):
    root = tmp_path / "failed-startup"
    failed, failed_connection = _spawn(
        root, tmp_path / "legacy-failed", fail_startup=True
    )
    status, detail = _receive(failed_connection)
    assert status == "error"
    assert "deliberate startup failure" in detail
    _stop(failed, failed_connection, request_stop=False)
    assert not (root / "sim-intent.sqlite3").exists()
    assert not (root / "blobs").exists()

    recovered, recovered_connection = _spawn(
        root, tmp_path / "legacy-recovered"
    )
    assert _receive(recovered_connection)[0] == "acquired"
    _stop(recovered, recovered_connection)
