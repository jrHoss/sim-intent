"""Deterministic authoritative schema export (Task 19, ADR-004).

Publishes two checked-in artifacts:

``schema/openapi.json``
    The backend OpenAPI document.  It is the API contract authority and the
    only input to the generated TypeScript client types.

``schema/simulation-intent.schema.json``
    The JSON Schema of the ``SimulationIntent`` payload contract.

Both are emitted deterministically -- sorted keys, two-space indent, LF line
endings, exactly one trailing newline -- so a drift check is a byte comparison.

The OpenAPI document is always generated in ``production`` runtime mode.  The
REPLAY fallback routes are registered only in ``replay``/``test`` mode, so
generating in any other mode would make the published contract depend on
``SIM_INTENT_MODE``.

Usage::

    python scripts/export_schema.py            # write
    python scripts/export_schema.py --check    # verify only, exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_mode import RuntimeMode  # noqa: E402
from app.server import create_app  # noqa: E402
from ir.schema import export_json_schema  # noqa: E402

SCHEMA_DIR = ROOT / "schema"
OPENAPI_PATH = SCHEMA_DIR / "openapi.json"
IR_SCHEMA_PATH = SCHEMA_DIR / "simulation-intent.schema.json"


def render(document: dict[str, Any]) -> str:
    """Deterministic JSON bytes: sorted keys, indent 2, one trailing newline."""

    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_openapi() -> dict[str, Any]:
    """Generate the OpenAPI document in production mode."""

    app = create_app(
        ROOT / ".sim_intent_cache" / "schema-export", mode=RuntimeMode.PRODUCTION
    )
    return app.openapi()


def build_ir_schema() -> dict[str, Any]:
    return export_json_schema()


def targets() -> list[tuple[Path, str]]:
    return [
        (OPENAPI_PATH, render(build_openapi())),
        (IR_SCHEMA_PATH, render(build_ir_schema())),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the authoritative backend schema artifacts"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify without writing; exit 1 when an artifact would change",
    )
    args = parser.parse_args(argv)

    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    drifted: list[str] = []
    for path, text in targets():
        relative = path.relative_to(ROOT).as_posix()
        current = (
            path.read_text(encoding="utf-8").replace("\r\n", "\n")
            if path.is_file()
            else None
        )
        if current == text:
            continue
        if args.check:
            drifted.append(relative)
            continue
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {relative}")

    if args.check:
        if drifted:
            print("schema drift detected:", file=sys.stderr)
            for relative in drifted:
                print(f"  {relative}", file=sys.stderr)
            print(
                "regenerate with: python scripts/export_schema.py",
                file=sys.stderr,
            )
            return 1
        print("checked-in schema artifacts match the backend")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
