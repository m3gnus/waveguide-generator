"""The unlinked frame preview is the solved frame: real STEP, real ingest, real gmsh.

A CAD-authored horn modelled radiating along the assembly's +Y is ingested
twice through the production ``ingest_bundle``: as modelled (+z, what the user
is shown before confirming) and in the confirmed +y frame (what is solved).
The preview's matrix, applied to the as-modelled geometry, must be the solved
geometry, and the record must state the matrix the preview used.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from server.cadlink.ingest import IngestRefusal, ingest_bundle
from server.cadlink.solver_frame import (
    CONTRACT,
    confirm_frame,
    frame_matrix,
    frame_preview,
    record_frame_refusal,
)
from server.cadlink.store import CadLinkStore
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.solver.imported import imported_anchor_frame
from test_cadlink_wgreturn import _manifest as wgreturn_manifest


gmsh = pytest.importorskip("gmsh")
meshio = pytest.importorskip("meshio")

SOURCE_ID = "throat"
MESH = {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {SOURCE_ID: 8}}


def _write_authored_bundle(root: Path, name: str, *, radiates: str = "+y") -> Path:
    """A tiny closed-throat horn authored in CAD, radiating along ``radiates``."""

    from hornlab_mesher.cad import write_step
    from hornlab_mesher.geometry import PointGridHornGeometry
    from hornlab_mesher.step_import import advanced_face_order, gmsh_surface_tags

    n_phi, n_length = 16, 6
    inner = np.empty((n_phi, n_length + 1, 3), dtype=float)
    for phi_index in range(n_phi):
        phi = math.tau * phi_index / n_phi
        for length_index in range(n_length + 1):
            fraction = length_index / n_length
            radius = 10.0 + 20.0 * fraction
            inner[phi_index, length_index] = (
                radius * math.cos(phi), radius * math.sin(phi), 60.0 * fraction,
            )
    outer = inner.copy()
    radial = np.linalg.norm(outer[:, :, :2], axis=2)
    outer[:, :, 0] *= (radial + 4.0) / radial
    outer[:, :, 1] *= (radial + 4.0) / radial
    geometry = PointGridHornGeometry(inner_points=inner, outer_points=outer, wall_thickness_mm=4.0)

    bundle = root / "workspace" / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    modelled = root / f"{name}-along-z.step"
    _run_in_gmsh_session(write_step, geometry, modelled, open_throat=False)
    step_path = bundle / "assembly.step"

    def author_along() -> tuple[list[float], list[float]]:
        # The mesher writes horns along +Z; the CAD author placed this one
        # along ``radiates``. A rigid OCC rotation, so every face survives.
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        shapes = gmsh.model.occ.importShapes(str(modelled))
        if radiates == "+y":
            gmsh.model.occ.rotate(shapes, 0, 0, 0, 1, 0, 0, -math.pi / 2)
        elif radiates != "+z":
            raise AssertionError(radiates)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step_path))
        box = gmsh.model.getBoundingBox(-1, -1)
        gmsh.clear()
        return list(box[:3]), list(box[3:])

    low, high = _run_in_gmsh_session(author_along)
    step = step_path.read_bytes()

    def throat_face() -> tuple[int, float]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(step_path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        surfaces = gmsh_surface_tags()
        faces = advanced_face_order(step_path)
        planes = [
            tag for tag in surfaces if str(gmsh.model.getType(2, tag)).casefold() == "plane"
        ]
        # The closed throat is the plane face through the origin.
        surface = min(
            planes,
            key=lambda tag: float(np.linalg.norm(gmsh.model.occ.getCenterOfMass(2, tag))),
        )
        area = float(gmsh.model.occ.getMass(2, surface))
        gmsh.clear()
        return faces[surfaces.index(surface)], area

    face_id, area = _run_in_gmsh_session(throat_face)
    manifest = wgreturn_manifest(step)
    manifest["document"] = {"name": "Authored horn", "native_id": "urn:adsk.wipprod:fs.file:authored"}
    manifest["assembly"]["bbox_mm"] = [low, high]
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["instances"] = []
    manifest["scope"]["included"][0]["wglink_instance_id"] = None
    manifest["scope"]["skipped"] = []
    manifest["sources"] = [{
        "id": SOURCE_ID,
        "role": "HF",
        "instance_id": None,
        "required": True,
        "default_drive_channel_id": "drive-hf",
        "patch_policy": "single-connected",
        "expected_connected_components": 1,
        "selectors": {"advanced_face_indices": [face_id]},
        "observed": {"face_count": 1, "total_area_mm2": area, "per_face_area_mm2": [area], "bodies": ["speaker"]},
        "suggested_resolution_mm": 8,
    }]
    (bundle / "wgreturn.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


def _ingest(bundle: Path, data_dir: Path, **prep: object) -> dict:
    return _run_in_gmsh_session(
        ingest_bundle,
        bundle,
        MESH,
        [],
        CadLinkStore(data_dir / "cadlink.db"),
        data_dir,
        prep_options=dict(prep),
    )


def _nodes_and_source(record: dict) -> tuple[np.ndarray, np.ndarray]:
    viewport = record["viewport_mesh"]
    path = viewport["store_path"] if viewport.get("available") else record["mesh_store_path"]
    mesh = meshio.read(path, file_format="gmsh")
    points = np.asarray(mesh.points, dtype=float)
    tag = int(record["source_tags"][SOURCE_ID])
    source_nodes: list[np.ndarray] = []
    for block, tags in zip(mesh.cells, mesh.cell_data["gmsh:physical"], strict=True):
        if block.type == "triangle":
            source_nodes.append(block.data[np.asarray(tags) == tag].ravel())
    source = points[np.unique(np.concatenate(source_nodes))]
    return points, source


def _bbox(points: np.ndarray) -> np.ndarray:
    return np.stack([points.min(axis=0), points.max(axis=0)])


def test_the_preview_matrix_turns_the_modelled_geometry_into_the_solved_geometry(
    tmp_path: Path,
) -> None:
    bundle = _write_authored_bundle(tmp_path, "authored")
    data_dir = tmp_path / "data"
    store = CadLinkStore(data_dir / "cadlink.db")

    shown = _ingest(bundle, data_dir)
    assert shown["normalisation"]["solver_frame"]["axis"] == "+z"
    assert shown["normalisation"]["assembly_frame_is_solver_frame"] is True
    # Unconfirmed: nothing can solve it.
    assert record_frame_refusal(store, shown) is not None

    confirm_frame(store, shown, "+y")
    solved = _ingest(bundle, data_dir, solver_frame="+y")
    frame = solved["normalisation"]["solver_frame"]
    assert frame["axis"] == "+y" and frame["contract"] == CONTRACT
    assert frame["requirement"] == {"contract": CONTRACT, "export_frame": "root-component"}
    # One matrix: what was applied, what the record states, what the preview offers.
    preview = {item["axis"]: item for item in frame_preview(store, shown)["axes"]}
    # The record states the matrix OCC actually applied, rebuilt from its
    # rotations, so it may differ from the contract's in the last bit only.
    applied = np.asarray(solved["normalisation"]["matrix"], dtype=float)
    assert np.allclose(applied, frame_matrix("+y"), rtol=0.0, atol=1e-12)
    assert np.allclose(preview["+y"]["solverFromAssembly"], applied, rtol=0.0, atol=1e-12)
    assert solved["normalisation"]["solver_frame"]["matrix"] == preview["+y"]["solverFromAssembly"]
    assert solved["normalisation"]["assembly_frame_is_solver_frame"] is False
    assert solved["mesh_cache_key"] != shown["mesh_cache_key"]
    # The same project: its confirmation covers this record, and not the +z one.
    assert record_frame_refusal(store, solved) is None
    assert record_frame_refusal(store, shown) is not None

    shown_points, shown_source = _nodes_and_source(shown)
    solved_points, solved_source = _nodes_and_source(solved)
    # Mesh artifacts are in metres; a rotation with no translation needs no rescale.
    matrix = np.asarray(preview["+y"]["previewFromRecord"], dtype=float)
    previewed = shown_points @ matrix[:3, :3].T + matrix[:3, 3]
    # Modelled along +Y: the preview and the solve both put it along +Z.
    assert _bbox(shown_points)[1, 1] == pytest.approx(0.060, abs=5e-4)
    assert np.allclose(_bbox(previewed), _bbox(solved_points), atol=5e-5)
    assert _bbox(solved_points)[1, 2] == pytest.approx(0.060, abs=5e-4)
    # The throat disc (behind the 4 mm closed wall): a y-plane as modelled, a
    # z-plane of the solver solved, the same distance from the origin.
    assert np.ptp(shown_source[:, 1]) < 1e-9 and np.ptp(solved_source[:, 2]) < 1e-9
    assert solved_source[0, 2] == pytest.approx(shown_source[0, 1], abs=1e-9)
    assert np.ptp(shown_source[:, 2]) > 0.01 and np.ptp(solved_source[:, 1]) > 0.01
    previewed_source = shown_source @ matrix[:3, :3].T
    assert np.allclose(_bbox(previewed_source), _bbox(solved_source), atol=5e-5)

    # Every engine measures the solved record on the solver's +Z at the origin.
    observation = imported_anchor_frame(solved)
    assert np.allclose(observation["axis"], [0.0, 0.0, 1.0])
    assert np.allclose(observation["origin"], [0.0, 0.0, 0.0])


def test_a_solver_frame_is_refused_for_a_linked_return_and_an_unknown_axis(
    tmp_path: Path,
) -> None:
    bundle = _write_authored_bundle(tmp_path, "authored", radiates="+z")
    data_dir = tmp_path / "data"
    with pytest.raises(IngestRefusal, match="solver frame"):
        _ingest(bundle, data_dir, solver_frame="sideways")
    linked = json.loads((bundle / "wgreturn.json").read_text(encoding="utf-8"))
    linked_manifest = wgreturn_manifest(b"")
    linked["instances"] = linked_manifest["instances"]
    linked["coordinate_system"]["solver_anchor_instance_id"] = linked["instances"][0]["instance_id"]
    (bundle / "wgreturn.json").write_text(json.dumps(linked), encoding="utf-8")
    with pytest.raises(IngestRefusal, match="solver frame"):
        _ingest(bundle, data_dir, solver_frame="+y")


def test_the_modelled_frame_leaves_the_mesh_cache_key_unchanged(tmp_path: Path) -> None:
    """``+z`` is today's frame: naming it must not re-make a single cached mesh."""

    bundle = _write_authored_bundle(tmp_path, "authored", radiates="+z")
    data_dir = tmp_path / "data"
    implicit = _ingest(bundle, data_dir)
    explicit = _ingest(bundle, data_dir, solver_frame="+z")
    assert explicit["mesh_cache_key"] == implicit["mesh_cache_key"]
    assert explicit["mesh_cache_hit"] is True
