"""Real-geometry ingests that actually reduce the domain.

Every other `cut_planes` in the suite is hand-written into a record. These
build a small symmetric horn, put it through the real ingest, and assert on
what came back: the reduction itself, the tags and areas that must survive it,
the placement a vertically offset return arrives with, and the refusal when the
meshed boundary denies the cut.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from scripts.imported_ingest_fixtures import echoed_design_config
from server.cadlink.intake.ingest import build_deferred_viewport, ingest_bundle, solve_model_sha256
from server.cadlink.intake.isolated import _inject_mesh_child_fault
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


#: The flat membrane: a planar disc at z = 0 facing into the bore, which is the
#: face a real return tags (the add-in's ``_throat_faces`` and WG's
#: ``geometry_candidate_matches`` both want a planar disc on the throat plane).
FLAT_THROAT_DISC = 0
#: The rounded-cap membrane. Its only planar face is the throat plug's REAR, at
#: z = -4 mm, facing away from the fluid in the bore, so the linked-throat
#: contract binds to a source that looks away from the open axis.
REAR_CAP = 1


def _write_horn_step(
    path: Path, *, vertical_offset_mm: float, source_shape: int = FLAT_THROAT_DISC
) -> Any:
    from hornlab_mesher.cad import write_step
    from hornlab_mesher.geometry import PointGridHornGeometry

    inner, outer = _horn_points()
    geometry = PointGridHornGeometry(
        inner_points=inner,
        outer_points=outer,
        wall_thickness_mm=4.0,
        vertical_offset_mm=vertical_offset_mm,
        source_shape=source_shape,
    )
    _step_path, info = _run_in_gmsh_session(
        write_step, geometry, path, open_throat=False
    )
    return info


def _planar_faces_nearest_bore_first(gmsh: Any, surfaces: list[int]) -> list[int]:
    """Planar faces, the one nearest the bore (highest z) first."""

    planar = [
        surface
        for surface in surfaces
        if str(gmsh.model.getType(2, surface)).casefold() == "plane"
    ]
    assert planar, "the horn fixture always has a planar throat face"
    return sorted(
        planar,
        key=lambda surface: -float(gmsh.model.occ.getCenterOfMass(2, surface)[2]),
    )


def _measure_throat(step_path: Path) -> dict[str, float]:
    """Measure the planar throat face of the written body, as CAD would.

    The throat is the planar face nearest the bore: the z = 0 disc of the flat
    membrane, or -- on the rounded cap, where it is the only planar face -- the
    plug's rear.
    """

    import gmsh
    from hornlab_mesher.step_import import gmsh_surface_tags

    def measure() -> dict[str, float]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(step_path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        throat = _planar_faces_nearest_bore_first(gmsh, gmsh_surface_tags())[0]
        centre = [float(value) for value in gmsh.model.occ.getCenterOfMass(2, throat)]
        area = float(gmsh.model.occ.getMass(2, throat))
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
    "placed": "wgr_01J5A8QK3M9T2XVBH0RD7NWEE0",
    "sent": "wgr_01J5A8QK3M9T2XVBH0RD7NWEF0",
    "sent-centred": "wgr_01J5A8QK3M9T2XVBH0RD7NWEG0",
    "legacy": "wgr_01J5A8QK3M9T2XVBH0RD7NWEH0",
}


# Where a CAD user moved and turned the linked instance: a general axis and
# angle, so no coordinate plane or axis survives the move by accident.
_PLACEMENT_AXIS = tuple(float(value) for value in np.array([1.0, 2.0, 3.0]) / math.sqrt(14.0))
_PLACEMENT_ANGLE_RAD = 0.7
_PLACEMENT_TRANSLATION_MM = (120.0, -45.0, 30.0)


def _placement_matrix() -> np.ndarray:
    """``assembly_from_link`` for the placement: rotate about the origin, then move."""

    k = np.asarray(_PLACEMENT_AXIS, dtype=float)
    cross = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    matrix = np.eye(4)
    matrix[:3, :3] = (
        np.eye(3)
        + math.sin(_PLACEMENT_ANGLE_RAD) * cross
        + (1.0 - math.cos(_PLACEMENT_ANGLE_RAD)) * (cross @ cross)
    )
    matrix[:3, 3] = _PLACEMENT_TRANSLATION_MM
    return matrix


def _place_step(path: Path) -> list[float]:
    """Move the written body the way CAD moves a linked occurrence.

    ``rotate`` and ``translate`` are rigid OCC moves, so the throat stays a
    ``Plane`` -- a moved occurrence in a CAD STEP keeps its analytic faces.
    Returns the placed bounding box.
    """

    import gmsh

    def place() -> list[float]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        bodies = gmsh.model.getEntities(3)
        assert bodies, "the horn fixture is written as a solid"
        gmsh.model.occ.rotate(bodies, 0.0, 0.0, 0.0, *_PLACEMENT_AXIS, _PLACEMENT_ANGLE_RAD)
        gmsh.model.occ.translate(bodies, *_PLACEMENT_TRANSLATION_MM)
        gmsh.model.occ.synchronize()
        types = {str(gmsh.model.getType(2, tag)) for _dim, tag in gmsh.model.getEntities(2)}
        assert "Plane" in types, types
        box = [float(value) for value in gmsh.model.getBoundingBox(-1, -1)]
        gmsh.write(str(path))
        gmsh.clear()
        return box

    return _run_in_gmsh_session(place)


def _horn_bundle(
    tmp_path: Path,
    name: str,
    *,
    vertical_offset_mm: float = 0.0,
    placed: bool = False,
    source_shape: int = FLAT_THROAT_DISC,
    echoed_offset_mm: float | None = None,
) -> Path:
    """``echoed_offset_mm`` is the design's offset at return time, when it was
    changed in WG after the link was inserted with ``vertical_offset_mm``."""

    bundle = tmp_path / "workspace" / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    info = _write_horn_step(
        bundle / "assembly.step",
        vertical_offset_mm=vertical_offset_mm,
        source_shape=source_shape,
    )
    # The contract is measured on the body as WG exported it: link coordinates.
    throat = _measure_throat(bundle / "assembly.step")
    area = float(throat["area_mm2"])
    bbox_mm = [list(info.bounding_box_mm[0]), list(info.bounding_box_mm[1])]
    assembly_from_link = np.eye(4)
    if placed:
        box = _place_step(bundle / "assembly.step")
        bbox_mm = [box[:3], box[3:]]
        assembly_from_link = _placement_matrix()
    return _write_return(
        bundle,
        name,
        bbox_mm=bbox_mm,
        assembly_from_link=assembly_from_link,
        config=echoed_design_config(
            vertical_offset_mm if echoed_offset_mm is None else echoed_offset_mm
        ),
        source_contract={
            "role": "HF",
            "throat_z_mm": throat["centre"][2],
            **_fusion_throat_datums(throat["centre"]),
            "throat_diameter_mm": math.sqrt(4.0 * area / math.pi),
            "expected_disc_area_mm2": area,
        },
        observed_area_mm2=area,
    )


def _write_return(
    bundle: Path,
    name: str,
    *,
    bbox_mm: list[list[float]],
    assembly_from_link: np.ndarray,
    config: dict[str, Any],
    source_contract: dict[str, Any],
    observed_area_mm2: float,
    build_mode: str = "freestanding",
) -> Path:
    """Write ``wgreturn.json`` for the ``assembly.step`` already in ``bundle``."""

    area = observed_area_mm2
    step = (bundle / "assembly.step").read_bytes()
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
            "bbox_mm": bbox_mm,
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
                "config": config,
                "export_id": "wge_01J4Y2ZD000000000000000000",
                "export_sequence": 1,
                "geometry_hash": "sha256:" + "2" * 64,
                "origin_bundle_id": "wgb_01J4Y2ZF000000000000000000",
                "build_mode": build_mode,
                "parameter_prefix": "wg_horn_",
                "occurrence_path": name,
                "assembly_from_link": assembly_from_link.tolist(),
                "chirality": "original",
                "body_evidence": {
                    "local_body_state": "unmodified",
                    "baseline_fingerprint": _fingerprint(),
                    "observed_fingerprint": _fingerprint(),
                    "observed_at": "2026-08-18T09:14:03Z",
                },
                "source_contract": source_contract,
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


def _fusion_throat_datums(throat_centre_mm: list[float]) -> dict[str, Any]:
    """The throat datums as WGLink reads them off its Fusion construction.

    ``WG_THROAT_PLANE`` is an offset of the link component's XY plane, so the
    plane origin Fusion reports is the link origin projected onto it -- not the
    throat centre. ``WG_AXIS`` is the intersection of ``WG_SOLVER_CUT_PLANE_X``
    and ``WG_GEOM_MIDPLANE_Y``, so it passes through the throat centre at
    ``y = mesh.vertical_offset`` (``wglink_core._build_datums``; today's live
    return carried ``[0, 0, 0]`` and ``[0, 80, 0]``).
    """

    x, y, z = (float(value) for value in throat_centre_mm)
    return {
        "throat_plane_link": {"origin_mm": [0.0, 0.0, z], "normal": [0, 0, 1]},
        "axis_link": {"origin_mm": [x, y, 0.0], "direction": [0, 0, 1]},
    }


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
    defer_viewport: bool = False,
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
        # These bundles name their design, and a real return is prepared with
        # that design open. Passing it keeps the project gate doing its job
        # here instead of these symmetry tests silently exercising the
        # no-target path that let a return build into whatever was open.
        expected_design_id="wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
        defer_viewport=defer_viewport,
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
    # The imported path reports the ceiling it was held to, with provenance.
    # The mesh is built in the isolated CAD child, whose environment is an
    # allowlist with no WG2_* variables (server/cadlink/intake/isolation.py), so the
    # child resolves the ceiling from this host with no override in force.
    from server.mesh.builder import resolve_dense_solver_memory_limit

    applied = resolve_dense_solver_memory_limit(environ={})
    assert stats["dense_solver_memory_limit_bytes"] == applied.bytes
    assert stats["dense_solver_memory_limit_source"] == applied.source
    assert stats["dense_solver_physical_memory_source"] == applied.physical.source
    assert stats["dense_solver_physical_memory_known"] is applied.physical.known

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
    # source, in the frame the solver mirrors in. Compare the physical and
    # topological contracts rather than requiring byte-identical unstructured
    # tessellations. OCC surfaces that differ only by floating-point noise can
    # legitimately make Gmsh choose a different diagonal or add a few nodes.
    centred = _ingest(tmp_path / "centred", _horn_bundle(tmp_path / "centred", "round"))
    assert centred["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert centred["symmetry_verification"]["verified"] is True

    offset_stats = offset["mesh"]["stats"]
    centred_stats = centred["mesh"]["stats"]
    assert offset_stats["domain_multiplier"] == centred_stats["domain_multiplier"] == 4.0
    assert (
        offset_stats["dense_solver_domain_multiplier"]
        == centred_stats["dense_solver_domain_multiplier"]
        == 4
    )
    assert offset_stats["bounds_m"] == pytest.approx(
        centred_stats["bounds_m"], rel=0.0, abs=1.0e-9
    )
    assert offset_stats["tag_counts"].keys() == centred_stats["tag_counts"].keys()
    assert all(int(count) > 0 for count in offset_stats["tag_counts"].values())
    assert all(int(count) > 0 for count in centred_stats["tag_counts"].values())
    assert offset["healing"]["topology_before"] == centred["healing"]["topology_before"]
    assert offset["healing"]["topology_after"] == centred["healing"]["topology_after"]

    centred_frame = centred["anchor"]["throat_frame"]
    assert offset["anchor"]["throat_frame"].keys() == centred_frame.keys()
    for name, vector in centred_frame.items():
        assert offset["anchor"]["throat_frame"][name] == pytest.approx(
            vector, rel=0.0, abs=1.0e-9
        )

    assert centred["post_cut_source_areas"].keys() == offset["post_cut_source_areas"].keys()
    for source_id, provenance in centred["post_cut_source_areas"].items():
        placed = offset["post_cut_source_areas"][source_id]
        assert placed["parent_area_mm2"] == pytest.approx(
            provenance["parent_area_mm2"], rel=1.0e-9
        )
        assert placed["retained_child_area_mm2"] == pytest.approx(
            provenance["retained_child_area_mm2"], rel=1.0e-9
        )
    for record in (offset, centred):
        verification = record["symmetry_verification"]
        assert verification["cut_planes"] == ["x0", "y0"]
        assert verification["detected_planes"] == ["x0", "y0"]
        assert verification["off_plane_free_edge_count"] == 0
        assert verification["integrity_off_plane_open_edge_count"] == 0
        assert "fallback" not in verification

        integrity = record["mesh"]["integrity"]
        assert integrity["valid"] is True
        assert integrity["orientation_valid"] is True
        assert integrity["degenerate_triangle_count"] == 0
        assert integrity["duplicate_triangle_count"] == 0
        assert integrity["nonmanifold_edge_count"] == 0
        assert integrity["inconsistent_edge_count"] == 0
        assert integrity["self_intersection"]["intersecting_triangle_count"] == 0

    # Translation must not weaken the requested mesh-density ceiling. The
    # exact triangle inventory is not canonical, but a large complexity jump
    # would still expose a placement-sensitive sizing or topology regression.
    offset_frequency = offset["mesh"]["metadata"]["mesh_frequency_validation"]
    centred_frequency = centred["mesh"]["metadata"]["mesh_frequency_validation"]
    for frequency in (offset_frequency, centred_frequency):
        assert frequency["global_max_edge_m"] <= _SIZES["rigid_size_mm"] * 1.0e-3
        assert frequency["per_source"]["source-hf"]["max_edge_m"] <= (
            _SIZES["source_size_mm"]["source-hf"] * 1.0e-3
        )
    complexity_ratio = offset_stats["triangle_count"] / centred_stats["triangle_count"]
    assert 0.95 <= complexity_ratio <= 1.05
    vertex_ratio = (
        offset_stats["dense_solver_used_vertex_count"]
        / centred_stats["dense_solver_used_vertex_count"]
    )
    assert 0.95 <= vertex_ratio <= 1.05


def test_the_fusion_datum_model_matches_a_live_wglink_return() -> None:
    """``_fusion_throat_datums`` is a model of Fusion; this pins it to the real thing.

    WGLink's datum code needs Fusion (``wglink_core`` imports ``adsk`` and
    reads the datums' ``geometry`` off Fusion construction objects), so the
    round trip cannot run it. These are the datums the live 23 Nov return of
    260308tritonia-q carried (Mesh.VerticalOffset 80, throat at z = 0).
    """

    live = {
        "throat_plane_link": {"origin_mm": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0]},
        "axis_link": {"origin_mm": [0.0, 80.0, 0.0], "direction": [0.0, 0.0, 1.0]},
    }
    assert _fusion_throat_datums([0.0, 80.0, 0.0]) == live


def _sent_design_bundle(
    tmp_path: Path, design: Any, *, name: str = "sent"
) -> Path:
    """Send ``design`` to CAD and return what WGLink sends back, unedited.

    The outbound leg is real: the export serializer writes the WGLink bundle
    exactly as Send to CAD does, so the echoed config and the datums are the
    ones a live return carries. Fusion is not here, so its two products are
    reproduced from that bundle the way the add-in makes them: the body is the
    same CAD geometry written as a solid with its throat closed, and the
    source contract is read off the construction ``wglink_core._build_datums``
    builds from the bundle's datums (see :func:`_fusion_throat_datums`).

    Since bundle format 1.3 the sent geometry has its throat centre at the
    origin whatever the design's vertical offset, so this is the return of a
    throat-origin link; the design config is echoed unchanged. A link built
    before that, with the offset baked in, is ``_horn_bundle``'s
    ``vertical_offset_mm``.
    """

    from hornlab_mesher.cad import write_step

    from server.exports.cad_geometry import resolve_cad_geometry
    from server.exports.wglink_geometry import build_bundle

    design_id = "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M"
    staged = tmp_path / "outbound.wglink"
    _run_in_gmsh_session(
        build_bundle,
        design.model_dump(mode="json", by_alias=True),
        str(staged),
        {
            "bundleId": "wgb_01J4Y2ZF000000000000000000",
            "createdAt": "2026-11-23T14:47:00Z",
            "designId": design_id,
            "editVersion": 1,
            "designHash": "sha256:" + "1" * 64,
            "exportId": "wge_01J4Y2ZD000000000000000000",
            "sequence": 1,
            "parentExportId": None,
        },
        "wgl_01J4Y2WZQK8Z3TFD3E7V9XKQ4N",
        "sent horn",
        "sent_horn",
        "0.0.0-test",
    )
    outbound = json.loads((staged / "wglink.json").read_text(encoding="utf-8"))
    datums = outbound["datums"]
    parameters = {item["name"]: item for item in outbound["parameters"]}

    bundle = tmp_path / "workspace" / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    # WGLink closes the lofted throat with a planar patch: the face its
    # ``_throat_faces`` tags and the contract describes.
    body = dataclasses.replace(
        resolve_cad_geometry(design).resolved.geometry, source_shape=FLAT_THROAT_DISC
    )
    _step, info = _run_in_gmsh_session(
        write_step, body, bundle / "assembly.step", open_throat=False
    )
    diameter = float(parameters["wg_sent_horn_throat_dia"]["value"])
    area = math.pi * diameter * diameter / 4.0
    # Where Fusion's datums put the throat: on WG_SOLVER_CUT_PLANE_X, on
    # WG_GEOM_MIDPLANE_Y, in WG_THROAT_PLANE.
    centre = [
        float(datums["WG_SOLVER_CUT_PLANE_X"]["origin_mm"][0]),
        float(datums["WG_GEOM_MIDPLANE_Y"]["origin_mm"][1]),
        float(datums["WG_THROAT_PLANE"]["origin_mm"][2]),
    ]
    bbox_mm = [list(info.bounding_box_mm[0]), list(info.bounding_box_mm[1])]
    return _write_return(
        bundle,
        name,
        bbox_mm=bbox_mm,
        assembly_from_link=np.eye(4),
        config=outbound["design"]["config"],
        source_contract={
            "role": "HF",
            "throat_z_mm": centre[2],
            **_fusion_throat_datums(centre),
            "throat_diameter_mm": diameter,
            "expected_disc_area_mm2": area,
        },
        observed_area_mm2=area,
        build_mode=str(outbound["design"]["build_mode"]),
    )


def test_a_sent_offset_quarter_design_comes_back_as_a_quarter(tmp_path: Path) -> None:
    """Send to CAD and back, with nothing hand-written on the WG side.

    260308tritonia-q (Mesh.VerticalOffset 80, Mesh.Quadrants 1, an enclosure)
    came back from Fusion and solved as a half. Since bundle format 1.3 the
    sent part has its throat at the origin, so it comes back on the solver's
    mirror planes with no recentre at all, and the throat is the observation
    origin. (A link inserted with the offset baked in is
    ``test_a_legacy_offset_link_is_recentred_after_the_design_offset_changed``.)
    """

    pytest.importorskip("gmsh")
    from server.design.schema import DesignConfig

    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 60,
            "a": 45,
            "r0": 12.7,
            "mesh": {"vertical_offset": 80, "quadrants": 1},
            "enclosure": {
                "depth": 120,
                "space_l": 25,
                "space_r": 25,
                "space_t": 25,
                "space_b": 25,
            },
        }
    )
    record = _ingest(tmp_path, _sent_design_bundle(tmp_path, design))

    recentre = record["normalisation"]["vertical_recentre"]
    assert recentre["recorded_offset_mm"] == 80.0
    assert recentre["throat_centre_y_mm"] == pytest.approx(0.0, abs=1.0e-9)
    assert recentre["applied"] is False
    assert record["normalisation"]["matrix"][1][3] == pytest.approx(0.0, abs=1.0e-12)

    assert record["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert record["symmetry_verification"]["verified"] is True
    assert record["domain_decision"]["solver_domain"]["planes"] == ["x0", "y0"]
    assert record["mesh"]["stats"]["domain_multiplier"] == 4.0

    # The observation origin is the throat centre, at the solver origin; so is
    # the throat plane's own origin, and the axis is square to the plane.
    frame = record["anchor"]["throat_frame"]
    for name in ("origin_m", "source_center_m"):
        assert frame[name] == pytest.approx([0.0, 0.0, 0.0], abs=1.0e-9), name
    plane = record["normalisation"]["anchor_manifest_throat_plane"]
    assert plane["origin_mm"] == pytest.approx([0.0, 0.0, 0.0], abs=1.0e-9)
    assert plane["throat_centre_mm"] == pytest.approx([0.0, 0.0, 0.0], abs=1.0e-9)
    assert plane["axis_tilt_deg"] == pytest.approx(0.0, abs=1.0e-9)
    assert plane["axis_tilt_within_limit"] is True

    # As sent, the enclosure spans y = -90..90 mm about the throat. The quarter
    # kept is the one on the solver's mirror planes: it starts on y = 0 and
    # reaches the enclosure's 90 mm half-height.
    bounds = record["mesh"]["stats"]["bounds_m"]
    assert bounds["min_x"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["min_y"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["max_y"] == pytest.approx(0.090, abs=1.0e-4)


def test_a_legacy_offset_link_is_recentred_after_the_design_offset_changed(
    tmp_path: Path,
) -> None:
    """A link keeps the offset it was inserted with; the design may not.

    The body came back with its throat at y = 80, as the link was built, but
    the design now records Mesh.VerticalOffset 0. Reading the design's offset
    would leave the body where it is and solve the full domain; the returned
    throat says where the mirror plane is.
    """

    pytest.importorskip("gmsh")
    record = _ingest(
        tmp_path,
        _horn_bundle(tmp_path, "legacy", vertical_offset_mm=80.0, echoed_offset_mm=0.0),
    )

    recentre = record["normalisation"]["vertical_recentre"]
    assert recentre["recorded_offset_mm"] == 0.0
    assert recentre["throat_centre_y_mm"] == pytest.approx(80.0, abs=1.0e-6)
    assert recentre["applied"] is True
    assert recentre["applied_offset_mm"] == pytest.approx(80.0, abs=1.0e-6)
    assert record["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert record["symmetry_verification"]["verified"] is True
    assert record["mesh"]["stats"]["domain_multiplier"] == 4.0
    frame = record["anchor"]["throat_frame"]
    assert frame["origin_m"] == pytest.approx([0.0, 0.0, 0.0], abs=1.0e-9)


def test_a_throat_centred_return_of_an_offset_design_is_not_moved(tmp_path: Path) -> None:
    """A link built with the throat at the origin must not be recentred again.

    The design still records Mesh.VerticalOffset 80, and its enclosure is
    160 mm taller above the throat than below, so the returned body's y
    midpoint is exactly 80 mm: the midpoint alone would have moved it 80 mm
    down and put the observation origin 80 mm below the throat. The returned
    throat is at y = 0, which decides it.
    """

    pytest.importorskip("gmsh")
    from server.design.schema import DesignConfig

    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 60,
            "a": 45,
            "r0": 12.7,
            "mesh": {"vertical_offset": 80, "quadrants": 1},
            "enclosure": {
                "depth": 120,
                "space_l": 25,
                "space_r": 25,
                "space_t": 185,
                "space_b": 25,
            },
        }
    )
    record = _ingest(
        tmp_path,
        _sent_design_bundle(tmp_path, design, name="sent-centred"),
    )

    recentre = record["normalisation"]["vertical_recentre"]
    assert recentre["recorded_offset_mm"] == 80.0
    assert recentre["throat_centre_y_mm"] == pytest.approx(0.0, abs=1.0e-9)
    assert recentre["applied"] is False
    assert "already on y = 0" in recentre["reason"]
    assert record["normalisation"]["matrix"][1][3] == pytest.approx(0.0, abs=1.0e-12)

    frame = record["anchor"]["throat_frame"]
    for key in ("origin_m", "source_center_m"):
        assert frame[key] == pytest.approx([0.0, 0.0, 0.0], abs=1.0e-9), key
    # Taller above than below, so only the x0 mirror holds.
    assert record["symmetry"]["cut_planes"] == ["x0"]


@pytest.mark.parametrize(
    ("vertical_offset_mm", "defer_viewport"),
    [
        pytest.param(0.0, False, id="centred-inline-viewport"),
        # The API ingests with a deferred viewport, which replays the recipe
        # out of the sidecar JSON in its own child.
        pytest.param(80.0, True, id="offset-deferred-viewport"),
    ],
)
def test_a_return_moved_in_cad_is_normalised_back_with_its_throat_intact(
    tmp_path: Path, vertical_offset_mm: float, defer_viewport: bool
) -> None:
    """A linked instance the user moved and turned in CAD must still ingest.

    The body arrives rotated and translated, ``assembly_from_link`` records
    that placement, and the throat contract stays in link coordinates. Ingest
    undoes the placement. A general affine transform would rewrite the planar
    throat as a B-spline -- flat to 1e-12 mm, but no longer a ``Plane`` -- and
    the return was refused because the anchor throat did not resolve. Undone
    rigidly, it must give the unplaced return's frame and domain, recentring
    included, and the viewport replay must reproduce its geometry fingerprint.
    """

    pytest.importorskip("gmsh")
    placed = _ingest(
        tmp_path / "placed",
        _horn_bundle(
            tmp_path / "placed",
            "placed",
            vertical_offset_mm=vertical_offset_mm,
            placed=True,
        ),
        defer_viewport=defer_viewport,
    )
    unplaced = _ingest(
        tmp_path / "unplaced",
        _horn_bundle(
            tmp_path / "unplaced",
            "offset" if vertical_offset_mm else "round",
            vertical_offset_mm=vertical_offset_mm,
        ),
    )

    expected = np.linalg.inv(_placement_matrix())
    expected[1, 3] -= vertical_offset_mm
    assert np.asarray(placed["normalisation"]["matrix"]) == pytest.approx(
        expected, rel=0.0, abs=1.0e-9
    )
    placed_recentre = placed["normalisation"]["vertical_recentre"]
    unplaced_recentre = unplaced["normalisation"]["vertical_recentre"]
    assert placed_recentre["applied"] is unplaced_recentre["applied"] is bool(vertical_offset_mm)
    # The move is the returned throat's y, measured through the placement's
    # rotation, so it carries that rotation's round-off.
    assert placed_recentre["applied_offset_mm"] == pytest.approx(
        unplaced_recentre["applied_offset_mm"], rel=0.0, abs=1.0e-9
    )

    # The same domain: the placement undone leaves the body on its mirrors.
    for key in ("cut_planes", "candidate_planes"):
        assert placed["symmetry"][key] == unplaced["symmetry"][key]
    assert placed["symmetry"]["cut_planes"] == ["x0", "y0"]
    # Per-plane diagnostics carry the rotation's round-off (~1e-11 mm), so
    # the decision is compared exactly and the measurements to tolerance.
    # Which surface is worst is a measurement too -- an argmax over residuals.
    # On the flat throat disc every residual is round-off (4e-12 to 2e-11 mm,
    # measured), so that argmax is a tie the rotation breaks arbitrarily; it is
    # compared only where the worst residual stands above the tolerance.
    assert placed["symmetry"]["planes"].keys() == unplaced["symmetry"]["planes"].keys()
    for plane, diagnostics in unplaced["symmetry"]["planes"].items():
        moved = placed["symmetry"]["planes"][plane]
        assert moved.keys() == diagnostics.keys()
        residuals_are_round_off = all(
            float(record.get("max_residual_step_units") or 0.0) <= 1.0e-9
            for record in (diagnostics, moved)
        )
        for name, value in diagnostics.items():
            if name == "worst_residual_surface" and residuals_are_round_off:
                continue
            if isinstance(value, float):
                assert moved[name] == pytest.approx(value, rel=0.0, abs=1.0e-9), name
            else:
                assert moved[name] == value, name
    for record in (placed, unplaced):
        verification = record["symmetry_verification"]
        assert verification["verified"] is True
        assert verification["off_plane_free_edge_count"] == 0
        assert "fallback" not in verification
    placed_stats = placed["mesh"]["stats"]
    unplaced_stats = unplaced["mesh"]["stats"]
    assert placed_stats["domain_multiplier"] == unplaced_stats["domain_multiplier"] == 4.0
    assert (
        placed_stats["dense_solver_domain_multiplier"]
        == unplaced_stats["dense_solver_domain_multiplier"]
        == 4
    )
    assert placed_stats["bounds_m"] == pytest.approx(
        unplaced_stats["bounds_m"], rel=0.0, abs=1.0e-9
    )

    # The same record frame: the throat resolved, and its datum rode the matrix.
    unplaced_frame = unplaced["anchor"]["throat_frame"]
    assert placed["anchor"]["throat_frame"].keys() == unplaced_frame.keys()
    for name, vector in unplaced_frame.items():
        assert placed["anchor"]["throat_frame"][name] == pytest.approx(
            vector, rel=0.0, abs=1.0e-9
        )
    assert placed["post_cut_source_areas"].keys() == unplaced["post_cut_source_areas"].keys()
    for source_id, provenance in unplaced["post_cut_source_areas"].items():
        moved = placed["post_cut_source_areas"][source_id]
        assert moved["parent_area_mm2"] == pytest.approx(
            provenance["parent_area_mm2"], rel=1.0e-9
        )
        assert moved["retained_child_area_mm2"] == pytest.approx(
            provenance["retained_child_area_mm2"], rel=1.0e-9
        )

    # The viewport replays the normalisation in a fresh model and refuses a
    # fingerprint that differs from the solve's by a single bit.
    if defer_viewport:
        assert placed["viewport_mesh"]["pending"] is True
        built = build_deferred_viewport(placed, tmp_path / "placed" / "data")
        assert built is not None
        assert built["transformed_geometry_hash"] == placed["transformed_geometry_hash"]
    else:
        viewport = placed["viewport_mesh"]
        assert viewport["available"] is True, viewport
        assert viewport["transformed_geometry_hash"] == placed["transformed_geometry_hash"]


def test_a_leaking_reduced_domain_falls_back_to_the_full_domain(tmp_path: Path, caplog) -> None:
    """A hole in a reduced domain is a wrong answer, not a slower one.

    The boolean that cuts the model can drop a face it fails to intersect, so
    the leak is injected where it would appear: in the meshed boundary, after
    the cut, on a rigid wall away from both cut planes. Nothing upstream can
    see it -- the OCC areas still balance and ``integrity['valid']`` excludes
    open edges on purpose -- which is why it used to be solved in silence.
    """

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "capped")
    # This private context adds one known fixture name to the real mesh-child
    # envelope. The child installs it after confinement and the STEP scan, then
    # runs the same isolated build production uses.
    with _inject_mesh_child_fault("leaking-reduced-domain"):
        record = _ingest(tmp_path, bundle)

    assert record["symmetry"]["cut_planes"] == []
    assert "failed post-mesh verification" in record["symmetry"]["note"]
    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    assert verification["fallback"]["rejected_cut_planes"] == ["x0", "y0"]
    assert verification["fallback"]["capped_planes"] == []
    assert "leaks" in verification["fallback"]["reason"]
    # A leak names no plane, and every reduced mesh leaks here: x0 is tried
    # alone (a WG-cut y0-only half is not offered until stage 6), then the
    # whole model -- and no more than that.
    assert [
        (item["wg_cut_planes"], item["outcome"]) for item in verification["fallback"]["attempts"]
    ] == [
        (["x0", "y0"], "unverified"), (["x0"], "unverified"), ([], "kept"),
    ]
    assert verification["fallback"]["attempts"][0]["failure_codes"] == ["off-plane-open-edges"]
    assert record["domain_decision"]["verification"]["attempts"][1] == {
        "wg_cut_planes": ["x0"], "outcome": "unverified",
        "failure_codes": ["off-plane-open-edges"], "failed_planes": [],
    }

    finding = next(
        item for item in record["findings"] if item["kind"] == "symmetry-cut-unverified"
    )
    assert finding["blocking"] is False
    assert finding["rejected_cut_planes"] == ["x0", "y0"]
    assert finding["detail"] == "WG will solve the whole model; symmetry could not be used for this one."
    diagnostics = [entry.cad_mesh_recovery for entry in caplog.records if hasattr(entry, "cad_mesh_recovery")]
    assert len(diagnostics) == 1
    assert diagnostics[0]["symmetry_fallback"] == verification["fallback"]
    assert diagnostics[0]["user_outcome"] == "proceed"
    assert diagnostics[0]["solved_planes"] == []

    # A full domain is solvable, so the fallback mesh is a real artifact and
    # the polar sweep widened back out with it.
    assert record["mesh"]["stats"]["domain_multiplier"] == 1.0
    assert record["polar_grid_derivation"]["axes"]["vertical"]["minimum_deg"] == -180.0
    assert Path(record["mesh_store_path"]).is_file()

    # The next ingest serves the fallback mesh from cache; the finding is part
    # of the artifact's record, not of the meshing run that produced it.
    again = _ingest(tmp_path, bundle)
    assert again["mesh_cache_hit"] is True
    assert any(
        item["kind"] == "symmetry-cut-unverified" for item in again["findings"]
    )



def test_a_refused_symmetry_cut_falls_back_to_the_full_domain(tmp_path: Path, caplog) -> None:
    """The mesher's cutter refuses a cut that loses a retained portion of a face.

    That refusal ("OCC cut lost retained portions ...") is a failed
    optimisation, not a bad model: WG meshes the whole model through the same
    calm path as a cut that does not verify, and records why for developers.
    """

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "capped")
    with _inject_mesh_child_fault("cut-refused"):
        record = _ingest(tmp_path, bundle)

    assert record["symmetry"]["cut_planes"] == []
    assert record["symmetry"]["domain_planes"] == []
    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    assert verification["fallback"]["reason"].startswith("cut-refused: ")
    assert "lost retained portions" in verification["fallback"]["reason"]
    assert verification["fallback"]["detected_planes"] == ["x0", "y0"]
    assert [
        (item["wg_cut_planes"], item["outcome"]) for item in verification["fallback"]["attempts"]
    ] == [
        (["x0", "y0"], "cut-refused"), (["x0"], "cut-refused"), ([], "kept"),
    ]
    finding = next(
        item for item in record["findings"] if item["kind"] == "symmetry-cut-unverified"
    )
    assert finding["blocking"] is False
    assert finding["solved_domain"] == "full"
    assert finding["detail"] == "WG will solve the whole model; symmetry could not be used for this one."
    diagnostics = [entry.cad_mesh_recovery for entry in caplog.records if hasattr(entry, "cad_mesh_recovery")]
    assert len(diagnostics) == 1
    assert diagnostics[0]["symmetry_fallback"] == verification["fallback"]
    assert diagnostics[0]["solved_planes"] == []
    assert record["mesh"]["stats"]["domain_multiplier"] == 1.0


_RETURN_IDS.update(
    {
        "half-x0-leaking": "wgr_01J5A8QK3M9T2XVBH0RD7NWEV0",
        "named-y0": "wgr_01J5A8QK3M9T2XVBH0RD7NWEW0",
        "declared-named": "wgr_01J5A8QK3M9T2XVBH0RD7NWEX0",
        "named-x0": "wgr_01J5A8QK3M9T2XVBH0RD7NWEY0",
        "named-both": "wgr_01J5A8QK3M9T2XVBH0RD7NWEZ0",
        "mixed": "wgr_01J5A8QK3M9T2XVBH0RD7NWF00",
        "declared-refused": "wgr_01J5A8QK3M9T2XVBH0RD7NWF10",
        "short-time": "wgr_01J5A8QK3M9T2XVBH0RD7NWF20",
        "quarter-x0-leaking": "wgr_01J5A8QK3M9T2XVBH0RD7NWF30",
    }
)


def _assert_half_kept_on_x0(record: dict[str, Any], *, first_outcome: str) -> None:
    """A quarter whose y0 cut failed and whose x0 cut survived, solved as that half."""

    from server.solver import domain_record as dr

    assert record["symmetry"]["cut_planes"] == ["x0"]
    assert record["symmetry"]["domain_planes"] == ["x0"]
    assert "y0 left uncut" in record["symmetry"]["note"]
    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    fallback = verification["fallback"]
    assert fallback["kept_cut_planes"] == ["x0"]
    assert [(item["wg_cut_planes"], item["outcome"]) for item in fallback["attempts"]] == [
        (["x0", "y0"], first_outcome), (["x0"], "kept"),
    ]
    # The half is solved, and the polar sweep is one-sided on x0 only.
    assert record["mesh"]["stats"]["domain_multiplier"] == 2.0
    axes = record["polar_grid_derivation"]["axes"]
    assert axes["horizontal"]["minimum_deg"] == 0.0
    assert axes["vertical"]["minimum_deg"] == -180.0
    assert Path(record["mesh_store_path"]).is_file()

    decision = record["domain_decision"]
    assert decision["solver_domain"]["planes"] == ["x0"]
    assert decision["planes"]["x0"]["used"] is True
    assert decision["planes"]["x0"]["dropped_by"] is None
    assert decision["planes"]["y0"]["used"] is False
    assert decision["planes"]["y0"]["dropped_by"] == dr.DROPPED_BY_VERIFICATION
    assert decision["planes"]["y0"]["dropped_with"] == []
    # The visible note (decision 6): the design is a quarter and solves as a half.
    (note,) = decision["findings"]
    assert note["kind"] == dr.LOST_PLANE_KIND and note["lost_planes"] == ["y0"]
    assert note["solved_fraction"] == "half"
    finding = next(
        item for item in record["findings"] if item["kind"] == "symmetry-cut-unverified"
    )
    assert finding["solved_domain"] == "half"
    assert finding["cut_in_cad"] is False
    assert finding["detail"] == (
        "WG will solve the half model; it could not use the top/bottom (y = 0) symmetry "
        "plane for this one."
    )


def test_a_quarter_whose_y0_cut_leaks_is_solved_as_the_x0_half(tmp_path: Path) -> None:
    """One failed plane falls back to half, not full (decision 5).

    The leak follows the y0 cut only. A leak names no plane, so x0 is tried
    alone first -- and verifies, so the whole model is never meshed.
    """

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "capped")
    with _inject_mesh_child_fault("leaking-y0-reduced-domain"):
        record = _ingest(tmp_path, bundle)

    _assert_half_kept_on_x0(record, first_outcome="unverified")
    fallback = record["symmetry_verification"]["fallback"]
    assert fallback["rejected_cut_planes"] == ["y0"]
    assert "leaks" in fallback["reason"]
    # The next ingest serves the half from cache, with the same record.
    again = _ingest(tmp_path, bundle)
    assert again["mesh_cache_hit"] is True
    assert again["symmetry"]["domain_planes"] == ["x0"]


def test_a_quarter_whose_y0_cut_is_refused_is_cut_on_x0_alone(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "capped")
    with _inject_mesh_child_fault("cut-refused-with-y0"):
        record = _ingest(tmp_path, bundle)

    _assert_half_kept_on_x0(record, first_outcome="cut-refused")
    fallback = record["symmetry_verification"]["fallback"]
    assert fallback["reason"].startswith("cut-refused: ")
    assert fallback["rejected_cut_planes"] == []
    assert fallback["refused_cut_planes"] == ["y0"]
    assert fallback["detected_planes"] == ["y0"]
    # The mirror test accepted y0 before its cut was refused: the record keeps it.
    assert record["domain_decision"]["planes"]["y0"]["geometry"] == "accepted"
    assert record["domain_decision"]["planes"]["y0"]["dropped_by"] == "verification"


def test_a_quarter_whose_x0_cut_leaks_is_solved_whole_until_stage_6(tmp_path: Path) -> None:
    """x0 fails alone too; a WG-cut y0-only half is not offered (BEAT has none)."""

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "quarter-x0-leaking")
    with _inject_mesh_child_fault("leaking-x0-reduced-domain"):
        record = _ingest(tmp_path, bundle)

    assert record["symmetry"]["domain_planes"] == []
    fallback = record["symmetry_verification"]["fallback"]
    assert [(item["wg_cut_planes"], item["outcome"]) for item in fallback["attempts"]] == [
        (["x0", "y0"], "unverified"), (["x0"], "unverified"), ([], "kept"),
    ]
    planes = record["domain_decision"]["planes"]
    assert planes["x0"]["dropped_by"] == planes["y0"]["dropped_by"] == "verification"
    finding = next(
        item for item in record["findings"] if item["kind"] == "symmetry-cut-unverified"
    )
    assert finding["solved_domain"] == "full"


def test_a_declared_half_keeps_its_plane_when_wgs_own_cut_fails(tmp_path: Path) -> None:
    """Only WG's cut is given up; the author's plane is never dropped."""

    pytest.importorskip("gmsh")
    bundle = _reduced_bundle(tmp_path, "half-x0-leaking")
    with _inject_mesh_child_fault("leaking-x0-reduced-domain"):
        record = _ingest(tmp_path, bundle)

    assert record["symmetry"]["declared_cut_planes"] == ["y0"]
    assert record["symmetry"]["cut_planes"] == []
    assert record["symmetry"]["domain_planes"] == ["y0"]
    fallback = record["symmetry_verification"]["fallback"]
    assert [(item["wg_cut_planes"], item["outcome"]) for item in fallback["attempts"]] == [
        (["x0"], "unverified"), ([], "kept"),
    ]
    assert record["mesh"]["stats"]["domain_multiplier"] == 2.0
    finding = next(
        item for item in record["findings"] if item["kind"] == "symmetry-cut-unverified"
    )
    assert finding["cut_in_cad"] is True
    assert finding["detail"].startswith("WG will solve the half model as cut in CAD")


def _counted_imported_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing: dict[str, str],
    *,
    refuse_cuts: Any = (),
    deadline: float | None = None,
    **bundle_kwargs: Any,
) -> tuple[list[tuple[str, ...]], Any]:
    """Build in this process with the reduced-mesh check failing named planes.

    ``failing`` maps a plane to the failure code the check reports whenever
    that plane is in the domain (the code alone, naming no plane, for a global
    code such as ``off-plane-open-edges``). ``refuse_cuts`` lists the WG cut
    plane sets the cutter refuses (``"any"``: every cut). Returns the domains
    each attempt meshed, and the build (or the exception it raised).
    """

    import hornlab_mesher

    from server.mesh import imported
    from server.mesh.imported import build_imported_mesh
    from hornlab_mesher.symmetry import GLOBAL_FAILURES

    real = imported.verify_imported_reduced_mesh
    meshed: list[tuple[str, ...]] = []

    def failing_check(points_mm: Any, triangles: Any, *, cut_planes: Any) -> dict[str, Any]:
        planes = tuple(cut_planes)
        meshed.append(planes)
        record = real(points_mm, triangles, cut_planes=planes)
        hit = [plane for plane in planes if plane in failing]
        if hit:
            codes = sorted({failing[plane] for plane in hit})
            record.update(
                verified=False, reason="injected", failure_codes=codes,
                failed_planes=[plane for plane in hit if failing[plane] not in GLOBAL_FAILURES],
            )
        return record

    real_cut = hornlab_mesher.auto_cut_occ_geometry

    def refusing(groups: Any, *, planes: Any = None, **kwargs: Any) -> Any:
        candidates = tuple(planes or ())
        if candidates and (refuse_cuts == "any" or candidates in refuse_cuts):
            error = RuntimeError("OCC cut lost retained portions of surfaces [3]")
            error.accepted_planes = candidates  # type: ignore[attr-defined]
            error.report = {"planes": {}}  # type: ignore[attr-defined]
            raise error
        return real_cut(groups, planes=planes, **kwargs)

    monkeypatch.setattr(imported, "verify_imported_reduced_mesh", failing_check)
    monkeypatch.setattr(hornlab_mesher, "auto_cut_occ_geometry", refusing)
    name = bundle_kwargs.pop("name")
    if bundle_kwargs.get("planes"):
        bundle = _reduced_bundle(tmp_path, name, **bundle_kwargs)
        options = {"symmetry_mode": "auto", "declared_cut_planes": list(bundle_kwargs["planes"])}
    else:
        bundle = _horn_bundle(tmp_path, name)
        options = {"symmetry_mode": "auto"}
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    try:
        built = _run_in_gmsh_session(
            build_imported_mesh, bundle / "assembly.step", manifest, _SIZES,
            options=options, include_viewport_mesh=False, deadline=deadline,
        )
    except Exception as exc:  # noqa: BLE001 - returned to the test to assert on
        built = exc
    return meshed, built


def _plane_entries(built: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from server.solver.domain_record import cad_plane_entries

    return cad_plane_entries(
        built["symmetry"],
        domain_planes=built["symmetry"]["domain_planes"],
        verification=built["symmetry_verification"],
    )


def test_a_failure_naming_y0_drops_y0_without_trying_it_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("gmsh")
    meshed, built = _counted_imported_build(
        tmp_path, monkeypatch, {"y0": "missing-cut-rim"}, name="named-y0",
    )
    assert meshed == [("x0", "y0"), ("x0",)]
    assert built["symmetry"]["domain_planes"] == ["x0"]
    attempts = built["symmetry_verification"]["fallback"]["attempts"]
    assert attempts[0]["failed_planes"] == ["y0"]
    assert attempts[0]["failure_codes"] == ["missing-cut-rim"]
    # The same geometry is not mirror-tested twice: x0's verdict is reused.
    assert built["symmetry"]["reused_verdict_planes"] == ["x0"]
    assert built["symmetry"]["symmetry_rules"] == "shared-check-per-plane-v2"


def test_a_failure_naming_x0_goes_to_the_whole_model_not_a_wg_y0_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Until stage 6 a WG-cut top/bottom-only half is not offered (BEAT has none)."""

    pytest.importorskip("gmsh")
    meshed, built = _counted_imported_build(
        tmp_path, monkeypatch, {"x0": "capped-cut-plane"}, name="named-x0",
    )
    assert meshed == [("x0", "y0"), ()]
    assert built["symmetry"]["domain_planes"] == []


def test_a_failure_naming_both_planes_goes_straight_to_the_whole_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("gmsh")
    meshed, built = _counted_imported_build(
        tmp_path, monkeypatch, {"x0": "missing-cut-rim", "y0": "missing-cut-rim"},
        name="named-both",
    )
    assert meshed == [("x0", "y0"), ()]
    entries = _plane_entries(built)
    assert entries["x0"]["dropped_by"] == entries["y0"]["dropped_by"] == "verification"


def test_a_refused_cut_then_an_unverified_one_drop_both_planes_to_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every plane any attempt gave up is recorded, whichever attempt it was."""

    pytest.importorskip("gmsh")
    meshed, built = _counted_imported_build(
        tmp_path, monkeypatch, {"x0": "off-plane-open-edges"},
        refuse_cuts=[("x0", "y0")], name="mixed",
    )
    assert meshed == [("x0",), ()]
    fallback = built["symmetry_verification"]["fallback"]
    assert [(item["wg_cut_planes"], item["outcome"]) for item in fallback["attempts"]] == [
        (["x0", "y0"], "cut-refused"), (["x0"], "unverified"), ([], "kept"),
    ]
    assert fallback["rejected_cut_planes"] == ["x0"]
    assert fallback["refused_cut_planes"] == ["y0"]
    entries = _plane_entries(built)
    for plane in ("x0", "y0"):
        assert entries[plane]["used"] is False
        assert entries[plane]["dropped_by"] == "verification"


def test_a_refused_wg_cut_on_a_declared_half_keeps_the_declared_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declared plane is never a cut candidate, nor in the refused cut."""

    pytest.importorskip("gmsh")
    meshed, built = _counted_imported_build(
        tmp_path, monkeypatch, {}, refuse_cuts="any", name="declared-refused", planes=("y0",),
    )
    assert meshed == [("y0",)]
    assert built["symmetry"]["domain_planes"] == ["y0"]
    assert built["symmetry"]["candidate_planes"] == []
    attempts = built["symmetry_verification"]["fallback"]["attempts"]
    assert [(item["wg_cut_planes"], item["outcome"]) for item in attempts] == [
        (["x0"], "cut-refused"), ([], "kept"),
    ]


def test_a_retry_that_would_starve_the_whole_model_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the meshing time nearly spent, a failed quarter goes straight to whole."""

    pytest.importorskip("gmsh")
    import time

    meshed, built = _counted_imported_build(
        tmp_path, monkeypatch, {"x0": "off-plane-open-edges", "y0": "off-plane-open-edges"},
        deadline=time.monotonic() + 1.0, name="short-time",
    )
    assert meshed == [("x0", "y0"), ()]
    attempts = built["symmetry_verification"]["fallback"]["attempts"]
    assert [(item["wg_cut_planes"], item["outcome"]) for item in attempts] == [
        (["x0", "y0"], "unverified"), (["x0"], "skipped-for-time"), ([], "kept"),
    ]


def test_a_failure_naming_a_declared_plane_is_refused_after_one_mesh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WG's own cut cannot mend the author's plane, so it is not given up for it."""

    pytest.importorskip("gmsh")
    from server.mesh.imported import ImportedMeshError

    meshed, built = _counted_imported_build(
        tmp_path, monkeypatch, {"y0": "missing-cut-rim"}, name="declared-named",
        planes=("y0",),
    )
    assert meshed == [("x0", "y0")]
    assert isinstance(built, ImportedMeshError)
    assert "the meshed boundary denies it" in str(built)


@pytest.mark.parametrize(
    ("planes", "domain", "words"),
    [((), "full", "the whole model"), (("x0",), "half", "the half model as cut in CAD"),
     (("x0", "y0"), "quarter", "the quarter model as cut in CAD")],
)
def test_the_fallback_line_names_the_domain_actually_solved(planes, domain, words) -> None:
    from server.cadlink.intake.ingest import SOLVED_DOMAIN_BY_PLANES, symmetry_fallback_detail
    from server.solver.imported import imported_domain_planes

    solved = SOLVED_DOMAIN_BY_PLANES[len(imported_domain_planes({"symmetry": {"domain_planes": list(planes)}}))]
    assert solved == domain
    assert words in symmetry_fallback_detail(solved)
    if planes:
        assert "whole" not in symmetry_fallback_detail(solved)


# ------------------------------------------------- returns already cut in CAD

_RETURN_IDS.update(
    {
        "half": "wgr_01J5A8QK3M9T2XVBH0RD7NWEE0",
        "half-undeclared": "wgr_01J5A8QK3M9T2XVBH0RD7NWEF0",
        "half-forced": "wgr_01J5A8QK3M9T2XVBH0RD7NWEG0",
        "half-leaking": "wgr_01J5A8QK3M9T2XVBH0RD7NWEH0",
        "half-capped": "wgr_01J5A8QK3M9T2XVBH0RD7NWEJ0",
        "quarter": "wgr_01J5A8QK3M9T2XVBH0RD7NWEK0",
        "quarter-unlinked": "wgr_01J5A8QK3M9T2XVBH0RD7NWEM0",
    }
)


def _cut_step_open(path: Path, planes: tuple[str, ...]) -> None:
    """Cut a written STEP open on the named planes, keeping the positive side.

    The cut leaves the plane *open*: the boundary the solver mirrors is not a
    surface of the model, so the retained shell has a free rim there and no
    face. That is what a usable half looks like -- the reported half model
    is itself an open surface body -- and it is what the sibling CLI and WG's
    own cutter both produce. A solid boolean would leave a cap, which meshes as
    a rigid wall rather than a mirror; ``test_a_capped_declared_half...``
    covers that case with the refusal it earns.

    A quarter is cut here for real, on both planes at once, rather than
    inferred from the half: the retained throat is a quarter disc, the rim runs
    along two planes, and only an actual cut exercises that.
    """

    import gmsh

    def cut() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        box = [float(value) for value in gmsh.model.getBoundingBox(-1, -1)]
        volumes = gmsh.model.getEntities(3)
        assert volumes, "the horn fixture is written as a solid"
        # Drop the volume but keep its faces, then trim the faces: the same
        # open-cut shape ``hornlab_mesher.step_prepare`` makes.
        gmsh.model.occ.remove(volumes, recursive=False)
        gmsh.model.occ.synchronize()
        surfaces = [tag for _dim, tag in sorted(gmsh.model.getEntities(2))]
        margin = 10.0
        low = [box[axis] - margin for axis in range(3)]
        high = [box[axis + 3] + margin for axis in range(3)]
        for plane in planes:
            low[{"x0": 0, "y0": 1}[plane]] = 0.0
        half_space = gmsh.model.occ.addBox(
            low[0],
            low[1],
            low[2],
            high[0] - low[0],
            high[1] - low[1],
            high[2] - low[2],
        )
        gmsh.model.occ.synchronize()
        gmsh.model.occ.intersect(
            [(2, surface) for surface in surfaces],
            [(3, half_space)],
            removeObject=True,
            removeTool=True,
        )
        gmsh.model.occ.synchronize()
        # Sew the trimmed faces back into one shell, so the file has one root
        # body the way a CAD surface body does rather than eight loose faces.
        gmsh.model.occ.healShapes(sewFaces=True, makeSolids=False)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
        gmsh.clear()

    _run_in_gmsh_session(cut)


def _name_throat_shell(path: Path, label: str) -> None:
    """Name the shell that carries the throat face, the way Fusion names bodies.

    ``hornlab_mesher.step_import`` reads a body name off
    ``SHELL_BASED_SURFACE_MODEL('name', (#open_shell))``, which is how a Fusion
    surface body arrives. Gmsh writes those names empty, so this fills one in
    -- a string edit inside an existing record, changing no topology -- to give
    the fixture an unlinked, CAD-named source with no WG design behind it.
    """

    import gmsh

    def throat_face_index() -> int:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(path), highestDimOnly=False)
        gmsh.model.occ.synchronize()
        surfaces = [tag for _dim, tag in sorted(gmsh.model.getEntities(2))]
        throat = _planar_faces_nearest_bore_first(gmsh, surfaces)[0]
        gmsh.clear()
        # The open cut leaves no face on y = 0, so the planar faces left are the
        # throat disc and the plug's rear; the throat is the one nearer the bore.
        return surfaces.index(throat)

    index = _run_in_gmsh_session(throat_face_index)
    text = path.read_text(errors="replace")
    face_ids = [int(match) for match in re.findall(r"#(\d+)\s*=\s*ADVANCED_FACE", text)]
    face_id = face_ids[index]
    shell_match = re.search(
        r"#(\d+)\s*=\s*OPEN_SHELL\s*\(\s*'[^']*'\s*,\s*\(\s*#" + str(face_id) + r"\s*\)",
        text,
    )
    assert shell_match, "each trimmed face is written as its own open shell"
    shell_id = shell_match.group(1)
    replaced, count = re.subn(
        r"(=\s*SHELL_BASED_SURFACE_MODEL\s*\(\s*)'[^']*'(\s*,\s*\(\s*#"
        + shell_id
        + r"\s*\))",
        lambda match: match.group(1) + f"'{label}'" + match.group(2),
        text,
    )
    assert count == 1, count
    path.write_text(replaced)


def _count_step_shell_bodies(path: Path) -> int:
    """Count the STEP body entities, the way the CAD adapter counts them."""

    text = re.sub(r"'(?:''|[^'])*'", "''", path.read_text(errors="replace"))
    return len(
        re.findall(
            r"\b(?:MANIFOLD_SOLID_BREP|SHELL_BASED_SURFACE_MODEL)\s*\(", text, re.I
        )
    )


def _reduced_bundle(
    tmp_path: Path,
    name: str,
    *,
    planes: tuple[str, ...] = ("y0",),
    declare: bool = True,
    unlinked: bool = False,
    source_shape: int = FLAT_THROAT_DISC,
) -> Path:
    """The round horn, cut open on ``planes`` in CAD, optionally declaring it."""

    bundle = _horn_bundle(tmp_path, name, source_shape=source_shape)
    assembly = bundle / "assembly.step"
    _cut_step_open(assembly, planes)
    step = assembly.read_bytes()
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    manifest["files"]["assembly.step"] = {
        "sha256": "sha256:" + hashlib.sha256(step).hexdigest(),
        "size_bytes": len(step),
        "media_type": "model/step",
        "purpose": "exterior-assembly",
    }
    # What CAD measured on the model it actually exported: the retained
    # fraction of the disc, halved once per plane the cut passed through it.
    source = manifest["sources"][0]
    retained = float(source["observed"]["total_area_mm2"]) / float(2 ** len(planes))
    source["observed"]["total_area_mm2"] = retained
    source["observed"]["per_face_area_mm2"] = [retained]
    # An open half is a surface body, and the inventory has to say so. Gmsh's
    # STEP writer emits one SHELL_BASED_SURFACE_MODEL per trimmed face, so the
    # honest inventory for this fixture is one entry per face rather than one
    # entry that would make the scope gate disagree with the file.
    faces = _count_step_shell_bodies(bundle / "assembly.step")
    template = manifest["scope"]["included"][0]
    manifest["scope"]["included"] = [
        {**template, "object_id": f"{name}-face-{index}", "body_kind": "surface"}
        for index in range(faces)
    ]
    manifest["assembly"]["n_bodies_expected"] = faces
    if unlinked:
        # A Fusion-first return: no WG design behind it, so the source is the
        # CAD body's own name and nothing checks it against a design's throat.
        # This is the shape of the reported half model, and the shape in
        # which an undeclared half would otherwise be solved in silence.
        _name_throat_shell(assembly, "HF")
        step = assembly.read_bytes()
        manifest["files"]["assembly.step"] = {
            "sha256": "sha256:" + hashlib.sha256(step).hexdigest(),
            "size_bytes": len(step),
            "media_type": "model/step",
            "purpose": "exterior-assembly",
        }
        manifest["instances"] = []
        manifest["coordinate_system"].pop("solver_anchor_instance_id", None)
        for entry in manifest["scope"]["included"]:
            entry["wglink_instance_id"] = None
        source["instance_id"] = None
        source["selectors"] = {"shell_names": ["HF"]}
        source["patch_policy"] = "explicit-disconnected"
        source["expected_connected_components"] = 1
    if declare:
        manifest["assembly"]["domain"] = {
            "kind": "half" if len(planes) == 1 else "quarter",
            "cut_planes": list(planes),
            "declared_by": "cad-author",
            "evidence": {
                plane: {"min_mm": 0.0, "max_mm": 34.0, "tolerance_mm": 0.05}
                for plane in planes
            },
        }
        manifest["required_features"] = [
            *manifest["required_features"],
            "reduced-domain-v1",
        ]
    (bundle / "wgreturn.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return bundle


def test_a_declared_linked_half_with_a_minted_source_id_ending_in_r_is_mirrored(
    tmp_path: Path,
) -> None:
    """Re-review S3-2c, finding 1: a WG-minted id's letters are never read as a side.

    ``wglink_send._throat_source_identity`` mints a linked throat's id from its
    instance, so an id whose last character happens to be R after a digit
    would refuse that instance every time if it were read as "right".
    """

    pytest.importorskip("gmsh")
    minted = "wgs-QBQ26C6122140K1H055R"
    bundle = _reduced_bundle(tmp_path, "half")
    manifest_path = bundle / "wgreturn.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["id"] = minted
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    data_dir = tmp_path / "data"
    record = _run_in_gmsh_session(
        ingest_bundle, bundle, {**_SIZES, "source_size_mm": {minted: 8}}, [],
        CadLinkStore(data_dir / "cadlink.db"), data_dir,
        prep_options={"symmetry_mode": "auto"},
        expected_design_id="wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
    )
    assert "y0" in record["symmetry"]["domain_planes"]
    assert list(record["source_tags"]) == [minted]


def test_a_declared_half_is_mirrored_rather_than_cut_again(tmp_path: Path) -> None:
    """The half the author cut is the domain, and WG says so rather than guessing.

    The cutter correctly declines a plane the model does not straddle, so
    without the declaration this model comes back ``full`` -- solved as an open
    shell radiating through its own cut face. The declared plane is never cut
    again; the other plane still is, where the mirror test validates it
    (M1c-auto: the smallest model the checks allow).
    """

    pytest.importorskip("gmsh")
    record = _ingest(tmp_path, _reduced_bundle(tmp_path, "half"))

    symmetry = record["symmetry"]
    # The declared plane is the author's cut; WG cut only the other one.
    assert symmetry["cut_planes"] == ["x0"]
    assert symmetry["declared_cut_planes"] == ["y0"]
    assert symmetry["domain_planes"] == ["x0", "y0"]
    assert symmetry["planes"]["y0"]["source"] == "declared-by-cad-author"
    assert symmetry["planes"]["x0"]["accepted"] is True

    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    assert verification["declared_cut_planes"] == ["y0"]
    assert "y0" in verification["detected_planes"]
    assert verification["off_plane_free_edge_count"] == 0
    assert "fallback" not in verification

    # The solve reduces to a quarter, and the polar sweep with it.
    assert record["mesh"]["stats"]["domain_multiplier"] == 4.0
    assert record["mesh"]["stats"]["dense_solver_domain_multiplier"] == 4
    derivation = record["polar_grid_derivation"]
    assert derivation["cut_planes"] == ["x0", "y0"]

    bounds = record["mesh"]["stats"]["bounds_m"]
    assert bounds["min_y"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["min_x"] == pytest.approx(0.0, abs=1.0e-9)

    # The half throat still carries its source tag: the retained half of the
    # contract's disc is what the linked selector is matched against.
    assert int(record["mesh"]["stats"]["tag_counts"]["101"]) > 0

    finding = next(
        item for item in record["findings"] if item["kind"] == "declared-reduced-domain"
    )
    assert finding["blocking"] is False
    assert finding["declared_cut_planes"] == ["y0"]


def test_the_same_half_returned_undeclared_is_recovered_from_its_geometry(
    tmp_path: Path,
) -> None:
    """An unlinked open half is solved as the reduced domain of the whole.

    A linked return whose throat was halved fails role resolution, so it is
    already refused. An unlinked one has no design to contradict: its clean
    cut through the throat is recovered from its geometry (stage 3), exactly
    the domain the same half declared by hand is solved with.
    """

    from server.jobs.runtime.imported_refusals import _imported_open_half_refusal

    pytest.importorskip("gmsh")
    record = _ingest(
        tmp_path,
        _reduced_bundle(tmp_path, "half-undeclared", declare=False, unlinked=True),
    )
    declared = _ingest(
        tmp_path / "declared",
        _reduced_bundle(tmp_path / "declared", "half", unlinked=True),
    )

    assert record["symmetry"]["domain_planes"] == declared["symmetry"]["domain_planes"]
    assert "y0" in record["symmetry"]["domain_planes"]
    assert record["mesh"]["stats"]["domain_multiplier"] == declared["mesh"]["stats"]["domain_multiplier"]
    assert record["mesh_content_sha256"] == declared["mesh_content_sha256"]
    interpretation = record["domain_interpretation"]
    assert interpretation["reading"] == "reduced"
    assert interpretation["planes"] == ["y0"]
    assert interpretation["evidence"]["recovered"] is True

    assert not any(item["blocking"] for item in record["findings"] if "domain" in item["kind"])
    finding = next(
        item
        for item in record["findings"]
        if item["kind"] == "recovered-reduced-domain"
    )
    assert finding["blocking"] is False
    assert finding["planes"] == ["y0"]
    assert _imported_open_half_refusal(record) is None


def test_solve_model_identity_separates_domain_collision_and_unchanged_resend(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    bundle = _reduced_bundle(tmp_path, "half", unlinked=True)
    declared = _ingest(tmp_path / "declared", bundle)
    resent = _ingest(tmp_path / "resent", bundle)
    assert declared["solve_model_sha256"] == resent["solve_model_sha256"]

    manifest_path = bundle / "wgreturn.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assembly"].pop("domain")
    manifest["required_features"].remove("reduced-domain-v1")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    open_shell = _ingest(tmp_path / "open-shell", bundle)
    assert declared["transformed_geometry_hash"] == open_shell["transformed_geometry_hash"]
    assert declared["symmetry"]["domain_planes"] == ["x0", "y0"]
    # Undeclared, the same half is recovered from its geometry (stage 3): the
    # same domain, read another way, is another solve model.
    assert open_shell["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert open_shell["domain_interpretation"]["evidence"]["recovered"] is True
    assert declared["solve_model_sha256"] != open_shell["solve_model_sha256"]

    # The old fingerprint records only the centre and area: these distinct
    # 2x2 and 4x1 triangulated faces both have (12, 12, 0) and area 4.
    from copy import deepcopy
    from server.mesh.artifact import mesh_text_sha256
    from server.mesh.imported import _geometry_fingerprint

    def rectangle_mesh(width: float, height: float) -> str:
        x0, x1 = 12 - width / 2, 12 + width / 2
        y0, y1 = 12 - height / 2, 12 + height / 2
        return (f"$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n4\n"
                f"1 {x0} {y0} 0\n2 {x1} {y0} 0\n3 {x1} {y1} 0\n4 {x0} {y1} 0\n"
                "$EndNodes\n$Elements\n2\n1 2 2 101 1 1 2 3\n"
                "2 2 2 101 1 1 3 4\n$EndElements\n")

    assert _geometry_fingerprint([((12, 12, 0), 2 * 2)]) == _geometry_fingerprint([((12, 12, 0), 4 * 1)])
    square = deepcopy(declared)
    square["mesh_content_sha256"] = mesh_text_sha256(rectangle_mesh(2, 2))
    wide = deepcopy(declared)
    wide["mesh_content_sha256"] = mesh_text_sha256(rectangle_mesh(4, 1))
    assert square["transformed_geometry_hash"] == wide["transformed_geometry_hash"]
    assert solve_model_sha256(square) != solve_model_sha256(wide)

    changed = deepcopy(declared)
    changed["normalisation"]["matrix"][0][3] += 1.0
    assert solve_model_sha256(changed) != declared["solve_model_sha256"]
    changed = deepcopy(declared)
    changed["symmetry"]["domain_planes"] = []
    assert solve_model_sha256(changed) != declared["solve_model_sha256"]
    changed = deepcopy(declared)
    changed["source_tags"]["source-hf"] = 102
    assert solve_model_sha256(changed) != declared["solve_model_sha256"]


def test_forcing_the_full_domain_on_a_declared_half_is_refused(tmp_path: Path) -> None:
    """There is no other half to restore, so 'full' would solve a different model."""

    pytest.importorskip("gmsh")
    from server.cadlink.intake.ingest import IngestRefusal

    with pytest.raises(IngestRefusal, match="full domain cannot be forced"):
        _ingest(
            tmp_path,
            _reduced_bundle(tmp_path, "half-forced"),
            symmetry_mode="full",
        )


def test_a_leaking_declared_half_is_refused_instead_of_solved_whole(
    tmp_path: Path,
) -> None:
    """The auto-cut fallback is the wrong kindness here.

    A rejected auto-cut can be re-meshed whole because the whole model is in
    the STEP. A declared half's other side is not, so meshing what arrived and
    solving it as a full model answers a different question.
    """

    pytest.importorskip("gmsh")
    from server.cadlink.intake.ingest import IngestRefusal

    bundle = _reduced_bundle(tmp_path, "half-leaking")
    with _inject_mesh_child_fault("leaking-reduced-domain"):
        with pytest.raises(IngestRefusal, match="the meshed boundary denies it"):
            _ingest(tmp_path, bundle)


def test_a_leaking_declared_quarter_is_refused_without_meshing_it_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WG cut nothing here, so there is no WG cut to give up.

    Every plane of a declared quarter is the author's. The auto-cut fallback
    would re-mesh the same model with the same declared planes only for the
    refusal after it to reject it again, so it is skipped and the one mesh's
    verdict is refused directly.
    """

    pytest.importorskip("gmsh")

    from hornlab_mesher import step_import

    from server.mesh.imported import ImportedMeshError, build_imported_mesh
    from server.tests.child_fault_fixtures import install_reduced_domain_leak

    monkeypatch.setattr(step_import, "postprocess_mesh", step_import.postprocess_mesh)
    install_reduced_domain_leak()
    leaking = step_import.postprocess_mesh
    calls: list[object] = []

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("symmetry_planes"))
        return leaking(*args, **kwargs)

    monkeypatch.setattr(step_import, "postprocess_mesh", counted)
    bundle = _reduced_bundle(tmp_path, "quarter", planes=("x0", "y0"))
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    with pytest.raises(ImportedMeshError, match="the meshed boundary denies it"):
        _run_in_gmsh_session(
            build_imported_mesh,
            bundle / "assembly.step",
            manifest,
            _SIZES,
            options={"symmetry_mode": "auto", "declared_cut_planes": ["x0", "y0"]},
            include_viewport_mesh=False,
        )
    assert len(calls) == 1


def test_a_capped_declared_half_is_refused_because_a_cap_is_a_wall(
    tmp_path: Path,
) -> None:
    """Splitting a solid and deleting half leaves a face on the plane.

    That face meshes as a rigid baffle, not as a mirror, so the solve would be
    of a half model sealed against a wall. The remedy is to delete the face, so
    the refusal has to name the cap rather than talk about symmetry.
    """

    pytest.importorskip("gmsh")
    from server.cadlink.intake.ingest import IngestRefusal

    bundle = _horn_bundle(tmp_path, "half-capped")
    assembly = bundle / "assembly.step"

    import gmsh

    def solid_cut() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(assembly), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        box = [float(value) for value in gmsh.model.getBoundingBox(-1, -1)]
        margin = 10.0
        half_space = gmsh.model.occ.addBox(
            box[0] - margin,
            0.0,
            box[2] - margin,
            (box[3] - box[0]) + 2.0 * margin,
            (box[4] - box[1]) + 2.0 * margin,
            (box[5] - box[2]) + 2.0 * margin,
        )
        gmsh.model.occ.intersect(gmsh.model.getEntities(3), [(3, half_space)])
        gmsh.model.occ.synchronize()
        gmsh.write(str(assembly))
        gmsh.clear()

    _run_in_gmsh_session(solid_cut)
    step = assembly.read_bytes()
    manifest = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    manifest["files"]["assembly.step"] = {
        "sha256": "sha256:" + hashlib.sha256(step).hexdigest(),
        "size_bytes": len(step),
        "media_type": "model/step",
        "purpose": "exterior-assembly",
    }
    source = manifest["sources"][0]
    half_area = float(source["observed"]["total_area_mm2"]) / 2.0
    source["observed"]["total_area_mm2"] = half_area
    source["observed"]["per_face_area_mm2"] = [half_area]
    manifest["assembly"]["domain"] = {
        "kind": "half",
        "cut_planes": ["y0"],
        "declared_by": "cad-author",
        "evidence": {"y0": {"min_mm": 0.0, "max_mm": 34.0, "tolerance_mm": 0.05}},
    }
    manifest["required_features"] = [
        *manifest["required_features"],
        "reduced-domain-v1",
    ]
    (bundle / "wgreturn.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(IngestRefusal, match="capped, not open"):
        _ingest(tmp_path, bundle)


def test_a_declared_quarter_is_cut_on_both_planes_and_solved_as_a_quarter(
    tmp_path: Path,
) -> None:
    """A real quarter, not a half with the second plane assumed.

    The cut is made on both planes at once, so the retained throat is a quarter
    disc, the free rim runs along two planes, and the linked contract is
    matched against a quarter of its disc rather than a half of it. None of
    that is exercised by extrapolating from the half case.
    """

    pytest.importorskip("gmsh")
    record = _ingest(tmp_path, _reduced_bundle(tmp_path, "quarter", planes=("x0", "y0")))

    symmetry = record["symmetry"]
    assert symmetry["cut_planes"] == []
    assert symmetry["declared_cut_planes"] == ["x0", "y0"]
    assert symmetry["domain_planes"] == ["x0", "y0"]
    assert {plane: symmetry["planes"][plane]["source"] for plane in ("x0", "y0")} == {
        "x0": "declared-by-cad-author",
        "y0": "declared-by-cad-author",
    }

    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    assert verification["declared_cut_planes"] == ["x0", "y0"]
    assert sorted(verification["detected_planes"]) == ["x0", "y0"]
    assert verification["off_plane_free_edge_count"] == 0
    assert verification["straddling_planes"] == []
    assert verification["wrong_side_planes"] == []
    assert "fallback" not in verification

    assert record["mesh"]["stats"]["domain_multiplier"] == 4.0
    assert record["mesh"]["stats"]["dense_solver_domain_multiplier"] == 4
    assert record["mesh"]["integrity"]["off_plane_open_edge_count"] == 0

    # Both retained quadrants, and only those.
    bounds = record["mesh"]["stats"]["bounds_m"]
    assert bounds["min_x"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["min_y"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["max_x"] > 0.0 and bounds["max_y"] > 0.0

    # The source role survives the declaration: the quarter throat still
    # carries its own physical tag, and nothing was demoted to rigid.
    mesh_text = Path(record["mesh_store_path"]).read_text(encoding="utf-8")
    assert (
        '"wg-import-v1|tag=101|source_id=source-hf|instance_id=anchor|role=HF"'
        in mesh_text
    )
    assert int(record["mesh"]["stats"]["tag_counts"]["101"]) > 0

    derivation = record["polar_grid_derivation"]
    assert derivation["cut_planes"] == ["x0", "y0"]
    assert derivation["axes"]["horizontal"]["minimum_deg"] == 0.0
    assert derivation["axes"]["vertical"]["minimum_deg"] == 0.0
    assert derivation["axes"]["diagonal"]["minimum_deg"] == 0.0

    finding = next(
        item for item in record["findings"] if item["kind"] == "declared-reduced-domain"
    )
    assert finding["blocking"] is False
    assert finding["declared_cut_planes"] == ["x0", "y0"]


def test_an_undeclared_quarter_is_recovered_on_both_planes(tmp_path: Path) -> None:
    """Two clean cuts through the throat: recovered from the geometry as a quarter."""

    pytest.importorskip("gmsh")
    record = _ingest(
        tmp_path,
        _reduced_bundle(
            tmp_path,
            "quarter-unlinked",
            planes=("x0", "y0"),
            declare=False,
            unlinked=True,
        ),
    )

    assert record["symmetry"]["domain_planes"] == ["x0", "y0"]
    assert record["symmetry"]["cut_planes"] == []
    assert record["mesh"]["stats"]["domain_multiplier"] == 4.0
    assert record["symmetry_verification"]["verified"] is True

    finding = next(
        item
        for item in record["findings"]
        if item["kind"] == "recovered-reduced-domain"
    )
    assert finding["blocking"] is False
    assert finding["planes"] == ["x0", "y0"]


# ----------------------------------- a reduced domain vs the full model it mirrors

_RETURN_IDS.update(
    {
        "rear": "wgr_01J5A8QK3M9T2XVBH0RD7NWEN0",
        "rear-full": "wgr_01J5A8QK3M9T2XVBH0RD7NWEP0",
    }
)

#: Physical tag of the one source in these fixtures.
_SOURCE_TAG = 101


def _solver_arrays(record: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import meshio

    mesh = meshio.read(record["mesh_store_path"], file_format="gmsh")
    return (
        np.asarray(mesh.points, dtype=float),
        np.asarray(mesh.get_cells_type("triangle"), dtype=np.int64),
        np.asarray(mesh.get_cell_data("gmsh:physical", "triangle")),
    )


def _area_vectors(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    return np.cross(
        points[triangles[:, 1]] - points[triangles[:, 0]],
        points[triangles[:, 2]] - points[triangles[:, 0]],
    )


def _winding_against_full(
    solved: dict[str, Any], full: dict[str, Any]
) -> dict[str, Any]:
    """Compare the solved domain's winding with the forced full domain's.

    Two independent readings. The mirrored signed volume: each reduced
    component is closed by its mirror images, and the cut-plane caps add
    nothing to an origin-based volume sum, so the domain multiplier times the
    solved mesh's signed volume is the full body's volume, sign included --
    +1 of the full domain's when wound alike, -1 when inverted. And a local
    one: each solved triangle's normal against the full-domain triangle with
    the nearest centroid. The tessellations differ, and across the 4 mm wall
    the nearest centroid is sometimes on the other face, so a correct quarter
    agrees on ~98% of its triangles and an inverted one on ~2% (measured).
    """

    sp, st, stags = _solver_arrays(solved)
    fp, ft, ftags = _solver_arrays(full)
    s_normals, f_normals = _area_vectors(sp, st), _area_vectors(fp, ft)
    s_centroids, f_centroids = sp[st].mean(axis=1), fp[ft].mean(axis=1)
    nearest = np.argmin(
        ((s_centroids[:, None, :] - f_centroids[None, :, :]) ** 2).sum(axis=2), axis=1
    )
    agreement = np.einsum("ij,ij->i", s_normals, f_normals[nearest]) > 0.0

    def volume(points: np.ndarray, triangles: np.ndarray) -> float:
        p0, p1, p2 = (points[triangles[:, k]] for k in range(3))
        return float(np.einsum("ij,ij->i", p0, np.cross(p1, p2)).sum() / 6.0)

    multiplier = float(solved["mesh"]["stats"]["domain_multiplier"])
    return {
        "volume_ratio": multiplier * volume(sp, st) / volume(fp, ft),
        "agreement": float(np.mean(agreement)),
        "source_z": (
            float(_area_vectors(sp, st[stags == _SOURCE_TAG]).sum(axis=0)[2]),
            float(_area_vectors(fp, ft[ftags == _SOURCE_TAG]).sum(axis=0)[2]),
        ),
    }


def test_the_quarter_is_wound_like_the_full_domain_it_mirrors(tmp_path: Path) -> None:
    """The flat throat disc, the face a real return tags, faces into the bore.

    ``orientation_valid`` cannot see a whole component wound the wrong way --
    an inverted mesh is perfectly consistent -- so the quarter is compared with
    the same return forced to stay whole.
    """

    pytest.importorskip("gmsh")
    quarter = _ingest(tmp_path / "q", _horn_bundle(tmp_path / "q", "round"))
    full = _ingest(
        tmp_path / "f", _horn_bundle(tmp_path / "f", "full"), symmetry_mode="full"
    )

    assert quarter["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert full["symmetry"]["cut_planes"] == []
    orientation = quarter["symmetry_verification"]["reduced_orientation"]
    assert orientation["checked"] is True
    assert orientation["component_count"] >= 1
    assert orientation["inverted_component_count"] == 0

    comparison = _winding_against_full(quarter, full)
    assert comparison["volume_ratio"] == pytest.approx(1.0, abs=0.01)
    assert comparison["agreement"] >= 0.95
    # The disc drives into the bore, +z, in both domains.
    assert comparison["source_z"][0] > 0.0
    assert comparison["source_z"][1] > 0.0
    postprocess = quarter["mesh"]["metadata"]["postprocess"]
    assert postprocess["flipped_global"] == 0
    assert int(postprocess.get("symmetry_source_parent_conflicts") or 0) == 0


def test_a_rear_facing_source_is_never_solved_inverted(tmp_path: Path) -> None:
    """A tagged source that looks away from the fluid, on a real ingest.

    The rounded cap's only planar face is the throat plug's rear, so the
    linked-throat contract binds to it and the source faces -z. The full model
    is wound outward, that face included. A mesher that winds the quarter so
    its source faces +z inverts every triangle of it, and the solve differs
    from the full domain by 87-100% -- with ``orientation_valid`` still true.

    The pinned mesher winds a reduced component from its mirrored parent, so
    the quarter is kept, wound like the full model, and the disagreeing source
    is reported rather than obeyed. the reduced-mesh check still stands
    behind it: a mesher that inverted the quarter again would cost the
    reduction, not solve it inverted.
    """

    pytest.importorskip("gmsh")
    auto = _ingest(
        tmp_path / "q",
        _horn_bundle(tmp_path / "q", "rear", source_shape=REAR_CAP),
    )
    full = _ingest(
        tmp_path / "f",
        _horn_bundle(tmp_path / "f", "rear-full", source_shape=REAR_CAP),
        symmetry_mode="full",
    )

    comparison = _winding_against_full(auto, full)
    assert comparison["volume_ratio"] == pytest.approx(1.0, abs=0.01)
    assert comparison["agreement"] >= 0.95
    # The source looks away from the bore in the full model, and still does in
    # what is solved.
    assert comparison["source_z"][1] < 0.0
    assert comparison["source_z"][0] < 0.0

    # Asked of the record, because the CAD child imports its own mesher. A
    # child without the mode is running a mesher WG no longer pins.
    assert _child_orients_from_parent(full), full["mesh"]["metadata"]["postprocess"]
    assert auto["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert "fallback" not in auto["symmetry_verification"]
    orientation = auto["symmetry_verification"]["reduced_orientation"]
    assert orientation["inverted_component_count"] == 0
    postprocess = auto["mesh"]["metadata"]["postprocess"]
    assert postprocess["reduced_orientation"] == "mirrored-parent"
    assert postprocess["flipped_global"] == 0
    assert postprocess["symmetry_parent_volume_flipped"] == 0
    assert postprocess["symmetry_parent_volume_kept"] >= 1
    # Kept, and recorded for a developer: the source disagreed with the
    # parent. It is not a user warning, because the winding is right and there
    # is nothing to do about it.
    assert postprocess["symmetry_source_parent_conflicts"] >= 1
    assert not any(
        "wound like the full model" in warning
        for warning in auto["mesh"]["stats"]["warnings"]
    )


_RETURN_IDS["rear-quarter"] = "wgr_01J5A8QK3M9T2XVBH0RD7NWEQ0"


def test_a_declared_quarter_with_a_rear_facing_source_is_never_solved_inverted(
    tmp_path: Path,
) -> None:
    """A declared domain has no whole model to fall back to.

    The author cut it in CAD, so the other three quarters are not in the STEP.
    It is wound like the full model and solved. Were it wound against it, it
    would be refused rather than solved, because the missing quarters are not
    there to fall back to.
    """

    pytest.importorskip("gmsh")
    full = _ingest(
        tmp_path / "f",
        _horn_bundle(tmp_path / "f", "rear-full", source_shape=REAR_CAP),
        symmetry_mode="full",
    )
    bundle = _reduced_bundle(
        tmp_path / "q", "rear-quarter", planes=("x0", "y0"), source_shape=REAR_CAP
    )

    assert _child_orients_from_parent(full), full["mesh"]["metadata"]["postprocess"]
    declared = _ingest(tmp_path / "q", bundle)
    assert declared["symmetry"]["domain_planes"] == ["x0", "y0"]
    orientation = declared["symmetry_verification"]["reduced_orientation"]
    assert orientation["inverted_component_count"] == 0
    comparison = _winding_against_full(declared, full)
    assert comparison["volume_ratio"] == pytest.approx(1.0, abs=0.01)
    assert comparison["agreement"] >= 0.95
    assert comparison["source_z"][0] < 0.0
    assert comparison["source_z"][1] < 0.0


_RETURN_IDS.update(
    {
        "inverted": "wgr_01J5A8QK3M9T2XVBH0RD7NWER0",
        "quarter-inverted": "wgr_01J5A8QK3M9T2XVBH0RD7NWES0",
    }
)


def test_an_inverted_auto_cut_quarter_falls_back_to_the_full_domain(
    tmp_path: Path,
) -> None:
    """What the rear-cap tests promise, on a real ingest: the reduction is lost.

    The pinned mesher winds a reduced component from its mirrored parent, so no
    fixture comes back inverted any more. The inversion is injected where one
    would appear instead -- the postprocessed quarter, every triangle reversed,
    before WG verifies it. It is closed modulo its cut planes and consistent,
    so only the reduced-mesh winding check stands between it and the solver.
    """

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "inverted")
    with _inject_mesh_child_fault("inverted-reduced-domain"):
        record = _ingest(tmp_path, bundle)

    assert record["symmetry"]["cut_planes"] == []
    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    fallback = verification["fallback"]
    assert fallback["rejected_cut_planes"] == ["x0", "y0"]
    assert fallback["capped_planes"] == []
    # A reversed winding names no plane; every reduced mesh is inverted here.
    assert [item["wg_cut_planes"] for item in fallback["attempts"]] == [
        ["x0", "y0"], ["x0"], [],
    ]
    assert fallback["attempts"][0]["failure_codes"] == ["inverted-orientation"]
    # Rejected for its winding alone: the reduced boundary itself was sound.
    assert fallback["off_plane_free_edge_count"] == 0
    assert fallback["reduced_orientation"]["inverted_component_count"] >= 1
    assert "wound against the model they mirror" in fallback["reason"]

    finding = next(
        item for item in record["findings"] if item["kind"] == "symmetry-cut-unverified"
    )
    assert finding["blocking"] is False
    assert finding["rejected_cut_planes"] == ["x0", "y0"]
    assert finding["detail"] == "WG will solve the whole model; symmetry could not be used for this one."
    # Technical reasons/remedies stay in the verification evidence.
    assert "wound against the model they mirror" in fallback["reason"]
    assert "Tag a source face that faces the bore" in fallback["remedy"]

    # The fault reaches reduced meshes only, so the fallback is the full model
    # wound outward, read from the arrays the solver will read.
    assert record["mesh"]["stats"]["domain_multiplier"] == 1.0
    points, triangles, _tags = _solver_arrays(record)
    p0, p1, p2 = (points[triangles[:, k]] for k in range(3))
    assert float(np.einsum("ij,ij->i", p0, np.cross(p1, p2)).sum()) > 0.0


def test_an_inverted_declared_quarter_is_refused_instead_of_solved(
    tmp_path: Path,
) -> None:
    """A declared quarter has no whole model to fall back to, so it is refused."""

    pytest.importorskip("gmsh")
    from server.cadlink.intake.ingest import IngestRefusal

    bundle = _reduced_bundle(tmp_path, "quarter-inverted", planes=("x0", "y0"))
    with _inject_mesh_child_fault("inverted-reduced-domain"):
        with pytest.raises(
            IngestRefusal, match="wound against the model they mirror"
        ) as refused:
            _ingest(tmp_path, bundle)

    message = str(refused.value)
    assert "Tag a source face that faces the bore" in message
    assert "Or return the whole model." in message


_RETURN_IDS["cached-inverted"] = "wgr_01J5A8QK3M9T2XVBH0RD7NWET0"


def _reverse_msh_triangles(msh_text: str) -> str:
    """Wind every triangle of an ASCII Gmsh 2.2 artifact the other way."""

    lines = msh_text.split("\n")
    start = lines.index("$Elements") + 2
    end = lines.index("$EndElements")
    for index in range(start, end):
        parts = lines[index].split()
        if len(parts) > 3 and parts[1] == "2":
            parts[-2], parts[-1] = parts[-1], parts[-2]
            lines[index] = " ".join(parts)
    return "\n".join(lines)


def test_a_cached_inverted_reduced_mesh_is_rebuilt_instead_of_served(
    tmp_path: Path,
) -> None:
    """The winding check guards a cache hit, not only a fresh build.

    Nothing in the mesh cache key names the rule a reduced component was wound
    by, so a quarter cached by an earlier build -- whose mesher wound it from a
    rear-facing source -- sits under the key a current build computes. It is
    planted here as that build left it: the same quarter wound inside out, and
    a sidecar written before the orientation verdict existed. A re-ingest must
    not hand it to a solver.
    """

    pytest.importorskip("gmsh")
    bundle = _horn_bundle(tmp_path, "cached-inverted")
    first = _ingest(tmp_path, bundle)
    assert first["mesh_cache_hit"] is False
    assert first["symmetry"]["cut_planes"] == ["x0", "y0"]

    mesh_path = Path(first["mesh_store_path"])
    sidecar_path = mesh_path.with_suffix(".json")
    inverted = _reverse_msh_triangles(mesh_path.read_text(encoding="utf-8"))
    inverted_sha256 = "sha256:" + hashlib.sha256(inverted.encode("utf-8")).hexdigest()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["symmetry_verification"].pop("reduced_orientation")
    sidecar["content_sha256"] = inverted_sha256
    mesh_path.write_text(inverted, encoding="utf-8")
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    second = _ingest(tmp_path, bundle)

    # The planted entry is judged before it is accepted, found inverted, and
    # rebuilt: still the quarter, now wound like the model it mirrors.
    assert second["mesh_cache_hit"] is False
    assert second["symmetry"]["cut_planes"] == ["x0", "y0"]
    assert second["mesh_content_sha256"] != inverted_sha256
    orientation = second["symmetry_verification"]["reduced_orientation"]
    assert orientation["checked"] is True
    assert orientation["inverted_component_count"] == 0
    points, triangles, _tags = _solver_arrays(second)
    p0, p1, p2 = (points[triangles[:, k]] for k in range(3))
    assert float(np.einsum("ij,ij->i", p0, np.cross(p1, p2)).sum()) > 0.0

    # The rebuild replaced the planted entry, so the next ingest is a sound hit
    # that carries the verdict it was accepted on.
    third = _ingest(tmp_path, bundle)
    assert third["mesh_cache_hit"] is True
    assert third["mesh_content_sha256"] == second["mesh_content_sha256"]
    assert (
        third["symmetry_verification"]["reduced_orientation"]["inverted_component_count"]
        == 0
    )


def _child_orients_from_parent(record: dict[str, Any]) -> bool:
    """Did the mesher in the CAD child wind reduced domains from their parent?

    Asked of a record rather than of this process: the ingest runs in the
    isolated CAD child, whose interpreter need not import the mesher this one
    does. A mesher with the mode echoes it on every postprocess, a full domain
    included, and one without it records no mode at all.
    """

    postprocess = record["mesh"]["metadata"]["postprocess"]
    return postprocess.get("reduced_orientation") == "mirrored-parent"
