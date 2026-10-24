"""First-solve CAD sizing, independent of source inventory and saved setups."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path

import pytest

from server.cadlink.joblane.default_setup import default_setup
from server.cadlink.joblane.preparation_inputs import SnapshotContext, _load_setup
from server.cadlink.joblane.project_setup import inventory_sha256
from server.jobs.imported_setup import setup_content

from cad_backends import JobsHarness
from test_cad_preparation import _accept, _manifest, _revision
from test_cad_project_setup import _project, _project_return, _put_setup


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


def test_fem_region_only_defaults_intentionally_use_25_mm_walls() -> None:
    # No source exists to suggest a size: FEM-only walls still use the shared default.
    manifest = {"sources": [], "scope": {"fem_regions": [{
        "region_id": "air", "surface_groups": [{"kind": "moving", "drive_channel_id": "drive-hf"}],
    }]}}
    assert default_setup(manifest).geometry["mesh"] == {
        "rigid_size_mm": 25, "transition_mm": 100, "source_size_mm": {},
    }


@pytest.fixture
def harness(tmp_path):
    instance = JobsHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _legacy_automatic(manifest):
    setup = json.loads(setup_content(default_setup(manifest, {"engine": "metal", "accuracy": "accurate"})))
    setup["geometry"]["mesh"] = {
        "rigid_size_mm": 4, "transition_mm": 4,
        "source_size_mm": {source["id"]: 4 for source in manifest["sources"]},
    }
    return setup


def _existing_setup(harness, *, automatic=True, role="HF", suggestion=4):
    design, lineage = _project(harness, 45)
    bundle, digest = _project_return(harness, "existing", design, lineage)
    manifest = json.loads((harness.workspace / bundle / "wgreturn.json").read_text())
    manifest["sources"][0].update(role=role, suggested_resolution_mm=suggestion)
    body = json.dumps(manifest).encode()
    (harness.workspace / bundle / "wgreturn.json").write_bytes(body)
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    setup = _legacy_automatic(manifest)
    if not automatic:
        setup.pop("origin")
    revision = _revision(harness.store, setup)
    inventory = inventory_sha256(manifest["sources"])
    harness.store.record_project_setup_if_absent(lineage, inventory, revision)
    _accept(harness.store, "cmd-1", bundle, digest)
    return lineage, inventory, revision, setup


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize(("role", "suggestion", "expected"), [("HF", 2, 2), ("HF", 4, 6), ("LF", 30, 25)])
def test_next_preparation_revises_only_the_automatic_mesh(harness, caplog, named, role, suggestion, expected) -> None:
    lineage, inventory, old, setup = _existing_setup(harness, role=role, suggestion=suggestion)
    before = harness.store.get_setup_revision(old)
    if named:
        from server.jobs.model_solve import _record_for_model
        _record_for_model(harness.store, lineage, {"sources": [{**_manifest(b"STEP")["sources"][0], "role": role}]}, old)
    with caplog.at_level(logging.INFO):
        summary = harness.prepare(**({"setup_revision_id": old} if named else {}))
    assert summary["state"] == "accepted", summary
    new = summary["setupRevisionId"]
    assert new != old
    updated = json.loads(harness.store.get_setup_revision(new)["setup_json"])
    expected_mesh = {"rigid_size_mm": 25, "transition_mm": 100, "source_size_mm": {"source-hf": expected}}
    assert updated == {**setup, "geometry": {**setup["geometry"], "mesh": expected_mesh}}
    assert harness.submitted[-1].geometry.mesh.model_dump() == expected_mesh
    assert harness.bound_setup_revision() == new
    assert harness.store.get_setup_revision(old) == before
    assert harness.store.get_model_setup(lineage, inventory)["revision_id"] == new
    assert "Recomputed automatic CAD mesh sizes" in caplog.text
    assert old in caplog.text and new in caplog.text

    # Another preparation reuses the refreshed revision, without history churn.
    retained = {"retained_path": harness.ingest.calls[-1]["bundle_path"]}
    ctx = SnapshotContext(harness.store, harness.data_dir, harness.workspace)
    assert _load_setup(ctx, None, retained)[1] == new
    assert caplog.text.count("Recomputed automatic CAD mesh sizes") == 1


@pytest.mark.parametrize("retained_origin", [False, True])
def test_a_person_saved_setup_with_identical_sizes_is_untouched(harness, retained_origin) -> None:
    lineage, inventory, revision, setup = _existing_setup(harness, automatic=retained_origin)
    # An explicit save freezes even unchanged settings copied from an automatic setup.
    status, saved = _put_setup(harness, lineage, setup, [{"id": "source-hf", "role": "HF", "required": True}])
    assert status == 200 and saved["revision_id"] == revision
    before = harness.store.get_setup_revision(revision)
    summary = harness.prepare()
    assert summary["state"] == "accepted"
    assert summary["setupRevisionId"] == revision
    assert harness.submitted[-1].geometry.mesh.model_dump() == setup["geometry"]["mesh"]
    assert harness.store.get_setup_revision(revision) == before
    assert harness.store.get_model_setup(lineage, inventory)["revision_id"] == revision


def test_a_later_user_revision_protects_the_old_automatic_setup(harness) -> None:
    lineage, inventory, old, setup = _existing_setup(harness)
    before = harness.store.get_setup_revision(old)
    edited = {**setup, "options": {**setup["options"], "engine": "beat-cpu"}}
    edited.pop("origin")  # The editor's publisher omits origin on every user save.
    status, saved = _put_setup(harness, lineage, edited, [{"id": "source-hf", "role": "HF", "required": True}])
    assert status == 200, saved

    # An explicit old revision also goes through the loader, but is not refreshed.
    from server.cadlink.intake.ingest import retain_snapshot
    bundle = harness.workspace / "wgreturn/existing.wgreturn"
    retained = retain_snapshot(bundle, harness.data_dir)
    ctx = SnapshotContext(harness.store, harness.data_dir, harness.workspace)
    assert _load_setup(ctx, old, retained)[1] == old
    summary = harness.prepare()
    assert summary["state"] == "accepted"
    assert summary["setupRevisionId"] == saved["revision_id"]
    assert harness.submitted[-1].geometry.mesh.model_dump() == setup["geometry"]["mesh"]
    assert harness.store.get_setup_revision(old) == before
    assert harness.store.get_model_setup(lineage, inventory)["revision_id"] == saved["revision_id"]


@pytest.mark.parametrize("identical", [False, True])
def test_a_user_save_during_refresh_wins_without_overwriting_it(harness, monkeypatch, identical) -> None:
    lineage, inventory, old, setup = _existing_setup(harness)
    edited = {**setup, "origin": None, "label": "Saved choice"}
    user_revision = old if identical else _revision(harness.store, edited)
    replace = harness.store.replace_model_setup_if_current

    def save_then_replace(*args):
        harness.store.record_model_setup(lineage, inventory, user_revision)
        return replace(*args)

    monkeypatch.setattr(harness.store, "replace_model_setup_if_current", save_then_replace)
    summary = harness.prepare()
    assert summary["state"] == "accepted"
    assert summary["setupRevisionId"] == user_revision
    assert harness.store.get_model_setup(lineage, inventory)["revision_id"] == user_revision
    assert harness.submitted[-1].geometry.mesh.model_dump() == setup["geometry"]["mesh"]


def test_saved_run_reopens_with_its_original_automatic_sizes_after_refresh(harness) -> None:
    from server.jobs.imported_setup import solve_request_for, validate_setup
    from server.jobs.run_workspace import read_run_workspace
    from test_runs_workspace import _job

    _lineage, _inventory, old, setup = _existing_setup(harness)
    # A completed run of the previous automatic revision owns its exact request.
    record = harness.ingest(
        harness.workspace / "wgreturn/existing.wgreturn", setup["geometry"]["mesh"], [],
        harness.store, harness.data_dir, prep_options={}, commit_guard=lambda _conn: True,
    )
    request = solve_request_for(
        validate_setup(setup), ingest_id=record["ingest_id"],
        manifest_sha256=record["manifest_sha256"], artifact_sha256=record["artifact_sha256"],
        acknowledged_findings=[],
    ).model_dump(mode="json")
    _job(harness.jobs_store, "saved-run", request, {"cad": {"setup": {"revision_id": old}}})
    before = harness.jobs_store.get_job_row("saved-run")
    assert harness.prepare()["setupRevisionId"] != old
    opened = read_run_workspace(harness.jobs_store.get_job_row("saved-run"), harness.store)
    assert opened.setup.revision_id == old and opened.setup.exact
    assert opened.refusal is None
    assert opened.prepared["ingest_id"] == record["ingest_id"]
    assert opened.setup.content["geometry"]["mesh"] == setup["geometry"]["mesh"]
    assert harness.jobs_store.get_job_row("saved-run") == before


def test_selecting_an_old_automatic_run_after_a_user_revision_stays_user_owned(harness) -> None:
    from server.jobs.model_solve import _record_for_model

    lineage, inventory, old, setup = _existing_setup(harness)
    edited = {**setup, "origin": None, "label": "User settings"}
    user_revision = _revision(harness.store, edited)
    harness.store.record_model_setup(lineage, inventory, user_revision)
    # The production path for solving a restored run selects its old revision.
    _record_for_model(harness.store, lineage, {"sources": _manifest(b"STEP")["sources"]}, old)
    assert harness.store.get_model_setup(lineage, inventory)["revision_id"] == old
    # The provenance survives a process restart as well as a pointer change.
    harness.restart()
    summary = harness.prepare()
    assert summary["state"] == "accepted", summary
    assert summary["setupRevisionId"] == old
    assert harness.submitted[-1].geometry.mesh.model_dump() == setup["geometry"]["mesh"]


def _nowaistbanding_box(tmp_path: Path):
    """A planar 500 x 287 x 275 mm surrogate with a conservatively large HF face."""

    gmsh = pytest.importorskip("gmsh")
    from hornlab_mesher.step_import import advanced_face_order_for_surfaces, gmsh_surface_tags
    from server.mesh.gmsh_worker import _run_in_gmsh_session

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
    return step_path, {
        "sources": [source], "instances": [],
        "coordinate_system": {"length_unit": "mm"},
        "assembly": {"n_bodies_expected": 1, "bbox_mm": [[-250, -143.5, 0], [250, 143.5, 275]]},
        "scope": {"included": [], "fem_air_volumes": []},
    }


def test_nowaistbanding_sized_step_mesh_is_well_below_solver_ceiling(tmp_path: Path, record_property) -> None:
    """The whole front is a source, larger than the real throat; not the curved original."""
    from server.mesh.gmsh_worker import _run_in_gmsh_session
    from server.mesh.imported import build_imported_mesh

    step_path, manifest = _nowaistbanding_box(tmp_path)
    area = manifest["sources"][0]["observed"]["total_area_mm2"]
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


@pytest.mark.parametrize("fine_walls", [False, True])
def test_existing_automatic_nowaistbanding_setup_prepares_and_solves_below_ceiling(
    harness, tmp_path, record_property, fine_walls,
) -> None:
    """Real STEP child, ingest and preparation; the harness queues the resulting solve."""
    from server.cadlink.intake.ingest import ingest_bundle

    step_path, shape = _nowaistbanding_box(tmp_path)
    step = step_path.read_bytes()
    manifest = _manifest(step)
    manifest.update(shape)
    manifest["coordinate_system"].update({
        "handedness": "right", "matrix_convention": "row-major-local-to-parent",
        "solver_anchor_instance_id": None,
    })
    manifest["assembly"]["file"] = "assembly.step"
    manifest["scope"].update({
        "selection": "root", "skipped": [], "status": "clean",
        "included": [{"object_id": "box", "name": "box", "body_kind": "solid",
                      "visible": True, "external_reference": "local", "wglink_instance_id": None}],
    })
    manifest["sources"][0]["default_drive_channel_id"] = "drive-hf"
    manifest["document"] = {"name": "NoWaistbanding-shaped", "native_id": "urn:mesh-defaults-box"}
    lineage = harness.store.claim_cad_document_lineage("urn:mesh-defaults-box", "NoWaistbanding-shaped")
    setup = _legacy_automatic(manifest)
    if fine_walls:
        setup.pop("origin")  # A person deliberately saved the finer sizes.
    old = _revision(harness.store, setup)
    harness.store.record_project_setup_if_absent(lineage, inventory_sha256(manifest["sources"]), old)
    bundle = harness.workspace / "wgreturn/box.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    body = json.dumps(manifest).encode()
    (bundle / "wgreturn.json").write_bytes(body)
    _accept(harness.store, "cmd-1", "wgreturn/box.wgreturn", "sha256:" + hashlib.sha256(body).hexdigest())

    summary = harness.prepare(ingest=ingest_bundle)
    if fine_walls:
        assert summary["reason"] == "mesh_too_large", summary
        message = summary["message"]
        assert message.startswith("This model meshes to "), message
        assert "WG's limit is 22,000" in message
        assert "Waveguide Generator ▸ Sources & Mesh…" in message
        assert "raise Walls (now 4 mm; default 25 mm)" in message
        assert "Coarse surface group. Check the model size, or choose Solve anyway." in message
        assert "Preparing the mesh failed" not in message
        assert summary.get("preparationId") is None
        assert not harness.submitted
        # The real isolated mesh builder accepts the explicit choice, with the
        # same fine settings. Job-level consent and binding are tested in test_cad_held.
        record = ingest_bundle(bundle, setup["geometry"]["mesh"], [], harness.store, harness.data_dir,
                               prep_options={"mesh_limit_override": True})
        assert record["mesh"]["stats"]["triangle_count"] > 22_000
        return
    assert summary["state"] == "accepted", summary
    assert summary["setupRevisionId"] != old
    assert harness.submitted[-1].geometry.mesh.model_dump() == {
        "rigid_size_mm": 25, "transition_mm": 100, "source_size_mm": {"HF": 6},
    }
    record = json.loads(harness.store.get_ingest(summary["preparationId"])["record_json"])
    stats = record["mesh"]["stats"]
    record_property("prepared_triangle_count", stats["triangle_count"])
    assert 0 < stats["triangle_count"] < 22000
    assert harness.bound_request()["geometry"]["ingest_id"] == summary["preparationId"]
    assert json.loads(harness.store.get_setup_revision(old)["setup_json"]) == setup


def test_mesh_too_large_is_an_actionable_refusal_on_both_ingest_routes() -> None:
    from server.cadlink.api import _ingest_error
    from server.cadlink.onshape.api import _onshape_error
    from server.mesh.errors import MeshTooLarge

    error = MeshTooLarge(28_944, 22_000, mesh_sizes={
        "rigid_size_mm": 4, "transition_mm": 100, "source_size_mm": {"HF": 2},
    })
    for formatter in (_ingest_error, _onshape_error):
        response = formatter(error)
        assert response.status_code == 422
        assert response.detail == str(error)
