"""The automatic solver frame through real ingestion: real STEP, real gmsh.

The production ``ingest_bundle`` meshes generated models; the suggestion is
computed from the record exactly as preparation computes it
(``solver_frame.ensure_frame_suggestion``). This covers what the synthetic
arrays cannot: WG's own symmetry cut and the mirror-back of its reduced mesh,
the frame inverse after a confirmed non-+z preparation, and an axial drive
channel in a frame whose forward axis is not CAD +Z.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from server.cadlink.frame_infer import infer_record_frame, survey_mesh_from_record
from server.cadlink.ingest import ingest_bundle
from server.cadlink.solver_frame import (
    CONTRACT,
    confirm_frame,
    ensure_frame_suggestion,
    frame_spec,
    record_frame_refusal,
    spec_matrix,
)
from server.cadlink.store import CadLinkStore
from server.mesh.artifact import read_verified_import_mesh
from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.solver.beat_imported import _drive_groups, _Gmsh22Mesh, beat_imported_frame
from server.solver.imported import (
    imported_anchor_frame,
    imported_domain_planes,
    imported_symmetry_from_cut_planes,
)

import cad_frame_fixtures as fx
from test_cadlink_wgreturn import _manifest as wgreturn_manifest

pytest.importorskip("gmsh")
pytest.importorskip("meshio")


def _party() -> list:
    return fx.party_meh_like(0.25)


def _rear_horn() -> list:
    return fx.horn_box(rear_lf=True)


def _meh_horn() -> list:
    return fx.horn_box(mf_taps=True)


def _bundle(root: Path, name: str, builder, pose: str) -> tuple[Path, dict[str, Any]]:
    bundle = root / "workspace" / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    step_path = bundle / "assembly.step"
    authored = _run_in_gmsh_session(fx.write_step_fixture, builder, pose, step_path)
    manifest = wgreturn_manifest(step_path.read_bytes())
    manifest["document"] = {"name": name, "native_id": f"urn:adsk.wipprod:fs.file:{name}"}
    manifest["assembly"]["bbox_mm"] = authored["bbox"]
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["instances"] = []
    manifest["scope"]["included"][0]["wglink_instance_id"] = None
    manifest["scope"]["skipped"] = []
    manifest["sources"] = [
        {
            "id": source_id,
            "role": entry["role"],
            "instance_id": None,
            "required": True,
            "default_drive_channel_id": f"drive-{source_id}",
            "patch_policy": "single-connected" if len(entry["faces"]) == 1 else "explicit-disconnected",
            "expected_connected_components": len(entry["faces"]),
            "selectors": {"advanced_face_indices": entry["faces"]},
            "observed": {
                "face_count": len(entry["faces"]),
                "total_area_mm2": sum(entry["areas"]),
                "per_face_area_mm2": entry["areas"],
                "bodies": ["speaker"],
            },
            "suggested_resolution_mm": 3,
        }
        for source_id, entry in sorted(authored["sources"].items())
    ]
    (bundle / "wgreturn.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle, manifest


def _mesh_sizes(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "rigid_size_mm": 12,
        "transition_mm": 20,
        "source_size_mm": {source["id"]: 3 for source in manifest["sources"]},
    }


def _ingest(bundle: Path, manifest: dict, data_dir: Path, **prep: object) -> dict[str, Any]:
    return _run_in_gmsh_session(
        ingest_bundle,
        bundle,
        _mesh_sizes(manifest),
        [],
        CadLinkStore(data_dir / "cadlink.db"),
        data_dir,
        prep_options=dict(prep),
    )


def _areas(mesh) -> tuple[float, dict[str, float]]:
    corners = mesh.points_mm[mesh.triangles]
    areas = 0.5 * np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    )
    by_source = {source.source_id: float(areas[mesh.tags == source.tag].sum()) for source in mesh.sources}
    return float(areas.sum()), by_source


def test_the_suggestion_from_wgs_own_cut_matches_the_full_model(tmp_path: Path) -> None:
    """Mirror-back validated against the full geometry, both through ingestion."""

    bundle, manifest = _bundle(tmp_path, "meh-horn", _meh_horn, "+z")
    data_dir = tmp_path / "data"
    store = CadLinkStore(data_dir / "cadlink.db")
    cut = _ingest(bundle, manifest, data_dir)
    whole = _ingest(bundle, manifest, data_dir, symmetry_mode="full")
    assert cut["symmetry"]["cut_planes"] == ["x0", "y0"]  # a quarter
    assert not whole["symmetry"].get("cut_planes")

    mirrored = survey_mesh_from_record(cut, read_verified_import_mesh(cut))
    full = survey_mesh_from_record(whole, read_verified_import_mesh(whole))
    assert np.allclose(mirrored.points_mm.min(axis=0), full.points_mm.min(axis=0), atol=0.05)
    assert np.allclose(mirrored.points_mm.max(axis=0), full.points_mm.max(axis=0), atol=0.05)
    mirrored_area, mirrored_sources = _areas(mirrored)
    full_area, full_sources = _areas(full)
    assert mirrored_area == pytest.approx(full_area, rel=0.01)
    assert set(mirrored_sources) == set(full_sources) == {"hf", "mf"}
    for source_id, area in full_sources.items():
        assert mirrored_sources[source_id] == pytest.approx(area, rel=0.02), source_id

    from_cut = infer_record_frame(cut, read_verified_import_mesh(cut))
    from_whole = infer_record_frame(whole, read_verified_import_mesh(whole))
    assert from_cut.status == from_whole.status == "automatic", (from_cut.reason, from_cut.evidence)
    assert from_cut.axis == from_whole.axis == "+z"
    assert from_cut.confidence == pytest.approx(from_whole.confidence, abs=0.1)
    suggestion = ensure_frame_suggestion(store, cut)
    assert suggestion["status"] == "automatic" and suggestion["axis"] == "+z"


def test_a_model_facing_x_is_found_and_confirming_it_meshes_the_v2_frame(tmp_path: Path) -> None:
    bundle, manifest = _bundle(tmp_path, "party-facing-x", _party, "+x")
    data_dir = tmp_path / "data"
    store = CadLinkStore(data_dir / "cadlink.db")
    shown = _ingest(bundle, manifest, data_dir)
    suggestion = ensure_frame_suggestion(store, shown)
    assert suggestion["status"] == "automatic", suggestion["reason"]
    assert suggestion["axis"] == "+x"
    confirm_frame(store, shown, suggestion["axis"])

    solved = _ingest(bundle, manifest, data_dir, solver_frame="+x")
    frame = solved["normalisation"]["solver_frame"]
    assert (frame["contract"], frame["axis"], frame["up"], frame["up_source"]) == (CONTRACT, "+x", "+z", "default")
    assert np.allclose(solved["normalisation"]["matrix"], spec_matrix(frame_spec("+x", manifest)), atol=1e-12)
    assert record_frame_refusal(store, solved) is None
    # Surveyed back through the confirmed frame's inverse (and WG's own cut in
    # that frame), the prepared record says the same.
    again = infer_record_frame(solved, read_verified_import_mesh(solved))
    assert again.status == "automatic" and again.axis == "+x"


def test_an_axial_channel_follows_the_chosen_forward_axis(tmp_path: Path) -> None:
    """Model facing +x with a rear-facing source: axial drive is along CAD +x.

    Metal and BEAT drive an axial source at ``n . axis`` in the record's frame
    and flip a tag facing back along it (``beat_imported._drive_groups``). In a
    +x preparation that axis is solver +Z, which is CAD +x: the throat is
    driven forward and the rear source flipped to drive outward, as they would
    be for the same model modelled along +z.
    """

    bundle, manifest = _bundle(tmp_path, "rear-facing-x", _rear_horn, "+x")
    data_dir = tmp_path / "data"
    record = _ingest(bundle, manifest, data_dir, solver_frame="+x")
    assert record["normalisation"]["solver_frame"]["axis"] == "+x"

    observation = imported_anchor_frame(record)
    solver_from_assembly = np.asarray(record["normalisation"]["matrix"], dtype=float)
    assert np.allclose(observation["axis"], [0.0, 0.0, 1.0])
    assert np.allclose(solver_from_assembly[:3, :3].T @ observation["axis"], [1.0, 0.0, 0.0], atol=1e-9)

    beat = beat_imported_frame(record, beat_native_plane(record))
    mesh = _Gmsh22Mesh.parse(read_verified_import_mesh(record)).rotated(beat.rotation)
    orientation = mesh.axial_orientation()
    tags = record["source_tags"]
    hf, lf = int(tags["hf"]), int(tags["lf"])
    assert orientation[hf][0] > 0.9 * orientation[hf][1]
    assert orientation[lf][0] < -0.9 * orientation[lf][1]
    groups = _drive_groups(frozenset({hf, lf}), "axial", orientation)
    assert groups == [(1.0, frozenset({hf})), (-1.0, frozenset({lf}))]

    # The same model modelled along +z and solved as modelled drives the same way.
    modelled_bundle, modelled_manifest = _bundle(tmp_path, "rear-facing-z", _rear_horn, "+z")
    modelled = _ingest(modelled_bundle, modelled_manifest, tmp_path / "data-z")
    modelled_beat = beat_imported_frame(modelled, beat_native_plane(modelled))
    modelled_mesh = _Gmsh22Mesh.parse(read_verified_import_mesh(modelled)).rotated(modelled_beat.rotation)
    modelled_tags = modelled["source_tags"]
    modelled_groups = _drive_groups(
        frozenset({int(modelled_tags["hf"]), int(modelled_tags["lf"])}),
        "axial",
        modelled_mesh.axial_orientation(),
    )
    assert modelled_groups == [
        (1.0, frozenset({int(modelled_tags["hf"])})),
        (-1.0, frozenset({int(modelled_tags["lf"])})),
    ]


def beat_native_plane(record: dict[str, Any]) -> str | None:
    return imported_symmetry_from_cut_planes(imported_domain_planes(record)).native_plane
