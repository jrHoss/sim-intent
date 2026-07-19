"""Task 5 DoD tests: mesh-format ingestion (Abaqus INP + STL).

The INP/STL fixtures are GENERATED here from bracket.step via gmsh (fixture
generation in test setup is explicitly allowed by EXECUTION_PLAN Task 5; the
read-only tests/fixtures/ directory is not touched). gmsh emits named ELSETs
from physical groups but no NSETs, so the generator appends one NSET block by
hand — that is the deck feature the round-trip must preserve.
"""

import math
from pathlib import Path

import gmsh
import pytest

from geom.meshes import MeshInventory, load_mesh, parse_inp, parse_stl
from geom.parser import parse_step

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRACKET_STEP = FIXTURES / "bracket.step"

CAD_BOTTOM_FACE = 8  # bracket base bottom, per Task 4 labels
NSET_NAME = "BOLT_HOLE_NODES"
ELSET_BOLTS = "BOLT_HOLES"
ELSET_BOTTOM = "BASE_BOTTOM"
ELSET_VOLUME = "ALL_VOLUME"


def _generate_bracket_meshes(out_dir: Path) -> tuple[Path, Path]:
    """Mesh bracket.step once; write an INP (named ELSETs + appended NSET) and
    a full-boundary binary STL."""
    inp_path = out_dir / "bracket_mesh.inp"
    stl_path = out_dir / "bracket_mesh.stl"
    if gmsh.isInitialized():
        raise RuntimeError("fixture generation requires exclusive use of gmsh")
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("bracket_meshfix")
        gmsh.model.occ.importShapes(str(BRACKET_STEP))
        gmsh.model.occ.synchronize()
        volumes = [tag for _, tag in gmsh.model.getEntities(3)]
        gmsh.model.addPhysicalGroup(3, volumes, name=ELSET_VOLUME)
        gmsh.model.addPhysicalGroup(2, [11, 12], name=ELSET_BOLTS)
        gmsh.model.addPhysicalGroup(2, [CAD_BOTTOM_FACE], name=ELSET_BOTTOM)
        gmsh.option.setNumber("Mesh.MeshSizeMin", 12)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 25)
        gmsh.model.mesh.generate(3)
        gmsh.write(str(inp_path))
        # STL gets the whole boundary, not just the physical surfaces.
        gmsh.option.setNumber("Mesh.SaveAll", 1)
        gmsh.option.setNumber("Mesh.Binary", 1)
        gmsh.write(str(stl_path))
        bolt_nodes: set[int] = set()
        for face in (11, 12):
            tags, _, _ = gmsh.model.mesh.getNodes(2, face, includeBoundary=True)
            bolt_nodes.update(int(t) for t in tags)
    finally:
        gmsh.finalize()

    ids = sorted(bolt_nodes)
    lines = [f"*NSET, NSET={NSET_NAME}"]
    for i in range(0, len(ids), 8):
        lines.append(", ".join(str(n) for n in ids[i : i + 8]))
    content = inp_path.read_text(encoding="utf-8")
    if not content.endswith("\n"):
        content += "\n"
    inp_path.write_text(content + "\n".join(lines) + "\n", encoding="utf-8")
    return inp_path, stl_path


def _set_ids_from_deck(path: Path, keyword: str, name: str) -> list[int]:
    """Native ids listed under *NSET/*ELSET `name` in the deck text."""
    target = f"*{keyword},{keyword}={name}".upper()
    ids: list[int] = []
    collecting = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("*"):
            collecting = line.replace(" ", "").upper() == target
            continue
        if collecting and line:
            ids.extend(int(tok) for tok in line.split(",") if tok.strip())
    return sorted(ids)


@pytest.fixture(scope="module")
def mesh_paths(tmp_path_factory) -> tuple[Path, Path]:
    return _generate_bracket_meshes(tmp_path_factory.mktemp("mesh_fixtures"))


@pytest.fixture(scope="module")
def inp_inventory(mesh_paths):
    return parse_inp(mesh_paths[0])


@pytest.fixture(scope="module")
def stl_inventory(mesh_paths):
    return parse_stl(mesh_paths[1])


@pytest.fixture(scope="module")
def cad_faces():
    return parse_step(BRACKET_STEP)


# ---------------------------------------------------------------- INP


def test_inp_preserves_set_names(inp_inventory):
    names = {r.name for r in inp_inventory.regions}
    assert {NSET_NAME, ELSET_BOLTS, ELSET_BOTTOM, ELSET_VOLUME} <= names
    assert inp_inventory.region(NSET_NAME).kind == "node_set"
    for elset in (ELSET_BOLTS, ELSET_BOTTOM, ELSET_VOLUME):
        assert inp_inventory.region(elset).kind == "element_set"
    for region in inp_inventory.regions:
        assert region.ids == sorted(set(region.ids))
        assert region.ids


def test_inp_set_ids_are_native_deck_ids(mesh_paths, inp_inventory):
    inp = mesh_paths[0]
    assert inp_inventory.region(ELSET_BOLTS).ids == _set_ids_from_deck(inp, "ELSET", ELSET_BOLTS)
    assert inp_inventory.region(ELSET_BOTTOM).ids == _set_ids_from_deck(inp, "ELSET", ELSET_BOTTOM)
    assert inp_inventory.region(NSET_NAME).ids == _set_ids_from_deck(inp, "NSET", NSET_NAME)


def test_inp_json_round_trip_preserves_set_names(inp_inventory):
    reloaded = MeshInventory.from_json(inp_inventory.to_json())
    assert reloaded == inp_inventory
    assert {r.name for r in reloaded.regions} == {r.name for r in inp_inventory.regions}


def test_inp_parse_is_deterministic(mesh_paths, inp_inventory):
    assert parse_inp(mesh_paths[0]) == inp_inventory


def test_inp_counts_and_native_node_ids(inp_inventory):
    assert inp_inventory.format == "abaqus"
    assert inp_inventory.n_nodes > 0
    assert inp_inventory.n_elements > 0
    # The NSET's native node ids (bolt-hole surface nodes) must appear among
    # the boundary facets' node ids — both speak native ids, not indices.
    boundary_nodes = {n for f in inp_inventory.facets for n in f.node_ids}
    assert set(inp_inventory.region(NSET_NAME).ids) <= boundary_nodes


def test_inp_boundary_facets_sane(inp_inventory):
    facets = inp_inventory.facets
    assert facets
    assert [f.id for f in facets] == list(range(1, len(facets) + 1))
    for f in facets:
        assert f.area > 0
        assert math.isclose(math.hypot(*f.normal), 1.0, rel_tol=1e-9)
        assert len(set(f.node_ids)) == 3


def test_inp_facet_groups_partition_facets(inp_inventory):
    groups = inp_inventory.facet_groups
    assert groups
    grouped = sorted(fid for g in groups for fid in g.facet_ids)
    assert grouped == [f.id for f in inp_inventory.facets]
    assert math.isclose(
        sum(g.area for g in groups),
        sum(f.area for f in inp_inventory.facets),
        rel_tol=1e-9,
    )
    assert [g.id for g in groups] == list(range(1, len(groups) + 1))


def _bottom_group(inventory):
    candidates = [g for g in inventory.facet_groups if g.normal[2] < -0.999]
    assert candidates, "no downward-facing facet group found"
    return max(candidates, key=lambda g: g.area)


@pytest.mark.parametrize("which", ["inp", "stl"])
def test_bottom_group_matches_cad_bottom_face(which, inp_inventory, stl_inventory, cad_faces):
    """The grounding analog: the mesh boundary exposes the base bottom with the
    same area/centroid/normal a CAD FaceRecord would give."""
    inventory = inp_inventory if which == "inp" else stl_inventory
    cad_bottom = next(f for f in cad_faces if f.tag == CAD_BOTTOM_FACE)
    group = _bottom_group(inventory)
    assert math.isclose(group.area, cad_bottom.area, rel_tol=0.05)
    for mesh_c, cad_c in zip(group.centroid, cad_bottom.centroid):
        assert math.isclose(mesh_c, cad_c, abs_tol=2.0)


# ---------------------------------------------------------------- STL


def test_stl_loads_with_stable_ids(mesh_paths, stl_inventory):
    facets = stl_inventory.facets
    assert facets
    assert [f.id for f in facets] == list(range(1, len(facets) + 1))
    assert stl_inventory.n_elements == len(facets)
    assert parse_stl(mesh_paths[1]) == stl_inventory  # deterministic reparse


def test_stl_facets_sane(stl_inventory):
    assert stl_inventory.format == "stl"
    assert stl_inventory.regions == []  # STL carries no sets
    for f in stl_inventory.facets:
        assert f.area > 0
        assert math.isclose(math.hypot(*f.normal), 1.0, rel_tol=1e-9)
        assert all(1 <= n <= stl_inventory.n_nodes for n in f.node_ids)


def test_stl_json_round_trip(stl_inventory):
    assert MeshInventory.from_json(stl_inventory.to_json()) == stl_inventory


# ---------------------------------------------------------------- dispatch


def test_load_mesh_dispatch(mesh_paths):
    assert load_mesh(mesh_paths[0]).format == "abaqus"
    assert load_mesh(mesh_paths[1]).format == "stl"
    with pytest.raises(ValueError, match="unsupported mesh format"):
        load_mesh(BRACKET_STEP)
