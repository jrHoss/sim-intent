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
)
from eval.versioning import (  # noqa: E402
    EVALUATION_CASE_MIGRATIONS,
    REPLAY_MANIFEST_FILENAME,
    build_replay_manifest,
    render_replay_manifest,
)
from ir.schema import SimulationIntent  # noqa: E402
from ir.schema_version import SCHEMA_VERSION_FIELD  # noqa: E402
from ir.versioning import SIMULATION_INTENT_MIGRATIONS  # noqa: E402


class StampError(RuntimeError):
    """A payload could not be stamped without changing its meaning."""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def stamp_by_insertion(path: Path, version: int) -> str:
    original_text = read_text(path)
    original = json.loads(original_text)
    if not isinstance(original, Mapping):
        raise StampError(f"{path}: not a JSON object")
    if SCHEMA_VERSION_FIELD in original:
        if original[SCHEMA_VERSION_FIELD] == version:
            return original_text
        raise StampError(
            f"{path}: already declares version {original[SCHEMA_VERSION_FIELD]!r}"
        )
    stamped_text = insert_top_level_version(original_text, version)
    verify_semantics(path, original, json.loads(stamped_text))
    return stamped_text


# --------------------------------------------------------------------------
# Strategy: canonical re-emission (machine-generated documents)
# --------------------------------------------------------------------------


def stamp_fallback_record(path: Path) -> str:
    original = json.loads(read_text(path))
    if not isinstance(original, Mapping):
        raise StampError(f"{path}: not a JSON object")
    nested = original.get(NESTED_INTENT_KEY)
    if not isinstance(nested, Mapping):
        raise StampError(f"{path}: missing '{NESTED_INTENT_KEY}'")
    # The stamper is the one-shot migration action itself, so it constructs the
    # typed model directly.  Every *runtime* read of this file afterwards goes
    # through app.record_versions.load_fallback_record.
    intent = SimulationIntent.model_validate(dict(nested), strict=True)
    body = {key: value for key, value in original.items() if key != NESTED_INTENT_KEY}
    stamped = build_fallback_envelope(body, intent)
    verify_semantics(path, original, stamped, nested_keys=(NESTED_INTENT_KEY,))
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
            f"{path}: stamping would change the document's meaning; refusing"
        )


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------


def stamp_targets(root: Path) -> list[tuple[Path, str]]:
    """Return ``(path, stamped_text)`` for every approved target."""

    targets: list[tuple[Path, str]] = []
    intent_version = SIMULATION_INTENT_MIGRATIONS.current_version
    case_version = EVALUATION_CASE_MIGRATIONS.current_version

    for path in sorted((root / "examples").glob("*.json")):
        targets.append((path, stamp_by_insertion(path, intent_version)))

    demo = root / "docs" / "task13-bracket-demo.json"
    if demo.is_file():
        targets.append((demo, stamp_by_insertion(demo, intent_version)))

    for path in sorted((root / "eval" / "cases").glob("*.json")):
        targets.append((path, stamp_by_insertion(path, case_version)))

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
