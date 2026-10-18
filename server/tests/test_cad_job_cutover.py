"""S4-F1: production compatibility shims, upgrade and durable acceptance."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from cad_backends import JobsHarness, compat_prepare
from server.cadlink import api
from server.cadlink.joblane.job_shims import sweep_pending_solves
from server.cadlink.operations import REASON_CODES
from server.cadlink.joblane.preparation import operation_summary
from server.cadlink.joblane.setup import solve_request_for, validate_setup
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
    from server.cadlink.intake.ingest import meshing_semantics_fingerprint
    from server.cadlink.joblane.preparation import retain_operation_snapshot

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
        first = await compat_prepare(request.app.state, "cmd-1", setup_revision_id=revision, wait=True)
        assert first["operation"]["reason"] == "findings_need_review"
        assert h.row()["state"] == "accepted"  # ledger acceptance, not preparation state
        listed = await api.list_cad_operations(request, pending=True, limit=100)
        detail = await api.get_cad_operation("cmd-1", request)
        assert listed["operations"][0]["state"] == detail["state"] == "needs_user_input"
        assert detail["stage"] == "ready"
        prep = detail["preparation"]["preparationId"]
        # The approval the jobs route records (POST /api/jobs/{id}/approvals).
        job = h.jobs_store.latest_cad_job("cmd-1")
        h.jobs_store.approve_cad_preparation(job["id"], prep, ["review-1"])
        approved = await api.get_cad_operation("cmd-1", request)
        assert approved["approvals"] == [{"preparation_id": prep, "finding_id": "review-1"}]
        resumed = await compat_prepare(request.app.state, "cmd-1", wait=True)
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
    from server.cadlink.intake.solve_command import SOLVE_ACKS_DIRECTORY

    bundle, manifest = _write_return(h.workspace)
    _deliver_file(h, bundle, manifest)
    h.runtime._ensure_prep_lane = lambda: None
    from server.cadlink.joblane.job_shims import run_delivery_pass
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


def test_a_second_manual_press_cannot_replace_a_preparing_intent(h):
    from server.cadlink.joblane.job_shims import accept_operation_solve
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
    from server.cadlink.joblane.job_shims import accept_operation_solve
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
    from server.cadlink.joblane.preparation import PreparationInput
    from server.cadlink.joblane.job_shims import accept_operation_solve
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
    from server.tests._asgi import _post
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


@pytest.mark.parametrize("crash", [False, True])
def test_bound_pending_cancel_never_submits_and_replays_stop_after_commit(h, monkeypatch, crash):
    from server.cadlink.joblane.job_shims import accept_operation_solve
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
    bundle, manifest = _received(h)
    from test_cad_preparation import _deliver_file
    delivery = _deliver_file(h, bundle, manifest) / "cmd-1.json"
    from server.cadlink.intake import solve_command
    claim = delivery.with_name(solve_command.CLAIM_PREFIX + "retention.json")
    delivery.rename(claim)
    h.runtime._ensure_prep_lane = lambda: None
    missing = h.workspace.with_name("unmounted")
    h.workspace.rename(missing)
    assert h._loop.run(sweep_pending_solves(h.context())) == 0
    assert h.row()["state"] == "received" and h.row()["snapshot_json"] is None
    assert h.jobs_store.latest_cad_job("cmd-1") is None
    missing.rename(h.workspace)
    claim.unlink()
    assert h._loop.run(sweep_pending_solves(h.context())) == 1
    assert h.row()["state"] == "accepted" and h.row()["snapshot_json"] is not None
    shutil.rmtree(h.workspace)
    assert cad_of(h.jobs_store.latest_cad_job("cmd-1"))["snapshot"]


@pytest.mark.parametrize("recovery", ["startup", "delivery"])
def test_interrupted_manual_intake_waits_for_its_first_prepare(h, recovery):
    from server.cadlink.joblane.manual_solve import create_manual_solve
    from server.cadlink.joblane.preparation import retain_operation_snapshot
    from server.cadlink.joblane.job_shims import run_delivery_pass
    from server.cadlink.intake.ingest import retained_snapshot_path
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
    result = h._loop.run(compat_prepare(_request(h).app.state, "cmd-1", wait=True))["operation"]
    assert result["stage"] == "validating" and result["reason"] == "snapshot_invalid"
    if expected:
        assert result["message"] == expected
    else:
        assert result["message"] and "retained snapshot" not in result["message"]


@pytest.mark.parametrize("recovery", ["startup", "delivery"])
@pytest.mark.parametrize("error", [OSError, sqlite3.OperationalError, RuntimeError, KeyError, TypeError])
def test_each_row_is_isolated_and_code_failures_are_retried_with_bounded_backoff(h, monkeypatch, caplog, recovery, error):
    from server.cadlink.joblane import job_shims
    from server.cadlink.joblane.job_shims import run_delivery_pass
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
    from server.cadlink.joblane import job_shims
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
    from server.cadlink.intake import solve_command
    from server.cadlink.joblane.job_shims import run_delivery_pass
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
    from server.cadlink.joblane.manual_solve import create_manual_solve
    from server.cadlink.intake.ingest import retained_snapshot_path
    from server.cadlink.joblane.preparation import retain_operation_snapshot, DAMAGED_COPY_MESSAGE
    from server.cadlink.joblane.job_shims import accept_operation_solve
    _received(h)
    retain_operation_snapshot(h.store, h.data_dir, h.workspace, "cmd-1")
    snapshot = json.loads(h.row()["snapshot_json"])
    path = retained_snapshot_path(h.data_dir, snapshot["manifest_sha256"])
    record = h.ingest(path, {}, [], h.store, h.data_dir, prep_options={}, commit_guard=lambda _conn: True, retained_copy=True)
    create_manual_solve(h.store, h.data_dir, "manual-1", record["ingest_id"])
    h._loop.run(accept_operation_solve(h.context(), "manual-1", manual=True))
    shutil.rmtree(path)
    result = h._loop.run(compat_prepare(_request(h).app.state, "manual-1", wait=True))["operation"]
    assert result["state"] == "needs_user_input" and result["stage"] == "validating"
    assert result["reason"] == "preparation_failed" and result["message"] == DAMAGED_COPY_MESSAGE
    assert len(h.ingest.calls) == 1 and h.submitted == []


@pytest.mark.parametrize("decision", ["accepted", "refused"])
def test_failed_admission_stays_unconfirmed_until_a_durable_decision(h, monkeypatch, decision):
    from server.cadlink.intake import solve_command
    from server.cadlink.joblane.job_shims import run_delivery_pass
    from test_cad_preparation import _deliver_file
    bundle, manifest = _received(h)
    inbox = _deliver_file(h, bundle, manifest)
    h.runtime._ensure_prep_lane = lambda: None
    real = h.runtime.accept_cad_solve
    async def broken(*args, **kwargs):
        raise sqlite3.OperationalError("admission storage unavailable")
    monkeypatch.setattr(h.runtime, "accept_cad_solve", broken)
    monkeypatch.setattr(solve_command, "RETENTION_PASSES", 2)
    monkeypatch.setattr(solve_command, "ACK_FAILURE_PASSES", 2)
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
    # Beyond both former delivery bounds, backoff keeps the claim and emits
    # no answer. The pending sweep must not bypass this hold in the same pass.
    assert run() == []
    assert not ack.exists()
    assert list(inbox.glob(solve_command.CLAIM_PREFIX + "*"))
    assert h.runtime._cad_admission_retries["cmd-1"][0] == 5
    assert h.row()["state"] == "received"
    assert h.jobs_store.latest_cad_job("cmd-1") is None
    monkeypatch.setattr(h.runtime, "accept_cad_solve", real)
    h.runtime._cad_admission_retries["cmd-1"] = (5, 0)
    if decision == "refused":
        solve_command.record_outcome(h.store, "cmd-1", state="refused", reason="The request is invalid.")
    assert run() == (["cmd-1"] if decision == "accepted" else [])
    answer = json.loads(ack.read_text())
    assert answer["outcome"] == decision
    assert not list(inbox.glob(solve_command.CLAIM_PREFIX + "*"))
    if decision == "accepted":
        assert answer["jobId"] == h.row()["job_id"]
        assert answer["jobId"] and operation_summary(h.row(), h.jobs_store)["reason"] is None
    else:
        assert answer["reason"] == "The request is invalid." and answer["jobId"] is None
        assert h.row()["state"] == "rejected"
    assert run() == []
    assert h.jobs_store.list_jobs()[1] == (1 if decision == "accepted" else 0)
    assert json.loads(ack.read_text())["outcome"] == decision


@pytest.mark.parametrize("state", ["processing", "needs_user_input", "cancel_requested"])
def test_delivery_pass_retries_failed_startup_migrations(h, monkeypatch, state):
    from server.cadlink.joblane.job_shims import run_delivery_pass
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
    first = h._loop.run(compat_prepare(request.app.state, "cmd-1", wait=True))["operation"]
    assert first["stage"] == "ready" and first["preparationId"]
    shutil.rmtree(h.data_dir / "imports" / "bundles")
    inputs = json.loads(h.row()["inputs_json"])
    (h.workspace / inputs["bundle_path"] / "manifest.json").write_text("{invalid")
    second = h._loop.run(compat_prepare(request.app.state, "cmd-1", wait=True))["operation"]
    assert second["preparationId"] == first["preparationId"]
    assert second["stage"] == "validating"
    assert second["reason"] == "snapshot_invalid" and second["message"]


def test_prepare_during_claim_retention_is_visibly_refused_with_settings(h):
    from server.cadlink.intake import solve_command
    from test_cad_preparation import _deliver_file
    bundle, manifest = _received(h)
    (h.workspace / bundle).rename(h.workspace / "gone")
    delivery = _deliver_file(h, bundle, manifest) / "cmd-1.json"
    delivery.rename(delivery.with_name(solve_command.CLAIM_PREFIX + "prepare-wait.json"))
    revision = _revision(h.store, _setup())
    with pytest.raises(ValueError, match="press Prepare again with these settings"):
        h._loop.run(compat_prepare(_request(h).app.state, "cmd-1", setup_revision_id=revision, frame_axis="+x",
                                   submit=False, approvals=("old-prep", ["review"])))
    assert h.jobs_store.latest_cad_job("cmd-1") is None


@pytest.mark.parametrize("status, reason, stage, expected", [
    ("preparing", None, "received", "received"),
    ("preparing", None, "preparing-mesh", "processing"),
    ("preparing", None, "waiting-for-update-restart", "needs_user_input"),
    *[("error", reason, "error", state) for reason, state in REASON_CODES.items()
      if state in {"needs_user_input", "rejected"}],
    ("cancelled", None, "cancelled", "cancelled"),
    ("queued", None, "queued", "accepted"),
])
def test_f2_cad_state_matches_the_operation_view_using_only_the_job(h, status, reason, stage, expected):
    from server.jobs.cad_intent import CadSolveIntent
    from server.jobs.models import CadState

    _received(h)
    intent = CadSolveIntent(operation_id="cmd-1", bundle_path="wgreturn/model.wgreturn", manifest_sha256="sha256:" + "1" * 64, return_id="return")
    record = h.runtime._preparing_record(intent)
    cad = record["task_metadata"]["cad"]
    cad.update(
        snapshot={"document_name": "Speaker", "manifest_sha256": "sha256:" + "1" * 64,
                  "artifact_sha256": "sha256:" + "2" * 64, "project_lineage_id": "lineage"},
        setup={"revision_id": "rev", "digest": "sha256:" + "3" * 64, "origin": "wg_defaults"},
        frame={"axis": "+x", "provenance": "automatic"}, last_stage="ready",
        preparation={"preparation_id": "prep", "ingest_id": "ingest", "blocking_finding_ids": ["review"], "report_sha256": "report",
                     "approvals": [{"preparation_id": "prep", "finding_id": "review"}]},
    )
    record.update(status=status, stage=stage, stage_message="Restart pending", started_at="2026-10-01T00:00:00Z" if stage == "preparing-mesh" else None)
    if reason:
        cad["refusal"] = {"code": reason, "message": "Refused precisely"}
    if status == "queued":
        record["config_json"] = {"geometry": {"type": "imported"}}
    job_id, _, _ = h.jobs_store.create_job_idempotent(record, submission_key="cad-solve:cmd-1", request_sha256="delivery")
    job = h.jobs_store.get_job_row(job_id)
    state = h.runtime._serialize_job(job)["cad_state"]
    assert CadState.model_validate(state).model_dump() == state
    summary = operation_summary(h.row(), h.jobs_store)
    assert state["state"] == summary["state"] == expected
    for job_field, operation_field in [("operation_id", "operationId"), ("job_id", "jobId"), ("setup_defaults", "setupDefaults"), ("frame_axis_automatic", "frameAxisAutomatic"), ("updated_at", "updatedAt")]:
        assert state[job_field] == summary[operation_field]
    for field in ("stage", "reason", "message"):
        assert state[field] == summary[field]
    assert state["received_at"] == job["created_at"]
    assert state["snapshot"] == cad["snapshot"]
    assert state["preparation"] == {k: cad["preparation"][k] for k in ("preparation_id", "ingest_id", "blocking_finding_ids", "report_sha256")}
    assert state["approvals"] == cad["preparation"]["approvals"]
    h.store.close()  # the read model cannot consult the operation ledger
    assert h.runtime._serialize_job(job)["cad_state"] == state


@pytest.mark.parametrize("record_kind", ["direct", "old_cad", "bare_intent"])
def test_f2_cad_state_keeps_missing_historical_data_null(h, record_kind):
    from server.jobs.cad_intent import CadSolveIntent

    intent = CadSolveIntent(operation_id="old", bundle_path="old.wgreturn", manifest_sha256="sha256:" + "1" * 64, return_id="return")
    row = h.runtime._preparing_record(intent)
    if record_kind != "bare_intent":
        row.update(status="queued", config_json={})
    if record_kind in {"direct", "bare_intent"}:
        row["task_metadata"] = {}
    state = h.runtime._serialize_job(row)["cad_state"]
    if record_kind == "direct":
        assert state is None
    else:
        assert state["operation_id"] == "old"
        assert state["snapshot"] is state["preparation"] is None
        assert state["approvals"] == []


@pytest.mark.parametrize("path", ["reconcile", "compat", "sweep", "delivery", "malformed"])
@pytest.mark.parametrize("origin, provenance, axis", [("wg_defaults", "automatic", "+x"), ("user", "chosen", None)])
def test_f2_acceptance_joins_copy_the_jobs_defaults_and_automatic_axis(h, monkeypatch, path, origin, provenance, axis):
    from server.cadlink.joblane import job_shims
    from server.cadlink.joblane.preparation import reconcile_with_jobs
    from server.cadlink.joblane.job_shims import run_delivery_pass
    from server.jobs.cad_intent import CadSolveIntent

    _received(h)
    record = h.runtime._preparing_record(CadSolveIntent(operation_id="cmd-1", bundle_path="unused.wgreturn", manifest_sha256="sha256:" + "1" * 64, return_id="return"))
    record.update(status="queued", config_json={})
    record["task_metadata"]["cad"].update(setup={"origin": origin}, frame={"axis": "+x", "provenance": provenance})
    job_id, _, _ = h.jobs_store.create_job_idempotent(record, submission_key="cad-solve:cmd-1", request_sha256="old-request")
    ctx = h.context()
    ctx.job_for_submission = h.jobs_store.job_for_submission_key
    if path == "reconcile":
        reconcile_with_jobs(ctx, "cmd-1")
    elif path == "compat":
        h._loop.run(job_shims.accept_operation_solve(ctx, "cmd-1", manual=True))
    elif path == "sweep":
        h._loop.run(sweep_pending_solves(ctx))
    elif path == "malformed":
        async def interrupted_join(*args, **kwargs):
            raise job_shims.BadSolvePayload("broken legacy inputs")
        monkeypatch.setattr(job_shims, "accept_operation_solve", interrupted_join)
        h._loop.run(job_shims.accept_operation_solve_isolated(ctx, "cmd-1"))
    else:
        h._loop.run(run_delivery_pass(ctx, spawn=lambda *_args: None))
    outcome = json.loads(h.row()["outcome_json"] or "{}")
    assert "cad_state" in h.runtime._serialize_job(h.jobs_store.get_job_row(job_id))
    assert outcome.get("setup_defaults", False) == (origin == "wg_defaults")
    assert outcome.get("frame_axis_automatic") == axis
    assert h.row()["job_id"] == job_id


def test_f2_early_ledger_acceptance_reads_provenance_from_the_later_bound_job(h):
    from server.cadlink.joblane.job_shims import accept_operation_solve

    _received(h)
    h.runtime._ensure_prep_lane = lambda: None
    job_id = h._loop.run(accept_operation_solve(h.context(), "cmd-1", manual=True))
    early = h.row()["outcome_json"]
    cad = dict(cad_of(h.jobs_store.get_job_row(job_id)))
    cad.update(setup={"origin": "wg_defaults"}, frame={"axis": "+y", "provenance": "automatic"})
    waiting = h.jobs_store.get_job_row(job_id)
    assert h.jobs_store.admit_cad_press(job_id, waiting["config_json"])
    assert h.jobs_store.claim_preparing_job(job_id, stage="ready", stage_message="Prepared", progress=0.09)
    cad.pop("manual_waiting", None)
    request = solve_request_for(validate_setup(_setup(engine="metal")), ingest_id="wgi_" + "0" * 26,
                                manifest_sha256="sha256:" + "1" * 64, artifact_sha256="sha256:" + "2" * 64,
                                acknowledged_findings=[])
    assert h._loop.run(h.runtime._bind_cad_job(job_id, request, cad)) == "bound"
    summary = operation_summary(h.row(), h.jobs_store)
    assert summary["setupDefaults"] and summary["frameAxisAutomatic"] == "+y"
    assert h.runtime._serialize_job(h.jobs_store.get_job_row(job_id))["cad_state"]["setup_defaults"]
    assert json.loads(early or "{}").get("frame_axis_automatic") is None
    assert h.row()["outcome_json"] == early


def test_f2_solve_again_carries_setup_frame_preparation_and_operation(h):
    from server.jobs.cad_intent import CadSolveIntent

    h.runtime._ensure_prep_lane = lambda: None
    cad = {"setup": {"revision_id": "setup", "origin": "user"}, "frame": {"axis": "+x", "provenance": "chosen"},
           "preparation": {"preparation_id": "prep", "approvals": []}}
    async def flow():
        parent = await h.runtime.accept_cad_solve(
            CadSolveIntent(operation_id="cmd-1", bundle_path="unavailable.wgreturn", manifest_sha256="sha256:" + "1" * 64, return_id="return"),
            "cad-solve:cmd-1", prepare=False, cad_record=cad,
            refusal={"code": "findings_need_review", "message": "Review"},
        )
        child = await h.runtime.solve_cad_again(parent)
        assert cad_of(h.jobs_store.get_job_row(child)) == {**cad, "operation_id": "cmd-1", "solve_again_press_sha256": cad_of(h.jobs_store.get_job_row(child))["solve_again_press_sha256"]}
    h._loop.run(flow())


@pytest.mark.parametrize("press", [False, True])
@pytest.mark.parametrize("state", ["received", "processing", "needs_user_input"])
def test_missing_return_after_bound_is_visibly_refused(h, press, state):
    from server.cadlink.joblane.job_shims import run_delivery_pass
    bundle, _ = _received(h)
    if state != "received":
        generation = h.store.claim("cmd-1", 0)
        if state == "needs_user_input":
            h.store.record_outcome("cmd-1", generation, state, reason="preparation_failed", outcome={"message": "Old failure"})
    original = h.workspace / bundle
    original.rename(original.with_name("gone"))
    async def flow():
        if press:
            detail = (await compat_prepare(_request(h).app.state, "cmd-1", wait=True))["operation"]
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
    h._loop.run(flow())
    assert h.submitted == []


def test_f2_solve_again_with_another_axis_does_not_carry_the_parents_frame(h):
    from server.jobs.cad_intent import CadSolveIntent

    h.runtime._ensure_prep_lane = lambda: None
    cad = {"setup": {"revision_id": "setup", "origin": "user"}, "frame": {"axis": "+y", "provenance": "automatic"},
           "preparation": {"preparation_id": "prep", "approvals": []}}
    async def flow():
        parent = await h.runtime.accept_cad_solve(
            CadSolveIntent(operation_id="cmd-2", bundle_path="unavailable.wgreturn", manifest_sha256="sha256:" + "2" * 64, return_id="return"),
            "cad-solve:cmd-2", prepare=False, cad_record=cad,
            refusal={"code": "findings_need_review", "message": "Review"},
        )
        same = await h.runtime.solve_cad_again(parent, frame_axis="+y")
        assert cad_of(h.jobs_store.get_job_row(same))["frame"] == cad["frame"]
    h._loop.run(flow())

    async def other():
        parent = await h.runtime.accept_cad_solve(
            CadSolveIntent(operation_id="cmd-3", bundle_path="unavailable.wgreturn", manifest_sha256="sha256:" + "3" * 64, return_id="return"),
            "cad-solve:cmd-3", prepare=False, cad_record=cad,
            refusal={"code": "frame_confirmation_required", "message": "Frame"},
        )
        child = await h.runtime.solve_cad_again(parent, frame_axis="+x")
        record = cad_of(h.jobs_store.get_job_row(child))
        assert "frame" not in record
        assert h.runtime._serialize_job(h.jobs_store.get_job_row(child))["cad_state"]["frame_axis_automatic"] is None
    h._loop.run(other())


def test_review_compatibility_details_fill_a_partial_historical_job_record(h):
    from server.cadlink.intake.ingest import meshing_semantics_fingerprint
    from server.cadlink.joblane.preparation import retain_operation_snapshot
    from server.jobs.cad_intent import CadSolveIntent

    _received(h)
    revision = _revision(h.store, _setup(engine="metal"))
    generation = h.store.claim("cmd-1", 0)
    retain_operation_snapshot(h.store, h.data_dir, h.workspace, "cmd-1")
    snapshot = json.loads(h.row()["snapshot_json"])
    retained = h.data_dir / "imports" / "bundles" / (snapshot["manifest_sha256"].removeprefix("sha256:") + ".wgreturn")
    ingest = h.ingest(retained, {}, [], h.store, h.data_dir, prep_options={}, commit_guard=lambda conn: True, retained_copy=True)
    prep_id = ingest["ingest_id"]
    h.store.advance_operation("cmd-1", generation, setup_revision_id=revision)
    h.store.record_preparation("cmd-1", generation, preparation_id=prep_id, ingest_id=prep_id,
                               snapshot_sha256=snapshot["manifest_sha256"], setup_revision_id=revision,
                               report_sha256=ingest["report_sha256"], blocking_finding_ids=["review-1"],
                               meshing_semantics=meshing_semantics_fingerprint())
    h.store.add_approvals("cmd-1", prep_id, ["review-1"])
    record = h.runtime._preparing_record(CadSolveIntent(operation_id="cmd-1", bundle_path="old.wgreturn",
                                                      manifest_sha256=snapshot["manifest_sha256"], return_id="return"))
    record.update(status="queued", config_json={})
    record["task_metadata"]["cad"] = {"operation_id": "cmd-1"}
    job_id, _, _ = h.jobs_store.create_job_idempotent(record, submission_key="cad-solve:cmd-1", request_sha256="old")
    job = h.runtime._serialize_job(h.jobs_store.get_job_row(job_id))
    assert job["cad_state"]["snapshot"] is job["cad_state"]["preparation"] is None
    detail = h._loop.run(api.get_cad_operation("cmd-1", _request(h)))
    assert detail["snapshot"]["manifestSha256"] == snapshot["manifest_sha256"]
    assert detail["preparationId"] == prep_id
    assert detail["preparation"]["ingestId"] == prep_id
    assert detail["preparation"]["reportSha256"] == ingest["report_sha256"]
    assert detail["setupRevisionId"] == revision
    assert detail["approvals"] == [{"preparation_id": prep_id, "finding_id": "review-1"}]
