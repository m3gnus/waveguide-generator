"""First-solve CAD sizing, independent of source inventory and saved setups."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from server.cadlink.joblane.default_setup import default_setup


def _source(role: str, suggestion: float) -> dict:
    return {
        "id": role, "role": role, "required": True,
        "suggested_resolution_mm": suggestion, "default_drive_channel_id": role,
    }


def test_party_three_way_uses_owner_defaults() -> None:
    manifest = {"sources": [_source("HF", 4), _source("MF", 15), _source("LF", 30)]}
    assert default_setup(manifest).geometry["mesh"] == {
        "rigid_size_mm": 25, "transition_mm": 100,
        "source_size_mm": {"HF": 6, "MF": 15, "LF": 25},
    }


@pytest.mark.parametrize(("role", "suggestion", "expected"), [
    ("HF", 4, 6), (" hf ", 4, 6), ("MF", 15, 15), ("LF", 30, 25),
    ("PASSIVE_CARDIOID", 25, 25), ("PORT_EXIT", 25, 25),
    ("HF", 2.5, 2.5), ("LF", 40, 40), ("source", 7, 7),
])
def test_only_fixed_role_suggestions_are_replaced(role, suggestion, expected) -> None:
    mesh = default_setup({"sources": [_source(role, suggestion)]}).geometry["mesh"]
    assert mesh == {
        "rigid_size_mm": 25, "transition_mm": 100,
        "source_size_mm": {role: expected},
    }


def test_nowaistbanding_sized_step_mesh_is_well_below_solver_ceiling(tmp_path: Path, record_property) -> None:
    """A 500 x 287 x 275 mm single body, one HF face, real quarter meshing.

    The whole front is a source (larger than the real throat), so this is a
    deliberately conservative planar sizing regression, not an exact copy of
    the curved original. The real mesher and its unchanged cap both run.
    """

    gmsh = pytest.importorskip("gmsh")
    from hornlab_mesher.step_import import advanced_face_order_for_surfaces, gmsh_surface_tags
    from server.mesh.gmsh_worker import _run_in_gmsh_session
    from server.mesh.imported import build_imported_mesh

    step_path = tmp_path / "assembly.step"

    def write_box() -> tuple[int, float]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.addBox(-250, -143.5, 0, 500, 287, 275)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step_path))
        surfaces = gmsh_surface_tags()
        face_order = advanced_face_order_for_surfaces(step_path, surfaces)
        front = next(tag for tag in surfaces
                     if abs(gmsh.model.occ.getCenterOfMass(2, tag)[2]) < 1e-6)
        area = gmsh.model.occ.getMass(2, front)
        face_id = face_order[surfaces.index(front)]
        gmsh.clear()
        return face_id, area

    face_id, area = _run_in_gmsh_session(write_box)
    source = {
        **_source("HF", 4), "instance_id": None,
        "patch_policy": "single-connected", "expected_connected_components": 1,
        "selectors": {"advanced_face_indices": [face_id]},
        "observed": {"face_count": 1, "total_area_mm2": area,
                     "per_face_area_mm2": [area], "bodies": ["box"]},
    }
    manifest = {
        "sources": [source], "instances": [],
        "coordinate_system": {"length_unit": "mm"},
        "assembly": {"n_bodies_expected": 1, "bbox_mm": [[-250, -143.5, 0], [250, 143.5, 275]]},
        "scope": {"included": [], "fem_air_volumes": []},
    }
    mesh = default_setup(manifest).geometry["mesh"]
    assert mesh == {"rigid_size_mm": 25, "transition_mm": 100, "source_size_mm": {"HF": 6}}
    # Independent area/edge-length estimate of a quarter-domain planar mesh.
    area_mm2 = 2 * (500 * 287 + 500 * 275 + 287 * 275)
    quarter_estimate = ((area_mm2 - area) / 25**2 + area / 6**2) / math.sqrt(3)
    assert quarter_estimate < 3000
    result = _run_in_gmsh_session(
        build_imported_mesh, step_path, manifest, mesh,
        options={"symmetry_mode": "auto"}, include_viewport_mesh=False,
    )
    stats = result["stats"]
    record_property("triangle_count", stats["triangle_count"])
    record_property("quarter_area_estimate", quarter_estimate)
    assert stats["domain_multiplier"] == 4
    assert 0 < stats["triangle_count"] < 6000
