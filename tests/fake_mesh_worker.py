"""Controllable subprocess used by R5.2 containment tests."""

import json
import sys
import time

from mesh.profile import GMSH_TET_V1


def mesh_response(nodes):
    return {
        "protocol_version": GMSH_TET_V1.worker_protocol_version,
        "operation": "mesh",
        "status": "ok",
        "mesh": {
            "gmsh_version": GMSH_TET_V1.gmsh_version,
            "profile_id": GMSH_TET_V1.profile_id,
            "profile_version": GMSH_TET_V1.profile_version,
            "target_size_mm": float(sys.argv[-1]),
            "nodes": nodes,
            "tetrahedra": [[1, 2, 3, 4]],
        },
    }


mode = sys.argv[1]
if mode == "sleep":
    time.sleep(10)
elif mode == "crash":
    raise SystemExit(7)
elif mode == "malformed":
    sys.stdout.write("not-json")
elif mode == "large":
    sys.stdout.write("x" * 4096)
elif mode == "unavailable":
    sys.stdout.write(json.dumps({
        "protocol_version": GMSH_TET_V1.worker_protocol_version,
        "operation": "mesh",
        "status": "rejected",
        "code": "gmsh_unavailable",
    }))
elif mode == "huge":
    sys.stdout.write(json.dumps(mesh_response([
        {"tag": 1, "coordinates": [-1e308, 0.0, 0.0]},
        {"tag": 2, "coordinates": [1e308, 0.0, 0.0]},
        {"tag": 3, "coordinates": [-1e308, 1.0, 0.0]},
        {"tag": 4, "coordinates": [-1e308, 0.0, 1.0]},
    ])))
elif mode == "duplicate":
    sys.stdout.write(json.dumps(mesh_response([
        {"tag": 1, "coordinates": [0.0, 0.0, 0.0]},
        {"tag": 2, "coordinates": [1.0, 0.0, 0.0]},
        {"tag": 3, "coordinates": [0.0, 1.0, 0.0]},
        {"tag": 4, "coordinates": [0.0, 1.0, -0.0]},
    ])))
else:
    raise SystemExit(2)
