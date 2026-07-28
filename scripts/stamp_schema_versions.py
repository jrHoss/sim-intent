"""Deterministic schema-version stamping for checked-in payloads (Task 19).

Only *setup-bearing* checked-in payloads are stamped:

- ``examples/*.json`` and ``docs/task13-bracket-demo.json``
  -- complete ``SimulationIntent`` documents;
- ``eval/cases/*.json`` -- ``EvaluationCase`` record versions;
- ``eval/fallback/*.json`` -- fallback-envelope versions plus the nested
  ``proposed_ir`` ``SimulationIntent`` version.

Plus the sidecar ``eval/replay/manifest.json``, which declares the replay
record version without touching the strict ``Interpretation`` bodies (D-1).

Deliberately **not** stamped: ``eval/replay/*.json`` bodies, ``eval/results*``,
geometry ground-truth fixtures, golden solver artifacts, and the disposable
``.sim_intent_cache`` runtime caches.

Two strategies keep the diff reviewable:

``insert``
    Hand-formatted documents get a single textual line inserted after the
    opening brace, so their formatting and key order survive untouched.

``canonical``
    Machine-generated documents are re-emitted exactly as their producer emits
    them (``json.dumps(..., indent=2, sort_keys=True)`` plus one trailing
    newline), so the stamped file is byte-identical to a regeneration.

Every write is verified: the stamped document, with its declared versions
removed again, must parse equal to the original document.  That equality is the
migration evidence required before rewriting a checked-in payload.

A *declared* version is never taken as evidence that the document is valid.
Before a supported-version document is returned unchanged -- and after any
document is stamped -- it is validated through its family's authoritative
loader, so a malformed checked-in payload fails ``--check`` whether it declares
version 1, version 2, or nothing at all.  Validation never rewrites: a valid
schema-version-1 document stays byte-identical at version 1.

Usage::

    python scripts/stamp_schema_versions.py            # write
    python scripts/stamp_schema_versions.py --check    # verify only, exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.record_versions import (  # noqa: E402
    FALLBACK_RECORD_MIGRATIONS,
    NESTED_INTENT_KEY,
    build_fallback_envelope,
    load_fallback_record,
)
from eval.schema import load_evaluation_case  # noqa: E402
from eval.versioning import (  # noqa: E402
    EVALUATION_CASE_MIGRATIONS,
    REPLAY_MANIFEST_FILENAME,
    build_replay_manifest,
    render_replay_manifest,
)
from ir.schema import SimulationIntent  # noqa: E402
from ir.schema_version import SCHEMA_VERSION_FIELD  # noqa: E402
from ir.versioning import (  # noqa: E402
    SIMULATION_INTENT_MIGRATIONS,
    MigrationRegistry,
    load_simulation_intent,
)


class StampError(RuntimeError):
    """A payload could not be stamped without changing its meaning."""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Safe diagnostics
# --------------------------------------------------------------------------


def repository_path(path: Path) -> str:
    """Identify a target by its repository-relative path and nothing more.

    A diagnostic must be reproducible on any checkout, so an absolute host path
    is reduced to the normal repository-relative label.
    """

    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


#: The reason reported when a loader rejects a document without a typed code.
GENERIC_CONTRACT_CODE = "payload_contract_invalid"


def _contract_failure(path: Path, exc: Exception) -> StampError:
    """Report *which file* failed and a server-owned reason code.

    Only the repository-relative path and the family's own stable ``code`` are
    published.  The underlying exception text is deliberately dropped: it can
    quote document content and host paths, and it is not a stable contract.
    """

    code = getattr(exc, "code", None)
    if not isinstance(code, str) or not code:
        code = GENERIC_CONTRACT_CODE
    family = getattr(exc, "family", None)
    if isinstance(family, str) and family:
        code = f"{family}.{code}"
    return StampError(
        f"{repository_path(path)}: the document does not satisfy its declared "
        f"schema contract ({code})"
    )


def load_json_object(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(read_text(path))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StampError(
            f"{repository_path(path)}: the file is not valid JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise StampError(f"{repository_path(path)}: not a JSON object")
    return payload


def write_text(path: Path, text: str) -> None:
    # LF only, deterministic on every platform (binding correction 9).
    path.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------
# Strategy: textual insertion (hand-formatted documents)
# --------------------------------------------------------------------------


def insert_top_level_version(text: str, version: int) -> str:
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        raise StampError("document does not start with a JSON object")
    brace = text.index("{")
    newline = text.find("\n", brace)
    if newline == -1:
        raise StampError("document is not line-formatted")
    head, rest = text[: newline + 1], text[newline + 1 :]
    indent = len(rest) - len(rest.lstrip(" "))
    line = f'{" " * indent}"{SCHEMA_VERSION_FIELD}": {version},\n'
    return head + line + rest


def validate_declared_version(
    path: Path, payload: Mapping[str, Any], registry: MigrationRegistry
) -> None:
    """Validate one declaration before any stamping or normalization.

    Composite records call this for both the envelope and its nested intent.
    In particular, a future declaration must reach the family registry before
    any builder can replace it with the current version.
    """

    try:
        registry.check_version(payload, source=repository_path(path))
    except Exception as exc:
        raise _contract_failure(path, exc) from exc


# --------------------------------------------------------------------------
# Typed validation: a declared version is not evidence of a valid document
# --------------------------------------------------------------------------
#
# Each family is validated by its own authoritative loader -- the same one the
# runtime uses -- rather than by a second implementation living here.  Every
# loader performs the fixed ADR-004 order: structural gate, explicit version
# declaration, version bounds, sequential ``n -> n + 1`` migration, then strict
# typed validation.  A *current-version* document therefore runs zero migrations
# and is judged directly by the current typed schema, while a *legacy* document
# is judged by the same schema after the registered migration path has carried
# it forward.  Neither branch rewrites the file.

Validator = Any  # Callable[[Mapping[str, Any], str], object]


def validate_simulation_intent_document(
    payload: Mapping[str, Any], source: str
) -> None:
    load_simulation_intent(payload, source=source)


def validate_evaluation_case_document(payload: Mapping[str, Any], source: str) -> None:
    load_evaluation_case(dict(payload), source=source)


def validate_fallback_record_document(payload: Mapping[str, Any], source: str) -> None:
    """Validate the envelope *and* the nested ``SimulationIntent``."""

    load_fallback_record(payload, source=source)


def validate_document(path: Path, payload: Mapping[str, Any], validator: Validator) -> None:
    """Run one family's authoritative loader, reporting failures safely."""

    try:
        validator(payload, repository_path(path))
    except StampError:
        raise
    except Exception as exc:  # typed schema-version failures and pydantic errors
        raise _contract_failure(path, exc) from exc


def stamp_by_insertion(
    path: Path, registry: MigrationRegistry, validator: Validator
) -> str:
    original_text = read_text(path)
    original = load_json_object(path)
    if SCHEMA_VERSION_FIELD in original:
        validate_declared_version(path, original, registry)
        # A supported declaration is a claim, not proof.  Validate before
        # returning the bytes unchanged.
        validate_document(path, original, validator)
        return original_text
    stamped_text = insert_top_level_version(original_text, registry.current_version)
    stamped = json.loads(stamped_text)
    verify_semantics(path, original, stamped)
    validate_document(path, stamped, validator)
    return stamped_text


# --------------------------------------------------------------------------
# Strategy: canonical re-emission (machine-generated documents)
# --------------------------------------------------------------------------


def stamp_fallback_record(path: Path) -> str:
    original = load_json_object(path)
    nested = original.get(NESTED_INTENT_KEY)
    if not isinstance(nested, Mapping):
        raise StampError(f"{repository_path(path)}: missing '{NESTED_INTENT_KEY}'")
    envelope_declared = SCHEMA_VERSION_FIELD in original
    nested_declared = SCHEMA_VERSION_FIELD in nested
    if envelope_declared:
        validate_declared_version(path, original, FALLBACK_RECORD_MIGRATIONS)
    if nested_declared:
        validate_declared_version(path, nested, SIMULATION_INTENT_MIGRATIONS)
    if envelope_declared and nested_declared:
        # Both declarations are supported; prove the envelope and the nested
        # intent are actually valid before returning the bytes unchanged.
        validate_document(path, original, validate_fallback_record_document)
        return read_text(path)
    # The stamper is the one-shot migration action itself, so it constructs the
    # typed model directly.  Every *runtime* read of this file afterwards goes
    # through app.record_versions.load_fallback_record.
    try:
        intent = SimulationIntent.model_validate(dict(nested), strict=True)
    except Exception as exc:
        raise _contract_failure(path, exc) from exc
    body = {key: value for key, value in original.items() if key != NESTED_INTENT_KEY}
    stamped = build_fallback_envelope(body, intent)
    verify_semantics(path, original, stamped, nested_keys=(NESTED_INTENT_KEY,))
    validate_document(path, stamped, validate_fallback_record_document)
    return json.dumps(stamped, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def strip_declared_versions(
    payload: Mapping[str, Any], nested_keys: Iterable[str] = ()
) -> dict[str, Any]:
    """Remove the declared versions this tool adds -- and only those."""

    result = {
        key: value for key, value in payload.items() if key != SCHEMA_VERSION_FIELD
    }
    for key in nested_keys:
        nested = result.get(key)
        if isinstance(nested, Mapping):
            result[key] = {
                inner_key: inner_value
                for inner_key, inner_value in nested.items()
                if inner_key != SCHEMA_VERSION_FIELD
            }
    return result


def verify_semantics(
    path: Path,
    original: Mapping[str, Any],
    stamped: Mapping[str, Any],
    nested_keys: Iterable[str] = (),
) -> None:
    before = strip_declared_versions(original, nested_keys)
    after = strip_declared_versions(stamped, nested_keys)
    if before != after:
        raise StampError(
            f"{repository_path(path)}: stamping would change the document's "
            "meaning; refusing"
        )


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------


def stamp_targets(root: Path) -> list[tuple[Path, str]]:
    """Return ``(path, stamped_text)`` for every approved target."""

    targets: list[tuple[Path, str]] = []

    for path in sorted((root / "examples").glob("*.json")):
        targets.append((
            path,
            stamp_by_insertion(
                path,
                SIMULATION_INTENT_MIGRATIONS,
                validate_simulation_intent_document,
            ),
        ))

    demo = root / "docs" / "task13-bracket-demo.json"
    if demo.is_file():
        targets.append((
            demo,
            stamp_by_insertion(
                demo,
                SIMULATION_INTENT_MIGRATIONS,
                validate_simulation_intent_document,
            ),
        ))

    for path in sorted((root / "eval" / "cases").glob("*.json")):
        targets.append((
            path,
            stamp_by_insertion(
                path, EVALUATION_CASE_MIGRATIONS, validate_evaluation_case_document
            ),
        ))

    for path in sorted((root / "eval" / "fallback").glob("*.json")):
        targets.append((path, stamp_fallback_record(path)))

    replay_dir = root / "eval" / "replay"
    manifest_path = replay_dir / REPLAY_MANIFEST_FILENAME
    targets.append(
        (manifest_path, render_replay_manifest(build_replay_manifest(replay_dir)))
    )
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stamp schema versions onto approved checked-in payloads"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify without writing; exit 1 when any target would change",
    )
    args = parser.parse_args(argv)

    try:
        targets = stamp_targets(ROOT)
    except StampError as exc:
        print(f"stamping refused: {exc}", file=sys.stderr)
        return 2

    drifted: list[str] = []
    written = 0
    for path, text in targets:
        relative = path.relative_to(ROOT).as_posix()
        current = read_text(path) if path.is_file() else None
        if current is not None and current.replace("\r\n", "\n") == text:
            continue
        if args.check:
            drifted.append(relative)
            continue
        write_text(path, text)
        written += 1
        print(f"stamped {relative}")

    if args.check:
        if drifted:
            print("schema-version stamping drift:", file=sys.stderr)
            for relative in drifted:
                print(f"  {relative}", file=sys.stderr)
            return 1
        print(f"all {len(targets)} versioned payloads are stamped and current")
        return 0

    print(f"{written} file(s) updated; {len(targets)} target(s) checked")
    print(
        "versions: simulation_intent="
        f"{SIMULATION_INTENT_MIGRATIONS.current_version} "
        f"evaluation_case={EVALUATION_CASE_MIGRATIONS.current_version} "
        f"fallback_record={FALLBACK_RECORD_MIGRATIONS.current_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
