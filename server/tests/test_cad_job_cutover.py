"""S4-F1: production compatibility shims, upgrade and durable acceptance."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cad_backends import JobsHarness
from server.cadlink import api
from server.cadlink.job_shims import sweep_pending_solves
from server.cadlink.preparation import operation_summary
from server.cadlink.setup import solve_request_for, validate_setup
from server.jobs.cad_intent import cad_of
from test_cad_preparation import _received, _revision, _setup


@pytest.fixture
def h(tmp_path):
    harness = JobsHarness(tmp_path)
    yield harness
    harness.close()


def _request(h):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        cadlink_store=h.store, jobs_runtime=h.runtime, data_dir=str(h.data_dir),
        cad_workspace=SimpleNamespace(selected_path=lambda: h.workspace),
        cad_job_shims=True, update_restart=None,
    )))


@pytest.mark.parametrize("state", ["received", "needs_user_input", "processing", "cancel_requested", "bound"])
def test_upgrade_migrates_every_pending_state_once_without_losing_preparation(h, state):
    from server.cadlink.ingest import meshing_semantics_fingerprint
    from server.cadlink.preparation import retain_operation_snapshot

    _received(h)
    h.runtime._ensure_prep_lane = lambda: None
    revision = _revision(h.store, _setup(engine="metal"))
    if state != "received":
        generation = h.store.claim("cmd-1", 0)
        retain_operation_snapshot(h.store, h.data_dir, h.workspace, "cmd-1")
        snapshot = json.loads(h.row()["snapshot_json"])
        record = h.ingest(h.data_dir / "imports" / "bundles" / (snapshot["manifest_sha256"].removeprefix("sha256:") + ".wgreturn"),
                          {}, [], h.store, h.data_dir, prep_options={}, commit_guard=lambda conn: True, retained_copy=True)
        prep_id = record["ingest_id"]
        h.store.advance_operation("cmd-1", generation, setup_revision_id=revision)
        h.store.record_preparation("cmd-1", generation, preparation_id=prep_id, ingest_id=prep_id,
                                   snapshot_sha256=snapshot["manifest_sha256"], setup_revision_id=revision,
                                   report_sha256=record["report_sha256"], blocking_finding_ids=["review-1"],
                                   meshing_semantics=meshing_semantics_fingerprint())
        h.store.add_approvals("cmd-1", prep_id, ["review-1"])
        if state == "needs_user_input":
            h.store.record_outcome("cmd-1", generation, state, reason="findings_need_review", outcome={"message": "Review exactly this finding."})
        elif state == "bound":
            request = solve_request_for(validate_setup(_setup(engine="metal")), ingest_id=prep_id,
                                        manifest_sha256=snapshot["manifest_sha256"], artifact_sha256=snapshot["artifact_sha256"],
                                        acknowledged_findings=[], client_request_id="cad-solve:cmd-1")
            h.store.bind_request("cmd-1", generation, setup_revision_id=revision, request_json=request.model_dump_json())
        elif state == "cancel_requested":
            h.store.request_cancel("cmd-1")
    before = h.row()
    async def migrate():
        return await sweep_pending_solves(h.context())
    assert h._loop.run(migrate()) == 1
    assert h._loop.run(migrate()) == 0
    job = h.jobs_store.latest_cad_job("cmd-1")
    assert job is not None
    expected = {"received": "preparing", "needs_user_input": "error", "processing": "error", "cancel_requested": "cancelled", "bound": "queued"}
    assert job["status"] == expected[state]
    assert (job["run_number"] is not None) == (state == "bound")
    if state != "received":
        cad = cad_of(job)
        assert cad["preparation"]["preparation_id"] == before["preparation_id"]
        assert cad["preparation"]["approvals"] == json.loads(before["approvals_json"])
        assert cad["setup"]["revision_id"] == revision
        assert cad["frame"] == {"axis": None, "provenance": "linked", "confirmed": False, "requirement": None}
    if state == "needs_user_input":
        assert cad_of(job)["refusal"] == {"code": "findings_need_review", "message": "Review exactly this finding."}
        assert job["stage_message"] == "Review exactly this finding."
        events = h.jobs_store.replay_events(0)
        assert events[-1]["type"] == "error"
        assert events[-1]["payload"]["message"] == "Review exactly this finding."
    if state == "bound":
        assert job["config_json"] == json.loads(before["request_json"])
        assert job["has_mesh_artifact"]
    assert h.row()["state"] == "accepted"
    assert h.row()["attempt_generation"] == before["attempt_generation"]
    assert h.jobs_store.list_jobs()[1] == 1


def test_upgrade_recovers_a_legacy_submission_key_without_rehashing_its_request(h):
    _received(h)
    h.jobs_store.create_job_idempotent(
        {"id": "legacy-job", "status": "queued", "created_at": "2026-09-01", "updated_at": "2026-09-01",
         "queued_at": "2026-09-01", "config_json": {}, "config_summary_json": {}},
        submission_key="cad-solve:cmd-1", request_sha256="old-solve-request-hash",
    )
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    assert h.row()["job_id"] == "legacy-job"
    assert h.jobs_store.list_jobs()[1] == 1
    assert h.submitted == []


def test_pending_list_detail_approvals_and_solve_again_follow_the_job(h):
    _received(h)
    h.ingest.findings = [{"id": "review-1", "kind": "healing-performed", "blocking": True}]
    revision = _revision(h.store, _setup(engine="metal"))
    request = _request(h)
    async def flow():
        first = await api.post_prepare_cad_operation("cmd-1", api.PrepareOperationRequest(setupRevisionId=revision, wait=True), request)
        assert first["operation"]["reason"] == "findings_need_review"
        assert h.row()["state"] == "accepted"  # ledger acceptance, not preparation state
        listed = await api.list_cad_operations(request, pending=True, limit=100)
        detail = await api.get_cad_operation("cmd-1", request)
        assert listed["operations"][0]["state"] == detail["state"] == "needs_user_input"
        assert detail["stage"] == "ready"
        prep = detail["preparation"]["preparationId"]
        approved = await api.post_cad_operation_approvals("cmd-1", api.OperationApprovalsRequest(preparationId=prep, findingIds=["review-1"]), request)
        assert approved["approvals"] == [{"preparation_id": prep, "finding_id": "review-1"}]
        resumed = await api.post_prepare_cad_operation("cmd-1", api.PrepareOperationRequest(wait=True), request)
        assert resumed["operation"]["state"] == "accepted"
        assert resumed["operation"]["preparationId"] == prep
        assert (await api.list_cad_operations(request, pending=True, limit=100))["operations"] == []
        return first, resumed
    first, resumed = h._loop.run(flow())
    assert len(h.ingest.calls) == len(h.submitted) == 1
    parent = h.jobs_store.job_for_submission_key("cad-solve:cmd-1")
    child = h.jobs_store.job_for_submission_key(f"cad-solve-again:{parent}")
    assert resumed["operation"]["jobId"] == child
    assert h.jobs_store.get_job_row(parent)["run_number"] is None
    assert h.jobs_store.get_job_row(child)["run_number"] == 1


def test_an_acceptance_acknowledgement_names_the_durable_preparing_job(h):
    from test_cad_preparation import _write_return, _deliver_file
    from server.cadlink.solve_command import SOLVE_ACKS_DIRECTORY

    bundle, manifest = _write_return(h.workspace)
    _deliver_file(h, bundle, manifest)
    h.runtime._ensure_prep_lane = lambda: None
    from server.cadlink.preparation import run_delivery_pass
    assert h._loop.run(run_delivery_pass(h.context(), spawn=lambda *_args: None)) == ["cmd-1"]
    ack = json.loads((h.data_dir / "ipc" / "wglink" / SOLVE_ACKS_DIRECTORY / "cmd-1.json").read_text())
    job = h.jobs_store.latest_cad_job("cmd-1")
    assert ack["jobId"] == job["id"]
    assert job["status"] == "preparing" and job["run_number"] is None
    assert h.row()["job_id"] == job["id"]
    assert h.ingest.calls == []


def test_a_bound_solver_failure_still_reads_as_an_accepted_operation(h):
    _received(h)
    h.prepare(setup_revision_id=_revision(h.store, _setup()))
    job = h.latest_job()
    h.jobs_store.update_job(job["id"], status="error", error_message="solver failed")
    summary = operation_summary(h.row(), h.jobs_store)
    assert (summary["state"], summary["stage"], summary["reason"], summary["jobId"]) == ("accepted", "submitted", None, job["id"])


def test_dismissing_a_refused_child_never_revives_its_parent_on_reconnect(h):
    _received(h)
    h.ingest.findings = [{"id": "review-1", "kind": "warning", "blocking": True, "message": "Review"}]
    revision = _revision(h.store, _setup())
    assert h.prepare(setup_revision_id=revision)["state"] == "needs_user_input"
    assert h.prepare(setup_revision_id=revision)["state"] == "needs_user_input"
    assert h.jobs_store.list_jobs()[1] == 2
    async def dismiss():
        request = _request(h)
        result = await api.post_cancel_cad_operation("cmd-1", request)
        assert result["state"] == "cancelled" and result["stage"] is None
        detail = await api.get_cad_operation("cmd-1", request)
        assert detail["state"] == "cancelled" and detail["jobId"] is None
        assert (await api.list_cad_operations(request, pending=True, limit=100))["operations"] == []
    h._loop.run(dismiss())
    assert h.jobs_store.list_jobs()[1] == 0
    assert h.row()["state"] == "accepted"  # the delivery receipt remains immutable


def test_a_second_manual_press_cannot_replace_a_preparing_intent(h):
    from server.cadlink.job_shims import accept_operation_solve
    _received(h)
    h.runtime._ensure_prep_lane = lambda: None
    first = _revision(h.store, _setup(engine="metal"))
    second = _revision(h.store, _setup(engine="bempp"))
    async def press():
        job_id = await accept_operation_solve(h.context(), "cmd-1", manual=True)
        await h.runtime.prepare_cad_solve(job_id, setup_revision_id=first, submit=True)
        await h.runtime.prepare_cad_solve(job_id, setup_revision_id=second, submit=True)
        assert h.jobs_store.get_job_row(job_id)["config_json"]["setup_revision_id"] == first
    h._loop.run(press())


def test_a_bound_error_keeps_the_default_settings_note():
    from server.jobs.cad_preparation import job_operation_view
    view = job_operation_view({
        "id": "bound", "status": "error", "config_json": {"design": {}},
        "task_metadata": {"cad": {"operation_id": "op", "setup": {"origin": "wg_defaults"}}},
    })
    assert view["state"] == "accepted" and view["stage"] == "submitted"
    assert view["setupDefaults"] is True and "default" in view["message"].lower()


def test_job_stage_events_push_the_derived_operation_to_the_unchanged_frontend(h, monkeypatch):
    import asyncio
    from server.cadlink.job_shims import accept_operation_solve
    _received(h)
    h.runtime._ensure_prep_lane = lambda: None
    monkeypatch.setattr(api, "ingest_bundle", h.ingest)
    async def flow():
        request = _request(h)
        request.app.state.cad_job_shims = False
        context = api._preparation_context(request.app.state)
        observed = h.runtime.events.subscribe()
        try:
            job_id = await accept_operation_solve(context, "cmd-1", manual=True)
            while not observed.empty():
                observed.get_nowait()
            # The press removes the manual hold; this probe advances just the
            # durable stage, independently of the preparation test harness.
            await h.runtime.prepare_cad_solve(job_id, submit=True)
            event = h.jobs_store.claim_preparing_job(job_id, stage="validating", stage_message="Validating", progress=0.1)
            assert event is not None
            h.runtime.events.publish(event)
            while True:
                pushed = await asyncio.wait_for(observed.get(), timeout=2)
                if pushed.get("kind") == "cadOperation" and pushed["operation"]["state"] == "processing":
                    break
            assert pushed["operation"]["stage"] == "validating"
            assert pushed["operation"]["operationId"] == "cmd-1"
        finally:
            h.runtime.events.unsubscribe(observed)
            tasks = list(request.app.state.cad_preparations)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    h._loop.run(flow())


@pytest.mark.parametrize("cancelled", [False, True])
def test_an_inadmissible_legacy_bind_migrates_to_a_refused_job_and_the_sweep_continues(h, monkeypatch, cancelled):
    from dataclasses import replace
    from server.jobs.runtime import EngineUnavailableError
    from server.cadlink.preparation import PreparationInput
    from server.cadlink.job_shims import accept_operation_solve
    bundle, manifest = _received(h)
    revision = _revision(h.store, _setup(engine="metal"))
    # Make a genuine immutable request using the existing prepared fixture,
    # then recreate its pre-acceptance ledger state in a separate command.
    h.prepare(setup_revision_id=revision)
    original_request = h.jobs_store.latest_cad_job("cmd-1")["config_json"]
    from test_cad_preparation import _accept
    _accept(h.store, "cmd-2", bundle, manifest)
    generation = h.store.claim("cmd-2", 0)
    h.store.bind_request("cmd-2", generation, setup_revision_id=revision,
                         request_json=json.dumps({**original_request, "client_request_id": "cad-solve:cmd-2"}))
    if cancelled:
        h.store.request_cancel("cmd-2")
    _accept(h.store, "cmd-3", bundle, manifest)
    async def rejected(request, **kwargs):
        raise EngineUnavailableError("This engine is unavailable on this host")
    monkeypatch.setattr(h.runtime, "submit", rejected)
    h.runtime._ensure_prep_lane = lambda: None
    ctx = replace(h.context(), submission_refusals=(EngineUnavailableError,))
    assert h._loop.run(sweep_pending_solves(ctx)) == 2
    refused = h.jobs_store.latest_cad_job("cmd-2")
    assert refused["status"] == ("cancelled" if cancelled else "error")
    assert refused["run_number"] is None
    if not cancelled:
        assert cad_of(refused)["refusal"]["code"] == "engine_unavailable"
        assert "unavailable" in cad_of(refused)["refusal"]["message"]
    assert operation_summary(h.row("cmd-2"), h.jobs_store)["state"] == (
        "cancelled" if cancelled else "needs_user_input"
    )
    assert h.jobs_store.latest_cad_job("cmd-3")["status"] == "preparing"
    assert h._loop.run(accept_operation_solve(ctx, "cmd-2", PreparationInput())) == refused["id"]


def test_compatibility_reads_initialize_a_fresh_runtime_without_app_startup(tmp_path):
    import asyncio
    from server.app import create_app
    from test_update_transaction_contract import _post
    async def read():
        app = create_app(data_dir=tmp_path / "data")
        assert app.state.jobs_runtime._started is False
        try:
            status, raw = await _post(app, "/api/cadlink/operations", None, method="GET")
            assert status == 200 and json.loads(raw) == {"operations": []}
            assert app.state.jobs_runtime._started is True
        finally:
            tasks = list(app.state.cad_preparations)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.state.jobs_runtime.shutdown()
            app.state.cadlink_store.close()
    asyncio.run(read())


@pytest.mark.parametrize("reason", ["findings_need_review", "update_restart_pending"])
def test_upgrade_refuses_a_processing_retry_even_when_it_carries_an_old_reason(h, reason):
    from server.jobs.cad_intent import INTERRUPTED_MESSAGE
    _received(h)
    generation = h.store.claim("cmd-1", 0)
    h.store.record_outcome("cmd-1", generation, "needs_user_input", reason=reason,
                           outcome={"message": "Previous attempt's refusal"})
    h.store.claim("cmd-1", generation)
    h.runtime._ensure_prep_lane = lambda: None
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    job = h.jobs_store.latest_cad_job("cmd-1")
    assert job["status"] == "error" and job["run_number"] is None
    assert cad_of(job)["refusal"] == {"code": "interrupted", "message": INTERRUPTED_MESSAGE}


def test_upgrade_keeps_an_unspecified_legacy_refusal_reason_and_message_unspecified(h):
    _received(h)
    generation = h.store.claim("cmd-1", 0)
    h.store.record_outcome("cmd-1", generation, "needs_user_input")
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    job = h.jobs_store.latest_cad_job("cmd-1")
    assert cad_of(job)["refusal"] == {"code": None, "message": None}
    summary = operation_summary(h.row(), h.jobs_store)
    assert (summary["state"], summary["reason"], summary["message"]) == ("needs_user_input", None, None)


def test_dismissal_finishes_a_job_first_receipt_join_before_deleting_the_refusal(h, monkeypatch):
    from server.cadlink import job_shims
    _received(h)
    generation = h.store.claim("cmd-1", 0)
    h.store.record_outcome("cmd-1", generation, "needs_user_input", reason="setup_required",
                           outcome={"message": "Choose settings"})
    original = job_shims.record_outcome
    def lose_receipt(*args, **kwargs):
        raise RuntimeError("receipt write lost")
    monkeypatch.setattr(job_shims, "record_outcome", lose_receipt)
    with pytest.raises(RuntimeError, match="receipt write lost"):
        h._loop.run(job_shims.accept_operation_solve(h.context(), "cmd-1"))
    root = h.jobs_store.latest_cad_job("cmd-1")["id"]
    assert h.row()["job_id"] is None
    monkeypatch.setattr(job_shims, "record_outcome", original)
    result = h._loop.run(api.post_cancel_cad_operation("cmd-1", _request(h)))
    assert result["state"] == "cancelled" and result["stage"] is None
    assert h.row()["state"] == "accepted" and h.row()["job_id"] == root
    assert h.jobs_store.latest_cad_job("cmd-1") is None
    assert h._loop.run(sweep_pending_solves(h.context())) == 0
