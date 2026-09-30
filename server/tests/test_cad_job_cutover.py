"""S4-F1: production compatibility shims, upgrade and durable acceptance."""

from __future__ import annotations

import json
import sqlite3
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


@pytest.mark.parametrize("crash", [False, True])
def test_bound_pending_cancel_never_submits_and_replays_stop_after_commit(h, monkeypatch, crash):
    from server.cadlink.job_shims import accept_operation_solve
    _received(h)
    generation = h.store.claim("cmd-1", 0)
    request = solve_request_for(validate_setup(_setup(engine="metal")), ingest_id="wgi_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                                manifest_sha256="sha256:" + "1" * 64, artifact_sha256="sha256:" + "2" * 64,
                                acknowledged_findings=[], client_request_id="cad-solve:cmd-1")
    h.store.bind_request("cmd-1", generation, setup_revision_id=_revision(h.store, _setup()), request_json=request.model_dump_json())
    h.store.request_cancel("cmd-1")
    real_durable = h.jobs_store.make_durable
    committed = []
    def durable():
        committed.append(1)
        if crash and len(committed) == 1:
            assert h.jobs_store.latest_cad_job("cmd-1")["status"] == "cancelled"
            raise RuntimeError("crash before stop")
        return real_durable()
    async def submit(*args, **kwargs):
        raise AssertionError("Cancelled bind must never be submitted")
    monkeypatch.setattr(h.jobs_store, "make_durable", durable)
    monkeypatch.setattr(h.runtime, "submit", submit)
    if crash:
        with pytest.raises(RuntimeError, match="crash before stop"):
            h._loop.run(accept_operation_solve(h.context(), "cmd-1"))
        assert h.row()["state"] == "cancel_requested"
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    assert h.jobs_store.latest_cad_job("cmd-1")["status"] == "cancelled"
    assert h.submitted == []
    assert h.jobs_store.replay_events(0)[-1]["type"] == "cancelled"
    assert len(committed) == (2 if crash else 1)


def test_startup_recovers_retention_before_accepting_a_persisted_delivery(h):
    import shutil
    _received(h)
    h.runtime._ensure_prep_lane = lambda: None
    from server.cadlink import solve_command
    solve_command._live_waits["cmd-1"] = solve_command._LiveWait(True, None)
    missing = h.workspace.with_name("unmounted")
    h.workspace.rename(missing)
    assert h._loop.run(sweep_pending_solves(h.context())) == 0
    assert h.row()["state"] == "received" and h.row()["snapshot_json"] is None
    assert h.jobs_store.latest_cad_job("cmd-1") is None
    missing.rename(h.workspace)
    from server.cadlink import solve_command
    solve_command._live_waits.clear()
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    assert h.row()["state"] == "accepted" and h.row()["snapshot_json"] is not None
    shutil.rmtree(h.workspace)
    assert cad_of(h.jobs_store.latest_cad_job("cmd-1"))["snapshot"]


@pytest.mark.parametrize("recovery", ["startup", "delivery"])
def test_interrupted_manual_intake_waits_for_its_first_prepare(h, recovery):
    from server.cadlink.manual_solve import create_manual_solve
    from server.cadlink.preparation import retain_operation_snapshot, run_delivery_pass
    from server.cadlink.ingest import retained_snapshot_path
    _received(h)
    retain_operation_snapshot(h.store, h.data_dir, h.workspace, "cmd-1")
    snapshot = json.loads(h.row()["snapshot_json"])
    record = h.ingest(retained_snapshot_path(h.data_dir, snapshot["manifest_sha256"]), {}, [], h.store, h.data_dir,
                      prep_options={}, commit_guard=lambda conn: True, retained_copy=True)
    create_manual_solve(h.store, h.data_dir, "manual-1", record["ingest_id"])
    h.runtime._ensure_prep_lane = lambda: None
    if recovery == "startup":
        h._loop.run(sweep_pending_solves(h.context()))
    else:
        h._loop.run(run_delivery_pass(h.context(), spawn=lambda *_args: None))
    job = h.jobs_store.latest_cad_job("manual-1")
    assert job["status"] == "preparing" and job["started_at"] is None
    assert cad_of(job)["manual_waiting"] is True
    assert job["config_json"].get("submit") is False
    assert job["id"] not in h.runtime._prep_queue
    assert h.submitted == []


@pytest.mark.parametrize("bad_field,bad_value", [("inputs_json", "{"), ("inputs_json", "{}"), ("request_json", "{}")])
def test_one_malformed_row_between_good_rows_is_refused_and_does_not_block_sweep(h, bad_field, bad_value, caplog):
    from test_cad_preparation import _accept
    bundle, manifest = _received(h)
    _accept(h.store, "cmd-2", bundle, manifest)
    _accept(h.store, "cmd-3", bundle, manifest)
    with h.store._lock, h.store._transaction() as conn:
        conn.execute(f"UPDATE cad_operations SET {bad_field} = ? WHERE operation_id = 'cmd-2'", (bad_value,))
    h.runtime._ensure_prep_lane = lambda: None
    assert h._loop.run(sweep_pending_solves(h.context())) == 3
    for op in ("cmd-1", "cmd-3"):
        assert h.jobs_store.latest_cad_job(op)["status"] == "preparing"
    refused = h.jobs_store.latest_cad_job("cmd-2")
    assert refused["status"] == "error"
    assert cad_of(refused)["refusal"]["code"] == "request_payload_invalid"
    assert "could not be migrated" in cad_of(refused)["refusal"]["message"]
    assert "cmd-2" in caplog.text
    assert h._loop.run(sweep_pending_solves(h.context())) == 0


def test_dismissal_timestamp_is_durable_and_at_least_the_refusals_timestamp(h):
    from datetime import datetime, timezone
    _received(h)
    h.ingest.findings = [{"id": "review", "kind": "warning", "blocking": True}]
    refused = h.prepare(setup_revision_id=_revision(h.store, _setup()))
    # Exercise ordering even when the refusal is ahead of the ledger clock.
    job = h.jobs_store.latest_cad_job("cmd-1")
    h.jobs_store.update_job(job["id"], updated_at="2099-01-01T00:00:00.500000")
    refused = operation_summary(h.row(), h.jobs_store)
    dismissed = h._loop.run(api.post_cancel_cad_operation("cmd-1", _request(h)))
    assert dismissed["state"] == "cancelled"
    assert dismissed["updatedAt"].endswith("Z") and "." not in dismissed["updatedAt"]
    assert dismissed["attemptGeneration"] == refused["attemptGeneration"]
    assert datetime.fromisoformat(dismissed["updatedAt"]).astimezone(timezone.utc) >= datetime.fromisoformat(refused["updatedAt"]).astimezone(timezone.utc)
    assert operation_summary(h.row(), h.jobs_store)["updatedAt"] == dismissed["updatedAt"]


def test_replay_stops_an_existing_keyed_job_when_the_ledger_still_requests_cancel(h):
    _received(h)
    h.store.claim("cmd-1", 0)
    h.store.request_cancel("cmd-1")
    h.jobs_store.create_job_idempotent(
        {"id": "committed-before-stop", "status": "queued", "created_at": "2026-09-01", "updated_at": "2026-09-01",
         "queued_at": "2026-09-01", "config_json": {}, "config_summary_json": {}},
        submission_key="cad-solve:cmd-1", request_sha256="legacy-hash",
    )
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    assert h.jobs_store.get_job_row("committed-before-stop")["status"] == "cancelled"
    assert h.row()["job_id"] == "committed-before-stop"
    assert h.submitted == []


@pytest.mark.parametrize("press", [False, True])
@pytest.mark.parametrize("state", ["received", "processing", "needs_user_input"])
def test_missing_return_after_bound_is_visibly_refused_and_dismissal_never_replays(h, press, state):
    from server.cadlink import solve_command
    from server.cadlink.preparation import run_delivery_pass
    bundle, _ = _received(h)
    if state != "received":
        generation = h.store.claim("cmd-1", 0)
        if state == "needs_user_input":
            h.store.record_outcome("cmd-1", generation, state, reason="preparation_failed", outcome={"message": "Old failure"})
    original = h.workspace / bundle
    hidden = original.with_name("gone")
    original.rename(hidden)
    # An expired HTTP hold, including a client that never retries.
    solve_command._live_waits["cmd-1"] = solve_command._LiveWait(False, 0)
    async def flow():
        if press:
            result = await api.post_prepare_cad_operation("cmd-1", api.PrepareOperationRequest(wait=True), _request(h))
            detail = result["operation"]
        else:
            if state == "received":
                await run_delivery_pass(h.context(), spawn=lambda *_: None)
            else:
                await sweep_pending_solves(h.context())
            await h.runtime.wait_cad_preparations()
            detail = operation_summary(h.row(), h.jobs_store)
        assert detail["state"] == "needs_user_input"
        assert detail["reason"] == "preparation_failed" and detail["message"]
        assert detail["stage"] == "validating"
        dismissed = await api.post_cancel_cad_operation("cmd-1", _request(h))
        assert dismissed["state"] == "cancelled"
        hidden.rename(original)
        assert await sweep_pending_solves(h.context()) == 0
        assert await run_delivery_pass(h.context(), spawn=lambda *_: None) == []
        assert h.jobs_store.latest_cad_job("cmd-1") is None
    h._loop.run(flow())
    assert h.submitted == []


@pytest.mark.parametrize("active", [False, True])
def test_real_cancel_route_wins_while_retention_is_blocked(h, monkeypatch, active):
    import asyncio
    import threading
    from server.cadlink import job_shims
    _received(h)
    if active:
        h.store.claim("cmd-1", 0)
    entered, release = threading.Event(), threading.Event()
    retain = job_shims.retain_operation_snapshot
    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return retain(*args)
    monkeypatch.setattr(job_shims, "retain_operation_snapshot", blocked)
    async def race():
        accepting = asyncio.create_task(job_shims.accept_operation_solve(h.context(), "cmd-1"))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            result = await api.post_cancel_cad_operation("cmd-1", _request(h))
            assert result["state"] in {"cancelled", "cancel_requested"}
        finally:
            release.set()
        await accepting
        await h.runtime.wait_cad_preparations()
    h._loop.run(race())
    job = h.jobs_store.latest_cad_job("cmd-1")
    assert job is None or job["status"] == "cancelled"
    assert h.ingest.calls == h.submitted == []


@pytest.mark.parametrize("invalid", ["changed", "malformed"])
def test_return_refusal_keeps_preparations_exact_stage_and_message(h, invalid):
    bundle, _ = _received(h)
    if invalid == "changed":
        with h.store._lock, h.store._transaction() as conn:
            inputs = json.loads(h.row()["inputs_json"])
            inputs["manifest_sha256"] = "sha256:" + "0" * 64
            conn.execute("UPDATE cad_operations SET inputs_json = ? WHERE operation_id = 'cmd-1'", (json.dumps(inputs),))
        expected = "The return bundle changed after Fusion asked WG to solve it. Send it again from Fusion."
    else:
        (h.workspace / bundle / "manifest.json").write_text("{not json")
        expected = None
    result = h._loop.run(api.post_prepare_cad_operation("cmd-1", api.PrepareOperationRequest(wait=True), _request(h)))["operation"]
    assert result["stage"] == "validating" and result["reason"] == "snapshot_invalid"
    if expected:
        assert result["message"] == expected
    else:
        assert result["message"] and "retained snapshot" not in result["message"]


@pytest.mark.parametrize("recovery", ["startup", "delivery"])
@pytest.mark.parametrize("error", [OSError, sqlite3.OperationalError, RuntimeError, KeyError, TypeError])
def test_each_row_is_isolated_and_code_failures_are_retried_with_bounded_backoff(h, monkeypatch, caplog, recovery, error):
    from server.cadlink import job_shims
    from server.cadlink.preparation import run_delivery_pass
    from test_cad_preparation import _accept
    bundle, manifest = _received(h)
    _accept(h.store, "cmd-2", bundle, manifest)
    _accept(h.store, "cmd-3", bundle, manifest)
    h.runtime._ensure_prep_lane = lambda: None
    real = job_shims.accept_operation_solve
    async def broken(ctx, op, **kwargs):
        if op == "cmd-2":
            raise error("temporary failure")
        return await real(ctx, op, **kwargs)
    monkeypatch.setattr(job_shims, "accept_operation_solve", broken)
    def run():
        return h._loop.run(sweep_pending_solves(h.context()) if recovery == "startup" else run_delivery_pass(h.context(), spawn=lambda *_: None))
    run()
    assert h.jobs_store.latest_cad_job("cmd-1") and h.jobs_store.latest_cad_job("cmd-3")
    assert h.jobs_store.latest_cad_job("cmd-2") is None
    assert "cmd-2" in caplog.text
    attempts, next_retry = h.runtime._cad_admission_retries["cmd-2"]
    assert attempts == 1 and next_retry <= job_shims.time.monotonic() + 60
    monkeypatch.setattr(job_shims, "accept_operation_solve", real)
    h.runtime._cad_admission_retries["cmd-2"] = (attempts, 0)
    run()
    assert h.jobs_store.latest_cad_job("cmd-2")["status"] == "preparing"


def test_bad_payload_fallback_recovers_a_job_committed_before_the_error(h, monkeypatch):
    from server.cadlink import job_shims
    _received(h)
    h.runtime._ensure_prep_lane = lambda: None
    real = job_shims.accept_operation_solve
    async def committed_then_failed(ctx, op, **kwargs):
        await real(ctx, op, **kwargs)
        raise job_shims.BadSolvePayload("failure after commit")
    monkeypatch.setattr(job_shims, "accept_operation_solve", committed_then_failed)
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    assert h.jobs_store.latest_cad_job("cmd-1")["status"] == "preparing"
    assert h.row()["job_id"] == h.jobs_store.job_for_submission_key("cad-solve:cmd-1")


def test_startup_keeps_a_persisted_claim_until_its_file_bound_then_lane_refuses(h, monkeypatch):
    from server.cadlink import solve_command
    from server.cadlink.preparation import run_delivery_pass
    from test_cad_preparation import _deliver_file
    bundle, manifest = _received(h)
    (h.workspace / bundle).rename(h.workspace / "gone")
    delivery = _deliver_file(h, bundle, manifest) / "cmd-1.json"
    claim = delivery.with_name(solve_command.CLAIM_PREFIX + "restart.json")
    delivery.rename(claim)
    assert h._loop.run(sweep_pending_solves(h.context())) == 0
    assert h.row()["state"] == "received" and h.jobs_store.latest_cad_job("cmd-1") is None
    monkeypatch.setattr(solve_command, "RETENTION_PASSES", 1)
    async def expired():
        await run_delivery_pass(h.context(), spawn=lambda *_: None)
        await h.runtime.wait_cad_preparations()
        detail = operation_summary(h.row(), h.jobs_store)
        assert detail["reason"] == "preparation_failed" and detail["message"]
        assert detail["stage"] == "validating"
    h._loop.run(expired())
    assert not claim.exists() and h.submitted == []


def test_prepare_of_manual_solve_with_missing_copy_keeps_damaged_copy_remedy(h):
    import shutil
    from server.cadlink.manual_solve import create_manual_solve
    from server.cadlink.ingest import retained_snapshot_path
    from server.cadlink.preparation import retain_operation_snapshot, DAMAGED_COPY_MESSAGE
    from server.cadlink.job_shims import accept_operation_solve
    _received(h)
    retain_operation_snapshot(h.store, h.data_dir, h.workspace, "cmd-1")
    snapshot = json.loads(h.row()["snapshot_json"])
    path = retained_snapshot_path(h.data_dir, snapshot["manifest_sha256"])
    record = h.ingest(path, {}, [], h.store, h.data_dir, prep_options={}, commit_guard=lambda _conn: True, retained_copy=True)
    create_manual_solve(h.store, h.data_dir, "manual-1", record["ingest_id"])
    h._loop.run(accept_operation_solve(h.context(), "manual-1", manual=True))
    shutil.rmtree(path)
    result = h._loop.run(api.post_prepare_cad_operation("manual-1", api.PrepareOperationRequest(wait=True), _request(h)))["operation"]
    assert result["state"] == "needs_user_input" and result["stage"] == "validating"
    assert result["reason"] == "preparation_failed" and result["message"] == DAMAGED_COPY_MESSAGE
    assert len(h.ingest.calls) == 1 and h.submitted == []


@pytest.mark.parametrize("hold", ["live", "claim"])
def test_dismissal_joins_existing_terminal_job_even_while_delivery_waits(h, monkeypatch, hold):
    import shutil
    from server.cadlink import solve_command
    from server.cadlink.preparation import run_delivery_pass
    from test_cad_preparation import _deliver_file
    bundle, manifest = _received(h)
    h.ingest.findings = [{"id": "review", "kind": "warning", "blocking": True}]
    h.prepare(setup_revision_id=_revision(h.store, _setup()))
    # The job committed, but its receipt join did not survive. Retention is
    # unavailable while the delivery still has a live or persisted hold.
    with h.store._lock, h.store._transaction() as conn:
        conn.execute("UPDATE cad_operations SET state = 'received', job_id = NULL, snapshot_json = NULL WHERE operation_id = 'cmd-1'")
    shutil.rmtree(h.data_dir / "imports" / "bundles")
    original = h.workspace / bundle
    hidden = original.with_name("gone")
    original.rename(hidden)
    if hold == "live":
        monkeypatch.setattr(solve_command, "_live_waits", {"cmd-1": solve_command._LiveWait(True, None)})
    else:
        delivery = _deliver_file(h, bundle, manifest) / "cmd-1.json"
        delivery.rename(delivery.with_name(solve_command.CLAIM_PREFIX + "dismiss.json"))
    async def dismiss():
        assert await sweep_pending_solves(h.context()) == 0
        assert h.row()["state"] == "received"  # Startup still respects the hold.
        result = await api.post_cancel_cad_operation("cmd-1", _request(h))
        assert result["state"] == "cancelled"
        assert h.row()["job_id"]
        assert h.jobs_store.latest_cad_job("cmd-1") is None
        hidden.rename(original)
        solve_command._live_waits.clear()
        assert await sweep_pending_solves(h.context()) == 0
        await run_delivery_pass(h.context(), spawn=lambda *_: None)
        assert h.jobs_store.latest_cad_job("cmd-1") is None
    h._loop.run(dismiss())
    assert h.submitted == []


def test_failed_admission_keeps_claim_then_refuses_at_bound_and_still_retries(h, monkeypatch):
    from server.cadlink import solve_command
    from server.cadlink.preparation import run_delivery_pass
    from test_cad_preparation import _deliver_file
    bundle, manifest = _received(h)
    inbox = _deliver_file(h, bundle, manifest)
    h.runtime._ensure_prep_lane = lambda: None
    real = h.runtime.accept_cad_solve
    async def broken(*args, **kwargs):
        raise sqlite3.OperationalError("admission storage unavailable")
    monkeypatch.setattr(h.runtime, "accept_cad_solve", broken)
    monkeypatch.setattr(solve_command, "RETENTION_PASSES", 6)
    ack = h.data_dir / "ipc" / "wglink" / solve_command.SOLVE_ACKS_DIRECTORY / "cmd-1.json"
    def run():
        return h._loop.run(run_delivery_pass(h.context(), spawn=lambda *_: None))
    for attempt in range(1, 6):
        if attempt > 1:
            h.runtime._cad_admission_retries["cmd-1"] = (attempt - 1, 0)
        assert run() == []
        assert not ack.exists()
        assert list(inbox.glob(solve_command.CLAIM_PREFIX + "*"))
        assert h.row()["state"] == "received"
    summary = operation_summary(h.row(), h.jobs_store)
    assert summary["reason"] == "admission_retrying"
    assert "admission storage unavailable" in summary["message"]
    # The capped backoff stays in effect; reaching the claim bound is not a
    # sixth admission attempt and does not turn the operation terminal.
    assert run() == []
    answer = json.loads(ack.read_text())
    assert answer["outcome"] == "refused" and answer["jobId"] is None
    assert "retry bound" in answer["reason"] and "admission storage unavailable" in answer["reason"]
    assert not list(inbox.glob(solve_command.CLAIM_PREFIX + "*"))
    assert h.row()["state"] == "received"
    monkeypatch.setattr(h.runtime, "accept_cad_solve", real)
    h.runtime._cad_admission_retries["cmd-1"] = (5, 0)
    assert run() == ["cmd-1"]
    assert h.row()["job_id"] and operation_summary(h.row(), h.jobs_store)["reason"] is None


@pytest.mark.parametrize("state", ["processing", "needs_user_input", "cancel_requested"])
def test_delivery_pass_retries_failed_startup_migrations(h, monkeypatch, state):
    from server.cadlink.preparation import run_delivery_pass
    _received(h)
    generation = h.store.claim("cmd-1", 0)
    if state == "needs_user_input":
        h.store.record_outcome("cmd-1", generation, state, reason="findings_need_review",
                               outcome={"message": "Review the original finding."})
    elif state == "cancel_requested":
        h.store.request_cancel("cmd-1")
    h.runtime._ensure_prep_lane = lambda: None
    real = h.runtime.accept_cad_solve
    async def broken(*args, **kwargs):
        raise OSError("transient storage failure")
    monkeypatch.setattr(h.runtime, "accept_cad_solve", broken)
    assert h._loop.run(sweep_pending_solves(h.context())) == 0
    assert h.row()["state"] == state
    monkeypatch.setattr(h.runtime, "accept_cad_solve", real)
    # No hot retry before the deadline.
    assert h._loop.run(run_delivery_pass(h.context(), spawn=lambda *_: None)) == []
    h.runtime._cad_admission_retries["cmd-1"] = (1, 0)
    assert h._loop.run(run_delivery_pass(h.context(), spawn=lambda *_: None)) == ["cmd-1"]
    assert h.row()["state"] == "accepted" and h.row()["job_id"]


def test_continuation_snapshot_refusal_uses_current_stage_despite_old_preparation(h):
    import shutil
    _received(h)
    h.ingest.findings = [{"id": "review", "kind": "warning", "blocking": True}]
    request = _request(h)
    first = h._loop.run(api.post_prepare_cad_operation(
        "cmd-1", api.PrepareOperationRequest(wait=True), request))["operation"]
    assert first["stage"] == "ready" and first["preparationId"]
    shutil.rmtree(h.data_dir / "imports" / "bundles")
    inputs = json.loads(h.row()["inputs_json"])
    (h.workspace / inputs["bundle_path"] / "manifest.json").write_text("{invalid")
    second = h._loop.run(api.post_prepare_cad_operation(
        "cmd-1", api.PrepareOperationRequest(wait=True), request))["operation"]
    assert second["preparationId"] == first["preparationId"]
    assert second["stage"] == "validating"
    assert second["reason"] == "snapshot_invalid" and second["message"]


def test_prepare_during_claim_retention_is_visibly_refused_with_settings(h):
    from fastapi import HTTPException
    from server.cadlink import solve_command
    from test_cad_preparation import _deliver_file
    bundle, manifest = _received(h)
    (h.workspace / bundle).rename(h.workspace / "gone")
    delivery = _deliver_file(h, bundle, manifest) / "cmd-1.json"
    delivery.rename(delivery.with_name(solve_command.CLAIM_PREFIX + "prepare-wait.json"))
    revision = _revision(h.store, _setup())
    with pytest.raises(HTTPException) as caught:
        h._loop.run(api.post_prepare_cad_operation(
            "cmd-1", api.PrepareOperationRequest(setupRevisionId=revision, frameAxis="+x", submit=False,
                                                 approvals={"preparationId": "old-prep", "findingIds": ["review"]}),
            _request(h)))
    assert caught.value.status_code == 409
    assert "press Prepare again with these settings" in caught.value.detail
    assert h.jobs_store.latest_cad_job("cmd-1") is None


def test_real_cancel_before_admission_is_refused_when_delivery_is_collected(h):
    from server.cadlink import solve_command
    from server.cadlink.preparation import run_delivery_pass
    from test_cad_preparation import _deliver_file
    bundle, manifest = _received(h)
    inbox = _deliver_file(h, bundle, manifest)
    async def cancel_then_collect():
        result = await api.post_cancel_cad_operation("cmd-1", _request(h))
        assert result["state"] == "cancelled"
        assert h.row()["job_id"] is None
        assert await run_delivery_pass(h.context(), spawn=lambda *_: None) == []
    h._loop.run(cancel_then_collect())
    ack_path = h.data_dir / "ipc" / "wglink" / solve_command.SOLVE_ACKS_DIRECTORY / "cmd-1.json"
    ack = json.loads(ack_path.read_text())
    assert ack["outcome"] == "refused" and ack["jobId"] is None
    assert ack["reason"] == "This solve was cancelled before admission."
    assert h.row()["state"] == "cancelled"
    assert not list(inbox.iterdir())
    assert h.jobs_store.list_jobs()[1] == 0
    assert h.ingest.calls == h.submitted == []
