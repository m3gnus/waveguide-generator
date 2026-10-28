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
from server.mesh.builder import DENSE_SOLVER_MEMORY_LIMIT_ENV, DENSE_SOLVER_MEMORY_LIMIT_UNSAFE_ENV

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
    harness, tmp_path, record_property, fine_walls, monkeypatch,
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

    if fine_walls:
        # Exercise the triangle choice independently of this host's RAM. The
        # pre-mesh memory guard can refuse the first preparation too, so both
        # child builds need this budget. Memory safety is covered separately in
        # test_mesh_builder and test_cad_child_memory_override.
        monkeypatch.setenv(DENSE_SOLVER_MEMORY_LIMIT_ENV, str(32 * 1024**3))
        monkeypatch.setenv(DENSE_SOLVER_MEMORY_LIMIT_UNSAFE_ENV, "1")
    summary = harness.prepare(ingest=ingest_bundle)
    if fine_walls:
        assert summary["reason"] == "mesh_too_large", summary
        # The isolated mesher's exact count and solved domain reach status,
        # even though the ceiling stopped ingestion before publication.
        from server.cadlink.joblane.request_status import project_request_status
        from server.jobs.cad_preparation import job_cad_state

        job = harness.jobs_store.latest_cad_job("cmd-1")
        status, _ = project_request_status(
            {"commandId": "cmd-1", "outcome": "accepted", "kind": "prepare_and_solve"},
            None, job, job_cad_state(job),
        )
        assert status["state"] == "needsYou"
        assert status["result"]["mesh"]["trianglesSolved"] > 22000
        assert status["result"]["mesh"]["domainKind"] == "quarter"
        assert status["result"]["mesh"]["solvedFraction"] == .25
        message = summary["message"]
        assert message.startswith("This model meshes to "), message
        assert "WG's limit is 22,000" in message
        assert "Waveguide Generator ▸ Sources & Mesh…" in message
        assert "raise Walls (now 4 mm; default 25 mm)" in message
        assert "add a coarser size zone. Check the model size, or choose Solve anyway." in message
        assert "surface group" not in message
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
    response = _onshape_error(error)
    assert response.status_code == 422 and response.detail == str(error)
    # The browser Prepare path answers with the choice: Solve anyway or a coarser mesh.
    response = _ingest_error(error)
    assert response.status_code == 422
    assert response.detail == {"code": "mesh_too_large", "message": error.message(),
                               "next_step": "In Fusion, open Sources & Mesh… and raise Walls or add a coarser "
                               "size zone, or check the model size.",
                               "triangle_count": 28_944, "limit": 22_000}


def test_browser_prepare_tells_wgs_own_failure_calmly_and_records_the_raw_text(monkeypatch) -> None:
    import errno

    import server.cadlink.api as api
    from server.cadlink.intake.ingest import IngestRefusal
    from server.cadlink.intake.isolation import ChildRefusal
    from server.cadlink.wgreturn import WgReturnChanged, WgReturnUnsupported, WgReturnValidationError

    recorded = []
    monkeypatch.setattr(api, "record_preparation_diagnostic", lambda *args, **kwargs: recorded.append((args, kwargs)))

    def stage1(cause: BaseException) -> IngestRefusal:
        try:
            raise IngestRefusal("stage 1 bundle validation", str(cause)) from cause
        except IngestRefusal as refusal:
            return refusal

    raw = "$.sources[wgs-1].suggested_resolution_mm: must equal mesh.source_size_mm"
    # A return whose manifest fails validation (the Fine2 case).
    response = api._ingest_error(stage1(WgReturnValidationError(raw)), document_name="Fine horn")
    assert response.status_code == 422
    assert response.detail == {
        "code": "snapshot_invalid", "failure": "internal", "retry": False,
        "message": "Waveguide Generator could not prepare Fine horn. This has been recorded for the developers.",
        "diagnostic": f"stage 1 bundle validation: {raw}"}
    (stage, evidence), options = recorded[0]
    assert stage == "internal-failure" and evidence == {"code": "snapshot_invalid", "detail": response.detail["diagnostic"]}
    assert options["phase"] == "cad-ingest" and options["names"] == ["Fine horn"]
    # WG's own pipeline: a worker, its meshing stage, its normalisation, its own code.
    for internal in (ChildRefusal("stage 7 meshing", "worker exited 9"), RuntimeError("mesher"), ImportError("old mesher"),
                     IngestRefusal("stage 7 meshing", "normalisation mismatch: exterior replay recipe is missing."),
                     IngestRefusal("stage 7 meshing", "deferred viewport inputs are unreadable: x"),
                     ValueError("bad array"), TypeError("None")):
        detail = api._ingest_error(internal, document_name="Fine horn").detail
        assert (detail["code"], detail["failure"], detail["retry"]) == ("preparation_failed", "internal", True), internal
        assert "recorded for the developers" in detail["message"]
    # What the user can act on is told in its own words, and never recorded.
    recorded.clear()
    for cause, code, words in (
        (FileNotFoundError("gone"), "return_unavailable", "cannot read the copy"),
        (OSError(errno.EIO, "evicted"), "return_unavailable", "cannot read the copy"),
        (WgReturnUnsupported("unsupported wgreturn_version '2.0'"), "return_unsupported", "Update Waveguide Generator"),
        (OSError(errno.ENOSPC, "No space left on device"), "disk_full", "The disk is full"),
        (WgReturnChanged("sent again"), "snapshot_changed", "changed after"),
    ):
        detail = api._ingest_error(stage1(cause), document_name="Fine horn").detail
        assert (detail["code"], detail["failure"]) == (code, "user_action"), cause
        assert words in f"{detail['message']} {detail['next_step']}", detail
        assert "recorded" not in detail["message"] and "diagnostic" not in detail
    assert api._ingest_error(OSError(errno.ENOSPC, "full"), document_name="Fine horn").detail["code"] == "disk_full"
    # WG's own bug while reading a return is WG's failure, not an invalid return.
    assert api._ingest_error(stage1(KeyError("files"))).detail["code"] == "preparation_failed"
    recorded.clear()
    # Modelling mistakes the user fixes keep their words and are not recorded.
    for modelling in (
        IngestRefusal("stage 2 project gate", "the selected CAD return belongs to another CAD-linked project"),
        IngestRefusal("stage 5 role resolution", "role resolution: required source 'HF' is missing"),
        IngestRefusal("stage 5 role resolution", "role resolution: source 'HF' has no geometric throat candidate"),
        IngestRefusal("stage 7 meshing", "This return declares FEM regions; this Waveguide Generator has FEM "
                      "regions turned off.", user_worded=True),
        IngestRefusal("stage 7 meshing", "Horn: FEM symmetry supports only x0 and y0.", user_worded=True),
    ):
        assert api._ingest_error(modelling).detail == str(modelling), modelling
    drift = IngestRefusal("stage 5 sources", "source 'HF' area drift", area_drift_sources=["HF"])
    assert api._ingest_error(drift).detail["area_drift_sources"] == ["HF"]
    assert recorded == []


def test_a_returns_name_is_read_leniently_once_for_both_readers(tmp_path) -> None:
    from server.cadlink.intake.returns_folder import return_document_name
    from server.cadlink.joblane.request_status import bundle_document_name
    from server.cadlink.limits import MAX_WGRETURN_JSON_BYTES

    folder = tmp_path / "wgreturn" / "Fine2.wgreturn"
    folder.mkdir(parents=True)
    assert return_document_name(folder) == "Fine2"
    manifest = folder / "wgreturn.json"
    manifest.write_text(json.dumps({"document": {"name": " Fine horn "}, "sources": 3}))
    assert return_document_name(folder) == "Fine horn"
    operation = {"inputs_json": json.dumps({"bundle_path": "wgreturn/Fine2.wgreturn"})}
    assert bundle_document_name(tmp_path, operation) == "Fine horn"
    # A manifest larger than any valid one is not parsed for a name.
    manifest.write_text(json.dumps({"document": {"name": "Fine horn"}, "pad": "x" * MAX_WGRETURN_JSON_BYTES}))
    assert return_document_name(folder) == "Fine2"
    assert bundle_document_name(tmp_path, operation) == "Fine2"


def test_a_return_for_a_newer_reader_is_unsupported_not_invalid() -> None:
    from server.cadlink.wgreturn import WgReturnUnsupported, WgReturnValidationError, validate_manifest

    with pytest.raises(WgReturnUnsupported):
        validate_manifest({"wgreturn_version": "2.0", "required_features": []})
    with pytest.raises(WgReturnUnsupported):
        validate_manifest({"wgreturn_version": "1.1", "required_features": ["teleport-v9"]})
    with pytest.raises(WgReturnValidationError) as invalid:
        validate_manifest({"wgreturn_version": "1.1", "required_features": []})
    assert not isinstance(invalid.value, WgReturnUnsupported)


def test_browser_prepare_answers_the_ceiling_with_a_choice_and_carries_solve_anyway(monkeypatch, tmp_path) -> None:
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from server.cadlink import api
    from server.mesh.errors import MeshTooLarge, SolverMemoryTooLarge

    seen: list[dict] = []

    def ingest(*_args, prep_options, **_kwargs):
        seen.append(dict(prep_options))
        if prep_options.get("mesh_limit_override"):
            raise SolverMemoryTooLarge("Solver mesh uses 1 P1 vertex/DOFs ... safety ceiling")
        raise MeshTooLarge(28_944, 22_000)

    monkeypatch.setattr(api, "ingest_bundle", ingest)
    monkeypatch.setattr(api, "_resolve_ingest_bundle", lambda *_: tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=None, data_dir=tmp_path)))
    body = {"bundlePath": "wgreturn/x.wgreturn", "mesh": {"rigidSizeMm": 4, "transitionMm": 100, "sourceSizeMm": {}}}
    with pytest.raises(HTTPException) as refused:
        asyncio.run(api.post_ingest(api.CadReturnIngestRequest.model_validate(body), request))
    assert refused.value.status_code == 422
    assert refused.value.detail["code"] == "mesh_too_large"
    assert refused.value.detail["triangle_count"] == 28_944 and refused.value.detail["limit"] == 22_000
    assert "mesh_limit_override" not in seen[-1]  # the cache key of every older mesh is unchanged
    with pytest.raises(HTTPException) as memory:
        asyncio.run(api.post_ingest(api.CadReturnIngestRequest.model_validate({**body, "meshLimitOverride": True}), request))
    assert seen[-1]["mesh_limit_override"] is True
    assert memory.value.status_code == 422
    assert memory.value.detail == {"code": "solver_memory_exceeded", "message": SolverMemoryTooLarge.USER_MESSAGE,
                                   "next_step": SolverMemoryTooLarge.NEXT_STEP}


def test_fem_region_rules_and_their_budget_are_the_users_to_fix_in_both_lanes() -> None:
    from server.cadlink.intake.ingest import _fem_rule
    from server.cadlink.intake.isolation import ChildRefusal
    from server.cadlink.joblane.failure_codes import MODEL_NEEDS_CHANGE, refusal_code
    from server.cadlink.intake.ingest import IngestRefusal
    from server.mesh.fem_regions import FemRegionMeshError

    assert _fem_rule(FemRegionMeshError("FEM symmetry supports only x0 and y0"))
    assert _fem_rule(ChildRefusal("stage 7 meshing", "the total FEM regions wall-clock budget was exhausted",
                                  details={"budget_exhausted": True}))
    assert not _fem_rule(ChildRefusal("stage 7 meshing", "worker exited 9"))
    # The Fusion lane ends such a refusal as model_needs_change, never as WG's failure.
    assert refusal_code(IngestRefusal("stage 7 meshing", "budget", user_worded=True)) == MODEL_NEEDS_CHANGE
    assert refusal_code(IngestRefusal("stage 7 meshing", "normalisation mismatch")) == "preparation_failed"


@pytest.mark.parametrize("invalid_selector", [False, True])
def test_source_model_rules_and_inconsistent_step_selectors_have_one_classification(monkeypatch, invalid_selector):
    import server.cadlink.api as api
    from server.cadlink.intake.ingest import IngestRefusal
    from server.cadlink.intake.isolated import _reraise_typed
    from server.cadlink.intake.isolation import ChildRefusal
    from server.cadlink.joblane.failure_codes import MODEL_NEEDS_CHANGE, preparation_failure_code, refusal_code
    from server.mesh.imported import RoleResolutionError, _advanced_face_identifier_surfaces

    # Exercise the real source-identifier resolver, then the child's typed
    # reconstruction. Source choices are user actions; a numeric selector
    # absent from the same STEP cannot be repaired by selecting a source.
    if invalid_selector:
        with pytest.raises(RoleResolutionError) as caught:
            _advanced_face_identifier_surfaces([999999], [17], [1])
        source_error = caught.value
    else:
        source_error = RoleResolutionError("role resolution: required source 'HF' is missing")
    with pytest.raises(RoleResolutionError) as transported:
        _reraise_typed(ChildRefusal(
            "stage 7 meshing", str(source_error), error_type="RoleResolutionError",
            details={"invalid_selector": source_error.invalid_selector},
        ))
    try:
        raise IngestRefusal("stage 5 role resolution", str(transported.value)) from transported.value
    except IngestRefusal as refusal:
        error = refusal
    recorded = []
    monkeypatch.setattr(api, "record_preparation_diagnostic", lambda *a, **k: recorded.append((a, k)))
    response = api._ingest_error(error, document_name="Speaker")
    if invalid_selector:
        assert preparation_failure_code(error) == refusal_code(error) == "preparation_failed"
        assert response.detail["failure"] == "internal"
        assert "999999" not in response.detail["message"]
        assert "999999" in response.detail["diagnostic"]
        assert len(recorded) == 1
    else:
        assert preparation_failure_code(error) is None
        assert refusal_code(error) == MODEL_NEEDS_CHANGE
        assert response.detail == str(error)
        assert recorded == []
