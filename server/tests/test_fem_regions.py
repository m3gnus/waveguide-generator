"""Tetrahedral meshing of declared FEM regions (fem-regions-v1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from server.mesh.fem_regions import (
    FemRegionMeshError,
    SurfaceDescriptor,
    assign_faces,
    build_fem_region_mesh,
    parse_physical_name,
    physical_name,
    read_fem_region_domain,
)
from server.mesh.gmsh_worker import _run_in_gmsh_session

# A 40 x 30 x 20 mm air box: the diaphragm is the z = 0 face and the port
# opening is the x = 40 face.
BOX = (40.0, 30.0, 20.0)


def _write_step(path: Path, *, boxes=((0.0, 0.0, 0.0),)) -> Path:
    def build() -> None:
        import gmsh

        gmsh.model.add("fixture")
        try:
            for origin in boxes:
                gmsh.model.occ.addBox(*origin, *BOX)
            gmsh.model.occ.synchronize()
            gmsh.write(str(path))
        finally:
            gmsh.model.remove()

    _run_in_gmsh_session(build)
    return path


def _face(area, centroid, normal, low, high):
    return {"area_mm2": area, "centroid_mm": list(centroid), "normal": normal,
            "bbox_mm": [list(low), list(high)]}


CONE = _face(1200.0, (20.0, 15.0, 0.0), [0.0, 0.0, 1.0], (0, 0, 0), (40, 30, 0))
PORT = _face(600.0, (40.0, 15.0, 10.0), [1.0, 0.0, 0.0], (40, 0, 0), (40, 30, 20))


def _region(**overrides):
    region = {
        "region_id": "rear-chamber",
        "name": "Rear chamber",
        "object_id": "body-1",
        "file": "fem-regions/rear-chamber.step",
        "n_bodies_expected": 1,
        "mesh_size_mm": 8.0,
        "medium": {"sound_speed_m_per_s": 343.0, "density_kg_per_m3": 1.21, "bulk_loss_factor": 0.0},
        "surface_groups": [
            {"group_id": "cone", "name": "Cone", "kind": "moving", "mesh_size_mm": 4.0,
             "drive_channel_id": "drive-LF", "faces": [CONE]},
            {"group_id": "port", "name": "Port", "kind": "interface", "mesh_size_mm": 4.0,
             "faces": [PORT]},
        ],
    }
    region.update(overrides)
    return region


def _mesh(step: Path, region) -> dict:
    return _run_in_gmsh_session(build_fem_region_mesh, step, region)


def test_physical_names_round_trip():
    name = physical_name("rear-chamber", "cone", "moving")
    assert name == "wg-fem-v1|region=rear-chamber|group=cone|kind=moving"
    assert parse_physical_name(name) == {"region": "rear-chamber", "group": "cone", "kind": "moving"}
    assert parse_physical_name(physical_name("rear-chamber")) == {
        "region": "rear-chamber", "entity": "volume",
    }
    assert parse_physical_name("wg-import-v1|rigid") is None


def test_a_box_meshes_with_its_groups_and_walls(tmp_path):
    step = _write_step(tmp_path / "air.step")
    built = _mesh(step, _region())

    assert built["msh_text"].startswith("$MeshFormat\n4.1 0 8")
    names = built["physical_names"]
    assert set(names) == {
        "wg-fem-v1|region=rear-chamber|volume",
        "wg-fem-v1|region=rear-chamber|group=cone|kind=moving",
        "wg-fem-v1|region=rear-chamber|group=port|kind=interface",
        "wg-fem-v1|region=rear-chamber|group=_walls|kind=rigid",
    }
    assert names["wg-fem-v1|region=rear-chamber|group=cone|kind=moving"]["drive_channel_id"] == "drive-LF"
    assert built["stats"]["boundary_surfaces"] == 6
    assert built["stats"]["wall_surfaces"] == 4
    assert built["stats"]["size_fields"] == 2
    assert built["stats"]["tetrahedra"] > 0

    domain = read_fem_region_domain(built["msh_text"], "rear-chamber")
    assert domain.tetrahedra.shape[1] == 4
    cone = domain.surfaces["wg-fem-v1|region=rear-chamber|group=cone|kind=moving"]
    port = domain.surfaces["wg-fem-v1|region=rear-chamber|group=port|kind=interface"]
    assert np.allclose(domain.points_mm[cone.ravel(), 2], 0.0)
    assert np.allclose(domain.points_mm[port.ravel(), 0], 40.0)
    # The boundary triangles close the box: total area equals its surface.
    triangles = np.concatenate(list(domain.surfaces.values()))
    corners = domain.points_mm[triangles]
    area = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()
    assert area == pytest.approx(2 * (40 * 30 + 40 * 20 + 30 * 20), rel=1e-9)
    # The tetrahedra fill the box.
    tets = domain.points_mm[domain.tetrahedra]
    volume = np.abs(np.einsum("ij,ij->i", tets[:, 1] - tets[:, 0], np.cross(tets[:, 2] - tets[:, 0], tets[:, 3] - tets[:, 0]))).sum() / 6
    assert volume == pytest.approx(40 * 30 * 20, rel=1e-9)
    # The finer diaphragm group really is finer than the region size.
    cone_edges = np.linalg.norm(domain.points_mm[cone[:, 0]] - domain.points_mm[cone[:, 1]], axis=1)
    assert np.median(cone_edges) < 6.0


def test_a_face_that_matches_nothing_refuses_by_group(tmp_path):
    step = _write_step(tmp_path / "air.step")
    lost = _face(1200.0, (20.0, 15.0, 5.0), [0.0, 0.0, 1.0], (0, 0, 5), (40, 30, 5))
    region = _region()
    region["surface_groups"][0]["faces"] = [lost]
    with pytest.raises(FemRegionMeshError, match="Face 1 of surface group 'Cone'.*matches no surface"):
        _mesh(step, region)


def test_two_groups_cannot_claim_one_surface(tmp_path):
    step = _write_step(tmp_path / "air.step")
    region = _region()
    region["surface_groups"][1]["faces"] = [CONE]
    with pytest.raises(FemRegionMeshError, match="match the same surface"):
        _mesh(step, region)


def test_a_step_with_two_solids_refuses(tmp_path):
    step = _write_step(tmp_path / "two.step", boxes=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)))
    with pytest.raises(FemRegionMeshError, match="exactly one closed solid; its STEP holds 2"):
        _mesh(step, _region())


def _surface(tag, area, centroid, low, high, normal=None):
    return SurfaceDescriptor(tag, area, centroid, (low, high), normal)


def test_matching_rejects_a_normal_that_disagrees_and_ambiguity():
    face = _face(100.0, (0.0, 0.0, 0.0), [0.0, 0.0, 1.0], (-5, -5, 0), (5, 5, 0))
    tilted = _surface(1, 100.0, (0.0, 0.0, 0.0), (-5, -5, 0), (5, 5, 0), (1.0, 0.0, 0.0))
    region = {"region_id": "r", "surface_groups": [{"group_id": "g", "name": "G", "faces": [face]}]}
    with pytest.raises(FemRegionMeshError, match="matches no surface"):
        assign_faces(region, [tilted])
    flipped = _surface(2, 100.0, (0.0, 0.0, 0.0), (-5, -5, 0), (5, 5, 0), (0.0, 0.0, -1.0))
    assert assign_faces(region, [flipped]) == {"g": [2]}
    twin = _surface(3, 100.0, (0.0, 0.0, 0.0), (-5, -5, 0), (5, 5, 0), None)
    with pytest.raises(FemRegionMeshError, match="more than one surface"):
        assign_faces(region, [flipped, twin])


def test_the_isolated_child_meshes_a_region_and_keeps_typed_refusals(tmp_path, monkeypatch):
    from server.cadlink.build_flags import FEM_REGIONS_ENV
    from server.cadlink.intake.isolated import build_fem_region_mesh_isolated

    monkeypatch.setenv(FEM_REGIONS_ENV, "1")

    step = _write_step(tmp_path / "air.step")
    built = build_fem_region_mesh_isolated(step, _region())
    assert built["msh_text"].startswith("$MeshFormat\n4.1")
    assert built["stats"]["wall_surfaces"] == 4

    region = _region()
    region["surface_groups"][1]["faces"] = [CONE]
    with pytest.raises(FemRegionMeshError, match="match the same surface"):
        build_fem_region_mesh_isolated(step, region)


def test_fem_regions_are_advertised_only_to_an_opted_in_build(monkeypatch):
    from server.cadlink.build_flags import FEM_REGIONS_ENV
    from server.cadlink.fusion.fusion_delivery import capabilities

    monkeypatch.delenv(FEM_REGIONS_ENV, raising=False)
    assert "femRegions" not in capabilities()
    monkeypatch.setenv(FEM_REGIONS_ENV, "1")
    assert capabilities()["femRegions"] == 1


@pytest.mark.parametrize("kind", ["rigid", "moving"])
def test_user_walls_group_keeps_all_boundary_triangles_and_separate_stats(tmp_path, kind):
    from server.mesh.fem_regions import fem_region_mesh_stats

    region = _region()
    region["surface_groups"][0].update(group_id="walls", name="User walls", kind=kind)
    if kind == "rigid":
        region["surface_groups"][0].pop("drive_channel_id")
    built = _mesh(_write_step(tmp_path / "air.step"), region)
    stats = fem_region_mesh_stats(built, region)
    assert stats["surface_groups"]["walls"]["kind"] == kind
    assert stats["surface_groups"]["walls"]["triangle_count"] > 0
    assert stats["surface_groups"]["walls"]["area_mm2"] == pytest.approx(1200)
    assert stats["surface_groups"]["_walls"]["kind"] == "rigid"
    assert stats["surface_groups"]["_walls"]["triangle_count"] > 0
    assert stats["surface_groups"]["_walls"]["area_mm2"] == pytest.approx(3400)
    assert sum(group["area_mm2"] for group in stats["surface_groups"].values()) == pytest.approx(5200)


@pytest.mark.parametrize("damage", ["missing-name", "missing-triangles", "duplicate-triangles"])
def test_reader_refuses_incomplete_or_duplicate_boundary_coverage(tmp_path, damage):
    import meshio

    region = _region()
    built = _mesh(_write_step(tmp_path / "air.step"), region)
    path = tmp_path / "damaged.msh"
    path.write_text(built["msh_text"])
    mesh = meshio.read(path)
    name = physical_name(region["region_id"], "_walls", "rigid")
    tag = mesh.field_data[name][0]
    if damage == "missing-name":
        del mesh.field_data[name]
    else:
        for block, physical in zip(mesh.cells, mesh.cell_data["gmsh:physical"]):
            if block.type == "triangle" and np.all(physical == tag):
                if damage == "missing-triangles":
                    block.data = block.data[:-1]
                else:
                    block.data = np.concatenate([block.data, block.data[:1]])
                break
        # Rebuild matching cell data for meshio's writer.
        for index, block in enumerate(mesh.cells):
            for arrays in mesh.cell_data.values():
                old = arrays[index]
                arrays[index] = np.resize(old, len(block.data))
    meshio.write(path, mesh, file_format="gmsh22", binary=False)
    with pytest.raises(FemRegionMeshError, match="rear-chamber.*(group tag|surface groups)"):
        read_fem_region_domain(path.read_text(), region["region_id"])


@pytest.mark.parametrize("damage", ["missing", "empty", "wrong-kind"])
def test_stats_require_each_declared_group_and_kind(tmp_path, monkeypatch, damage):
    from server.mesh import fem_regions

    region = _region()
    built = _mesh(_write_step(tmp_path / "air.step"), region)
    domain = read_fem_region_domain(built["msh_text"], region["region_id"])
    name = physical_name(region["region_id"], "cone", "moving")
    surfaces = dict(domain.surfaces)
    if damage == "missing":
        del surfaces[name]
    elif damage == "empty":
        surfaces[name] = surfaces[name][:0]
    else:
        wrong = physical_name(region["region_id"], "cone", "rigid")
        surfaces[wrong] = surfaces.pop(name)
        built["physical_names"][wrong] = built["physical_names"].pop(name)
    monkeypatch.setattr(fem_regions, "read_fem_region_domain", lambda *args, **kwargs:
                        fem_regions.FemRegionDomain(domain.region_id, domain.points_mm, domain.tetrahedra, surfaces))
    with pytest.raises(FemRegionMeshError, match="rear-chamber.*Cone.*declared kind 'moving'"):
        fem_regions.fem_region_mesh_stats(built, region)


def test_mesh_scratch_uses_session_or_explicit_child_tree(tmp_path, monkeypatch):
    from server.mesh import fem_regions
    from server.platform import temp_session

    session = temp_session.TemporarySession.create(tmp_path)
    step = _write_step(tmp_path / "air.step")
    seen = []
    original = fem_regions.tempfile.TemporaryDirectory

    def record(*args, **kwargs):
        seen.append(Path(kwargs["dir"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(fem_regions.tempfile, "TemporaryDirectory", record)
    session.activate()
    try:
        built = _mesh(step, _region())
        read_fem_region_domain(built["msh_text"], "rear-chamber")
        child = session.path / "staging" / "tmp"
        child.mkdir(parents=True)
        fem_regions.fem_region_mesh_stats(built, _region(), scratch_dir=child)
        assert seen == [session.path, session.path, child]
    finally:
        session.close(remove=True)


def test_parent_accepts_child_stats_without_parsing_mesh(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from server.cadlink.intake import isolated
    from server.mesh import fem_regions

    monkeypatch.setenv("WG2_FEM_REGIONS", "1")
    artifact = tmp_path / "fem.msh"
    artifact.write_text("verified mesh")
    stats = {"tetrahedra": 42, "nodes": 24, "surface_groups": {"_walls": {"triangle_count": 12}}}

    @contextmanager
    def task(*args, **kwargs):
        yield SimpleNamespace(result={"built": {"stats": stats}}, artifacts={"fem.msh": artifact})

    monkeypatch.setattr(isolated, "isolated_step_task", task)
    monkeypatch.setattr(fem_regions, "read_fem_region_domain", lambda *args, **kwargs:
                        pytest.fail("parent must not parse the FEM mesh"))
    result = isolated.build_fem_region_mesh_isolated(tmp_path / "air.step", _region())
    assert result["stats"] == stats
    assert result["msh_text"] == "verified mesh"


def test_shared_deadline_covers_child_slot_wait_and_staging(tmp_path, monkeypatch):
    from server.cadlink.intake import isolation

    source = tmp_path / "air.step"
    source.write_text("STEP")
    now = [1.0]
    monkeypatch.setattr(isolation.time, "monotonic", lambda: now[0])
    seen = []

    class Slot:
        def acquire(self, timeout):
            seen.append(timeout)
            now[0] = 4.0  # The queue used three seconds of the shared budget.
            return True
        def release(self):
            seen.append("released")

    def run(envelope, **kwargs):
        assert envelope["limits"]["wall_time_s"] == 6.0
        assert kwargs["budget"].wall_time_s == 6.0
        return isolation.ChildOutcome({}, {}, "")

    monkeypatch.setattr(isolation, "_CHILD_SLOT", Slot())
    monkeypatch.setattr(isolation, "_run_child", run)
    with isolation.isolated_step_task("fem_region", {}, step_path=source,
                                      budget=isolation.MESH_BUDGET, deadline=10.0, stage="mesh"):
        pass
    assert seen == [9.0, "released"]
    now[0] = 11.0
    with pytest.raises(isolation.ChildRefusal, match="total FEM regions wall-clock budget"):
        with isolation.isolated_step_task("fem_region", {}, step_path=source,
                                          budget=isolation.MESH_BUDGET, deadline=10.0, stage="mesh"):
            pytest.fail("an expired budget cannot start a child")


@pytest.mark.parametrize("after_spawn", [7.0, 11.0])
def test_child_startup_consumes_shared_deadline_and_still_cleans_up(tmp_path, monkeypatch, after_spawn):
    from types import SimpleNamespace
    from server.cadlink.intake import isolation

    source = tmp_path / "air.step"
    source.write_text("STEP")
    now = [1.0]
    monkeypatch.setattr(isolation, "time", SimpleNamespace(monotonic=lambda: now[0]))
    start = isolation.start_child_process
    observed = []

    def spawn(*args, **kwargs):
        process = start(*args, **kwargs)
        now[0] = after_spawn
        return process

    def supervise(process, budget, **kwargs):
        observed.append(budget.wall_time_s)
        return "test deadline refusal"

    monkeypatch.setattr(isolation, "start_child_process", spawn)
    monkeypatch.setattr(isolation, "_supervise", supervise)
    message = "total FEM regions wall-clock budget" if after_spawn > 10.0 else "test deadline refusal"
    with pytest.raises(isolation.ChildRefusal, match=message):
        with isolation.isolated_step_task(
            "fem_region", {}, step_path=source, budget=isolation.MESH_BUDGET, deadline=10.0,
            stage="mesh", entrypoint="server.tests.isolation_doubles",
        ):
            pytest.fail("deadline refusal must not publish a result")
    assert observed == ([] if after_spawn > 10.0 else [3.0])
