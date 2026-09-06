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
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from server.cadlink.ingest import ingest_bundle
from server.cadlink.isolated import _inject_mesh_child_fault
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
        # These bundles name their design, and a real return is prepared with
        # that design open. Passing it keeps the project gate doing its job
        # here instead of these symmetry tests silently exercising the
        # no-target path that let a return build into whatever was open.
        expected_design_id="wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
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


def test_a_leaking_reduced_domain_falls_back_to_the_full_domain(tmp_path: Path) -> None:
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
    assert again["mesh_cache_hit"] is True
    assert any(
        item["kind"] == "symmetry-cut-unverified" for item in again["findings"]
    )


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
    face. That is what a usable half looks like -- the reported PartyMEH model
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
        planar = [
            index
            for index, (_dim, tag) in enumerate(sorted(gmsh.model.getEntities(2)))
            if str(gmsh.model.getType(2, tag)).casefold() == "plane"
        ]
        gmsh.clear()
        # The open cut leaves no face on y = 0, so the throat cap is the only
        # planar face left.
        assert len(planar) == 1, planar
        return planar[0]

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
) -> Path:
    """The round horn, cut open on ``planes`` in CAD, optionally declaring it."""

    bundle = _horn_bundle(tmp_path, name)
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
        # This is the shape of the reported PartyMEH model, and the shape in
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


def test_a_declared_half_is_mirrored_rather_than_cut_again(tmp_path: Path) -> None:
    """The half the author cut is the domain, and WG says so rather than guessing.

    The cutter correctly declines a plane the model does not straddle, so
    without the declaration this model comes back ``full`` -- solved as an open
    shell radiating through its own cut face.
    """

    pytest.importorskip("gmsh")
    record = _ingest(tmp_path, _reduced_bundle(tmp_path, "half"))

    symmetry = record["symmetry"]
    # Nothing was cut here; the domain is what the author already cut.
    assert symmetry["cut_planes"] == []
    assert symmetry["declared_cut_planes"] == ["y0"]
    assert symmetry["domain_planes"] == ["y0"]
    assert symmetry["planes"]["y0"]["source"] == "declared-by-cad-author"

    verification = record["symmetry_verification"]
    assert verification["verified"] is True
    assert verification["declared_cut_planes"] == ["y0"]
    assert "y0" in verification["detected_planes"]
    assert verification["off_plane_free_edge_count"] == 0
    assert "fallback" not in verification

    # The solve halves, and the polar sweep with it.
    assert record["mesh"]["stats"]["domain_multiplier"] == 2.0
    assert record["mesh"]["stats"]["dense_solver_domain_multiplier"] == 2
    derivation = record["polar_grid_derivation"]
    assert derivation["cut_planes"] == ["y0"]
    assert derivation["axes"]["vertical"]["minimum_deg"] == 0.0
    assert derivation["axes"]["horizontal"]["minimum_deg"] == -180.0

    bounds = record["mesh"]["stats"]["bounds_m"]
    assert bounds["min_y"] == pytest.approx(0.0, abs=1.0e-9)
    assert bounds["min_x"] < 0.0 < bounds["max_x"]

    # The half throat still carries its source tag: the retained half of the
    # contract's disc is what the linked selector is matched against.
    assert int(record["mesh"]["stats"]["tag_counts"]["101"]) > 0

    finding = next(
        item for item in record["findings"] if item["kind"] == "declared-reduced-domain"
    )
    assert finding["blocking"] is False
    assert finding["declared_cut_planes"] == ["y0"]


def test_the_same_half_returned_undeclared_is_reported_not_solved_in_silence(
    tmp_path: Path,
) -> None:
    """One dropdown apart, on a Fusion-first return nothing else can catch.

    A linked return whose throat was halved fails role resolution, so it is
    already refused. An unlinked one has no design to contradict: the half
    meshes cleanly and would be solved whole, radiating through its own cut
    face, unless something recognises the shape of a reduced domain.
    """

    pytest.importorskip("gmsh")
    record = _ingest(
        tmp_path,
        _reduced_bundle(tmp_path, "half-undeclared", declare=False, unlinked=True),
    )

    assert record["symmetry"]["domain_planes"] == []
    assert record["mesh"]["stats"]["domain_multiplier"] == 1.0
    assert record["symmetry_verification"]["undeclared_open_planes"] == ["y0"]

    finding = next(
        item
        for item in record["findings"]
        if item["kind"] == "undeclared-reduced-domain"
    )
    assert finding["blocking"] is True
    assert finding["detected_planes"] == ["y0"]
    assert "Model domain" in finding["detail"]


def test_forcing_the_full_domain_on_a_declared_half_is_refused(tmp_path: Path) -> None:
    """There is no other half to restore, so 'full' would solve a different model."""

    pytest.importorskip("gmsh")
    from server.cadlink.ingest import IngestRefusal

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
    from server.cadlink.ingest import IngestRefusal

    bundle = _reduced_bundle(tmp_path, "half-leaking")
    with _inject_mesh_child_fault("leaking-reduced-domain"):
        with pytest.raises(IngestRefusal, match="the meshed boundary denies it"):
            _ingest(tmp_path, bundle)


def test_a_capped_declared_half_is_refused_because_a_cap_is_a_wall(
    tmp_path: Path,
) -> None:
    """Splitting a solid and deleting half leaves a face on the plane.

    That face meshes as a rigid baffle, not as a mirror, so the solve would be
    of a half model sealed against a wall. The remedy is to delete the face, so
    the refusal has to name the cap rather than talk about symmetry.
    """

    pytest.importorskip("gmsh")
    from server.cadlink.ingest import IngestRefusal

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


def test_an_undeclared_quarter_is_recognised_on_both_planes(tmp_path: Path) -> None:
    """Two planes to miss instead of one, and neither may pass in silence."""

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

    assert record["symmetry"]["domain_planes"] == []
    assert record["mesh"]["stats"]["domain_multiplier"] == 1.0
    assert sorted(record["symmetry_verification"]["undeclared_open_planes"]) == [
        "x0",
        "y0",
    ]

    finding = next(
        item
        for item in record["findings"]
        if item["kind"] == "undeclared-reduced-domain"
    )
    assert finding["blocking"] is True
    assert sorted(finding["detected_planes"]) == ["x0", "y0"]
