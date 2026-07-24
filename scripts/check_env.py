"""Environment gate (Task 1; CalculiX reporting added by Task 18).

Verifies the toolchain needed by the headless core: gmsh can mesh a solid
into tetrahedra, and meshio / fastapi import cleanly. Additionally reports
whether the optional CalculiX ``ccx`` solver executable is present; its
absence is an `unavailable` capability outside the supported container, never
an environment-gate failure (ADR-007).

Prints "ENV OK" as the final line on success, exits nonzero otherwise.
"""

import shutil
import subprocess
import sys

GMSH_TET4 = 4  # gmsh element type id for 4-node tetrahedron


def report_ccx() -> None:
    executable = shutil.which("ccx")
    if not executable:
        print("CCX UNAVAILABLE (optional; solver capability reports unavailable)")
        return
    try:
        completed = subprocess.run(
            [executable, "-v"], capture_output=True, text=True, timeout=10
        )
        banner = " ".join((completed.stdout + completed.stderr).split()) or "unknown version"
    except (OSError, subprocess.TimeoutExpired):
        banner = "version query failed"
    print(f"CCX AVAILABLE: {banner}")


def main() -> int:
    import fastapi  # noqa: F401
    import meshio  # noqa: F401
    import gmsh

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("env_check_box")
        gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.synchronize()
        gmsh.model.mesh.generate(3)
        elem_types, elem_tags, _ = gmsh.model.mesh.getElements(3)
        tet_count = sum(
            len(tags)
            for etype, tags in zip(elem_types, elem_tags)
            if etype == GMSH_TET4
        )
        assert tet_count > 0, "gmsh meshed a box but produced no tetrahedra"
    finally:
        gmsh.finalize()

    report_ccx()
    print("ENV OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
