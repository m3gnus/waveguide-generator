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
        "wg-fem-v1|region=rear-chamber|group=walls|kind=rigid",
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


def test_the_isolated_child_meshes_a_region_and_keeps_typed_refusals(tmp_path):
    from server.cadlink.isolated import build_fem_region_mesh_isolated

    step = _write_step(tmp_path / "air.step")
    built = build_fem_region_mesh_isolated(step, _region())
    assert built["msh_text"].startswith("$MeshFormat\n4.1")
    assert built["stats"]["wall_surfaces"] == 4

    region = _region()
    region["surface_groups"][1]["faces"] = [CONE]
    with pytest.raises(FemRegionMeshError, match="match the same surface"):
        build_fem_region_mesh_isolated(step, region)


def test_fem_regions_are_advertised_only_to_an_opted_in_build(monkeypatch):
    from server.cadlink.fusion_delivery import FEM_REGIONS_ENV, capabilities

    monkeypatch.delenv(FEM_REGIONS_ENV, raising=False)
    assert "femRegions" not in capabilities()
    monkeypatch.setenv(FEM_REGIONS_ENV, "1")
    assert capabilities()["femRegions"] == 1
