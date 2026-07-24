"""Deterministic requirements.txt compatibility export (Task 18).

`uv.lock` is the authoritative direct and transitive dependency lock
(ADR-005). `requirements.txt` is a generated compatibility artifact produced
from that frozen lock by this script. Regenerate with:

    python scripts/export_requirements.py

Verify without writing (CI drift check, byte-exact):

    python scripts/export_requirements.py --check

The script requires the pinned `uv` executable on PATH (or set `UV` to its
location); see docs/environment.md for the supported uv version.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "requirements.txt"

BANNER = (
    "# GENERATED FILE - do not edit by hand.\n"
    "# uv.lock is the authoritative dependency lock (ADR-005, Task 18).\n"
    "# Regenerate with: python scripts/export_requirements.py\n"
    "# Verify with:     python scripts/export_requirements.py --check\n"
)

EXPORT_ARGS = [
    "export",
    "--frozen",
    "--no-dev",
    "--format",
    "requirements-txt",
]


def generate() -> bytes:
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise SystemExit("uv executable not found; set UV or add uv to PATH")
    completed = subprocess.run(
        [uv, *EXPORT_ARGS],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=120,
    )
    body = completed.stdout.replace(b"\r\n", b"\n")
    return BANNER.encode("utf-8") + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if requirements.txt differs byte-for-byte from the export",
    )
    args = parser.parse_args(argv)
    expected = generate()
    if args.check:
        actual = TARGET.read_bytes() if TARGET.is_file() else b""
        if actual != expected:
            print(
                "requirements.txt drift: regenerate with "
                "python scripts/export_requirements.py",
                file=sys.stderr,
            )
            return 1
        print("requirements.txt matches uv.lock export")
        return 0
    TARGET.write_bytes(expected)
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(expected)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
