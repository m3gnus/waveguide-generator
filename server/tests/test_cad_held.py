"""User choices are durable CAD requests, including across the old error format."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from server.jobs.cad_preparation import job_cad_state
from server.jobs.models import CadSolveAgainRequest
from server.jobs.runtime import JobConflictError
from server.jobs.store import JobStore
from server.mesh.builder import _enforce_artifact_triangle_ceiling
from server.mesh.errors import MeshTooLarge

from cad_backends import JobsHarness
from test_cad_preparation import _received, _revision, _setup
from test_job_cad_lane import _cad, _row, _settle, _second_return


@pytest.fixture
def h(tmp_path: Path):
    harness = JobsHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def test_clear_failed_preserves_new_and_legacy_holds(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    job = h.latest_job()
    state = job_cad_state(job)
    assert state is not None and state["held"] is True
    assert state["next_step"] and state["options"]
    assert (job["status"], job["stage"], job["stage_message"]) == ("held", "held", "Waiting for you")
    assert _cad(job)["hold"]["code"] == "setup_required"
    assert h._loop.run(h.runtime.clear_failed()) == []
    assert h.jobs_store.get_job_row(job["id"]) is not None
    # Earlier releases stored precisely the same refusal as an error.
    metadata = job["task_metadata"]
    metadata["cad"].pop("hold")
    h.jobs_store.update_job(job["id"], status="error", stage="error", task_metadata_json=json.dumps(metadata))
    old = _row(h, job["id"])
    assert job_cad_state(old)["held"] is True
    assert h._loop.run(h.runtime.clear_failed()) == []
    # A genuine error without a refusal still clears.
    metadata["cad"].pop("refusal")
    h.jobs_store.update_job(job["id"], task_metadata_json=json.dumps(metadata))
    assert job_cad_state(_row(h, job["id"]))["held"] is False
    assert h._loop.run(h.runtime.clear_failed()) == [job["id"]]


def test_a_prepared_request_is_ready_survives_clear_failed_and_continues(h: JobsHarness) -> None:
    _received(h)
    revision = _revision(h.store, _setup())
    summary = h.prepare(setup_revision_id=revision, submit=False)
    assert (summary["reason"], summary["attention"]) == ("ready_to_solve", "ready")
    ready = h.latest_job()
    state = job_cad_state(ready)
    # Kept and ready: not failed, not a question, no error recorded.
    assert (ready["status"], ready["stage"], ready["stage_message"]) == ("held", "held", "Ready to solve")
    assert ready["error_message"] is None
    assert state["held"] is True and state["prepare_failed"] is False
    assert state["attention"] == "ready"
    assert state["options"][0] == {"id": "retry", "label": "Solve"}
    events = [event for event in h.jobs_store.replay_events(0) or [] if event["jobId"] == ready["id"]]
    assert events and all(event["type"] != "failed" for event in events)
    assert events[-1]["payload"]["message"] == "Ready to solve"
    preparation_id = state["preparation"]["preparation_id"]

    # A genuine failure of another model, then Clear failed.
    _second_return(h)

    def crash(*_args, **_kwargs):
        raise RuntimeError("gmsh crashed")

    h.prepare("cmd-2", setup_revision_id=revision, ingest=crash)
    failed = h.jobs_store.latest_cad_job("cmd-2")
    assert failed["status"] == "error"
    assert h._loop.run(h.runtime.clear_failed()) == [failed["id"]]
    kept = _row(h, ready["id"])
    assert kept["status"] == "held" and job_cad_state(kept)["attention"] == "ready"

    # Solve continues it with the same prepared mesh.
    child = h._loop.run(h.runtime.solve_cad_again(ready["id"]))
    _settle(h)
    row = _row(h, child)
    assert row["status"] == "queued"
    assert _cad(row)["preparation"]["preparation_id"] == preparation_id


def test_legacy_ready_rows_stored_as_errors_read_ready_and_survive_clear_failed(h: JobsHarness) -> None:
    _received(h)
    h.prepare(setup_revision_id=_revision(h.store, _setup()), submit=False)
    job = h.latest_job()
    metadata = job["task_metadata"]
    metadata["cad"].pop("hold")
    # Earlier releases stored the prepared request as an error, through the refusal path.
    h.jobs_store.update_job(job["id"], status="error", stage="error", stage_message="Not solved",
                            error_message="Prepared. Press Solve to start it.",
                            task_metadata_json=json.dumps(metadata))
    state = job_cad_state(_row(h, job["id"]))
    assert state["held"] is True and state["prepare_failed"] is False
    assert state["attention"] == "ready"
    assert h._loop.run(h.runtime.clear_failed()) == []
    assert h.jobs_store.get_job_row(job["id"]) is not None


@pytest.mark.parametrize(("code", "attention", "stage_message"), [
    ("ready_to_solve", "ready", "Ready to solve"),
    ("mesh_too_large", "question", "Waiting for you"),
    ("frame_confirmation_required", "action", "Needs a change"),
    ("engine_cannot_solve_return", "action", "Needs a change"),
])
def test_a_migrated_refusal_is_kept_as_the_one_classifier_says(
    h: JobsHarness, code: str, attention: str, stage_message: str,
) -> None:
    """An operation from an earlier release, accepted on first launch with its refusal."""

    from server.jobs.cad_intent import CadSolveIntent

    h.runtime._ensure_prep_lane = lambda: None
    job_id = h._loop.run(h.runtime.accept_cad_solve(
        CadSolveIntent(operation_id="cmd-legacy", bundle_path="old.wgreturn",
                       manifest_sha256="sha256:" + "4" * 64, return_id="return"),
        "cad-solve:cmd-legacy", prepare=False, cad_record={},
        refusal={"code": code, "message": "Stored words"},
    ))
    row = _row(h, job_id)
    state = job_cad_state(row)
    assert (row["status"], row["stage_message"], row["error_message"]) == ("held", stage_message, None)
    assert state["held"] is True and state["attention"] == attention
    assert h._loop.run(h.runtime.clear_failed()) == []


def test_solve_anyway_carries_only_while_the_preparation_is_the_same() -> None:
    from server.jobs.cad_intent import CadSolveIntent, solve_again_intent

    intent = CadSolveIntent(operation_id="cmd-1", bundle_path="b.wgreturn", manifest_sha256="sha256:" + "1" * 64,
                            return_id="r", setup_revision_id="rev-a", frame_axis="+z", mesh_limit_override=True)
    parent = {"id": "parent", "config_json": intent.to_config(),
              "task_metadata": {"cad": {"setup": {"revision_id": "rev-a"},
                                        "mesh_limit_override": {"accepted": True, "source": "solve_anyway"}}}}
    assert solve_again_intent(parent).mesh_limit_override is True
    assert solve_again_intent(parent, setup_revision_id="rev-a", frame_axis="+z").mesh_limit_override is True
    # Other settings, or another axis, make another mesh: the ceiling asks again.
    assert solve_again_intent(parent, setup_revision_id="rev-b").mesh_limit_override is False
    assert solve_again_intent(parent, frame_axis="+y").mesh_limit_override is False
    # A new Solve anyway on the new preparation is its own choice.
    assert solve_again_intent(parent, setup_revision_id="rev-b", mesh_limit_override=True).mesh_limit_override is True


def _lineage(h: JobsHarness, job_id: str, lineage: str) -> None:
    row = _row(h, job_id)
    metadata = row["task_metadata"]
    metadata["cad"].setdefault("snapshot", {})["project_lineage_id"] = lineage
    h.jobs_store.update_job(job_id, task_metadata_json=json.dumps(metadata))


def test_newer_request_retires_only_older_holds_of_its_lineage(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    first = h.latest_job()["id"]
    _lineage(h, first, "project-a")
    # Accept another Fusion press without allowing its lane to race the setup.
    h.blocked = "Update pending"
    _second_return(h)
    second = h._loop.run(h.runtime.accept_cad_solve(
        h._intent("cmd-2"), "cad-solve:cmd-2", prepare=False,
        cad_record={"snapshot": {"project_lineage_id": "project-a"}},
    ))
    assert second != first
    retired = _row(h, first)
    assert retired["status"] == "cancelled" and retired["stage"] == "superseded"
    assert job_cad_state(retired)["reason"] == "superseded"
    assert not job_cad_state(retired)["held"]
    assert _cad(retired)["superseded_by"] == second
    with pytest.raises(JobConflictError, match="replaced"):
        h._loop.run(h.runtime.solve_cad_again(first))
    _lineage(h, second, "project-b")
    # An older preparation finishing after the newer press is retired too.
    h.jobs_store.update_job(first, status="preparing", cancellation_requested=False)
    h.jobs_store.refuse_preparing_job(first, code="setup_required", message="Choose settings")
    assert h.jobs_store.retire_held_cad_requests(first) == []  # unrelated lineage
    _lineage(h, second, "project-a")
    assert len(h.jobs_store.retire_held_cad_requests(first)) == 1
    assert _row(h, first)["status"] == "cancelled"


def test_solve_anyway_continues_and_records_explicit_override(h: JobsHarness) -> None:
    _received(h)
    seen = []
    original = h.ingest

    def mesh(*args, **kwargs):
        override = kwargs["prep_options"].get("mesh_limit_override", False)
        seen.append(override)
        _enforce_artifact_triangle_ceiling(28_944, mesh_limit_override=override)
        return original(*args, **kwargs)

    summary = h.prepare(setup_revision_id=_revision(h.store, _setup()), ingest=mesh)
    parent = h.latest_job()
    assert summary["reason"] == "mesh_too_large"
    state = job_cad_state(parent)
    assert state["options"][0] == {"id": "solve_anyway", "label": "Solve anyway"}
    assert "Sources & Mesh…" in state["next_step"] and "model size" in state["next_step"]
    h._ingest_override = mesh
    child = h._loop.run(h.runtime.solve_cad_again(parent["id"], **CadSolveAgainRequest(mesh_limit_override=True).press()))
    _settle(h)
    row = _row(h, child)
    assert row["status"] == "queued" and row["run_number"] == 1
    assert _cad(row)["mesh_limit_override"] == {"accepted": True, "source": "solve_anyway"}
    assert seen == [False, True]
    # The continuation answered the question: the parent is retired, not held.
    retired = _row(h, parent["id"])
    assert retired["status"] == "cancelled" and _cad(retired)["superseded_by"] == child
    assert job_cad_state(retired)["held"] is False
    # The same explicit press recovers the same child after binding.
    assert h._loop.run(h.runtime.solve_cad_again(parent["id"], mesh_limit_override=True)) == child
    with pytest.raises(MeshTooLarge):
        _enforce_artifact_triangle_ceiling(28_944)


def test_schema_six_upgrade_keeps_historical_jobs_and_snapshots_before_rebuild(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    row = h.latest_job()
    db = h.jobs_store.db_path
    h.jobs_store.update_job(row["id"], status="error", stage="error")
    h.jobs_store.close()
    # A real schema-6 CHECK, while retaining all existing rows and child tables.
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql=replace(sql, '''held'',', '') WHERE name='simulation_jobs'")
        conn.execute("PRAGMA user_version=6")
        conn.execute("PRAGMA writable_schema=OFF")
    reopened = JobStore(db)
    reopened.initialize()
    try:
        assert reopened.get_job_row(row["id"])["status"] == "error"
        assert job_cad_state(reopened.get_job_row(row["id"]))["held"] is True
        snapshot = Path(str(db) + ".pre-schema-7.bak")
        assert snapshot.exists()
        with sqlite3.connect(snapshot) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        reopened.update_job(row["id"], status="preparing")
        assert reopened.refuse_preparing_job(row["id"], code="setup_required", message="Settings")
        assert reopened.get_job_row(row["id"])["status"] == "held"
    finally:
        reopened.close()


_CALM = "Waveguide Generator could not prepare {}. This has been recorded for the developers."


def test_internal_failure_is_a_calm_failed_preparation_not_a_hold(h: JobsHarness, caplog) -> None:
    _received(h)

    def crash(*_args, **_kwargs):
        raise RuntimeError("gmsh crashed in /private/tmp/worker.py line 12")

    with caplog.at_level("WARNING"):
        h.prepare(setup_revision_id=_revision(h.store, _setup()), ingest=crash)
    job = h.latest_job()
    state = job_cad_state(job)
    name = state["snapshot"]["document_name"] or "this model"
    assert job["status"] == "error" and job["stage_message"] != "Waiting for you"
    assert state["held"] is False and state["prepare_failed"] is True
    assert state["attention"] == "failed"  # never a question for Fusion's panel
    assert state["message"] == _CALM.format(name)
    assert state["next_step"] is None
    assert [option["id"] for option in state["options"]] == ["retry", "dismiss"]
    assert "gmsh" not in json.dumps(state) and "gmsh" not in str(job["error_message"])
    assert "gmsh" not in json.dumps(_cad(job)["refusal"])
    item = h._loop.run(h.runtime.get_job(job["id"]))
    assert item["error_message"] == _CALM.format(name)
    # The raw detail is recorded once, for the developers.
    diagnostics = [record for record in caplog.records if getattr(record, "cad_diagnostic", None)]
    assert any(record.cad_diagnostic["stage"] == "internal-failure"
               and "gmsh crashed" in record.cad_diagnostic["evidence"]["detail"] for record in diagnostics)
    # Try again stays possible: a continuation is a new job.
    assert h._loop.run(h.runtime.clear_failed()) == [job["id"]]


def test_legacy_internal_failure_rows_read_calm_and_never_held(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    job = h.latest_job()
    metadata = job["task_metadata"]
    metadata["cad"].pop("hold")
    for code, raw in (("preparation_failed", "Preparing the mesh failed: Traceback (most recent call last)"),
                      ("submission_refused", "ValueError: bad request"),
                      ("snapshot_invalid", "manifest digest mismatch")):
        metadata["cad"]["refusal"] = {"code": code, "message": raw}
        h.jobs_store.update_job(job["id"], status="error", stage="error", error_message=raw,
                                task_metadata_json=json.dumps(metadata))
        state = job_cad_state(_row(h, job["id"]))
        assert state["held"] is False and state["prepare_failed"] is True, code
        assert state["message"].startswith("Waveguide Generator could not prepare "), code
        assert raw not in json.dumps(state)
        item = h._loop.run(h.runtime.get_job(job["id"]))
        assert raw not in str(item["error_message"]) and raw not in json.dumps(item["cad_provenance"])
        expected = ["dismiss"] if code == "snapshot_invalid" else ["retry", "dismiss"]
        assert [option["id"] for option in state["options"]] == expected
    # A legacy mesh-ceiling row stays the user's Solve anyway choice.
    metadata["cad"]["refusal"] = {"code": "preparation_failed", "message": "This model meshes to 28,944 triangles; …"}
    h.jobs_store.update_job(job["id"], task_metadata_json=json.dumps(metadata))
    state = job_cad_state(_row(h, job["id"]))
    assert state["held"] is True and state["prepare_failed"] is False
    assert state["options"][0]["id"] == "solve_anyway"


def test_an_interrupted_preparation_says_wg_stopped_and_claims_no_record(h: JobsHarness) -> None:
    _received(h, sized=False)
    h.prepare()
    job = h.latest_job()
    metadata = job["task_metadata"]
    metadata["cad"].pop("hold")
    metadata["cad"]["refusal"] = {"code": "interrupted", "message": "Preparation was interrupted"}
    h.jobs_store.update_job(job["id"], status="error", stage="error", task_metadata_json=json.dumps(metadata))
    state = job_cad_state(_row(h, job["id"]))
    assert state["held"] is False and state["prepare_failed"] is True and state["attention"] == "action"
    assert state["message"] == "Preparing Tritonia speaker stopped when Waveguide Generator closed."
    assert state["next_step"] == "Solve it again."
    assert "recorded" not in state["message"]
    assert [option["id"] for option in state["options"]] == ["retry", "dismiss"]


def test_memory_guard_after_solve_anyway_ends_calmly_without_a_hold(h: JobsHarness, caplog) -> None:
    from server.mesh.errors import SolverMemoryTooLarge

    _received(h)

    def too_big(*_args, **kwargs):
        if kwargs["prep_options"].get("mesh_limit_override"):
            raise SolverMemoryTooLarge("Solver mesh uses 40,000 P1 vertex/DOFs ... safety ceiling")
        _enforce_artifact_triangle_ceiling(28_944)

    h.prepare(setup_revision_id=_revision(h.store, _setup()), ingest=too_big)
    parent = h.latest_job()
    assert parent["status"] == "held"
    assert job_cad_state(parent)["attention"] == "question"
    h._ingest_override = too_big
    with caplog.at_level("WARNING"):
        child = h._loop.run(h.runtime.solve_cad_again(parent["id"], mesh_limit_override=True))
        _settle(h)
    row = _row(h, child)
    state = job_cad_state(row)
    assert row["status"] == "error"
    assert state["reason"] == "solver_memory_exceeded"
    assert state["held"] is False and state["prepare_failed"] is True
    assert state["attention"] == "action"
    assert state["message"] == SolverMemoryTooLarge.user_message(state["snapshot"]["document_name"])
    assert state["next_step"] == SolverMemoryTooLarge.NEXT_STEP  # the same for every CAD application
    assert "Fusion" not in state["next_step"]
    assert [option["id"] for option in state["options"]] == ["dismiss"]
    assert "P1 vertex" not in str(row["error_message"])
    # The guard's figures are recorded for the developers, naming the job.
    diagnostics = [record.cad_diagnostic for record in caplog.records if getattr(record, "cad_diagnostic", None)]
    assert any(item["stage"] == "memory-guard" and item["evidence"]["job_id"] == child
               and "P1 vertex" in item["evidence"]["detail"] for item in diagnostics)
    # Clearing the failed continuation never brings back the parent's Solve anyway.
    assert h._loop.run(h.runtime.clear_failed()) == [child]
    assert job_cad_state(_row(h, parent["id"]))["held"] is False


def test_a_wg_solve_of_the_held_model_retires_its_hold(h: JobsHarness) -> None:
    from dataclasses import replace

    _received(h, sized=False)
    h.prepare()
    held = h.latest_job()
    assert held["status"] == "held"
    manifest = _cad(held)["snapshot"]["manifest_sha256"]
    h.blocked = "Update pending"
    manual = h._loop.run(h.runtime.accept_cad_solve(
        replace(h._intent("cmd-1"), operation_id="manual-solve:one"), "manual-solve:one", prepare=False,
        cad_record={"snapshot": {"manifest_sha256": manifest}},
    ))
    retired = _row(h, held["id"])
    assert retired["status"] == "cancelled" and _cad(retired)["superseded_by"] == manual
    assert job_cad_state(retired)["held"] is False
    # Another model's WG solve leaves holds alone.
    h.jobs_store.update_job(held["id"], status="preparing", cancellation_requested=False)
    h.jobs_store.refuse_preparing_job(held["id"], code="setup_required", message="Choose settings")
    other = h._loop.run(h.runtime.accept_cad_solve(
        replace(h._intent("cmd-1"), operation_id="manual-solve:two"), "manual-solve:two", prepare=False,
        cad_record={"snapshot": {"manifest_sha256": "f" * 64}},
    ))
    assert other != manual
    assert _row(h, held["id"])["status"] == "held"


def test_the_memory_guard_keeps_its_type_across_the_child_boundary() -> None:
    from server.cadlink.intake.isolated import _reraise_typed
    from server.cadlink.intake.isolation import ChildRefusal
    from server.mesh.errors import SolverMemoryTooLarge

    refusal = ChildRefusal("stage 7 meshing", "Solver mesh uses 9 P1 vertex/DOFs ... safety ceiling",
                           error_type="SolverMemoryTooLarge")
    with pytest.raises(SolverMemoryTooLarge, match="safety ceiling"):
        _reraise_typed(refusal)


@pytest.mark.parametrize(("code", "raw", "message", "next_step", "options"), [
    ("snapshot_changed", "The return bundle changed after Fusion asked WG to solve it. Send it again from Fusion.",
     "{} changed after Fusion asked WG to solve it.", "Send it again from Fusion.", ["dismiss"]),
    ("return_unavailable", "The return Fusion sent is not in the WGLink folder, and WG has no copy it can use.",
     "WG cannot read the copy of {} it was sent.", "Send it again from Fusion.", ["retry", "dismiss"]),
    ("wglink_folder_unset", "No WGLink folder is selected, so WG cannot read the return Fusion sent.",
     "WG cannot read {}: no WGLink folder is selected.",
     "Choose the WGLink folder in Settings → CAD Link, then try again.", ["retry", "dismiss"]),
    ("excitation_refused", "This model is mirrored on z0, which does not contain the radiation axis; "
     "WG cannot drive it mirrored.", "This model is mirrored on z0, which does not contain the radiation axis; "
     "WG cannot drive it mirrored.", "Change the drive channels in this model's solve settings, "
     "or solve the whole model, then press Solve.", ["dismiss"]),
])
def test_failures_the_user_can_act_on_say_what_to_do_and_are_never_holds(
    h: JobsHarness, code: str, raw: str, message: str, next_step: str, options: list[str],
) -> None:
    from server.jobs.cad_preparation import calm_refusal_message

    _received(h, sized=False)
    h.prepare()
    job = h.latest_job()
    metadata = job["task_metadata"]
    metadata["cad"].pop("hold")
    stored = calm_refusal_message(code, raw, metadata["cad"], job_id=job["id"], operation_id="cmd-1")
    metadata["cad"]["refusal"] = {"code": code, "message": stored}
    h.jobs_store.update_job(job["id"], status="error", stage="error", error_message=stored,
                            task_metadata_json=json.dumps(metadata))
    state = job_cad_state(_row(h, job["id"]))
    name = state["snapshot"]["document_name"] or "this model"
    assert state["held"] is False and state["prepare_failed"] is True
    assert state["attention"] == "action"
    assert state["message"] == message.format(name)
    assert state["next_step"] == next_step
    assert [option["id"] for option in state["options"]] == options
    assert not state["message"].startswith("Waveguide Generator could not prepare ")


def test_a_manual_solve_whose_copy_is_gone_is_prepared_again_in_wg() -> None:
    from server.cadlink.operations import preparation_failure

    assert preparation_failure("return_unavailable", "Horn", manual=True)["next_step"] == (
        "Prepare it again in CAD Link, then solve it."
    )


#: The class of every reason an operation can carry, in the state it may
#: accompany (``operations.REASON_CODES``). A new code fails here until it is
#: classified on purpose: only a choice the user makes is a question.
EXPECTED_ATTENTION = {
    # Questions: WG asks them in its pop-up.
    "mesh_too_large": "question",
    "setup_required": "question",
    "findings_need_review": "question",
    # Prepared: Solve starts it. Neither a question nor a failure.
    "ready_to_solve": "ready",
    # Something to do; nothing is asked.
    "frame_confirmation_required": "action",
    "reduced_axis_not_allowed": "action",
    "engine_unavailable": "action",
    "engine_cannot_solve_return": "action",
    "excitation_refused": "action",
    "return_unavailable": "action",
    "wglink_folder_unset": "action",
    "solver_memory_exceeded": "action",
    "snapshot_changed": "action",
    "baseline_conflict": "action",
    "target_not_exact": "action",
    "request_payload_invalid": "action",
    "snapshot_unavailable": "action",
    "model_cut_unsolvable": "action",
    "adapter_refused": "action",
    "adapter_failed": "action",
    # WG's own failures.
    "preparation_failed": "failed",
    "submission_refused": "failed",
    # WG stopped while preparing: Solve prepares it again; nothing failed.
    "interrupted": "action",
    "snapshot_invalid": "failed",
    # Nothing asked: queued again by itself, or cancelled.
    "update_restart_pending": None,
    "superseded": None,
    "expired": None,
    "session_changed": None,
    "publication_failed": None,
    "adapter_not_started": None,
}


def test_attention_classifies_every_reason_and_asks_only_real_questions() -> None:
    from server.cadlink.operations import REASON_CODES, reason_attention

    assert set(EXPECTED_ATTENTION) == set(REASON_CODES)
    for code, state in REASON_CODES.items():
        assert reason_attention(state, code) == EXPECTED_ATTENTION[code], code
    # A legacy row storing the mesh ceiling as a failure is still its question.
    assert reason_attention("needs_user_input", "preparation_failed", "This model meshes to 1 triangles") == "question"
    # An unknown reason is never a question.
    assert reason_attention("needs_user_input", "some_future_code") == "action"
    assert reason_attention("accepted", None) is None


def test_a_hold_is_a_question_or_an_action_never_a_failure() -> None:
    from server.cadlink.operations import REASON_CODES, NEEDS_USER_INPUT, USER_ACTION_CODES, user_hold

    for code, state in REASON_CODES.items():
        if state != NEEDS_USER_INPUT:
            continue
        held = user_hold("held", {"refusal": {"code": code, "message": ""}})
        # A user-action failure ends as a failed preparation, never a hold.
        assert held is (EXPECTED_ATTENTION[code] in {"question", "ready", "action"}
                        and code not in USER_ACTION_CODES), code


def test_internal_failure_is_an_incident_that_survives_clear_failed(h: JobsHarness, tmp_path: Path) -> None:
    """The user's run list and the developers' evidence are independent."""

    from server.platform.incidents import configure_incidents

    incidents = configure_incidents(tmp_path / "wg-data", app_root=tmp_path / "app")
    _received(h)

    def crash(*_args, **_kwargs):
        raise RuntimeError(f"gmsh crashed in {tmp_path}/worker.py")

    h.prepare(setup_revision_id=_revision(h.store, _setup()), ingest=crash)
    job = h.latest_job()
    assert job["status"] == "error"
    [record] = incidents.list(kind="internal-failure")
    assert record["latest"]["ids"]["job_id"] == job["id"]
    assert "gmsh crashed" in record["latest"]["evidence"]["detail"]
    assert "crash" in record["latest"]["exception"]["stack"]  # the handled exception's stack
    assert str(tmp_path) not in json.dumps(record)
    log_path = h.jobs_store._job_log_path(job["id"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("preparing\nmeshing failed\n", encoding="utf-8")
    assert h._loop.run(h.runtime.clear_failed()) == [job["id"]]
    assert h.jobs_store.get_job_row(job["id"]) is None
    kept = incidents.get(record["id"])
    assert kept["jobLogs"][job["id"]]["tail"] == "preparing\nmeshing failed\n"
