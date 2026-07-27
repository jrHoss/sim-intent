"""Verified, atomic SHA-256 content-addressed local blob storage."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterator

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENTRY_RACE_ERRORS = (
    FileNotFoundError,
    NotADirectoryError,
    IsADirectoryError,
    PermissionError,
)
_LOCKS_GUARD = threading.Lock()
_STORAGE_LOCKS: dict[str, threading.RLock] = {}


class BlobIntegrityError(RuntimeError):
    pass


class SourceStorageLimitExceededError(RuntimeError):
    pass


class BlobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        lock_key = os.path.normcase(str(self.root))
        with _LOCKS_GUARD:
            self.coordination_lock = _STORAGE_LOCKS.setdefault(
                lock_key, threading.RLock()
            )

    @staticmethod
    def digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def key(self, digest: str) -> str:
        self._validate_digest(digest)
        return f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"

    def path_for_key(self, key: str) -> Path:
        parts = Path(key).parts
        if len(parts) != 4 or parts[0] != "sha256":
            raise BlobIntegrityError("invalid blob key")
        digest = parts[-1]
        self._validate_digest(digest)
        if parts[1:3] != (digest[:2], digest[2:4]):
            raise BlobIntegrityError("blob key does not match its digest")
        candidate = self.root.joinpath(*parts)
        if self.root.resolve() not in candidate.resolve().parents:
            raise BlobIntegrityError("blob key escapes storage root")
        return candidate

    def publish(self, content: bytes, expected_digest: str) -> str:
        self._validate_digest(expected_digest)
        if self.digest(content) != expected_digest:
            raise BlobIntegrityError("content does not match expected SHA-256")
        key = self.key(expected_digest)
        final = self.path_for_key(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            self._verify_path(final, expected_digest)
            return key

        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=final.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._verify_path(temporary, expected_digest)
            try:
                os.replace(temporary, final)
            except OSError:
                if not final.exists():
                    raise
            self._verify_path(final, expected_digest)
            return key
        finally:
            temporary.unlink(missing_ok=True)

    def publish_file(
        self, source: str | Path, expected_digest: str, expected_size: int
    ) -> str:
        """Publish an already-quarantined file without loading it into memory."""
        self._validate_digest(expected_digest)
        source_path = Path(source)
        if source_path.stat(follow_symlinks=False).st_size != expected_size:
            raise BlobIntegrityError("source size changed before publication")
        self._verify_path(source_path, expected_digest)
        key = self.key(expected_digest)
        final = self.path_for_key(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            self._verify_path(final, expected_digest)
            return key
        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=final.parent)
        temporary = Path(temporary_name)
        try:
            with source_path.open("rb") as source_stream, os.fdopen(fd, "wb") as target:
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            self._verify_path(temporary, expected_digest)
            try:
                os.replace(temporary, final)
            except OSError:
                if not final.exists():
                    raise
            self._verify_path(final, expected_digest)
            return key
        finally:
            temporary.unlink(missing_ok=True)

    def publish_file_with_limit(
        self, source: str | Path, expected_digest: str, expected_size: int,
        maximum_total_bytes: int,
    ) -> str:
        """Publish under the caller-held coordination lock without overcommit."""
        final = self.path_for_key(self.key(expected_digest))
        if final.exists():
            self._verify_path(final, expected_digest)
            return self.key(expected_digest)
        if expected_size > maximum_total_bytes - self.source_bytes():
            raise SourceStorageLimitExceededError("source storage capacity exceeded")
        return self.publish_file(source, expected_digest, expected_size)

    def source_bytes(self) -> int:
        """Count only fixed-layout regular CAS leaves, never following symlinks."""
        total = 0
        for path in self.iter_final_blobs():
            try:
                size = path.stat(follow_symlinks=False).st_size
                self._verify_path(path, path.name)
            except OSError:
                continue
            except BlobIntegrityError:
                continue
            total += size
        return total

    def read(self, key: str, expected_digest: str, expected_size: int) -> bytes:
        path = self.path_for_key(key)
        content = path.read_bytes()
        if len(content) != expected_size or self.digest(content) != expected_digest:
            raise BlobIntegrityError("stored blob failed size or SHA-256 verification")
        return content

    def cleanup_temporary(
        self, *, older_than_seconds: float = 3600, limit: int = 100
    ) -> int:
        """Remove only bounded stale publication temporaries."""
        if limit <= 0 or not self.root.is_dir():
            return 0
        cutoff = time.time() - older_than_seconds
        removed = 0
        with self.coordination_lock:
            for path in self._iter_leaf_files(temporary_only=True):
                if removed >= limit:
                    break
                try:
                    if path.stat(follow_symlinks=False).st_mtime <= cutoff:
                        path.unlink(missing_ok=True)
                        removed += 1
                except ENTRY_RACE_ERRORS:
                    continue
        return removed

    def iter_final_blobs(self) -> Iterator[Path]:
        yield from self._iter_leaf_files(temporary_only=False)

    def _iter_leaf_files(self, *, temporary_only: bool) -> Iterator[Path]:
        """Walk the fixed CAS depth without following symlinks."""
        sha_root = self.root / "sha256"
        if not sha_root.is_dir() or sha_root.is_symlink():
            return
        try:
            first_level = list(os.scandir(sha_root))
        except OSError:
            return
        for first in first_level:
            try:
                first_is_directory = first.is_dir(follow_symlinks=False)
            except ENTRY_RACE_ERRORS:
                continue
            if not first_is_directory:
                continue
            try:
                second_level = list(os.scandir(first.path))
            except OSError:
                continue
            for second in second_level:
                try:
                    second_is_directory = second.is_dir(follow_symlinks=False)
                except ENTRY_RACE_ERRORS:
                    continue
                if not second_is_directory:
                    continue
                try:
                    leaves = list(os.scandir(second.path))
                except OSError:
                    continue
                for leaf in leaves:
                    try:
                        leaf_is_file = leaf.is_file(follow_symlinks=False)
                    except ENTRY_RACE_ERRORS:
                        continue
                    if not leaf_is_file:
                        continue
                    is_temporary = leaf.name.startswith(".upload-")
                    if is_temporary != temporary_only:
                        continue
                    if not temporary_only and (
                        SHA256_RE.fullmatch(leaf.name) is None
                        or first.name != leaf.name[:2]
                        or second.name != leaf.name[2:4]
                    ):
                        continue
                    yield Path(leaf.path)

    @staticmethod
    def _validate_digest(digest: str) -> None:
        if not SHA256_RE.fullmatch(digest):
            raise BlobIntegrityError("invalid SHA-256 digest")

    @staticmethod
    def _verify_path(path: Path, expected_digest: str) -> None:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_digest:
            raise BlobIntegrityError("stored blob failed SHA-256 verification")
