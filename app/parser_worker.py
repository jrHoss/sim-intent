"""Versioned subprocess boundary for untrusted source parsing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from geom.analytic import analyze_identity_surfaces
from geom.inventory import FaceInventory, file_sha256
from geom.meshes import parse_inp
from geom.parser import parse_step

PROTOCOL_VERSION = 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in {"step", "inp"}:
        return 2
    kind, raw_path = args
    path = Path(raw_path)
    try:
        if kind == "step":
            inventory = FaceInventory(path.name, file_sha256(path), parse_step(path))
            analytic_surfaces = {
                str(tag): record.to_dict()
                for tag, record in analyze_identity_surfaces(path).items()
            }
        else:
            inventory = parse_inp(path)
            analytic_surfaces = None
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "status": "valid",
            "kind": kind,
            "inventory": inventory.to_dict(),
            "geometry_identity_surfaces": analytic_surfaces,
        }
    except Exception:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "status": "invalid",
            "kind": kind,
        }
    sys.stdout.write(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
