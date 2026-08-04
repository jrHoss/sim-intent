"""Versioned fresh-process Gmsh tetrahedral meshing operation."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

from mesh.profile import GMSH_TET_V1, apply_profile

PROTOCOL_VERSION = GMSH_TET_V1.worker_protocol_version
SAFE_CODES = {
    "empty_mesh", "gmsh_profile_option_unsupported", "gmsh_unavailable",
    "gmsh_version_unsupported", "invalid_cad", "mesh_generation_failed",
    "unsupported_solid_count", "unsupported_element_type",
}


class WorkerRejection(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _generate(path: Path, target_size_mm: float) -> dict:
    try:
        import gmsh
    except Exception as exc:
        raise WorkerRejection("gmsh_unavailable") from exc
    if getattr(gmsh, "__version__", None) != GMSH_TET_V1.gmsh_version:
        raise WorkerRejection("gmsh_version_unsupported")
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    null_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null_fd, 1)
    os.dup2(null_fd, 2)
    os.close(null_fd)
    initialized = False
    stage = "initialize"
    try:
        # Ignore mutable user and system gmshrc files; the profile owns options.
        gmsh.initialize(readConfigFiles=False)
        initialized = True
        try:
            apply_profile(gmsh, target_size_mm)
        except ValueError as exc:
            raise WorkerRejection(str(exc)) from exc
        gmsh.model.add("sim_intent_mesh")
        stage = "import"
        gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.synchronize()
        if len(sorted(gmsh.model.getEntities(3))) != 1:
            raise WorkerRejection("unsupported_solid_count")
        stage = "mesh"
        gmsh.model.mesh.generate(3)
        node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
        if len(node_tags) == 0 or len(coordinates) != len(node_tags) * 3:
            raise WorkerRejection("empty_mesh")
        nodes = [
            {"tag": int(tag), "coordinates": [
                float(coordinates[index * 3]),
                float(coordinates[index * 3 + 1]),
                float(coordinates[index * 3 + 2]),
            ]}
            for index, tag in enumerate(node_tags)
        ]
        types, element_tags, element_nodes = gmsh.model.mesh.getElements(3)
        tetrahedra: list[list[int]] = []
        for element_type, tags, flattened in zip(
            types, element_tags, element_nodes, strict=True
        ):
            name, dimension, order, node_count, _, primary_count = (
                gmsh.model.mesh.getElementProperties(element_type)
            )
            if (
                int(element_type) != 4 or name != "Tetrahedron 4"
                or int(dimension) != 3 or int(order) != 1
                or int(node_count) != 4 or int(primary_count) != 4
                or len(flattened) != len(tags) * 4
            ):
                raise WorkerRejection("unsupported_element_type")
            tetrahedra.extend(
                [int(value) for value in flattened[offset:offset + 4]]
                for offset in range(0, len(flattened), 4)
            )
        if not tetrahedra:
            raise WorkerRejection("empty_mesh")
        return {
            "gmsh_version": gmsh.__version__,
            "profile_id": GMSH_TET_V1.profile_id,
            "profile_version": GMSH_TET_V1.profile_version,
            "target_size_mm": target_size_mm,
            "nodes": nodes,
            "tetrahedra": tetrahedra,
        }
    except WorkerRejection:
        raise
    except Exception as exc:
        code = (
            "gmsh_unavailable" if stage == "initialize"
            else "invalid_cad" if stage == "import"
            else "mesh_generation_failed"
        )
        raise WorkerRejection(code) from exc
    finally:
        if initialized:
            try:
                gmsh.finalize()
            except Exception:
                pass
        # OCC can write directly to native stdout despite General.Terminal=0.
        # Flush C streams before restoring the JSON protocol descriptors.
        try:
            ctypes.CDLL(None).fflush(None)
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return 2
    try:
        raw = _generate(Path(args[0]), float(args[1]))
        payload = {"protocol_version": PROTOCOL_VERSION,
                   "operation": "mesh", "status": "ok", "mesh": raw}
    except WorkerRejection as exc:
        code = exc.code if exc.code in SAFE_CODES else "mesh_generation_failed"
        payload = {"protocol_version": PROTOCOL_VERSION,
                   "operation": "mesh", "status": "rejected", "code": code}
    except Exception:
        payload = {"protocol_version": PROTOCOL_VERSION,
                   "operation": "mesh", "status": "rejected",
                   "code": "mesh_generation_failed"}
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
