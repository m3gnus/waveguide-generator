"""STEP ADVANCED_FACE ids map to OCC surfaces by geometry, not record order.

gmsh numbers surfaces in the order OCC walks a shell, which is not always the
order the file lists the faces. A CAD return addresses its sources by
ADVANCED_FACE id, so a record-order zip labels the wrong surface as the source.
The fixture below lists one shell's faces in reverse; only the mapping by
geometry lands the source on the throat disc it names.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from server.cadlink.ingest import IngestRefusal, ingest_bundle
from server.cadlink.store import CadLinkStore
from server.mesh.gmsh_worker import _run_in_gmsh_session
from test_cadlink_wgreturn import _manifest as wgreturn_manifest

INSTANCE_ID = "3f2c9a4e-8b1d-4c6f-9e2a-7d5b1c0e8f43"
SOURCE_ID = "throat"


def _write_horn(bundle: Path, *, reverse_shell: bool) -> tuple[Path, object]:
    from hornlab_mesher.cad import write_step
    from hornlab_mesher.geometry import PointGridHornGeometry

    n_phi, n_length = 16, 6
    inner = np.empty((n_phi, n_length + 1, 3), dtype=float)
    for i in range(n_phi):
        phi = math.tau * i / n_phi
        for j in range(n_length + 1):
            fraction = j / n_length
            radius = 10.0 + 20.0 * fraction
            inner[i, j] = (radius * math.cos(phi), radius * math.sin(phi), 60.0 * fraction)
    outer = inner.copy()
    radial = np.linalg.norm(outer[:, :, :2], axis=2)
    outer[:, :, 0] *= (radial + 4.0) / radial
    outer[:, :, 1] *= (radial + 4.0) / radial
    geometry = PointGridHornGeometry(inner_points=inner, outer_points=outer, wall_thickness_mm=4.0)
    step_path, info = _run_in_gmsh_session(write_step, geometry, bundle / "assembly.step", open_throat=False)
    if reverse_shell:
        text = step_path.read_text(encoding="ascii")
        match = re.search(r"CLOSED_SHELL\('',\(([^)]*)\)\)", text)
        assert match is not None
        faces = [face.strip() for face in match.group(1).split(",")]
        reversed_shell = "CLOSED_SHELL('',(%s))" % ",".join(reversed(faces))
        step_path.write_text(text.replace(match.group(0), reversed_shell), encoding="ascii")
    return step_path, info


def _throat_face(step_path: Path) -> tuple[int, float]:
    """The throat disc's ADVANCED_FACE id (the only planar face) and its area."""

    import gmsh
    from hornlab_mesher.step_import import gmsh_surface_tags
    from hornlab_mesher.step_text import advanced_face_surface_kinds_from_text

    kinds = advanced_face_surface_kinds_from_text(step_path.read_text(encoding="ascii"))
    planar = [face for face, kind in kinds.items() if kind == "plane"]
    assert len(planar) == 1, kinds

    def area() -> float:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.occ.importShapes(str(step_path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        value = next(
            float(gmsh.model.occ.getMass(2, tag))
            for tag in gmsh_surface_tags()
            if str(gmsh.model.getType(2, tag)).casefold() == "plane"
        )
        gmsh.clear()
        return value

    return planar[0], _run_in_gmsh_session(area)


def _bundle(tmp_path: Path, *, reverse_shell: bool) -> tuple[Path, int]:
    bundle = tmp_path / "workspace" / "wgreturn" / "order.wgreturn"
    bundle.mkdir(parents=True)
    step_path, info = _write_horn(bundle, reverse_shell=reverse_shell)
    face_id, area = _throat_face(step_path)
    manifest = wgreturn_manifest(step_path.read_bytes())
    manifest["required_features"].append("source-identity-v1")
    manifest["assembly"]["bbox_mm"] = [list(info.bounding_box_mm[0]), list(info.bounding_box_mm[1])]
    manifest["coordinate_system"]["solver_anchor_instance_id"] = INSTANCE_ID
    manifest["scope"]["included"][0]["wglink_instance_id"] = INSTANCE_ID
    manifest["scope"]["skipped"] = []
    instance = manifest["instances"][0]
    instance["instance_id"] = INSTANCE_ID
    instance["source_contract"] = {
        "role": "HF", "throat_z_mm": 0,
        "throat_plane_link": {"origin_mm": [0, 0, 0], "normal": [0, 0, 1]},
        "axis_link": {"origin_mm": [0, 0, 0], "direction": [0, 0, 1]},
        "throat_diameter_mm": 20.0, "expected_disc_area_mm2": math.pi * 100.0,
    }
    manifest["sources"] = [{
        "id": SOURCE_ID, "role": "HF", "instance_id": INSTANCE_ID, "required": True,
        "default_drive_channel_id": "drive-hf", "patch_policy": "single-connected",
        "expected_connected_components": 1,
        "selectors": {"advanced_face_indices": [face_id]},
        "observed": {"face_count": 1, "total_area_mm2": area, "per_face_area_mm2": [area], "bodies": ["speaker"]},
        "suggested_resolution_mm": 8,
    }]
    (bundle / "wgreturn.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle, face_id


def _ingest(bundle: Path, tmp_path: Path) -> dict:
    data_dir = tmp_path / "data"
    return _run_in_gmsh_session(
        ingest_bundle,
        bundle,
        {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {SOURCE_ID: 8}},
        [],
        CadLinkStore(data_dir / "cadlink.db"),
        data_dir,
        expected_design_id="wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
        expected_instance_id=INSTANCE_ID,
    )


def _build_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the mesher in this process so a test can see or replace its calls."""

    from server.mesh.imported import build_imported_mesh

    def build(assembly_path, manifest, normalization, **kwargs):
        kwargs.pop("expected_sha256", None)
        kwargs.pop("expected_size_bytes", None)
        return build_imported_mesh(assembly_path, manifest, normalization, **kwargs)

    monkeypatch.setattr("server.cadlink.ingest.build_imported_mesh_isolated", build)


def _source_triangles(record: dict) -> int:
    counts = record["mesh"]["stats"]["tag_counts"]
    return int(counts["101"])


@pytest.mark.parametrize("reverse_shell", [False, True])
def test_the_source_lands_on_the_face_it_names(tmp_path: Path, reverse_shell: bool) -> None:
    """Record order and OCC's surface order agree for the plain file and differ
    for the reversed shell; the source is the throat disc either way."""

    pytest.importorskip("gmsh")
    bundle, _ = _bundle(tmp_path, reverse_shell=reverse_shell)
    record = _ingest(bundle, tmp_path)
    # The throat disc (radius 10 mm) at 8 mm sizing is a handful of triangles; a
    # mislabelled B-spline wall panel would carry many more or refuse on area.
    assert 0 < _source_triangles(record) < 40


def test_the_mapping_is_asked_with_the_applied_matrix_and_addressed_faces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("gmsh")
    import hornlab_mesher.step_import as step_import

    bundle, face_id = _bundle(tmp_path, reverse_shell=True)
    real = step_import.advanced_face_order_for_surfaces
    calls: list[dict] = []

    def spy(path, surfaces=None, **kwargs):
        calls.append({"model_from_step": np.asarray(kwargs["model_from_step"]), "addressed": list(kwargs["addressed_faces"])})
        return real(path, surfaces, **kwargs)

    monkeypatch.setattr(step_import, "advanced_face_order_for_surfaces", spy)
    _build_in_process(monkeypatch)
    record = _ingest(bundle, tmp_path)
    assert calls and calls[0]["addressed"] == [face_id]
    assert calls[0]["model_from_step"].shape == (4, 4)
    assert record["normalisation"]["matrix"] == calls[0]["model_from_step"].tolist()


def test_an_ambiguous_mapping_refuses_instead_of_using_record_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("gmsh")
    import hornlab_mesher.step_import as step_import

    bundle, _ = _bundle(tmp_path, reverse_shell=False)

    def refuse(*_args, **_kwargs):
        raise step_import.StepFaceOrderError("indistinguishable faces with different labels")

    monkeypatch.setattr(step_import, "advanced_face_order_for_surfaces", refuse)
    _build_in_process(monkeypatch)
    with pytest.raises(IngestRefusal, match="cannot be mapped to OCC surfaces.*indistinguishable"):
        _ingest(bundle, tmp_path)
