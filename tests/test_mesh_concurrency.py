"""Cross-process request idempotency and lineage race regressions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import multiprocessing
import os
from queue import Empty
import stat
import tempfile
import uuid

import pytest
from filelock import Timeout
from sqlalchemy import func, select

from app.blob_store import (
    BlobCoordinationPathError,
    BlobCoordinationTimeoutError,
    BlobStore,
)
from app.persistence import (
    MeshRevision,
    Persistence,
    create_sqlite_engine,
)
from mesh.artifacts import canonical_quality_bytes, canonical_topology_bytes
from tests.test_mesh_persistence import (
    create,
    documents,
    parents,
    persistence,
)


def _configure_process_tmpdir(directory: str) -> None:
    os.environ["TMPDIR"] = directory
    tempfile.tempdir = None


def _join_cleanly(process, description: str) -> None:
    process.join(timeout=20)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        raise AssertionError(f"{description} did not terminate")
    assert process.exitcode == 0


def _lock_path_worker(label, blob_root, tmpdir, cwd, results):
    try:
        os.chdir(cwd)
        _configure_process_tmpdir(tmpdir)
        path = BlobStore(blob_root).coordination_lock.path
        results.put((label, "ok", str(path), tempfile.gettempdir()))
    except Exception as exc:
        results.put((label, "error", type(exc).__name__, str(exc)))


def _lock_holder_worker(
    blob_root,
    tmpdir,
    acquired,
    release_requested,
    results,
):
    try:
        _configure_process_tmpdir(tmpdir)
        lock = BlobStore(blob_root).coordination_lock
        lock.acquire()
        acquired.set()
        if not release_requested.wait(timeout=20):
            raise AssertionError("lock holder was not released")
        lock.release()
        results.put(("released", str(lock.path)))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _lock_contender_worker(
    blob_root,
    tmpdir,
    holder_acquired,
    attempting,
    entered,
    results,
):
    try:
        _configure_process_tmpdir(tmpdir)
        if not holder_acquired.wait(timeout=20):
            raise AssertionError("lock holder never acquired")
        lock = BlobStore(blob_root).coordination_lock
        attempting.set()
        lock.acquire()
        entered.set()
        lock.release()
        results.put(("acquired", str(lock.path)))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _single_lock_worker(blob_root, tmpdir, results):
    try:
        _configure_process_tmpdir(tmpdir)
        lock = BlobStore(blob_root).coordination_lock
        lock.acquire()
        lock.release()
        results.put(("acquired", str(lock.path)))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _acquire_and_release(lock):
    with lock:
        return True


def test_external_lock_acquisition_oserror_is_typed_and_releases_thread_lock(
    tmp_path,
    monkeypatch,
):
    lock = BlobStore(tmp_path / "acquisition-oserror" / "blobs").coordination_lock
    original_error = PermissionError("forced external-lock permission failure")
    entered = False

    with monkeypatch.context() as patch:

        def fail_acquisition(*_args, **_kwargs):
            raise original_error

        patch.setattr(lock._process_lock, "acquire", fail_acquisition)
        with pytest.raises(
            BlobCoordinationPathError,
            match="^CAS coordination lock path is unavailable$",
        ) as captured:
            with lock:
                entered = True

    assert not entered
    assert captured.value.__cause__ is original_error
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(_acquire_and_release, lock).result(timeout=2)


def test_external_lock_timeout_is_typed_and_releases_thread_lock(
    tmp_path,
    monkeypatch,
):
    lock = BlobStore(tmp_path / "acquisition-timeout" / "blobs").coordination_lock
    original_error = Timeout(str(lock.path))
    entered = False

    with monkeypatch.context() as patch:

        def fail_acquisition(*_args, **_kwargs):
            raise original_error

        patch.setattr(lock._process_lock, "acquire", fail_acquisition)
        with pytest.raises(
            BlobCoordinationTimeoutError,
            match="^CAS coordination lock acquisition timed out$",
        ) as captured:
            with lock:
                entered = True

    assert not entered
    assert captured.value.__cause__ is original_error
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(_acquire_and_release, lock).result(timeout=2)


def test_coordination_lock_rejects_unwritable_directory(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX directory permission enforcement is required")

    lock = BlobStore(tmp_path / "unwritable-directory" / "blobs").coordination_lock
    lock.lock_directory.mkdir(parents=True)
    directory_before = lock.lock_directory.lstat()
    original_mode = stat.S_IMODE(directory_before.st_mode)
    probe = lock.lock_directory / "permission-probe"
    entered = False
    try:
        lock.lock_directory.chmod(0o500)
        try:
            descriptor = os.open(
                probe,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except PermissionError:
            pass
        else:
            os.close(descriptor)
            probe.unlink()
            pytest.skip(
                "effective privileges bypass directory write permissions"
            )

        with pytest.raises(
            BlobCoordinationPathError,
            match="^CAS coordination lock path is unavailable$",
        ) as captured:
            with lock:
                entered = True
        assert isinstance(captured.value.__cause__, PermissionError)
        assert not entered
        assert not lock.path.exists()
    finally:
        lock.lock_directory.chmod(original_mode)

    directory_after = lock.lock_directory.lstat()
    assert (directory_after.st_dev, directory_after.st_ino) == (
        directory_before.st_dev,
        directory_before.st_ino,
    )
    assert stat.S_ISDIR(directory_after.st_mode)
    assert not lock.lock_directory.is_symlink()
    assert not lock.path.exists()


def test_coordination_lock_rejects_lock_path_occupied_by_directory(tmp_path):
    lock = BlobStore(tmp_path / "lock-as-directory" / "blobs").coordination_lock
    lock.path.mkdir(parents=True)
    directory_before = lock.path.lstat()
    entered = False

    with pytest.raises(
        BlobCoordinationPathError,
        match="^CAS coordination lock path is not a regular file$",
    ):
        with lock:
            entered = True

    directory_after = lock.path.lstat()
    assert not entered
    assert (directory_after.st_dev, directory_after.st_ino) == (
        directory_before.st_dev,
        directory_before.st_ino,
    )
    assert stat.S_ISDIR(directory_after.st_mode)
    assert not lock.path.is_symlink()


def test_coordination_lock_success_is_reentrant_and_reusable(tmp_path):
    lock = BlobStore(tmp_path / "reentrant" / "blobs").coordination_lock

    with lock:
        with lock:
            assert lock._process_lock.is_locked

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(_acquire_and_release, lock).result(timeout=2)


def test_process_shared_lock_path_is_stable_across_tmpdir_and_root_spellings(
    tmp_path,
):
    canonical_root = tmp_path / "durable" / "blobs"
    different_root = tmp_path / "different" / "blobs"
    canonical_root.mkdir(parents=True)
    different_root.mkdir(parents=True)
    symlink_root = tmp_path / "blobs-link"
    symlink_root.symlink_to(canonical_root, target_is_directory=True)
    tmp_a = tmp_path / "tmp-a"
    tmp_b = tmp_path / "tmp-b"
    cwd_a = tmp_path / "cwd-a"
    cwd_b = tmp_path / "cwd-b"
    for directory in (tmp_a, tmp_b, cwd_a, cwd_b):
        directory.mkdir()

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    specifications = (
        ("canonical", canonical_root, tmp_a, cwd_a),
        ("symlink", symlink_root, tmp_b, cwd_b),
        ("different", different_root, tmp_b, cwd_a),
    )
    processes = [
        context.Process(
            target=_lock_path_worker,
            args=(
                label,
                str(root),
                str(tmpdir),
                str(cwd),
                results,
            ),
        )
        for label, root, tmpdir, cwd in specifications
    ]
    for process in processes:
        process.start()
    try:
        output = {
            item[0]: item[1:]
            for item in (results.get(timeout=20) for _ in processes)
        }
    except Empty as exc:
        raise AssertionError("lock-path worker produced no result") from exc
    finally:
        for process in processes:
            _join_cleanly(process, "lock-path worker")

    canonical = canonical_root.resolve()
    identity = os.path.normcase(str(canonical))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    expected = canonical.parent / ".sim-intent-locks" / f"cas-{digest}.lock"
    assert output["canonical"] == ("ok", str(expected), str(tmp_a))
    assert output["symlink"] == ("ok", str(expected), str(tmp_b))
    assert output["different"][0] == "ok"
    assert output["different"][1] != str(expected)
    assert output["different"][2] == str(tmp_b)
    assert expected.parent != canonical / "sha256"


def test_different_tmpdir_processes_contend_for_same_coordination_lock(tmp_path):
    blob_root = tmp_path / "durable" / "blobs"
    tmp_a = tmp_path / "tmp-a"
    tmp_b = tmp_path / "tmp-b"
    tmp_a.mkdir(parents=True)
    tmp_b.mkdir()
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release_requested = context.Event()
    attempting = context.Event()
    entered = context.Event()
    holder_results = context.Queue()
    contender_results = context.Queue()
    holder = context.Process(
        target=_lock_holder_worker,
        args=(
            str(blob_root),
            str(tmp_a),
            acquired,
            release_requested,
            holder_results,
        ),
    )
    contender = context.Process(
        target=_lock_contender_worker,
        args=(
            str(blob_root),
            str(tmp_b),
            acquired,
            attempting,
            entered,
            contender_results,
        ),
    )
    holder.start()
    assert acquired.wait(timeout=10)
    contender.start()
    try:
        assert attempting.wait(timeout=10)
        assert not entered.wait(timeout=0.5)
        assert holder.is_alive()
        release_requested.set()
        assert entered.wait(timeout=10)
        holder_output = holder_results.get(timeout=10)
        contender_output = contender_results.get(timeout=10)
    except Empty as exc:
        raise AssertionError("contention worker produced no result") from exc
    finally:
        release_requested.set()
        for process, description in (
            (holder, "lock holder"),
            (contender, "lock contender"),
        ):
            _join_cleanly(process, description)

    assert holder_output[0] == "released"
    assert contender_output == ("acquired", holder_output[1])


@pytest.mark.parametrize("termination", ["normal", "terminated"])
def test_stale_lock_file_is_not_ownership_after_release_or_termination(
    tmp_path,
    termination,
):
    blob_root = tmp_path / "durable" / "blobs"
    tmp_a = tmp_path / "tmp-a"
    tmp_b = tmp_path / "tmp-b"
    tmp_a.mkdir(parents=True)
    tmp_b.mkdir()
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release_requested = context.Event()
    holder_results = context.Queue()
    holder = context.Process(
        target=_lock_holder_worker,
        args=(
            str(blob_root),
            str(tmp_a),
            acquired,
            release_requested,
            holder_results,
        ),
    )
    holder.start()
    assert acquired.wait(timeout=10)
    expected_path = BlobStore(blob_root).coordination_lock.path
    if termination == "normal":
        release_requested.set()
        assert holder_results.get(timeout=10) == ("released", str(expected_path))
        _join_cleanly(holder, "normally released lock holder")
    else:
        holder.terminate()
        holder.join(timeout=10)
        assert not holder.is_alive()
        assert holder.exitcode is not None and holder.exitcode != 0

    assert expected_path.is_file()
    results = context.Queue()
    successor = context.Process(
        target=_single_lock_worker,
        args=(str(blob_root), str(tmp_b), results),
    )
    successor.start()
    try:
        assert results.get(timeout=10) == ("acquired", str(expected_path))
    except Empty as exc:
        raise AssertionError("stale-lock successor produced no result") from exc
    finally:
        _join_cleanly(successor, "stale-lock successor")
    assert expected_path.is_file()


@pytest.mark.parametrize(
    "hazard",
    ["directory_symlink", "directory_file", "lock_symlink"],
)
def test_coordination_lock_rejects_path_type_hazards(tmp_path, hazard):
    blob_root = tmp_path / hazard / "blobs"
    lock = BlobStore(blob_root).coordination_lock
    outside = tmp_path / f"{hazard}-outside"
    if hazard == "directory_symlink":
        outside.mkdir()
        lock.lock_directory.parent.mkdir(parents=True)
        lock.lock_directory.symlink_to(outside, target_is_directory=True)
    elif hazard == "directory_file":
        lock.lock_directory.parent.mkdir(parents=True)
        lock.lock_directory.write_text("not a directory", encoding="utf-8")
    else:
        lock.lock_directory.mkdir(parents=True)
        outside.write_text("not a lock", encoding="utf-8")
        lock.path.symlink_to(outside)

    with pytest.raises(BlobCoordinationPathError):
        lock.acquire()


def _race_worker(
    database_url,
    blob_root,
    identifiers,
    request_id,
    predecessor,
    top,
    report,
    barrier,
    results,
):
    store = Persistence(
        create_sqlite_engine(database_url),
        BlobStore(blob_root),
    )
    try:
        # Synchronize before lock acquisition.  Once both calls are ready, the
        # process-shared CAS lock intentionally serializes their full critical
        # sequences.
        barrier.wait(timeout=20)
        record = store.create_mesh_revision(
            project_id=identifiers["project_id"],
            model_id=identifiers["model_id"],
            model_version_id=identifiers["model_version_id"],
            setup_id=identifiers["setup_id"],
            setup_revision_id=identifiers["setup_revision_id"],
            predecessor_mesh_revision_id=predecessor,
            request_id=request_id,
            topology=top,
            quality=report,
        )
        results.put(("ok", record.id))
    except Exception as exc:
        results.put(
            (
                "error",
                type(exc).__name__,
                getattr(exc, "code", str(exc)),
            )
        )
    finally:
        store.dispose()


def _identifiers(ids):
    project, model, version, setup, revision = ids
    return {
        "project_id": project.id,
        "model_id": model.id,
        "model_version_id": version.id,
        "setup_id": setup.id,
        "setup_revision_id": revision.id,
    }


def _run_race(store, ids, calls):
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(len(calls))
    results = context.Queue()
    processes = [
        context.Process(
            target=_race_worker,
            args=(
                str(store.engine.url),
                str(store.blobs.root),
                _identifiers(ids),
                call["request_id"],
                call.get("predecessor"),
                call["topology"],
                call["quality"],
                barrier,
                results,
            ),
        )
        for call in calls
    ]
    for process in processes:
        process.start()
    output = []
    try:
        for _ in processes:
            output.append(results.get(timeout=30))
    except Empty as exc:
        raise AssertionError("concurrent mesh worker produced no result") from exc
    finally:
        for process in processes:
            process.join(timeout=30)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                raise AssertionError("concurrent mesh worker did not terminate")
            assert process.exitcode == 0
    return output


def test_concurrent_identical_requests_replay_the_winner(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    mesh_id = str(uuid.uuid4())
    top, report = documents(ids, mesh_id=mesh_id)
    output = _run_race(
        store,
        ids,
        [
            {
                "request_id": "same-request",
                "topology": top,
                "quality": report,
            },
            {
                "request_id": "same-request",
                "topology": top,
                "quality": report,
            },
        ],
    )
    assert sorted(output) == [("ok", mesh_id), ("ok", mesh_id)]
    with store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MeshRevision)) == 1


def test_concurrent_same_request_with_different_content_is_typed(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    first_top, first_report = documents(
        ids, mesh_id=str(uuid.uuid4()), settings_hash="b" * 64
    )
    second_top, second_report = documents(
        ids, mesh_id=str(uuid.uuid4()), settings_hash="c" * 64
    )
    output = _run_race(
        store,
        ids,
        [
            {
                "request_id": "colliding-request",
                "topology": first_top,
                "quality": first_report,
            },
            {
                "request_id": "colliding-request",
                "topology": second_top,
                "quality": second_report,
            },
        ],
    )
    assert sum(item[0] == "ok" for item in output) == 1
    assert [
        item[1:]
        for item in output
        if item[0] == "error"
    ] == [("MeshRequestConflictError", "request_id_conflict")]
    with store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MeshRevision)) == 1


def test_concurrent_different_requests_cannot_branch_one_predecessor(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    predecessor = create(store, ids, request_id="predecessor")
    first_top, first_report = documents(
        ids, mesh_id=str(uuid.uuid4()), settings_hash="c" * 64
    )
    second_top, second_report = documents(
        ids, mesh_id=str(uuid.uuid4()), settings_hash="d" * 64
    )
    output = _run_race(
        store,
        ids,
        [
            {
                "request_id": "successor-a",
                "predecessor": predecessor.id,
                "topology": first_top,
                "quality": first_report,
            },
            {
                "request_id": "successor-b",
                "predecessor": predecessor.id,
                "topology": second_top,
                "quality": second_report,
            },
        ],
    )
    assert sum(item[0] == "ok" for item in output) == 1
    assert [
        item[1:]
        for item in output
        if item[0] == "error"
    ] == [("MeshLineageConflictError", "mesh_lineage_conflict")]
    with store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MeshRevision)) == 2


def test_replay_survives_persistence_restart(tmp_path):
    store = persistence(tmp_path)
    ids = parents(store)
    mesh_id = str(uuid.uuid4())
    created = create(store, ids, mesh_id=mesh_id)
    restarted = Persistence(
        create_sqlite_engine(str(store.engine.url)),
        BlobStore(store.blobs.root),
    )
    replayed = create(restarted, ids, mesh_id=mesh_id)
    assert replayed.id == created.id


class ForcedMeshCreationError(RuntimeError):
    pass


def _failing_cleanup_race_worker(
    database_url,
    blob_root,
    worker_tmpdir,
    identifiers,
    top,
    report,
    cleanup_started,
    success_attempting,
    success_entered,
    success_committed,
    results,
):
    _configure_process_tmpdir(worker_tmpdir)
    store = Persistence(create_sqlite_engine(database_url), BlobStore(blob_root))
    original_publish = store.blobs.publish_with_status
    original_cleanup = store._cleanup_failed_mesh_publication
    publication_count = 0
    cleanup_count = 0

    def counted_publish(content, digest):
        nonlocal publication_count
        published = original_publish(content, digest)
        publication_count += 1
        return published

    def coordinated_cleanup(**kwargs):
        nonlocal cleanup_count
        cleanup_count += 1
        if cleanup_count == 1:
            cleanup_started.set()
            if not success_attempting.wait(timeout=20):
                raise AssertionError("successful creator never attempted race")
            if success_entered.wait(timeout=5.0) and not success_committed.wait(
                timeout=20
            ):
                raise AssertionError("successful creator entered but did not commit")
        return original_cleanup(**kwargs)

    @contextmanager
    def forced_transaction_failure():
        raise ForcedMeshCreationError("forced mesh database failure")
        yield

    store.blobs.publish_with_status = counted_publish
    store._cleanup_failed_mesh_publication = coordinated_cleanup
    store.transaction = forced_transaction_failure
    try:
        store.create_mesh_revision(
            project_id=identifiers["project_id"],
            model_id=identifiers["model_id"],
            model_version_id=identifiers["model_version_id"],
            setup_id=identifiers["setup_id"],
            setup_revision_id=identifiers["setup_revision_id"],
            predecessor_mesh_revision_id=None,
            request_id="forced-failing-request",
            topology=top,
            quality=report,
        )
        results.put(("unexpected-success", publication_count, cleanup_count))
    except Exception as exc:
        results.put(
            (
                "error",
                type(exc).__name__,
                str(exc),
                publication_count,
                cleanup_count,
                str(store.blobs.coordination_lock.path),
            )
        )
    finally:
        store.dispose()


def _successful_cleanup_race_worker(
    database_url,
    blob_root,
    worker_tmpdir,
    identifiers,
    top,
    report,
    cleanup_started,
    success_attempting,
    success_entered,
    success_committed,
    results,
):
    _configure_process_tmpdir(worker_tmpdir)
    store = Persistence(create_sqlite_engine(database_url), BlobStore(blob_root))
    try:
        if not cleanup_started.wait(timeout=20):
            raise AssertionError("failing creator never attempted cleanup")
        original_acquire = store.blobs.coordination_lock.acquire

        def signal_lock_attempt():
            success_attempting.set()
            original_acquire()
            success_entered.set()

        # Signal from inside the lock-acquisition call, not merely before mesh
        # validation, so the failing process cleans while this process is
        # deterministically attempting to enter the protected sequence.
        store.blobs.coordination_lock.acquire = signal_lock_attempt
        record = store.create_mesh_revision(
            project_id=identifiers["project_id"],
            model_id=identifiers["model_id"],
            model_version_id=identifiers["model_version_id"],
            setup_id=identifiers["setup_id"],
            setup_revision_id=identifiers["setup_revision_id"],
            predecessor_mesh_revision_id=None,
            request_id="forced-successful-request",
            topology=top,
            quality=report,
        )
        success_committed.set()
        results.put(
            (
                "ok",
                record.id,
                record.topology_sha256,
                record.quality_sha256,
                str(store.blobs.coordination_lock.path),
            )
        )
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        store.dispose()


def test_cross_process_failed_cleanup_cannot_delete_committed_mesh_artifacts(
    tmp_path,
):
    store = persistence(tmp_path)
    ids = parents(store)
    mesh_id = str(uuid.uuid4())
    top, report = documents(ids, mesh_id=mesh_id)
    top_digest = hashlib.sha256(canonical_topology_bytes(top)).hexdigest()
    report_digest = hashlib.sha256(canonical_quality_bytes(report)).hexdigest()
    context = multiprocessing.get_context("spawn")
    cleanup_started = context.Event()
    success_attempting = context.Event()
    success_entered = context.Event()
    success_committed = context.Event()
    failing_tmpdir = tmp_path / "failing-tmp"
    successful_tmpdir = tmp_path / "successful-tmp"
    failing_tmpdir.mkdir()
    successful_tmpdir.mkdir()
    failing_results = context.Queue()
    successful_results = context.Queue()
    common = (
        str(store.engine.url),
        str(store.blobs.root),
    )
    coordination = (
        cleanup_started,
        success_attempting,
        success_entered,
        success_committed,
    )
    failing = context.Process(
        target=_failing_cleanup_race_worker,
        args=(
            *common,
            str(failing_tmpdir),
            _identifiers(ids),
            top,
            report,
            *coordination,
            failing_results,
        ),
    )
    successful = context.Process(
        target=_successful_cleanup_race_worker,
        args=(
            *common,
            str(successful_tmpdir),
            _identifiers(ids),
            top,
            report,
            *coordination,
            successful_results,
        ),
    )
    failing.start()
    successful.start()
    try:
        failing_output = failing_results.get(timeout=30)
        successful_output = successful_results.get(timeout=30)
    except Empty as exc:
        raise AssertionError("forced cleanup race produced no result") from exc
    finally:
        for process in (failing, successful):
            process.join(timeout=30)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                raise AssertionError("forced cleanup race worker did not terminate")
            assert process.exitcode == 0

    assert failing_output == (
        "error",
        "ForcedMeshCreationError",
        "forced mesh database failure",
        2,
        2,
        str(store.blobs.coordination_lock.path),
    )
    assert successful_output == (
        "ok",
        mesh_id,
        top_digest,
        report_digest,
        str(store.blobs.coordination_lock.path),
    )
    with store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MeshRevision)) == 1
    project, model, version, setup, revision = ids
    record, top_bytes, report_bytes = store.read_mesh_revision(
        mesh_id,
        project_id=project.id,
        model_id=model.id,
        model_version_id=version.id,
        setup_id=setup.id,
        setup_revision_id=revision.id,
    )
    top_path = store.blobs.path_for_key(record.topology_artifact_key)
    report_path = store.blobs.path_for_key(record.quality_artifact_key)
    assert top_path.is_file() and report_path.is_file()
    assert store.blobs.read(
        record.topology_artifact_key,
        record.topology_sha256,
        record.topology_size_bytes,
    ) == top_bytes
    assert store.blobs.read(
        record.quality_artifact_key,
        record.quality_sha256,
        record.quality_size_bytes,
    ) == report_bytes
