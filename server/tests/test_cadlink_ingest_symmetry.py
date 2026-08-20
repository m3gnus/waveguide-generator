"""Real-geometry ingests that actually reduce the domain.

Every other `cut_planes` in the suite is hand-written into a record. These
build a small symmetric horn, put it through the real ingest, and assert on
what came back: the reduction itself, the tags and areas that must survive it,
the placement a vertically offset return arrives with, and the refusal when the
meshed boundary denies the cut.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from server.cadlink.ingest import ingest_bundle
from server.cadlink.store import CadLinkStore
from server.mesh.gmsh_worker import _run_in_gmsh_session


def _horn_points(n_phi: int = 16, n_length: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """A round horn: symmetric about x0 and y0, one-sided in z."""

    inner = np.empty((n_phi, n_length + 1, 3), dtype=float)
    for phi_index in range(n_phi):
        phi = math.tau * phi_index / n_phi
        for length_index in range(n_length + 1):
            fraction = length_index / n_length
            radius = 10.0 + 20.0 * fraction
            inner[phi_index, length_index] = (
                radius * math.cos(phi),
                radius * math.sin(phi),
                60.0 * fraction,
            )
    outer = inner.copy()
    radial = np.linalg.norm(outer[:, :, :2], axis=2)
    scale = (radial + 4.0) / radial
    outer[:, :, 0] *= scale
    outer[:, :, 1] *= scale
    return inner, outer


def _write_horn_step(path: Path, *, vertical_offset_mm: float) -> Any:
    from hornlab_mesher.cad import write_step
    from hornlab_mesher.geometry import PointGridHornGeometry

    inner, outer = _horn_points()
    geometry = PointGridHornGeometry(
        inner_points=inner,
        outer_points=outer,
        wall_thickness_mm=4.0,
        vertical_offset_mm=vertical_offset_mm,
    )
    _step_path, info = _run_in_gmsh_session(
        write_step, geometry, path, open_throat=False
    )
    return info


def _measure_throat(step_path: Path) -> dict[str, float]:
    """Measure the planar throat face of the written body, as CAD would."""

    import gmsh
    from hornlab_mesher.step_import import gmsh_surface_tags

    def measure() -> dict[str, float]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(step_path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        planar = [
            surface
            for surface in gmsh_surface_tags()
            if str(gmsh.model.getType(2, surface)).casefold() == "plane"
        ]
        assert len(planar) == 1, planar
        centre = [float(value) for value in gmsh.model.occ.getCenterOfMass(2, planar[0])]
        area = float(gmsh.model.occ.getMass(2, planar[0]))
        gmsh.clear()
        return {"centre": centre, "area_mm2": area}

    return _run_in_gmsh_session(measure)


def _fingerprint() -> dict[str, Any]:
    return {"is_solid": True, "volume_mm3": 1.0, "bbox_mm": [0, 0, 0, 1, 1, 1]}


# One valid wgr_ ULID per bundle in this module; the store keys returns by it.
_RETURN_IDS = {
    "round": "wgr_01J5A8QK3M9T2XVBH0RD7NWEA0",
    "offset": "wgr_01J5A8QK3M9T2XVBH0RD7NWEB0",
    "capped": "wgr_01J5A8QK3M9T2XVBH0RD7NWEC0",
    "full": "wgr_01J5A8QK3M9T2XVBH0RD7NWED0",
}


def _horn_bundle(
    tmp_path: Path, name: str, *, vertical_offset_mm: float = 0.0
) -> Path:
    bundle = tmp_path / "workspace" / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    info = _write_horn_step(bundle / "assembly.step", vertical_offset_mm=vertical_offset_mm)
    step = (bundle / "assembly.step").read_bytes()
    throat = _measure_throat(bundle / "assembly.step")
    area = float(throat["area_mm2"])
    manifest = {
        "wgreturn_version": "1.0",
        "required_features": [
            "checksummed-files-v1",
            "assembly-frame-v1",
            "instance-records-v1",
        ],
        "return": {
            "id": _RETURN_IDS[name],
            "created_at": "2026-08-18T09:14:03Z",
        },
        "generator": {
            "adapter": "test",
            "adapter_version": "1",
            "cad_app": "test",
            "cad_version": "1",
        },
        "document": {"name": name, "native_id": None},
        "coordinate_system": {
            "length_unit": "mm",
            "handedness": "right",
            "matrix_convention": "row-major-local-to-parent",
            "solver_anchor_instance_id": "anchor",
        },
        "assembly": {
            "file": "assembly.step",
            "n_bodies_expected": 1,
            "bbox_mm": [list(info.bounding_box_mm[0]), list(info.bounding_box_mm[1])],
        },
        "files": {
            "assembly.step": {
                "sha256": "sha256:" + hashlib.sha256(step).hexdigest(),
                "size_bytes": len(step),
                "media_type": "model/step",
                "purpose": "exterior-assembly",
            }
        },
        "scope": {
            "selection": "root",
            "included": [
                {
                    "object_id": name,
                    "name": name,
                    "body_kind": "solid",
                    "visible": True,
                    "external_reference": "local",
                    "wglink_instance_id": "anchor",
                }
            ],
            "skipped": [],
            "fem_air_volumes": [],
            "status": "clean",
        },
        "instances": [
            {
                "instance_id": "anchor",
                "design_id": "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
                "lineage_id": None,
                "edit_version": 1,
                "design_hash": "sha256:" + "1" * 64,
                # The design config WG shipped with the export, echoed back by
                # the CAD app: the only record of the placement it applied.
                "config": {
                    "root": {
                        "mesh": {"vertical_offset": {"value": vertical_offset_mm, "raw": None}}
                    }
                },
                "export_id": "wge_01J4Y2ZD000000000000000000",
                "export_sequence": 1,
                "geometry_hash": "sha256:" + "2" * 64,
                "origin_bundle_id": "wgb_01J4Y2ZF000000000000000000",
                "build_mode": "freestanding",
                "parameter_prefix": "wg_horn_",
                "occurrence_path": name,
                "assembly_from_link": [
                    [1, 0, 0, 0],
                    [0, 1, 0, 0],
                    [0, 0, 1, 0],
                    [0, 0, 0, 1],
                ],
                "chirality": "original",
                "body_evidence": {
                    "local_body_state": "unmodified",
                    "baseline_fingerprint": _fingerprint(),
                    "observed_fingerprint": _fingerprint(),
                    "observed_at": "2026-08-18T09:14:03Z",
                },
                "source_contract": {
                    "role": "HF",
                    "throat_z_mm": throat["centre"][2],
                    "throat_plane_link": {
                        "origin_mm": list(throat["centre"]),
                        "normal": [0, 0, 1],
                    },
                    "axis_link": {
                        "origin_mm": list(throat["centre"]),
                        "direction": [0, 0, 1],
                    },
                    "throat_diameter_mm": math.sqrt(4.0 * area / math.pi),
                    "expected_disc_area_mm2": area,
                },
            }
        ],
        "sources": [
            {
                "id": "source-hf",
                "role": "HF",
                "instance_id": "anchor",
                "required": True,
                "default_drive_channel_id": "drive-hf",
                "patch_policy": "single-connected",
                "expected_connected_components": 1,
                "selectors": {"linked_throat": {"instance_id": "anchor"}},
                "observed": {
                    "face_count": 1,
                    "total_area_mm2": area,
                    "per_face_area_mm2": [area],
                    "bodies": [name],
                },
                "suggested_resolution_mm": 8,
            }
        ],
        "acoustics": None,
    }
    (bundle / "wgreturn.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return bundle


_SIZES = {
    "rigid_size_mm": 20,
    "transition_mm": 30,
    "source_size_mm": {"source-hf": 8},
}


def _ingest(
    tmp_path: Path,
    bundle: Path,
    *,
    symmetry_mode: str = "auto",
) -> dict[str, Any]:
    pytest.importorskip("gmsh")
    data_dir = tmp_path / "data"
    store = CadLinkStore(data_dir / "cadlink.db")
    return _run_in_gmsh_session(
        ingest_bundle,
        bundle,
        _SIZES,
        [],
        store,
        data_dir,
        prep_options={"symmetry_mode": symmetry_mode},
    )


def test_symmetric_return_is_cut_to_a_quarter_with_tags_and_areas_intact(
    tmp_path: Path,
) -> None:
    pytest.importorskip("gmsh")
    record = _ingest(tmp_path, _horn_bundle(tmp_path, "round"))

    assert record["symmetry"]["cut_planes"] == ["x0", "y0"]
    # z0 is never a candidate: the native solvers cannot mirror it.
    assert record["symmetry"]["candidate_planes"] == ["x0", "y0"]
    assert set(record["symmetry"]["planes"]) == {"x0", "y0"}

    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    assert verification["detected_planes"] == ["x0", "y0"]
    assert verification["off_plane_free_edge_count"] == 0
    assert "fallback" not in verification
    assert record["mesh"]["integrity"]["off_plane_open_edge_count"] == 0

    # Tags survive the cut and its OCC boolean remapping.
    mesh_text = Path(record["mesh_store_path"]).read_text(encoding="utf-8")
    assert '"wg-import-v1|rigid"' in mesh_text
    assert (
        '"wg-import-v1|tag=101|source_id=source-hf|instance_id=anchor|role=HF"'
        in mesh_text
    )
    stats = record["mesh"]["stats"]
    assert int(stats["tag_counts"]["101"]) > 0
    assert stats["domain_multiplier"] == 4.0

    # Dense storage follows the verified quarter domain through the canonical
    # imported-symmetry mapping, and exposes the same diagnostics as a
    # parametric mesh. BEMPP mirrors four copies while Metal solves the retained
    # P1 vertices directly.
    assert stats["dense_solver_domain_multiplier"] == 4
    assert stats["dense_solver_used_vertex_count"] > 0
    assert stats["dense_solver_metal_dof_count"] == stats["dense_solver_used_vertex_count"]
    assert stats["dense_solver_metal_bytes_per_dof_squared"] == 88
    assert stats["dense_solver_bempp_bytes_per_vertex_squared"] == 96
    assert stats["dense_solver_aperture_triangle_count"] == 0
    assert stats["dense_solver_metal_estimate_bytes"] == (
        88 * stats["dense_solver_metal_dof_count"] ** 2
    )
    assert stats["dense_solver_bempp_estimate_bytes"] == (
        96 * stats["dense_solver_used_vertex_count"] ** 2
    )
    assert stats["dense_solver_memory_estimate_bytes"] == max(
        stats["dense_solver_metal_estimate_bytes"],
        stats["dense_solver_bempp_estimate_bytes"],
    )

    # The throat disc straddles both cut planes, so a quarter of it is left.
    provenance = record["post_cut_source_areas"]["source-hf"]
    assert provenance["predicted_retained_fraction"] == pytest.approx(0.25)
    assert provenance["retained_fraction"] == pytest.approx(0.25, rel=1.0e-6)
    assert provenance["retained_child_area_mm2"] == pytest.approx(
        0.25 * provenance["parent_area_mm2"], rel=1.0e-6
    )

    bounds = record["mesh"]["stats"]["bounds_m"]
    assert bounds["min_x"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["min_y"] == pytest.approx(0.0, abs=1.0e-9)

    derivation = record["polar_grid_derivation"]
    assert derivation["axes"]["horizontal"]["minimum_deg"] == 0.0
    assert derivation["axes"]["vertical"]["minimum_deg"] == 0.0


def test_user_can_force_the_same_symmetric_return_to_remain_full_domain(
    tmp_path: Path,
) -> None:
    pytest.importorskip("gmsh")
    record = _ingest(
        tmp_path,
        _horn_bundle(tmp_path, "full"),
        symmetry_mode="full",
    )

    assert record["symmetry"]["requested_mode"] == "full"
    assert record["symmetry"]["cut_planes"] == []
    assert record["mesh"]["stats"]["domain_multiplier"] == 1.0
    assert record["mesh"]["stats"]["dense_solver_domain_multiplier"] == 1
    bounds = record["mesh"]["stats"]["bounds_m"]
    assert bounds["min_x"] < 0.0 < bounds["max_x"]
    assert bounds["min_y"] < 0.0 < bounds["max_y"]


def test_vertically_offset_return_keeps_the_quarter_reduction(tmp_path: Path) -> None:
    """The placement CAD keeps must not cost the design its reduction.

    The exported body sits at y = mesh.vertical_offset on purpose. Ingest
    recentres it, so the mirror plane is where the solver can use it, and the
    throat datum -- the observation origin -- moves with the geometry.
    """

    pytest.importorskip("gmsh")
    offset = _ingest(tmp_path, _horn_bundle(tmp_path, "offset", vertical_offset_mm=80.0))

    recentre = offset["normalisation"]["vertical_recentre"]
    assert recentre["applied"] is True
    assert recentre["recorded_offset_mm"] == 80.0
    assert recentre["model_y_midpoint_mm"] == pytest.approx(80.0, abs=1.0e-6)
    assert offset["normalisation"]["matrix"][1][3] == pytest.approx(-80.0)

    assert offset["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert offset["symmetry_verification"]["verified"] is True

    bounds = offset["mesh"]["stats"]["bounds_m"]
    assert bounds["min_x"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["min_y"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["max_y"] == pytest.approx(0.030, abs=1.0e-6)

    # The observation frame is the throat datum, and it rode the same matrix.
    frame = offset["anchor"]["throat_frame"]
    assert frame["origin_m"][1] == pytest.approx(0.0, abs=1.0e-9)
    assert frame["source_center_m"][1] == pytest.approx(0.0, abs=1.0e-9)

    # Nothing acoustic changed: the same body, the same quarter of the same
    # source, in the frame the solver mirrors in.
    centred = _ingest(tmp_path / "centred", _horn_bundle(tmp_path / "centred", "round"))
    assert centred["post_cut_source_areas"].keys() == offset["post_cut_source_areas"].keys()
    for source_id, provenance in centred["post_cut_source_areas"].items():
        placed = offset["post_cut_source_areas"][source_id]
        assert placed["parent_area_mm2"] == pytest.approx(
            provenance["parent_area_mm2"], rel=1.0e-9
        )
        assert placed["retained_child_area_mm2"] == pytest.approx(
            provenance["retained_child_area_mm2"], rel=1.0e-9
        )
    assert offset["mesh"]["stats"]["triangle_count"] == centred["mesh"]["stats"][
        "triangle_count"
    ]


def test_a_leaking_reduced_domain_falls_back_to_the_full_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hole in a reduced domain is a wrong answer, not a slower one.

    The boolean that cuts the model can drop a face it fails to intersect, so
    the leak is injected where it would appear: in the meshed boundary, after
    the cut, on a rigid wall away from both cut planes. Nothing upstream can
    see it -- the OCC areas still balance and ``integrity['valid']`` excludes
    open edges on purpose -- which is why it used to be solved in silence.
    """

    pytest.importorskip("gmsh")
    import meshio
    from hornlab_mesher import step_import

    real_postprocess = step_import.postprocess_mesh
    punctured = {"count": 0}

    def puncture(mesh, source_specs, **kwargs):
        processed, repair, topology = real_postprocess(mesh, source_specs, **kwargs)
        if not kwargs.get("symmetry_planes"):
            return processed, repair, topology
        points = np.asarray(processed.points, dtype=float)
        triangles = np.asarray(processed.cells_dict["triangle"], dtype=np.int64)
        tags = np.asarray(
            processed.cell_data_dict["gmsh:physical"]["triangle"], dtype=np.int32
        )
        interior = next(
            index
            for index, triangle in enumerate(triangles)
            if tags[index] == 1
            and np.all(points[triangle, 0] > 1.0)
            and np.all(points[triangle, 1] > 1.0)
        )
        keep = np.ones(len(triangles), dtype=bool)
        keep[interior] = False
        punctured["count"] += 1
        return (
            meshio.Mesh(
                points=points,
                cells=[("triangle", triangles[keep])],
                cell_data={
                    "gmsh:physical": [tags[keep]],
                    "gmsh:geometrical": [tags[keep]],
                },
                field_data=processed.field_data,
            ),
            repair,
            topology,
        )

    monkeypatch.setattr(step_import, "postprocess_mesh", puncture)
    bundle = _horn_bundle(tmp_path, "capped")
    record = _ingest(tmp_path, bundle)

    assert punctured["count"] == 1
    assert record["symmetry"]["cut_planes"] == []
    assert "failed post-mesh verification" in record["symmetry"]["note"]
    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    assert verification["fallback"]["rejected_cut_planes"] == ["x0", "y0"]
    assert verification["fallback"]["capped_planes"] == []
    assert "leaks" in verification["fallback"]["reason"]

    finding = next(
        item for item in record["findings"] if item["kind"] == "symmetry-cut-unverified"
    )
    assert finding["blocking"] is True
    assert finding["rejected_cut_planes"] == ["x0", "y0"]
    assert "full domain was meshed" in finding["detail"]
    assert "Re-export" in finding["detail"]

    # A full domain is solvable, so the fallback mesh is a real artifact and
    # the polar sweep widened back out with it.
    assert record["mesh"]["stats"]["domain_multiplier"] == 1.0
    assert record["polar_grid_derivation"]["axes"]["vertical"]["minimum_deg"] == -180.0
    assert Path(record["mesh_store_path"]).is_file()

    # The next ingest serves the fallback mesh from cache; the finding is part
    # of the artifact's record, not of the meshing run that produced it.
    again = _ingest(tmp_path, bundle)
    assert punctured["count"] == 1
    assert again["mesh_cache_hit"] is True
    assert any(
        item["kind"] == "symmetry-cut-unverified" for item in again["findings"]
    )
