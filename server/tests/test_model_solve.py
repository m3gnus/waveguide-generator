"""One way to solve an imported model: ``POST /api/models/{id}/solve`` (model_solve.py)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
import pytest

from server.cadlink.intake.ingest import retain_snapshot
from server.cadlink.joblane.project_setup import project_setup
from server.cadlink.roles import inventory_sha256
from server.cadlink.store import CadLinkStore
from server.jobs.imported_setup import setup_content, setup_digest, validate_setup
from server.jobs.models_api import mount_models_api, register_mesh

from cad_backends import JobsHarness
from test_cad_preparation import _accept, _setup
from test_cad_project_setup import _project, _project_return, _select_engine
from test_jobs_api import _request
from test_models_api import app_for, setup_for
from test_mesh_import import registration, request as file_request


@pytest.fixture
def harness(tmp_path: Path):
    h = JobsHarness(tmp_path)
    yield h
    h.close()


# -- a CAD model, on the production preparation lane -----------------------------------


def _models_app(harness: JobsHarness) -> FastAPI:
    app = FastAPI()
    app.state.cadlink_store = harness.store
    app.state.data_dir = str(harness.data_dir)
    app.state.update_restart = None
    app.state.jobs_runtime = harness.runtime
    app.state.cad_job_shims = True
    mount_models_api(app)
    return app


def _cad_model(
    harness: JobsHarness, *, name: str = "speaker", mesh_snapshot: bool = False, mesh_limit_override: bool = False,
) -> dict[str, Any]:
    """A CAD model as WG has it on screen: a project's return, retained and ingested."""

    design_id, lineage = _project(harness, 90.0)
    bundle_path, manifest = _project_return(harness, name, design_id, lineage)
    if mesh_snapshot:
        from server.cadlink.wglink_protocol import default_mesh
        path = harness.workspace / bundle_path / 'wgreturn.json'
        value = json.loads(path.read_text())
        value['wgreturn_version'] = '1.1'
        value['assembly']['signature_hash'] = 'sha256:' + '5' * 64
        value['required_features'].append('mesh-sizing-v1')
        value['mesh'] = default_mesh(value['sources'])
        value['mesh'].update(wall_size_mm=31, grading_distance_mm=75, symmetry='off')
        path.write_text(json.dumps(value))
        manifest = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
    retained = retain_snapshot(harness.workspace / bundle_path, harness.data_dir)
    sources = json.loads((harness.workspace / bundle_path / "wgreturn.json").read_text())["sources"]

    def record(ingest_id: str, created_at: str) -> str:
        return json.dumps({
            "ingest_id": ingest_id,
            "created_at": created_at,
            "return_id": "wgr_screen",
            "manifest_sha256": retained["manifest_sha256"],
            "artifact_sha256": retained["artifact_sha256"],
            "document": {"name": "Speaker"},
            "project": {"lineage_id": lineage},
            "anchor": {"instance_id": "instance-1", "design_id": design_id, "throat_frame": None},
            "sources": sources,
            # Prepared with Solve anyway over the triangle ceiling, as a press
            # of ``_setup()`` prepares it.
            **({"mesh_limit_override": True, "mesh_sizes": _setup()["geometry"]["mesh"],
                "skipped_source_ids": [],
                "mesh_lookup_key": _anyway_key(harness, retained["manifest_sha256"], _setup())}
               if mesh_limit_override else {}),
        })

    row = harness.store.allocate_ingest(
        manifest_sha256=retained["manifest_sha256"],
        artifact_sha256=retained["artifact_sha256"],
        record_builder=record,
    )
    assert harness.store.get_model(lineage)["producer"] == "cad"
    return {
        "model_id": lineage, "prepared_id": str(row["ingest_id"]), "bundle_path": bundle_path,
        "manifest": manifest, "sources": sources,
    }


def _anyway_key(
    harness: Any, manifest_sha256: str, setup: dict[str, Any], frame_axis: str | None = None,
) -> str:
    """The mesh cache address a Solve anyway preparation of ``setup`` is made under."""

    from server.cadlink.intake.ingest import mesh_preparation, read_snapshot, retained_snapshot_path
    from server.jobs.cad_preparation import preparation_options
    from server.jobs.imported_setup import snapshot_setup

    bundle = read_snapshot(retained_snapshot_path(harness.data_dir, manifest_sha256), retained=True)
    effective = snapshot_setup(validate_setup(setup), bundle.manifest)
    return mesh_preparation(
        harness.store, bundle, effective.geometry["mesh"],
        list(effective.geometry.get("skipped_source_ids") or []),
        preparation_options(effective, frame_axis, mesh_limit_override=True),
    ).lookup_key


def _solve(harness: JobsHarness, app: FastAPI, model: dict[str, Any], **body: Any) -> tuple[int, dict]:
    payload = {"prepared_id": model["prepared_id"], **body}
    status, raw = harness._loop.run(
        _request(app, "POST", f"/api/models/{model['model_id']}/solve", body=payload)
    )
    return status, json.loads(raw)


def _settle(harness: JobsHarness) -> None:
    harness._loop.run(harness.runtime.wait_cad_preparations())


def _job_count(harness: JobsHarness) -> int:
    return harness.jobs_store.list_jobs(limit=500)[1]


def test_a_duplicate_press_while_the_job_is_active_returns_the_same_job(harness) -> None:
    app = _models_app(harness)
    model = _cad_model(harness)

    status, first = _solve(harness, app, model, client_request_id="press-1", setup=_setup(engine="metal"))
    assert status == 200, first
    _settle(harness)
    job = harness.jobs_store.get_job_row(first["job_id"])
    assert job["status"] == "queued"  # bound, and kept in flight (no scheduler here)

    # Another press of the same settings (a lost response, a double click, a
    # second window) is that job, whatever its own id or the run's name.
    status, again = _solve(harness, app, model, client_request_id="press-2",
                           setup=_setup(engine="metal"), label="Another name")
    assert (status, again["job_id"]) == (200, first["job_id"])
    # The exact replay of the first press recovers it too.
    assert _solve(harness, app, model, client_request_id="press-1", setup=_setup(engine="metal"))[1][
        "job_id"] == first["job_id"]
    assert _job_count(harness) == 1

    # Other settings are another solve.
    status, other = _solve(harness, app, model, client_request_id="press-3", setup=_setup(engine="beat-cpu"))
    assert status == 200 and other["job_id"] != first["job_id"]
    _settle(harness)
    assert _job_count(harness) == 2

    # The solve is the model's: its setup is what Fusion's next "Solve in WG" uses.
    saved = harness.store.get_model_setup(model["model_id"], inventory_sha256(model["sources"]))
    assert json.loads(harness.store.get_setup_revision(saved["revision_id"])["setup_json"])[
        "options"]["engine"] == "beat-cpu"
    # It went through the CAD lane: the manual intent and its setup revision.
    bound = harness.jobs_store.get_job_row(first["job_id"])
    assert bound["config_json"]["client_request_id"] == "cad-solve:manual-solve:press-1"
    assert bound["task_metadata"]["cad"]["setup"]["revision_id"] == _revision_of(harness, _setup(engine="metal"))


def test_a_press_of_a_mesh_prepared_with_solve_anyway_carries_the_choice(harness) -> None:
    """The browser path: Solve anyway prepared the mesh, so its Solve is not asked again.

    The press says so (``mesh_limit_override``); the manual intent records it and
    its preparation asks for the same override mesh, whose cache key it is part of.
    """

    from server.jobs.model_solve import ModelSolveRequest, press_digest

    app = _models_app(harness)
    model = _cad_model(harness, mesh_limit_override=True)
    status, body = _solve(harness, app, model, client_request_id="press-1",
                          setup=_setup(engine="metal"), mesh_limit_override=True)
    assert status == 200, body
    _settle(harness)
    job = harness.jobs_store.get_job_row(body["job_id"])
    assert job["status"] == "queued"
    assert job["config_json"]["client_request_id"] == "cad-solve:manual-solve:press-1"
    assert job["task_metadata"]["cad"]["mesh_limit_override"] == {"accepted": True, "source": "solve_anyway"}
    assert harness.ingest.calls[-1]["prep_options"]["mesh_limit_override"] is True
    # A press that did not choose it keeps the digest it had before the field existed.
    from server.contracts.canonical import canonical_utf8_strict_json

    plain = ModelSolveRequest(prepared_id="p", client_request_id="c", setup=_setup(engine="metal"))
    before = plain.model_dump(mode="json", exclude={"client_request_id", "mesh_limit_override"})
    assert press_digest(plain) == "sha256:" + hashlib.sha256(
        canonical_utf8_strict_json(before).encode("utf-8")).hexdigest()
    assert press_digest(plain) != press_digest(plain.model_copy(update={"mesh_limit_override": True}))


def test_solve_anyway_never_applies_to_a_mesh_prepared_without_it(harness) -> None:
    """The choice belongs to the prepared mesh it was made for: a press naming
    it for another preparation is asked about the ceiling again."""

    app = _models_app(harness)
    model = _cad_model(harness)
    status, body = _solve(harness, app, model, client_request_id="press-1",
                          setup=_setup(engine="metal"), mesh_limit_override=True)
    assert status == 200, body
    _settle(harness)
    job = harness.jobs_store.get_job_row(body["job_id"])
    assert "mesh_limit_override" not in job["task_metadata"]["cad"]
    assert "mesh_limit_override" not in harness.ingest.calls[-1]["prep_options"]


def test_solve_anyway_stays_with_the_mesh_sizes_it_was_chosen_for(harness) -> None:
    """A press with other mesh sizes makes another mesh: the ceiling asks again,
    even though the record on screen was prepared with Solve anyway."""

    app = _models_app(harness)
    model = _cad_model(harness, mesh_limit_override=True)
    status, body = _solve(harness, app, model, client_request_id="press-1",
                          setup=_setup(rigid=25.0, engine="metal"), mesh_limit_override=True)
    assert status == 200, body
    _settle(harness)
    job = harness.jobs_store.get_job_row(body["job_id"])
    assert "mesh_limit_override" not in job["task_metadata"]["cad"]
    assert "mesh_limit_override" not in harness.ingest.calls[-1]["prep_options"]


def _revision_of(harness: JobsHarness, setup: dict[str, Any]) -> str:
    parsed = validate_setup(setup)
    return str(harness.store.create_setup_revision(setup_content(parsed), setup_digest(parsed))["revision_id"])


@pytest.mark.parametrize("mesh_snapshot", [False, True])
@pytest.mark.parametrize("fusion_state", ["preparing", "bound"])
def test_a_wg_press_and_a_fusion_solve_in_wg_for_the_same_setup_are_one_job(harness, fusion_state, mesh_snapshot) -> None:
    app = _models_app(harness)
    model = _cad_model(harness, mesh_snapshot=mesh_snapshot)
    setup = _setup(engine="metal")
    revision = _revision_of(harness, setup)
    harness.store.record_model_setup(model["model_id"], inventory_sha256(model["sources"]), revision)
    _accept(harness.store, "cmd-1", model["bundle_path"], model["manifest"])
    if fusion_state == "preparing":
        harness.runtime._ensure_prep_lane = lambda: None  # still waiting for the lane
        harness._loop.run(harness.runtime.accept_cad_solve(harness._intent("cmd-1"), "cad-solve:cmd-1"))
    else:
        harness.prepare("cmd-1")
    fusion = harness.jobs_store.latest_cad_job("cmd-1")
    assert fusion["status"] == ("preparing" if fusion_state == "preparing" else "queued")

    status, pressed = _solve(harness, app, model, client_request_id="press-1", setup=setup)

    assert (status, pressed["job_id"]) == (200, fusion["id"])
    assert _job_count(harness) == 1
    if fusion_state == "bound":
        # The Fusion lane prepared with the model's own setup, which the WG press named.
        assert (harness.bound_setup_revision("cmd-1") == revision) is (not mesh_snapshot)


def test_frame_refusals_and_confirmation(harness) -> None:
    from test_cad_solver_frame_api import _record as frame_record

    app = _models_app(harness)
    record = {**frame_record(), "sources": [{"id": "source-hf", "role": "HF", "required": True}]}
    row = harness.store.allocate_ingest(
        manifest_sha256=record["manifest_sha256"],
        artifact_sha256=record["artifact_sha256"],
        record_builder=lambda ingest_id, created_at: json.dumps(
            {**record, "ingest_id": ingest_id, "created_at": created_at}
        ),
    )
    model = {"model_id": "wgl_authored", "prepared_id": str(row["ingest_id"])}

    # Authored in CAD, no axis confirmed and none chosen: never a frame
    # question. WG decides in the preparation lane; this record's solve waits
    # only for its retained return, which it has none of.
    status, refused = _solve(harness, app, model, client_request_id="press-1", setup=_setup())
    assert status == 409 and refused["error"]["code"] == "prepared_unavailable"
    # An axis this half model cannot be solved in is refused, and nothing is confirmed.
    status, refused = _solve(harness, app, model, client_request_id="press-2", setup=_setup(),
                             frame_axis="+q")
    assert status == 422
    assert _job_count(harness) == 0

    # An axis the user chose is confirmed for the model's project as part of the press.
    from server.cadlink.intake.readings import frame_preview
    from server.cadlink.interpretation.solver_frame import record_confirmation_key

    assert harness.store.get_frame_confirmation(record_confirmation_key(record)) is None
    status, accepted = _solve(harness, app, model, client_request_id="press-3", setup=_setup(),
                              frame_axis="-y")
    # The solve itself waits for the retained return, which this record has none of.
    assert status == 409 and accepted["error"]["code"] == "prepared_unavailable"
    assert frame_preview(harness.store, record)["confirmed"]["axis"] == "-y"


@pytest.mark.parametrize("mesh_snapshot", [False, True])
def test_findings_refuse_and_an_approved_press_resumes_the_same_preparation(harness, mesh_snapshot) -> None:
    app = _models_app(harness)
    model = _cad_model(harness, mesh_snapshot=mesh_snapshot)
    harness.ingest.findings = [{"id": "review-1", "blocking": True}]

    status, first = _solve(harness, app, model, client_request_id="press-1", setup=_setup(engine="metal"))
    assert status == 200
    _settle(harness)
    refused = harness.jobs_store.get_job_row(first["job_id"])
    assert refused["task_metadata"]["cad"]["refusal"]["code"] == "findings_need_review"
    preparation = refused["task_metadata"]["cad"]["preparation"]["preparation_id"]

    # The next press, with the findings reviewed, continues that refused solve
    # and resumes its preparation: nothing is meshed again.
    approvals = {"preparation_id": preparation, "finding_ids": ["review-1"]}
    status, second = _solve(harness, app, model, client_request_id="press-2",
                            setup=_setup(engine="metal"), approvals=approvals)
    assert status == 200 and second["job_id"] != first["job_id"]
    _settle(harness)
    child = harness.jobs_store.get_job_row(second["job_id"])
    assert child["parent_job_id"] == first["job_id"] or child["config_json"].get("parent_job_id") == first["job_id"]
    assert child["status"] == "queued"
    assert len(harness.ingest.calls) == 1
    # A replay of that press is the same child, not another continuation.
    assert _solve(harness, app, model, client_request_id="press-2", setup=_setup(engine="metal"),
                  approvals=approvals)[1]["job_id"] == second["job_id"]


def test_a_changed_engine_prepares_again_and_an_unchanged_one_reuses(harness) -> None:
    app = _models_app(harness)
    model = _cad_model(harness)
    harness.ingest.findings = [{"id": "review-1", "blocking": True}]

    first = _solve(harness, app, model, client_request_id="press-1", setup=_setup(engine="metal"))[1]
    _settle(harness)
    assert len(harness.ingest.calls) == 1
    # Another engine is a new setup revision, so a new preparation.
    second = _solve(harness, app, model, client_request_id="press-2", setup=_setup(engine="beat-cpu"))[1]
    _settle(harness)
    assert len(harness.ingest.calls) == 2
    changed = harness.jobs_store.get_job_row(second["job_id"])
    assert changed["task_metadata"]["cad"]["refusal"]["code"] == "findings_need_review"
    assert changed["task_metadata"]["cad"]["setup"]["revision_id"] == _revision_of(harness, _setup(engine="beat-cpu"))
    # The same settings again, under another run name, resume that preparation.
    third = _solve(harness, app, model, client_request_id="press-3",
                   setup={**_setup(engine="beat-cpu"), "label": "Run 4"})[1]
    _settle(harness)
    assert len(harness.ingest.calls) == 2
    assert harness.jobs_store.get_job_row(third["job_id"])["task_metadata"]["cad"]["preparation"][
        "preparation_id"] == changed["task_metadata"]["cad"]["preparation"]["preparation_id"]
    assert first["job_id"] != second["job_id"] != third["job_id"]


def test_refusals_of_the_press_itself(harness) -> None:
    app = _models_app(harness)
    model = _cad_model(harness)
    other = _cad_model(harness, name="other")

    status, body = _solve(harness, app, {**model, "prepared_id": "wgi_gone"}, client_request_id="p1",
                          setup=_setup())
    assert (status, body["error"]["code"]) == (404, "prepared_unavailable")
    status, body = _solve(harness, app, {**model, "prepared_id": other["prepared_id"]},
                          client_request_id="p2", setup=_setup())
    assert (status, body["error"]["code"]) == (409, "model_mismatch")
    status, body = _solve(harness, app, model, client_request_id="p3", setup_revision_id="wgs_gone")
    assert (status, body["error"]["code"]) == (409, "setup_required")
    harness.blocked = "Restart approved"
    status, body = _solve(harness, app, model, client_request_id="p4", setup=_setup())
    assert (status, body["error"]["code"]) == (409, "update_restart_pending")
    harness.blocked = None
    assert _job_count(harness) == 0
    # The same Solve id for another model's solve conflicts.
    assert _solve(harness, app, model, client_request_id="p5", setup=_setup())[0] == 200
    status, body = _solve(harness, app, {**other, "model_id": model["model_id"]}, client_request_id="p5",
                          setup=_setup())
    assert status == 409


def test_a_retirement_hold_and_an_update_restart_refuse_a_new_cad_solve(harness) -> None:
    from server.updates.restart import RestartApproval

    app = _models_app(harness)
    model = _cad_model(harness)
    approval = RestartApproval()
    harness.runtime.restart_approval = approval
    approval.retirement_hold.hold("test")
    status, body = _solve(harness, app, model, client_request_id="manual-1", setup=_setup())
    assert status == 409
    error = body["error"]
    assert (error["code"], error["retryable"], error["stage"], error["message"]) == (
        "update_restart_pending", True, "submission", approval.retirement_hold.refusal(),
    )
    assert harness.store.list_operations() == []
    approval.approve("v1")
    assert _solve(harness, app, model, client_request_id="manual-1", setup=_setup())[1]["error"][
        "message"] == approval.refusal()
    approval.release("test")
    assert approval.retirement_hold.held
    approval.retirement_hold.release()
    status, body = _solve(harness, app, model, client_request_id="manual-1", setup=_setup())
    assert status == 200
    assert harness.store.get_operation("manual-solve:manual-1") is not None


def test_d2_the_solver_selection_no_longer_overrides_a_saved_setup(harness) -> None:
    model = _cad_model(harness)
    revision = _revision_of(harness, _setup(engine="metal"))
    harness.store.record_model_setup(model["model_id"], inventory_sha256(model["sources"]), revision)
    _select_engine(harness, "beat-cpu", "accurate")

    setup, used = project_setup(harness.store, model["model_id"], model["sources"])
    assert used == revision
    assert setup.options["engine"] == "metal" and "accuracy" not in setup.options

    # A Fusion "Solve in WG" is prepared with the model's own setup.
    _accept(harness.store, "cmd-1", model["bundle_path"], model["manifest"])
    summary = harness.prepare("cmd-1")
    assert summary["state"] == "accepted"
    assert harness.submitted[-1].options.engine == "metal"
    assert harness.bound_setup_revision("cmd-1") == revision


# -- a file model, on the real jobs runtime --------------------------------------------


def _file_scenario(tmp_path: Path, scenario) -> None:
    async def run() -> None:
        store = CadLinkStore.for_data_dir(tmp_path)
        app, runtime = app_for(tmp_path, store)
        runtime._ensure_scheduler = lambda: None  # queued jobs stay in flight
        try:
            await scenario(store, app, runtime)
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(run())


async def _file_solve(app, model_id, body) -> tuple[int, dict]:
    status, raw = await _request(app, "POST", f"/api/models/{model_id}/solve", body=body)
    return status, json.loads(raw)


def test_a_file_solve_records_its_setup_revision_and_a_duplicate_is_the_same_job(tmp_path) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        model_id = "mesh-file:" + record["artifact_sha256"]
        setup = setup_for(record)
        body = {"prepared_id": record["ingest_id"], "client_request_id": "mesh-1", "setup": setup,
                "label": "tetra 1"}
        status, first = await _file_solve(app, model_id, body)
        assert status == 200, first
        job = runtime.store.get_job_row(first["job_id"])
        revision = setup_digest(validate_setup(setup))
        recorded = job["task_metadata"]["imported_geometry"]["setup_revision_id"]
        assert store.get_setup_revision(recorded)["content_sha256"] == revision
        assert job["config_json"]["client_request_id"] == "model-solve:mesh-1"
        assert job["label"] == "tetra 1"
        assert (await runtime.get_job(first["job_id"]))["cad_source"]["setup_revision_id"] == recorded

        # In flight: another press of the same setup, by revision or content, is that job.
        status, again = await _file_solve(app, model_id, {
            "prepared_id": record["ingest_id"], "client_request_id": "mesh-2", "setup_revision_id": recorded,
        })
        assert (status, again["job_id"]) == (200, first["job_id"])
        assert runtime.store.list_jobs()[1] == 1
        # The same id for another request conflicts.
        status, conflict = await _file_solve(app, model_id, {
            **body, "setup": {**setup, "options": {**setup["options"], "num_frequencies": 9}},
        })
        assert (status, conflict["error"]["code"]) == (409, "submission_key_conflict")

        # Once it ended, the same setup is a new run.
        runtime.store.update_job(first["job_id"], status="complete")
        status, next_run = await _file_solve(app, model_id, {**body, "client_request_id": "mesh-3"})
        assert status == 200 and next_run["job_id"] != first["job_id"]

    _file_scenario(tmp_path, scenario)


def test_a_file_solve_with_unreviewed_findings_is_refused(tmp_path) -> None:
    async def scenario(store, app, runtime):
        original = register_mesh(store, tmp_path, registration())
        row = store.allocate_ingest(
            manifest_sha256=original["manifest_sha256"],
            artifact_sha256=original["artifact_sha256"],
            record_builder=lambda ingest_id, created_at: json.dumps({
                **original, "ingest_id": ingest_id, "created_at": created_at,
                "report_sha256": "sha256:review", "findings": [{"id": "review", "blocking": True}],
            }),
        )
        record = json.loads(row["record_json"])
        model_id = "mesh-file:" + record["artifact_sha256"]
        body = {"prepared_id": record["ingest_id"], "client_request_id": "mesh-1", "setup": setup_for(record)}
        status, refused = await _file_solve(app, model_id, body)
        assert (status, refused["error"]["code"]) == (409, "findings_need_review")
        assert runtime.store.list_jobs()[1] == 0
        approvals = {"preparation_id": record["ingest_id"], "finding_ids": ["review"]}
        status, accepted = await _file_solve(app, model_id, {**body, "approvals": approvals})
        assert status == 200, accepted
        job = runtime.store.get_job_row(accepted["job_id"])
        assert job["config_json"]["geometry"]["acknowledged_findings"] == ["sha256:review:review"]

    _file_scenario(tmp_path, scenario)


def test_post_api_solve_and_retry_of_an_imported_run_are_its_models_solve(tmp_path) -> None:
    from server.jobs.api import create_jobs_router

    async def scenario(store, app, runtime):
        app.include_router(create_jobs_router(runtime))
        record = register_mesh(store, tmp_path, registration())
        exact = file_request(record).model_dump(mode="json")
        status, raw = await _request(app, "POST", "/api/solve", body=exact)
        assert status == 200, raw
        first = json.loads(raw)["job_id"]
        job = runtime.store.get_job_row(first)
        revision = job["task_metadata"]["imported_geometry"]["setup_revision_id"]
        assert revision and store.get_setup_revision(revision) is not None
        # The request is kept exactly as sent.
        assert job["config_json"]["geometry"]["drive_channels"] == exact["geometry"]["drive_channels"]
        # The same request while it is in flight is the same job.
        status, raw = await _request(app, "POST", "/api/solve", body=exact)
        assert json.loads(raw)["job_id"] == first

        # A retry solves the run again with the setup it recorded.
        runtime.store.update_job(first, status="complete")
        status, raw = await _request(app, "POST", f"/api/jobs/{first}/retry")
        assert status == 200, raw
        retried = runtime.store.get_job_row(json.loads(raw)["job_id"])
        assert retried["task_metadata"]["imported_geometry"]["setup_revision_id"] == revision
        assert retried["parent_job_id"] == first or retried["config_json"]["parent_job_id"] == first

    _file_scenario(tmp_path, scenario)


# -- review round 1: identity, setup recording, presses ---------------------------------


def _frame_record(axis: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    from test_cad_solver_frame_api import _record as frame_record

    record = {**frame_record(axis=axis), "sources": [{"id": "source-hf", "role": "HF", "required": True}]}
    if plan is not None:
        record["domain_interpretation"] = {"plan": plan}
    return record


def _ingest_record(store: CadLinkStore, record: dict[str, Any]) -> dict[str, Any]:
    row = store.allocate_ingest(
        manifest_sha256=record["manifest_sha256"], artifact_sha256=record["artifact_sha256"],
        record_builder=lambda ingest_id, created_at: json.dumps({**record, "ingest_id": ingest_id, "created_at": created_at}),
    )
    return json.loads(row["record_json"])


def _job(jobs, job_id: str, status: str, config: dict[str, Any], metadata: dict[str, Any]) -> None:
    jobs.create_job({
        "id": job_id, "status": status, "created_at": "2026-10-17T10:00:00", "updated_at": "2026-10-17T10:00:00",
        "queued_at": "2026-10-17T10:00:00", "config_json": config, "config_summary_json": {},
        "task_metadata": metadata,
    })


def test_the_in_flight_check_compares_the_solver_frame_and_the_domain_plan(tmp_path) -> None:
    from types import SimpleNamespace

    from server.jobs.model_solve import SolveTarget, _revision_inputs, active_model_job, record_identity
    from server.jobs.store import JobStore

    store = CadLinkStore(tmp_path / "cadlink.db")
    jobs = JobStore(tmp_path / "jobs.db")
    jobs.initialize()
    runtime = SimpleNamespace(store=jobs)
    revision = _revision_of(SimpleNamespace(store=store), _setup(engine="metal"))
    active_inputs = _revision_inputs(store, revision, {})
    along_x = _ingest_record(store, _frame_record("+x", {"planes": ["x0"]}))
    along_y = _frame_record("-y", {"planes": ["x0"]})
    other_plan = _frame_record("+x", {"planes": []})
    # A bound job meshed along +x, under one domain plan.
    _job(jobs, "bound-x", "queued", {"geometry": {"ingest_id": along_x["ingest_id"],
                                                  "manifest_sha256": along_x["manifest_sha256"]}},
         {"imported_geometry": {"model_id": "wgl_authored", "manifest_sha256": along_x["manifest_sha256"],
                                "setup_revision_id": revision}})

    def target(record):
        frame, domain = record_identity(record)
        return SolveTarget(inputs=active_inputs, frame=frame, domain=domain, compare_domain=True)

    assert active_model_job(runtime, store, "wgl_authored", along_x, target(along_x)) == "bound-x"
    # Another axis the user picked, or another domain reading, is another solve.
    assert active_model_job(runtime, store, "wgl_authored", along_x, target(along_y)) is None
    assert active_model_job(runtime, store, "wgl_authored", along_x, target(other_plan)) is None
    # A request still preparing, held to the axis its press showed.
    jobs.delete_job_with_event("bound-x")
    _job(jobs, "held-x", "preparing", {"type": "cad_intent", "operation_id": "op", "bundle_path": "b",
                                       "manifest_sha256": along_x["manifest_sha256"], "return_id": "r",
                                       "frame_axis": "+x", "setup_revision_id": revision}, {"cad": {}})
    assert active_model_job(runtime, store, "wgl_authored", along_x, target(along_x)) == "held-x"
    assert active_model_job(runtime, store, "wgl_authored", along_x, target(along_y)) is None
    # A Fusion request still preparing on WG's defaults, before its preparation:
    # it takes up the setup a press makes the model's, and only then matches.
    jobs.delete_job_with_event("held-x")
    defaults = _revision_of(SimpleNamespace(store=store), {**_setup(engine="auto"), "origin": "wg_defaults"})
    _job(jobs, "fusion", "preparing", {"type": "cad_intent", "operation_id": "cmd", "bundle_path": "b",
                                       "manifest_sha256": along_x["manifest_sha256"], "return_id": "r"},
         {"cad": {"setup": {"revision_id": defaults}}})
    assert active_model_job(runtime, store, "wgl_authored", along_x, target(along_x)) is None
    adopting = SolveTarget(inputs=active_inputs, frame=record_identity(along_x)[0],
                           domain=record_identity(along_x)[1], compare_domain=True, adopts=True)
    assert active_model_job(runtime, store, "wgl_authored", along_x, adopting) == "fusion"
    store.close()


def test_a_fusion_request_of_a_first_time_model_takes_up_the_wg_press_setup(harness) -> None:
    """P2: Fusion first, on a model with no setup: the press is that request, which prepares with it."""

    app = _models_app(harness)
    model = _cad_model(harness)
    _accept(harness.store, "cmd-1", model["bundle_path"], model["manifest"])
    harness.runtime._ensure_prep_lane = lambda: None  # Fusion's request waits for the lane
    harness._loop.run(harness.runtime.accept_cad_solve(harness._intent("cmd-1"), "cad-solve:cmd-1"))
    fusion = harness.jobs_store.latest_cad_job("cmd-1")
    assert harness.store.get_model_setup(model["model_id"], inventory_sha256(model["sources"])) is None

    status, pressed = _solve(harness, app, model, client_request_id="press-1", setup=_setup(engine="bempp"))

    assert (status, pressed["job_id"]) == (200, fusion["id"])
    assert _job_count(harness) == 1
    revision = _revision_of(harness, _setup(engine="bempp"))
    assert harness.store.get_model_setup(model["model_id"], inventory_sha256(model["sources"]))["revision_id"] == revision
    # Once the lane takes it up, it prepares with the press's settings, not WG's defaults.
    harness.runtime._ensure_prep_lane = type(harness.runtime)._ensure_prep_lane.__get__(harness.runtime)

    async def kick() -> None:
        harness.runtime._ensure_prep_lane()
        await harness.runtime.wait_cad_preparations()

    harness._loop.run(kick())
    assert harness.bound_setup_revision("cmd-1") == revision
    assert harness.submitted[-1].options.engine == "bempp"
    assert _job_count(harness) == 1


def test_a_press_records_its_setup_only_once_admitted(harness) -> None:
    app = _models_app(harness)
    model = _cad_model(harness)
    inventory = inventory_sha256(model["sources"])
    saved = lambda: (harness.store.get_model_setup(model["model_id"], inventory) or {}).get("revision_id")  # noqa: E731

    # Refused before anything is admitted: nothing recorded.
    harness.blocked = "Restart approved"
    assert _solve(harness, app, model, client_request_id="p1", setup=_setup(engine="metal"))[0] == 409
    harness.blocked = None
    assert saved() is None
    # Admitted: its setup is the model's.
    first = _solve(harness, app, model, client_request_id="p2", setup=_setup(engine="metal"))[1]["job_id"]
    _settle(harness)
    metal = _revision_of(harness, _setup(engine="metal"))
    assert saved() == metal
    # The settings change on screen (the publisher records them), then a press
    # of the job's own settings is answered by that job: the model's setup stays.
    other = _revision_of(harness, _setup(engine="beat-cpu"))
    harness.store.record_model_setup(model["model_id"], inventory, other)
    assert _solve(harness, app, model, client_request_id="p3", setup=_setup(engine="metal"))[1]["job_id"] == first
    assert saved() == other
    # A press naming a revision (an unedited restored run) records that revision.
    harness.jobs_store.update_job(first, status="complete")
    status, body = _solve(harness, app, model, client_request_id="p4", setup_revision_id=metal)
    assert status == 200 and body["job_id"] != first
    assert saved() == metal


def test_a_continuation_takes_the_press_label_and_only_its_own_settings(harness) -> None:
    app = _models_app(harness)
    model = _cad_model(harness)
    harness.ingest.findings = [{"id": "review-1", "blocking": True}]
    first = _solve(harness, app, model, client_request_id="p1", setup=_setup(engine="metal"), label="Speaker 1")[1]
    _settle(harness)
    preparation = harness.jobs_store.get_job_row(first["job_id"])["task_metadata"]["cad"]["preparation"]["preparation_id"]
    # Other settings are not that refused solve: a new one, beside it.
    other = _solve(harness, app, model, client_request_id="p2", setup=_setup(engine="beat-cpu"), label="Speaker 2")[1]
    _settle(harness)
    assert harness.jobs_store.get_job_row(other["job_id"])["config_json"].get("parent_job_id") is None
    # The same settings continue it, named as this press names the run.
    approvals = {"preparation_id": preparation, "finding_ids": ["review-1"]}
    child = _solve(harness, app, model, client_request_id="p3", setup=_setup(engine="metal"),
                   label="Speaker 3", approvals=approvals)[1]
    _settle(harness)
    row = harness.jobs_store.get_job_row(child["job_id"])
    assert row["parent_job_id"] == first["job_id"]
    assert row["label"] == "Speaker 3" and row["config_json"]["label"] == "Speaker 3"


def test_the_same_press_id_for_another_cad_press_conflicts(harness) -> None:
    app = _models_app(harness)
    model = _cad_model(harness)
    assert _solve(harness, app, model, client_request_id="p1", setup=_setup(engine="metal"))[0] == 200
    for changed in ({"frame_axis": "+x"}, {"setup": _setup(engine="beat-cpu")},
                    {"approvals": {"preparation_id": "wgi_x", "finding_ids": ["f"]}}):
        status, body = _solve(harness, app, model, client_request_id="p1",
                              **{"setup": _setup(engine="metal"), **changed})
        assert (status, body["error"]["code"]) == (409, "submission_key_conflict"), changed
    assert _job_count(harness) == 1


def test_unfiled_is_reserved(tmp_path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    record = {**_frame_record("+x"), "project": {"lineage_id": "unfiled"}}
    row = store.allocate_ingest(
        manifest_sha256=record["manifest_sha256"], artifact_sha256=record["artifact_sha256"],
        record_builder=lambda ingest_id, created_at: json.dumps({**record, "ingest_id": ingest_id}),
    )
    assert store.get_ingest(row["ingest_id"])["model_id"] == ""
    assert store.get_model("unfiled") is None
    with pytest.raises(ValueError, match="reserved"):
        store.record_model_setup("unfiled", "sha256:inventory", "wgs_1")
    with pytest.raises(ValueError, match="reserved"):
        store.record_project_setup_if_absent("unfiled", "sha256:inventory", "wgs_1")
    store.close()


def test_the_lane_never_remembers_defaults_under_the_reserved_unfiled_id(tmp_path) -> None:
    from server.cadlink.joblane.preparation_inputs import _remember_default_setup

    store = CadLinkStore(tmp_path / "cadlink.db")
    manifest = {"sources": [{"id": "source-hf", "role": "HF", "required": True}]}
    # A return whose record claims the reserved id: nothing is recorded, and the
    # solve goes on with its defaults.
    assert _remember_default_setup(store, manifest, {"project": {"lineage_id": "unfiled"}}, "wgs_defaults") is None
    assert store.get_model_setup("unfiled", inventory_sha256(manifest["sources"])) is None
    store.close()


def test_a_deduplicated_file_press_replays_to_its_job_after_it_ended(tmp_path) -> None:
    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        model_id = "mesh-file:" + record["artifact_sha256"]
        body = {"prepared_id": record["ingest_id"], "setup": setup_for(record)}
        first = (await _file_solve(app, model_id, {**body, "client_request_id": "mesh-1"}))[1]["job_id"]
        deduped = (await _file_solve(app, model_id, {**body, "client_request_id": "mesh-2"}))[1]["job_id"]
        assert deduped == first
        runtime.store.update_job(first, status="complete")
        # The lost answer to mesh-2, asked again: the job it was answered with.
        assert (await _file_solve(app, model_id, {**body, "client_request_id": "mesh-2"}))[1]["job_id"] == first
        assert runtime.store.list_jobs()[1] == 1

    _file_scenario(tmp_path, scenario)


def test_a_file_solve_is_refused_while_an_update_restart_is_pending(tmp_path) -> None:
    from server.updates.restart import RestartApproval

    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        model_id = "mesh-file:" + record["artifact_sha256"]
        body = {"prepared_id": record["ingest_id"], "client_request_id": "mesh-1", "setup": setup_for(record)}
        accepted = (await _file_solve(app, model_id, body))[1]["job_id"]
        approval = RestartApproval()
        approval.approve("0.3.6")
        runtime.restart_approval = approval
        status, refused = await _file_solve(app, model_id, {**body, "client_request_id": "mesh-2",
                                                            "label": "another"})
        assert (status, refused["error"]["code"], refused["error"]["retryable"]) == (
            409, "update_restart_pending", True)
        # A replay of an accepted press still recovers its job.
        assert (await _file_solve(app, model_id, body))[1]["job_id"] == accepted
        assert runtime.store.list_jobs()[1] == 1

    _file_scenario(tmp_path, scenario)


def test_held_fusion_engine_blocker_is_continued_only_by_model_solve(harness) -> None:
    from server.jobs.cad_preparation import job_operation_view
    from server.jobs.runtime import EngineUnavailableError
    from server.jobs.runtime.engine_policy import PrepareRemedy, SolveBlocker

    app = _models_app(harness)
    model = _cad_model(harness)
    setup = _setup(engine="beat-metal")
    revision = _revision_of(harness, setup)
    harness.store.record_model_setup(model["model_id"], inventory_sha256(model["sources"]), revision)
    blocker = SolveBlocker(code="engine_unavailable", engine="beat-metal",
                           message="BEAT Metal needs preparing. Prepare BEAT Metal to solve.",
                           remedies=[PrepareRemedy(engine="beat-metal")])
    harness.submit_error = EngineUnavailableError(blocker.message, blocker=blocker)
    _accept(harness.store, "cmd-engine", model["bundle_path"], model["manifest"])
    harness.runtime._ensure_prep_lane = lambda: None
    held_id = harness._loop.run(harness.runtime.accept_cad_solve(
        harness._intent("cmd-engine"), "cad-solve:cmd-engine"))
    harness.runtime._ensure_prep_lane = type(harness.runtime)._ensure_prep_lane.__get__(harness.runtime)

    async def settle():
        harness.runtime._ensure_prep_lane()
        await harness.runtime.wait_cad_preparations()
    harness._loop.run(settle())
    held = harness.jobs_store.get_job_row(held_id)
    view = job_operation_view(held)
    assert (view["state"], view["reason"]) == ("needs_user_input", "engine_unavailable")
    assert view["blocker"] == blocker.model_dump(mode="json")
    assert view["message"] == blocker.message
    assert view["remedies"] == view["blocker"]["remedies"]
    # Preparation becoming ready creates no new solve on the server.
    _settle(harness)
    assert _job_count(harness) == 1
    status, continued = _solve(harness, app, model, client_request_id="prepare-and-solve", setup=setup)
    assert status == 200
    _settle(harness)
    child = harness.jobs_store.get_job_row(continued["job_id"])
    assert child["parent_job_id"] == held_id
    assert child["status"] == "queued"
    assert _job_count(harness) == 2



def test_imported_active_solve_recovers_before_fresh_admission(tmp_path, monkeypatch):
    from server.jobs.model_solve import submit_imported_request
    from server.jobs.runtime import EngineUnavailableError

    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        request = file_request(record)
        request.client_request_id = None
        first = await submit_imported_request(runtime, request)
        async def unavailable(*args, **kwargs):
            raise EngineUnavailableError("The runtime became unavailable")
        monkeypatch.setattr(runtime, "validate_submission", unavailable)
        assert await submit_imported_request(runtime, request) == first
        runtime.store.update_job(first, status="complete")
        with pytest.raises(EngineUnavailableError):
            await submit_imported_request(runtime, request)
    _file_scenario(tmp_path, scenario)


def test_refused_imported_request_does_not_record_setup(tmp_path, monkeypatch):
    from server.jobs.model_solve import submit_imported_request
    from server.jobs.runtime import ImportedSolveRefusal

    async def scenario(store, app, runtime):
        record = register_mesh(store, tmp_path, registration())
        request = file_request(record)
        request.options.polar_config.inclination = 30
        request.options.polar_config.enabled_axes = ["horizontal", "vertical", "diagonal"]
        def unexpected(*args, **kwargs):
            pytest.fail("refused input must not create a setup revision")
        monkeypatch.setattr(store, "create_setup_revision", unexpected)
        with pytest.raises(ImportedSolveRefusal) as error:
            await submit_imported_request(runtime, request)
        assert error.value.reason_code == "imported_diagonal_inclination_unsupported"
        assert runtime.store.list_jobs()[1] == 0
    _file_scenario(tmp_path, scenario)



def test_wg_solve_uses_snapshot_mesh_over_saved_project_sizes(harness):
    from server.cadlink.snapshot_mesh import snapshot_mesh
    app = _models_app(harness)
    model = _cad_model(harness, mesh_snapshot=True)
    stored = _setup(rigid=4, engine='metal')
    revision = _revision_of(harness, stored)
    harness.store.record_model_setup(model['model_id'], inventory_sha256(model['sources']), revision)
    original = harness.store.get_setup_revision(revision)['setup_json']
    status, result = _solve(harness, app, model, client_request_id='snapshot-wg', setup_revision_id=revision)
    assert status == 200, result
    _settle(harness)
    job = harness.jobs_store.get_job_row(result['job_id'])
    assert job['status'] == 'queued', job
    manifest = json.loads((harness.workspace / model['bundle_path'] / 'wgreturn.json').read_text())
    expected = snapshot_mesh(manifest)
    assert job['config_json']['geometry']['mesh'] == expected.sizes
    effective_id = job['task_metadata']['cad']['setup']['revision_id']
    effective = json.loads(harness.store.get_setup_revision(effective_id)['setup_json'])
    assert effective['preparation']['symmetry_mode'] == 'full'
    assert harness.store.get_setup_revision(revision)['setup_json'] == original
    # A second press with the saved project sizes still means the same solve.
    status, again = _solve(harness, app, model, client_request_id='snapshot-wg-again', setup_revision_id=revision)
    assert status == 200, again
    assert again['job_id'] == result['job_id']


def test_snapshot_with_different_saved_drive_sources_records_a_refusal(harness):
    app = _models_app(harness)
    model = _cad_model(harness, mesh_snapshot=True)
    stale = _setup(engine='metal')
    stale['geometry']['drive_channels'][0]['source_ids'] = ['old-source']
    stale['geometry']['mesh']['source_size_mm'] = {'old-source': 8}
    status, result = _solve(harness, app, model, client_request_id='stale-sources', setup=stale)
    assert status == 200, result
    _settle(harness)
    job = harness.jobs_store.get_job_row(result['job_id'])
    assert job['status'] == 'held'
    assert job['task_metadata']['cad']['refusal']['code'] == 'setup_required'


def test_polar_projection_keeps_the_saved_user_setup_and_digest(harness) -> None:
    app = _models_app(harness)
    model = _cad_model(harness)
    setup = _setup(engine="metal")
    setup["options"]["polar_config"] = {
        "angle_range": [0, 160, 11], "angle_step": 16,
        "enabled_axes": ["horizontal"], "inclination": 35,
    }
    revision_id = _revision_of(harness, setup)
    before = dict(harness.store.get_setup_revision(revision_id))
    harness.ingest.polar_grid_derivation = {"axes": {axis: {} for axis in ("horizontal", "vertical", "diagonal")}}

    status, response = _solve(harness, app, model, client_request_id="polar-press", setup=setup)
    assert status == 200, response
    _settle(harness)
    row = harness.jobs_store.get_job_row(response["job_id"])
    assert row["status"] == "queued", row
    submitted = row["config_json"]
    assert submitted["options"]["polar_config"]["angle_range"] == [-180, 180, 24]
    assert submitted["options"]["polar_config"]["inclination"] == 45
    assert dict(harness.store.get_setup_revision(revision_id)) == before
    saved = project_setup(harness.store, model["model_id"], model["sources"])
    assert saved is not None and saved[1] == revision_id
    assert saved[0].options["polar_config"]["angle_range"] == [0, 160, 11]


# -- Solve anyway carries for exactly the preparation it was chosen for ----------------


def _anyway_target(harness: JobsHarness, manifest_sha256: str, frame_axis: str | None = None) -> Any:
    from server.cadlink.intake.ingest import read_snapshot, retained_snapshot_path
    from server.jobs.model_solve import SolveTarget

    manifest = read_snapshot(retained_snapshot_path(harness.data_dir, manifest_sha256), retained=True).manifest
    return SolveTarget(inputs="", frame={"axis": frame_axis} if frame_axis else None, manifest=manifest)


def _carries(harness: JobsHarness, record: dict[str, Any], setup: dict[str, Any], target: Any) -> bool:
    from server.jobs.model_solve import _solve_anyway_carries

    return _solve_anyway_carries(harness.store, harness.data_dir, record, validate_setup(setup), target)


def _with_preparation(setup: dict[str, Any], **preparation: Any) -> dict[str, Any]:
    return {**setup, "preparation": preparation}


@pytest.mark.parametrize(("press", "carries"), [
    (_setup(), True),
    (_setup(engine="metal"), True),  # a solve option is not a preparation
    (_with_preparation(_setup(), symmetry_mode="full"), False),
    (_with_preparation(_setup(), surface_deviation_mm=0.25), False),
    (_with_preparation(_setup(), area_drift_overrides=["source-hf"]), False),
    (_setup(rigid=25.0), False),
])
def test_solve_anyway_carries_only_for_the_preparation_it_was_chosen_for(harness, press, carries) -> None:
    """A symmetry 'auto' Solve anyway mesh is not a full-domain mesh 2-4x its size."""

    model = _cad_model(harness, mesh_limit_override=True)
    record = json.loads(harness.store.get_ingest(model["prepared_id"])["record_json"])
    target = _anyway_target(harness, record["manifest_sha256"])
    assert _carries(harness, record, press, target) is carries


def test_solve_anyway_does_not_carry_to_another_domain_reading(harness) -> None:
    """The mesh key leaves an evidenced domain plan out: a press that records
    the [x]-only reading of an offer prepared on [x, y] is another (larger) mesh."""

    from dataclasses import replace

    model = _cad_model(harness, mesh_limit_override=True)
    record = json.loads(harness.store.get_ingest(model["prepared_id"])["record_json"])
    record["domain_interpretation"] = {"plan": {"planes": ["x", "y"]}}
    target = _anyway_target(harness, record["manifest_sha256"])
    same = replace(target, domain={"planes": ["x", "y"]})
    assert _carries(harness, record, _setup(), same) is True
    assert _carries(harness, record, _setup(), replace(target, domain={"planes": ["x"]})) is False


def test_solve_anyway_does_not_carry_without_the_records_key_or_its_override(harness) -> None:
    model = _cad_model(harness, mesh_limit_override=True)
    record = json.loads(harness.store.get_ingest(model["prepared_id"])["record_json"])
    target = _anyway_target(harness, record["manifest_sha256"])
    assert _carries(harness, record, _setup(), target) is True
    # A record prepared before the key existed is not known to be the same mesh.
    assert _carries(harness, {k: v for k, v in record.items() if k != "mesh_lookup_key"}, _setup(), target) is False
    assert _carries(harness, {**record, "mesh_limit_override": False}, _setup(), target) is False
    # No retained copy: nothing can be known to be the same.
    assert _carries(harness, record, _setup(), None) is False


def test_solve_anyway_does_not_carry_to_other_skipped_sources(harness) -> None:
    design_id, lineage = _project(harness, 90.0)
    bundle_path, _ = _project_return(harness, "two-sources", design_id, lineage)
    path = harness.workspace / bundle_path / "wgreturn.json"
    value = json.loads(path.read_text())
    second = {**json.loads(json.dumps(value["sources"][0])), "id": "source-lf", "role": "LF",
              "default_drive_channel_id": "drive-lf"}
    second["selectors"]["appearance_labels"] = ["LF"]
    value["sources"].append(second)
    path.write_text(json.dumps(value))
    retained = retain_snapshot(harness.workspace / bundle_path, harness.data_dir)
    mesh = _setup()["geometry"]["mesh"]
    both = {**_setup(), "geometry": {
        "drive_channels": [{"id": "drive-hf", "source_ids": ["source-hf"]},
                           {"id": "drive-lf", "source_ids": ["source-lf"]}],
        "mesh": {**mesh, "source_size_mm": {"source-hf": 8.0, "source-lf": 8.0}},
    }}
    one = {**_setup(), "geometry": {**_setup()["geometry"], "skipped_source_ids": ["source-lf"]}}
    record = {"mesh_limit_override": True, "manifest_sha256": retained["manifest_sha256"],
              "mesh_lookup_key": _anyway_key(harness, retained["manifest_sha256"], both)}
    target = _anyway_target(harness, retained["manifest_sha256"])
    assert _carries(harness, record, both, target) is True
    # Another mesh, not a press WG cannot prepare.
    assert _anyway_key(harness, retained["manifest_sha256"], one) != record["mesh_lookup_key"]
    assert _carries(harness, record, one, target) is False
