"""A manual CAD-mode Solve is a backend-owned durable operation."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.cadlink import api
from server.cadlink import ingest as ingest_module
from server.cadlink import preparation as preparation_module
from server.cadlink.ingest import retain_snapshot
from server.cadlink.operations import PREPARE_AND_SOLVE
from server.cadlink.wgreturn import WgReturnIntegrityError

from cad_backends import JobsHarness as Harness
from test_cad_preparation import _revision, _setup, _write_return


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    h = Harness(tmp_path)
    yield h
    h.close()


def _ingest(harness: Harness, *, name: str = "speaker.wgreturn") -> tuple[str, Path]:
    relative, _manifest = _write_return(harness.workspace, name)
    retained = retain_snapshot(harness.workspace / relative, harness.data_dir)

    def record(ingest_id: str, created_at: str) -> str:
        return json.dumps(
            {
                "ingest_id": ingest_id,
                "created_at": created_at,
                "return_id": "wgr_manual",
                "manifest_sha256": retained["manifest_sha256"],
                "artifact_sha256": retained["artifact_sha256"],
                "document": {"name": "Speaker"},
                "project": None,
            }
        )

    row = harness.store.allocate_ingest(
        manifest_sha256=retained["manifest_sha256"],
        artifact_sha256=retained["artifact_sha256"],
        record_builder=record,
    )
    return str(row["ingest_id"]), Path(retained["retained_path"])


def _request(harness: Harness) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                cadlink_store=harness.store,
                data_dir=str(harness.data_dir),
                update_restart=None,
                jobs_runtime=harness.runtime,
                cad_job_shims=True,
            )
        )
    )


def _create(harness: Harness, ingest_id: str, operation_id: str = "manual-1"):
    return harness._loop.run(
        api.post_cad_operation(
            api.ManualSolveOperationRequest(operationId=operation_id, ingestId=ingest_id),
            _request(harness),
        )
    )


def test_retaining_refuses_a_bundle_swapped_after_initial_verification(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    relative, original_manifest = _write_return(
        harness.workspace, "speaker.wgreturn", step=b"ORIGINAL"
    )
    replacement_root = harness.workspace / "replacement"
    replacement_relative, _replacement_manifest = _write_return(
        replacement_root, "speaker.wgreturn", step=b"REPLACEMENT"
    )
    source = harness.workspace / relative
    replacement = replacement_root / replacement_relative
    real_copytree = ingest_module.shutil.copytree

    def swap_then_copy(source_path, destination, **kwargs):
        shutil.copy2(replacement / "wgreturn.json", source / "wgreturn.json")
        shutil.copy2(replacement / "assembly.step", source / "assembly.step")
        return real_copytree(source_path, destination, **kwargs)

    monkeypatch.setattr(ingest_module.shutil, "copytree", swap_then_copy)

    with pytest.raises(WgReturnIntegrityError, match="changed while WG was retaining it"):
        retain_snapshot(source, harness.data_dir)

    retained = harness.data_dir / "imports" / "bundles" / (
        original_manifest.removeprefix("sha256:") + ".wgreturn"
    )
    assert not retained.exists()
    assert list(retained.parent.iterdir()) == []


def test_a_manual_solve_prepares_from_the_retained_copy_with_the_folder_gone(
    harness: Harness,
) -> None:
    ingest_id, retained = _ingest(harness)
    created = _create(harness, ingest_id)
    shutil.rmtree(harness.workspace)
    replayed = _create(harness, ingest_id)

    solved = harness.prepare(
        "manual-1", setup_revision_id=_revision(harness.store, _setup(engine="metal"))
    )

    assert created.operation.state == "received"
    assert harness.store.get_operation("manual-1")["snapshot_json"] is not None
    assert replayed == created
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1")
    assert Path(harness.ingest.calls[0]["bundle_path"]) == retained


def test_the_same_operation_id_recovers_and_a_different_ingest_conflicts(
    harness: Harness,
) -> None:
    first, _retained = _ingest(harness, name="first.wgreturn")
    second, _other = _ingest(harness, name="second.wgreturn")

    original = _create(harness, first)
    recovered = _create(harness, first)
    conflict = _create(harness, second)

    assert recovered == original
    assert conflict.status_code == 409
    assert json.loads(conflict.body)["error"]["code"] == "operation_conflict"
    row = harness.store.get_operation("manual-1")
    assert row is not None and first in json.loads(row["inputs_json"])["bundle_path"]


def test_an_exact_replay_recovers_after_the_retained_cas_is_removed(harness: Harness) -> None:
    ingest_id, retained = _ingest(harness)
    created = _create(harness, ingest_id)
    shutil.rmtree(retained)

    replayed = _create(harness, ingest_id)

    assert replayed == created


def test_it_submits_under_cad_solve_key_with_the_selected_engine(harness: Harness) -> None:
    ingest_id, _retained = _ingest(harness)
    _create(harness, ingest_id)

    summary = harness.prepare(
        "manual-1", setup_revision_id=_revision(harness.store, _setup(engine="beat-cpu"))
    )

    assert summary["state"] == "accepted"
    assert harness.submitted[0].client_request_id == "cad-solve:manual-1"
    assert harness.submitted[0].options.engine == "beat-cpu"


def test_an_ingest_without_a_retained_copy_is_refused_409(harness: Harness) -> None:
    ingest_id, retained = _ingest(harness)
    shutil.rmtree(retained)

    response = _create(harness, ingest_id)

    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "snapshot_not_retained"
    assert harness.store.list_operations(kind=PREPARE_AND_SOLVE) == []


def test_an_unknown_ingest_is_404(harness: Harness) -> None:
    refused = _create(harness, "wgi_missing")
    assert refused.status_code == 404
    assert json.loads(refused.body)["error"]["code"] == "unknown_ingest"


def test_an_update_restart_latch_creates_no_manual_operation(harness: Harness) -> None:
    from server.updates.restart import RestartApproval

    ingest_id, _retained = _ingest(harness)
    approval = RestartApproval()
    approval.approve("0.3.4")
    request = _request(harness)
    request.app.state.update_restart = approval

    response = asyncio.run(
        api.post_cad_operation(
            api.ManualSolveOperationRequest(operationId="manual-1", ingestId=ingest_id),
            request,
        )
    )

    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == "update_restart_pending"
    assert harness.store.list_operations() == []


def test_an_exact_replay_recovers_while_an_update_restart_is_pending(harness: Harness) -> None:
    from server.updates.restart import RestartApproval

    ingest_id, _retained = _ingest(harness)
    created = _create(harness, ingest_id)
    approval = RestartApproval()
    approval.approve("0.3.4")
    request = _request(harness)
    request.app.state.update_restart = approval

    replayed = asyncio.run(
        api.post_cad_operation(
            api.ManualSolveOperationRequest(operationId="manual-1", ingestId=ingest_id),
            request,
        )
    )

    assert replayed == created


def test_a_manual_solve_whose_only_copy_is_damaged_waits_rather_than_rejects(
    harness: Harness,
) -> None:
    """A manual solve names `ingest/<id>`, which is not a return in the folder.

    `recover_manual_solve` always writes that bundle path, so there is no such
    thing as a manual solve without one -- and `exchange_bundle_path` refuses
    its shape. Reached from the damaged-copy path, that refusal used to land on
    a terminal `rejected`/`snapshot_invalid` carrying the internal string
    "bundlePath must name a .wgreturn bundle under the workspace's wgreturn/".
    """

    ingest_id, retained = _ingest(harness)
    _create(harness, ingest_id)
    assert json.loads(harness.row("manual-1")["inputs_json"])["bundle_path"] == (
        f"ingest/{ingest_id}"
    )
    (retained / "assembly.step").write_bytes(b"")  # a torn write in the only copy
    shutil.rmtree(harness.workspace / "wgreturn")  # and the return has left the folder

    summary = harness.prepare(
        "manual-1", setup_revision_id=_revision(harness.store, _setup(engine="metal"))
    )

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "preparation_failed")
    assert "bundlePath" not in summary["message"]
    assert summary["message"] == preparation_module.DAMAGED_COPY_MESSAGE
    assert harness.ingest.calls == [] and harness.submitted == []


def test_a_manual_intent_waits_for_its_first_press_across_restart(harness):
    ingest_id, _copy = _ingest(harness)
    result = _create(harness, ingest_id)
    assert result.operation.state == "received"
    job = harness.jobs_store.latest_cad_job("manual-1")
    assert job["task_metadata"]["cad"]["manual_waiting"] is True
    assert job["id"] not in harness.jobs_store.unheld_preparing_job_ids()
    assert harness.jobs_store.claim_preparing_job(
        job["id"], stage="validating", stage_message="Validating", progress=0.0,
    ) is None
    assert harness.ingest.calls == [] and harness.submitted == []
    harness.restart()
    recovered = harness.jobs_store.latest_cad_job("manual-1")
    assert recovered["id"] == job["id"] and recovered["status"] == "preparing"
    assert harness.ingest.calls == [] and harness.submitted == []
    solved = harness.prepare("manual-1", setup_revision_id=_revision(harness.store, _setup(engine="beat-cpu")))
    assert solved["state"] == "accepted" and len(harness.submitted) == 1


# The WG jobs routes and operation compatibility routes share one intake.
def _jobs_app(harness):
    from fastapi import FastAPI
    from server.jobs.api import create_jobs_router

    app = FastAPI()
    app.state = _request(harness).app.state
    app.include_router(create_jobs_router(harness.runtime, restart_approval=harness._latch))
    return app


async def _post_job(app, path, body):
    from test_jobs_api import _request as http_request

    status, content = await http_request(app, "POST", path, body=body)
    return status, json.loads(content)


@pytest.mark.parametrize("after", ["waiting", "refused", "bound"])
def test_f2_job_cad_solve_recovers_the_original_job_and_compatibility_operation(harness, after):
    ingest_id, retained = _ingest(harness)
    revision = _revision(harness.store, _setup(engine="metal"))
    harness.runtime._ensure_prep_lane = lambda: None
    app = _jobs_app(harness)
    body = {
        "client_request_id": "press-1", "ingest_id": ingest_id,
        "setup_revision_id": revision, "frame_axis": "+z", "label": "Speaker run",
        "approvals": {"preparation_id": "reviewed", "finding_ids": ["review-1"]},
        "submit": after != "refused",
    }

    async def flow():
        status, created = await _post_job(app, "/api/jobs/cad-solve", body)
        assert status == 200, created
        job_id = created["job_id"]
        row = harness.jobs_store.get_job_row(job_id)
        assert harness.jobs_store.job_for_submission_key("cad-solve:manual-solve:press-1") == job_id
        assert row["config_json"]["setup_revision_id"] == revision
        assert row["config_json"]["frame_axis"] == "+z"
        assert row["config_json"]["approvals"] == body["approvals"]
        assert row["label"] == body["label"]
        assert "manual_waiting" not in row["task_metadata"]["cad"]
        if after != "waiting":
            harness.runtime._ensure_prep_lane = type(harness.runtime)._ensure_prep_lane.__get__(harness.runtime)
            harness.runtime._ensure_prep_lane()
            await harness.runtime.wait_cad_preparations()
            assert harness.jobs_store.get_job_row(job_id)["status"] == ("queued" if after == "bound" else "error")
        # Recover without another press, retention, or an open restart latch.
        shutil.rmtree(retained)
        harness.blocked = "Restart approved"
        if after != "waiting":
            body["setup_revision_id"] = "revision-no-longer-retained"
        replay_status, replay = await _post_job(app, "/api/jobs/cad-solve", body)
        assert replay_status == 200 and replay == created
        compat = await api.post_cad_operation(
            api.ManualSolveOperationRequest(operationId="manual-solve:press-1", ingestId=ingest_id), _request(harness)
        )
        assert compat.operation.operation_id == "manual-solve:press-1"
        assert harness.jobs_store.list_jobs()[1] == 1
        assert harness.store.get_operation("manual-solve:press-1")["job_id"] == job_id
    harness._loop.run(flow())


@pytest.mark.parametrize("problem, expected, code", [
    ("conflict", 409, "operation_conflict"), ("unknown", 404, "unknown_ingest"),
    ("snapshot", 409, "snapshot_not_retained"), ("restart", 409, "update_restart_pending"),
    ("setup", 404, None),
])
def test_f2_job_cad_solve_maps_intake_refusals(harness, problem, expected, code):
    ingest_id, retained = _ingest(harness)
    harness.runtime._ensure_prep_lane = lambda: None
    app = _jobs_app(harness)
    body = {"client_request_id": "press-2", "ingest_id": ingest_id}
    async def flow():
        if problem == "conflict":
            assert (await _post_job(app, "/api/jobs/cad-solve", body))[0] == 200
            body["ingest_id"], _ = _ingest(harness, name="second.wgreturn")
        elif problem == "unknown":
            body["ingest_id"] = "gone"
        elif problem == "snapshot":
            shutil.rmtree(retained)
        elif problem == "restart":
            harness.blocked = "Restart approved"
        else:
            body["setup_revision_id"] = "gone"
        status, result = await _post_job(app, "/api/jobs/cad-solve", body)
        assert status == expected, result
        if code:
            assert result["error"]["code"] == code
        else:
            assert "Unknown setup revision" in result["detail"]
        if problem != "conflict":
            assert harness.store.get_operation("manual-solve:press-2") is None
            assert harness.jobs_store.list_jobs()[1] == 0
    harness._loop.run(flow())


@pytest.mark.parametrize("client_id", ["", "../press", "a:b", "a" * 129])
def test_f2_job_cad_solve_validates_the_client_id(harness, client_id):
    app = _jobs_app(harness)
    status, _ = harness._loop.run(_post_job(app, "/api/jobs/cad-solve", {"client_request_id": client_id, "ingest_id": "gone"}))
    assert status == 422
    assert harness.jobs_store.list_jobs()[1] == 0


def test_f2_job_solve_again_captures_the_first_press_and_continues_a_refused_job(harness):
    ingest_id, _ = _ingest(harness)
    revision = _revision(harness.store, _setup(engine="metal"))
    harness.ingest.findings = [{"id": "review-1", "blocking": True}]
    _create(harness, ingest_id)
    parent = harness.jobs_store.latest_cad_job("manual-1")["id"]
    app = _jobs_app(harness)
    async def flow():
        status, result = await _post_job(app, f"/api/jobs/{parent}/solve-again", {
            "setup_revision_id": revision, "frame_axis": "+z", "submit": False,
        })
        assert status == 200 and result["job_id"] == parent
        await harness.runtime.wait_cad_preparations()
        assert harness.jobs_store.get_job_row(parent)["task_metadata"]["cad"]["refusal"]["code"] == "findings_need_review"
        prep = harness.jobs_store.get_job_row(parent)["task_metadata"]["cad"]["preparation"]["preparation_id"]
        bad_status, bad = await _post_job(app, f"/api/jobs/{parent}/approvals", {"preparation_id": "another", "finding_ids": ["review-1"]})
        assert bad_status == 422 and "another preparation" in bad["detail"]
        approved_status, approved = await _post_job(app, f"/api/jobs/{parent}/approvals", {"preparation_id": prep, "finding_ids": ["review-1"]})
        assert approved_status == 200
        assert approved["cad_state"]["approvals"] == [{"preparation_id": prep, "finding_id": "review-1"}]
        assert harness.jobs_store.list_jobs()[1] == 1  # approvals do not solve
        harness.runtime._ensure_prep_lane = lambda: None
        body = {"setup_revision_id": revision, "frame_axis": "+z", "approvals": {"preparation_id": prep, "finding_ids": ["review-1"]}}
        status, child = await _post_job(app, f"/api/jobs/{parent}/solve-again", body)
        assert status == 200 and child["job_id"] != parent
        assert await _post_job(app, f"/api/jobs/{parent}/solve-again", body) == (status, child)
        row = harness.jobs_store.get_job_row(child["job_id"])
        assert row["config_json"]["approvals"] == body["approvals"]
        harness.runtime._ensure_prep_lane = type(harness.runtime)._ensure_prep_lane.__get__(harness.runtime)
        harness.runtime._ensure_prep_lane()
        await harness.runtime.wait_cad_preparations()
        assert harness.jobs_store.get_job_row(child["job_id"])["status"] == "queued"
        status, refused = await _post_job(app, f"/api/jobs/{child['job_id']}/solve-again", {})
        assert status == 409 and "retry it instead" in refused["detail"]
        # The compatibility Prepare of a bound job still returns acceptance.
        compat = await api.post_prepare_cad_operation("manual-1", api.PrepareOperationRequest(), _request(harness))
        assert compat["operation"]["state"] == "accepted"
    harness._loop.run(flow())


@pytest.mark.parametrize("route", ["solve-again", "approvals"])
def test_f2_job_cad_actions_refuse_an_unknown_job(harness, route):
    body = {} if route == "solve-again" else {"preparation_id": "prep", "finding_ids": ["finding"]}
    status, result = harness._loop.run(_post_job(_jobs_app(harness), f"/api/jobs/gone/{route}", body))
    assert status == 404 and result["detail"] == "Job not found"


@pytest.mark.parametrize("problem, expected", [("restart", 409), ("setup", 404)])
def test_f2_job_solve_again_maps_restart_and_unknown_setup(harness, problem, expected):
    ingest_id, _ = _ingest(harness)
    _create(harness, ingest_id)
    job_id = harness.jobs_store.latest_cad_job("manual-1")["id"]
    if problem == "restart":
        harness.blocked = "Restart approved"
    body = {"setup_revision_id": "gone"} if problem == "setup" else {}
    status, result = harness._loop.run(_post_job(_jobs_app(harness), f"/api/jobs/{job_id}/solve-again", body))
    assert status == expected
    if problem == "restart":
        assert result["error"]["code"] == "update_restart_pending"
    else:
        assert result["detail"] == "Unknown setup revision gone"
    assert harness.jobs_store.get_job_row(job_id)["task_metadata"]["cad"]["manual_waiting"]
