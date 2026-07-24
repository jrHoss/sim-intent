"""Versioned evaluation-corpus records (Task 19).

Two families live here:

``evaluation_case``
    The frozen 15-case corpus in ``eval/cases/*.json``.  Each file declares an
    ``EvaluationCase`` record version.  The case bodies contain deliberately
    *partial* IR fragments (``expected_ir_subset``,
    ``expected_structured_ir_subset``); those are never treated as
    ``SimulationIntent`` payloads and are never stamped.

``replay_record``
    The sanitized typed interpreter responses in ``eval/replay/*.json``.  Those
    bodies are the strict ``Interpretation`` LLM wire contract, so Task 19
    decision D-1 keeps them byte-unchanged and declares their version once, in
    the sidecar manifest ``eval/replay/manifest.json``.  The manifest document
    and the record bodies it lists are versioned together by construction.

This module imports no evaluation models, which keeps ``eval.schema`` free to
build its typed loader on top of the registry without an import cycle.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final, Mapping

from ir.schema_version import (
    EVALUATION_CASE_MINIMUM_SUPPORTED_VERSION,
    EVALUATION_CASE_SCHEMA_VERSION,
    REPLAY_RECORD_MINIMUM_SUPPORTED_VERSION,
    REPLAY_RECORD_SCHEMA_VERSION,
    SCHEMA_VERSION_FIELD,
)
from ir.versioning import (
    MigrationRegistry,
    PayloadStructureError,
    decode_json_object,
)


REPLAY_MANIFEST_FILENAME: Final[str] = "manifest.json"


EVALUATION_CASE_MIGRATIONS: Final[MigrationRegistry] = MigrationRegistry(
    family="evaluation_case",
    current_version=EVALUATION_CASE_SCHEMA_VERSION,
    minimum_supported_version=EVALUATION_CASE_MINIMUM_SUPPORTED_VERSION,
)
EVALUATION_CASE_MIGRATIONS.validate()


REPLAY_RECORD_MIGRATIONS: Final[MigrationRegistry] = MigrationRegistry(
    family="replay_record",
    current_version=REPLAY_RECORD_SCHEMA_VERSION,
    minimum_supported_version=REPLAY_RECORD_MINIMUM_SUPPORTED_VERSION,
)
REPLAY_RECORD_MIGRATIONS.validate()


# --------------------------------------------------------------------------
# Replay sidecar manifest
# --------------------------------------------------------------------------


def content_sha256(path: Path) -> str:
    """SHA-256 over LF-normalised file bytes.

    The replay bodies are checked in as LF in the Git index but Git may hand a
    Windows working tree CRLF, so hashing raw bytes would make the manifest
    platform-dependent and fail in CI.  Normalising CRLF to LF first keeps the
    manifest identical on every supported platform while remaining sensitive to
    any real content change.
    """

    digest = hashlib.sha256()
    digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def build_replay_manifest(replay_dir: str | Path) -> dict[str, Any]:
    """Build the deterministic sidecar manifest for ``eval/replay``."""

    directory = Path(replay_dir)
    records = {
        path.name: content_sha256(path)
        for path in sorted(directory.glob("*.json"), key=lambda item: item.name)
        if path.name != REPLAY_MANIFEST_FILENAME
    }
    return {
        SCHEMA_VERSION_FIELD: REPLAY_RECORD_MIGRATIONS.current_version,
        "records": records,
    }


def render_replay_manifest(manifest: Mapping[str, Any]) -> str:
    """Deterministic bytes: sorted keys, two-space indent, one trailing LF."""

    return json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n"


def load_replay_manifest(
    raw: Mapping[str, Any] | bytes | str, *, source: str | None = None
) -> dict[str, Any]:
    """Authoritative loader for the replay sidecar manifest."""

    family = REPLAY_RECORD_MIGRATIONS.family
    payload = decode_json_object(raw, family=family, source=source)
    migrated = REPLAY_RECORD_MIGRATIONS.migrate(payload, source=source)
    records = migrated.get("records")
    if not isinstance(records, Mapping) or not records:
        raise PayloadStructureError(
            "Replay manifest must declare a non-empty 'records' object.",
            family=family,
            source=source,
        )
    for name, digest in records.items():
        if not isinstance(name, str) or not name.endswith(".json"):
            raise PayloadStructureError(
                "Replay manifest record names must be JSON filenames.",
                family=family,
                source=source,
            )
        if not isinstance(digest, str) or len(digest) != 64:
            raise PayloadStructureError(
                "Replay manifest record hashes must be SHA-256 hex digests.",
                family=family,
                source=source,
            )
    return migrated


def verify_replay_directory(replay_dir: str | Path) -> dict[str, Any]:
    """Load the manifest and prove it exactly covers the replay bodies.

    Returns the loaded manifest.  Raises :class:`PayloadStructureError` when a
    body is unlisted, missing, or has drifted from its recorded hash.
    """

    directory = Path(replay_dir)
    manifest_path = directory / REPLAY_MANIFEST_FILENAME
    family = REPLAY_RECORD_MIGRATIONS.family
    if not manifest_path.is_file():
        raise PayloadStructureError(
            "Replay sidecar manifest is missing.",
            family=family,
            source=f"eval/replay/{REPLAY_MANIFEST_FILENAME}",
        )
    manifest = load_replay_manifest(
        manifest_path.read_text(encoding="utf-8"),
        source=f"eval/replay/{REPLAY_MANIFEST_FILENAME}",
    )
    recorded: Mapping[str, str] = manifest["records"]
    present = {
        path.name: path
        for path in directory.glob("*.json")
        if path.name != REPLAY_MANIFEST_FILENAME
    }
    unlisted = sorted(set(present) - set(recorded))
    absent = sorted(set(recorded) - set(present))
    if unlisted or absent:
        raise PayloadStructureError(
            "Replay sidecar manifest does not match the replay directory.",
            family=family,
            source=f"eval/replay/{REPLAY_MANIFEST_FILENAME}",
            unlisted=unlisted,
            absent=absent,
        )
    drifted = sorted(
        name for name, digest in recorded.items() if content_sha256(present[name]) != digest
    )
    if drifted:
        raise PayloadStructureError(
            "Replay record bytes do not match the sidecar manifest hashes.",
            family=family,
            source=f"eval/replay/{REPLAY_MANIFEST_FILENAME}",
            drifted=drifted,
        )
    return manifest
