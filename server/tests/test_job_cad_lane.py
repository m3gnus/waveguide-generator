"""The preparation lane and the job lifecycle of a CAD solve (S4-E2).

``JobRuntime.accept_cad_solve`` makes a ``preparing`` job; the lane prepares it and
binds it (``server/jobs/cad_preparation.py``). ``cad_backends.py`` runs the scenarios of
the operations backend on this lane too, and requires the same outcome, reason and
message. These tests are what parity cannot say: what only a job has (a status a stop
decides, a submission key, run-numberless refusals, a lane that is handed back by an update
restart), and that the lane reads and writes nothing of the operations' attempt machinery.

Meshing is the fenced stand-in ``FakeIngest``; the jobs system's own checks are the
stand-in of ``JobsHarness`` (the real ones run in ``test_job_cad_binding.py``).
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pytest

from server.cadlink.operations import PREPARE_AND_SOLVE, prepare_and_solve_request, request_digest
from server.cadlink.store import CadLinkStore
from server.jobs.cad_intent import (
    CAD_INTENT,
    INTERRUPTED_MESSAGE,
    STAGE_WAITING_FOR_RESTART,
    CadSolveIntent,
    intent_of,
    solve_again_intent,
)
from server.jobs.cad_preparation import job_operation_view
from server.jobs.runtime import JobConflictError
from server.jobs.store import SubmissionConflictError

from cad_backends import JobsHarness, Refused
from test_cad_preparation import _accept, _received, _revision, _setup, _write_return


class _Kill(BaseException):
    """The process dying: no handler in the lane catches it."""


@pytest.fixture
def h(tmp_path: Path):
    harness = JobsHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def _accept_direct(h: JobsHarness, operation_id: str = "cmd-1", **fields: Any) -> str:
    """Accept the solve as the delivery pass will: a job, with nothing recorded on the ledger."""

    job_id = h._loop.run(
        h.runtime.accept_cad_solve(h._intent(operation_id, **fields), f"cad-solve:{operation_id}")
    )
    h._latest[operation_id] = job_id
    return job_id


def _settle(h: JobsHarness) -> None:
    h._loop.run(h.runtime.wait_cad_preparations())


def _row(h: JobsHarness, job_id: str) -> dict[str, Any]:
    row = h.jobs_store.get_job_row(job_id)
    assert row is not None
    return row


def _cad(row: dict[str, Any]) -> dict[str, Any]:
    return (row.get("task_metadata") or {}).get("cad") or {}


def _second_return(h: JobsHarness, name: str = "cmd-2") -> None:
    bundle_path, manifest = _write_return(h.workspace, f"{name}.wgreturn", step=name.encode())
    _accept(h.store, name, bundle_path, manifest)


# -- the intent -----------------------------------------------------------------------


def test_the_intent_round_trips_and_names_the_delivery_it_came_from() -> None:
    intent = CadSolveIntent(
        operation_id="cmd-1", bundle_path="wgreturn/a.wgreturn", manifest_sha256="sha256:" + "1" * 64,
        return_id="wgr_1", setup_revision_id="wgs_1", frame_axis="+z",
        approvals={"preparation_id": "wgi_1", "finding_ids": ["f1"]}, submit=False, label="a run",
        parent_job_id="job-0", submission_key="cad-solve:cmd-1",
    )

    config = intent.to_config()

    assert config["type"] == CAD_INTENT
    assert CadSolveIntent.from_config(config) == intent
    assert CadSolveIntent.from_config(CadSolveIntent(
        "cmd-1", "b", "sha256:" + "2" * 64, "wgr_1"
    ).to_config()).submit is True
    # The digest is the ledger's own for the same command.
    target, inputs = prepare_and_solve_request(
        return_id="wgr_1", bundle_path="wgreturn/a.wgreturn", manifest_sha256="sha256:" + "1" * 64
    )
    assert intent.delivery_digest() == request_digest(PREPARE_AND_SOLVE, target, inputs)
    for missing in ("operation_id", "bundle_path", "manifest_sha256", "return_id"):
        broken = {key: value for key, value in config.items() if key != missing}
        with pytest.raises(ValueError, match=missing):
            CadSolveIntent.from_config(broken)
    with pytest.raises(ValueError):
        CadSolveIntent.from_config({"design": {}})
    assert intent_of({"config_json": {"design": {}}}) is None


def test_nothing_in_production_creates_a_preparing_job_yet() -> None:
    """S4-E2 is the new path, not yet called: S4-F1 owns the first production write.

    Until then no ``preparing`` row exists outside tests, so a release a rollback
    returns to never meets one (the compatibility decision lives with the change that
    first writes it: docs/architecture/CAD-OPERATIONS.md, "What the preparation-lane
    change must decide").
    """

    root = Path(__file__).resolve().parents[1]
    callers = {"accept_cad_solve", "solve_cad_again", "configure_cad_preparation", "CadSolveIntent("}
    homes = {"jobs/runtime.py", "jobs/cad_intent.py", "jobs/cad_preparation.py"}
    offenders = [
        f"{path.relative_to(root)}: {name}"
        for path in root.rglob("*.py")
        if "tests" not in path.parts and path.relative_to(root).as_posix() not in homes
        for name in callers
        if name in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# -- acceptance is idempotent by key -----------------------------------------------------


def test_accepting_the_same_delivery_twice_is_the_job_it_made(h: JobsHarness) -> None:
    _received(h)
    first = _accept_direct(h)
    _settle(h)
    again = _accept_direct(h)  # the delivery arrives again, after the job was bound
    assert again == first
    assert h.jobs_store.job_for_submission_key("cad-solve:cmd-1") == first
    assert h.jobs_store.list_jobs(limit=50)[1] == 1
    assert _row(h, first)["status"] == "queued"

    # The same key for another return is a conflict, and changes nothing.
    other = CadSolveIntent(
        operation_id="cmd-1", bundle_path="wgreturn/elsewhere.wgreturn",
        manifest_sha256="sha256:" + "9" * 64, return_id="wgr_1",
    )
    with pytest.raises(SubmissionConflictError):
        h._loop.run(h.runtime.accept_cad_solve(other, "cad-solve:cmd-1"))
    assert h.jobs_store.list_jobs(limit=50)[1] == 1

    # A different command is a different job.
    _second_return(h)
    second = _accept_direct(h, "cmd-2")
    assert second != first


def test_a_delivery_accepted_before_the_lane_ran_is_still_one_job(h: JobsHarness) -> None:
    _received(h)
    h.blocked = "An update restart is pending."  # the lane cannot start: both calls come first
    first = _accept_direct(h)
    again = _accept_direct(h)
    assert again == first
    assert h.jobs_store.list_jobs(limit=50)[1] == 1


def test_a_cad_solve_cannot_be_accepted_without_a_preparation_host(h: JobsHarness) -> None:
    h.runtime.configure_cad_preparation(None)
    _received(h)
    with pytest.raises(JobConflictError, match="no CAD preparation is configured"):
        _accept_direct(h)
    assert h.jobs_store.list_jobs(limit=50)[1] == 0


# -- no generation in use ------------------------------------------------------------------


def test_the_lane_uses_no_attempt_generation_and_writes_nothing_of_the_operations(
    h: JobsHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operation row is the acceptance ledger: the lane reads it and never writes it.

    Every store method of the operations' attempt machinery raises if it is called, and
    the row is the same, byte for byte, after the solve is bound.
    """

    _received(h)
    revision = _revision(h.store, _setup())
    before = h.store.get_operation("cmd-1")

    def forbidden(name: str):
        def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(f"CadLinkStore.{name} is the operations' attempt machinery")

        return refuse

    for name in (
        "claim", "advance_operation", "admit_frame_axis", "attempt_is_current", "bind_request",
        "add_approvals", "record_preparation", "record_outcome", "request_cancel",
        "requeue_operation", "settle_cancel", "record_snapshot", "record_legacy_outcome",
    ):
        monkeypatch.setattr(CadLinkStore, name, forbidden(name))

    job_id = _accept_direct(h, setup_revision_id=revision)
    _settle(h)

    row = _row(h, job_id)
    assert row["status"] == "queued" and row["run_number"] == 1
    assert h.store.get_operation("cmd-1") == before
    assert (before["state"], before["attempt_generation"], before["stage"]) == ("received", 0, "received")
    with closing(sqlite3.connect(h.store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cad_preparations").fetchone()[0] == 0
    # What the preparation recorded is the job's.
    cad = _cad(row)
    assert cad["preparation"]["preparation_id"] == h.submitted[0].geometry.ingest_id
    assert cad["setup"]["revision_id"] == revision
    assert cad["snapshot"]["manifest_sha256"] == json.loads(before["inputs_json"])["manifest_sha256"]


def test_a_lane_that_lost_its_job_commits_and_writes_nothing(h: JobsHarness) -> None:
    """A stop lands while the lane meshes: the mesher's record is not committed, and nothing follows."""

    _received(h)
    revision = _revision(h.store, _setup())
    h.ingest.during = lambda: h.dismiss()

    summary = h.prepare(setup_revision_id=revision)

    job = h.latest_job()
    assert (summary["state"], job["status"]) == ("cancelled", "cancelled")
    with closing(sqlite3.connect(h.store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingests").fetchone()[0] == 0
    assert h.submitted == [] and job["run_number"] is None
    # The lane's own writes are fenced the same way: none applies to a stopped job.
    store = h.jobs_store
    assert store.advance_preparing_job(job["id"], stage="ready", cad={"x": 1}) == (False, None)
    assert store.refuse_preparing_job(job["id"], code="submission_refused", message="no") is None
    assert store.hand_back_preparing_job(job["id"], stage="s", stage_message="m") is None
    assert store.claim_preparing_job(job["id"], stage="s", stage_message="m", progress=0.0) is None
    assert _row(h, job["id"])["status"] == "cancelled"


def test_a_job_stopped_before_its_lane_takes_it_is_never_prepared(h: JobsHarness) -> None:
    _received(h)
    h.blocked = "An update restart is pending."  # the lane waits; the user stops the solve
    job_id = _accept_direct(h)
    assert _row(h, job_id)["started_at"] is None

    h._loop.run(h.runtime.stop(job_id))
    h.blocked = None
    _settle(h)

    row = _row(h, job_id)
    assert (row["status"], row["run_number"]) == ("cancelled", None)
    assert h.ingest.calls == [] and h.submitted == []


def test_a_preparing_job_is_stopped_where_it_is(h: JobsHarness) -> None:
    """Unheld, being meshed and queued: a stop ends the job in each, and the lane never wakes it."""

    _received(h)
    _second_return(h, "cmd-2")
    revision = _revision(h.store, _setup())
    # Waiting for a lane.
    h.blocked = "An update restart is pending."
    waiting = _accept_direct(h, "cmd-2")
    assert h._loop.run(h.runtime.stop(waiting))["status"] == "cancelled"
    h.blocked = None
    # Being meshed (covered in ``test_a_lane_that_lost_its_job_commits_and_writes_nothing``),
    # and queued: a bound job is stopped by the ordinary queued rule.
    queued = _accept_direct(h, "cmd-1", setup_revision_id=revision)
    _settle(h)
    assert _row(h, queued)["status"] == "queued"
    assert h._loop.run(h.runtime.stop(queued))["status"] == "cancelled"
    assert _row(h, waiting)["status"] == _row(h, queued)["status"] == "cancelled"
    # A stopped CAD solve can be solved again: a new job continues it.
    assert h._loop.run(h.runtime.solve_cad_again(waiting)) != waiting


# -- refusal and Solve again ---------------------------------------------------------------


def test_a_refusal_ends_the_job_as_an_error_with_the_operations_reason_and_words(
    h: JobsHarness,
) -> None:
    _received(h, sized=False)

    summary = h.prepare()

    job = h.latest_job()
    assert job["status"] == "error" and job["run_number"] is None
    assert _cad(job)["refusal"] == {"code": "setup_required", "message": summary["message"]}
    assert job["error_message"] == summary["message"]
    assert (summary["state"], summary["reason"]) == ("needs_user_input", "setup_required")
    assert job["config_json"]["type"] == CAD_INTENT  # never had a request
    with pytest.raises(JobConflictError, match="ended before it had a solve request"):
        h._loop.run(h.runtime.get_effective_request(job["id"]))


def test_solve_again_is_a_new_job_that_carries_what_the_first_recorded(h: JobsHarness) -> None:
    _received(h)
    revision = _revision(h.store, _setup())
    h.submit_error = RuntimeError("database is locked")
    first = h.prepare(setup_revision_id=revision)
    assert first["reason"] == "submission_refused"
    parent = h.latest_job()

    child_id = h._loop.run(h.runtime.solve_cad_again(parent["id"]))

    assert child_id != parent["id"]
    _settle(h)
    child = _row(h, child_id)
    assert child["status"] == "queued" and child["run_number"] == 1
    assert child["parent_job_id"] == parent["id"]
    assert _cad(child)["setup"]["revision_id"] == revision  # the recorded setup, named by no one
    assert len(h.ingest.calls) == 1  # its preparation was resumed, not made again
    assert _row(h, parent["id"])["status"] == "error"  # the first stays as it ended


def test_two_solve_again_presses_share_one_continuation(h: JobsHarness) -> None:
    _received(h)
    h.submit_error = RuntimeError("database is locked")
    assert h.prepare(setup_revision_id=_revision(h.store, _setup()))["reason"] == "submission_refused"
    parent = h.latest_job()
    first = h._loop.run(h.runtime.solve_cad_again(parent["id"]))
    second = h._loop.run(h.runtime.solve_cad_again(parent["id"]))
    assert first == second
    _settle(h)
    child = _row(h, first)
    assert child["config_json"]["client_request_id"] != parent["config_json"]["submission_key"]
    assert len([row for row in h.jobs_store.list_jobs(limit=500)[0]
                if row["config_json"].get("parent_job_id") == parent["id"]]) == 1
    with pytest.raises(JobConflictError, match="continuing job"):
        h._loop.run(h.runtime.solve_cad_again(parent["id"]))


def test_retry_and_solve_again_share_one_continuation(h: JobsHarness) -> None:
    _received(h)
    h.submit_error = RuntimeError("database is locked")
    assert h.prepare(setup_revision_id=_revision(h.store, _setup()))["reason"] == "submission_refused"
    parent = h.latest_job()["id"]
    h.blocked = "An update restart is pending."

    async def both() -> tuple[str, str]:
        first, second = await asyncio.gather(
            h.runtime.retry(parent), h.runtime.solve_cad_again(parent)
        )
        return first, second

    first, second = h._loop.run(both())
    assert first == second
    h.release_latch()
    _settle(h)
    assert _row(h, first)["status"] == "queued"


def test_each_continuation_has_its_own_client_request_id(h: JobsHarness) -> None:
    _received(h)
    h.submit_error = RuntimeError("first bind refused")
    assert h.prepare(setup_revision_id=_revision(h.store, _setup()))["reason"] == "submission_refused"
    parent = h.latest_job()["id"]
    h.submit_error = RuntimeError("second bind refused")
    child = h._loop.run(h.runtime.solve_cad_again(parent))
    _settle(h)
    assert _row(h, child)["status"] == "error"
    grandchild = h._loop.run(h.runtime.solve_cad_again(child))
    _settle(h)
    assert _row(h, grandchild)["status"] == "queued"
    keys = [
        _row(h, parent)["config_json"]["submission_key"],
        _row(h, child)["config_json"]["submission_key"],
        _row(h, grandchild)["config_json"]["client_request_id"],
    ]
    assert len(set(keys)) == 3


def test_solve_again_names_the_setup_revision_it_was_given(h: JobsHarness) -> None:
    _received(h)
    refused = h.prepare(setup_revision_id="wgs_gone")
    assert (refused["state"], refused["reason"]) == ("needs_user_input", "setup_required")
    first = _revision(h.store, _setup(rigid=20.0))
    second = _revision(h.store, _setup(rigid=12.0))
    h.submit_error = RuntimeError("database is locked")
    assert h.prepare(setup_revision_id=first)["reason"] == "submission_refused"

    accepted = h.prepare(setup_revision_id=second)  # an explicit revision beats the recorded one

    assert accepted["state"] == "accepted"
    assert h.submitted[-1].geometry.mesh.rigid_size_mm == 12.0
    assert h.bound_setup_revision() == second


def test_an_approval_given_with_solve_again_applies_to_the_resumed_preparation(
    h: JobsHarness,
) -> None:
    _received(h)
    h.ingest.findings = [{"id": "healing-1", "kind": "healing-performed", "blocking": True}]
    revision = _revision(h.store, _setup())
    review = h.prepare(setup_revision_id=revision)
    assert (review["state"], review["reason"]) == ("needs_user_input", "findings_need_review")
    # A finding the preparation never reported, or another preparation's: not applied.
    other = h.prepare(approve_preparation_id=review["preparationId"], approve_finding_ids=("healing-9",))
    assert other["reason"] == "findings_need_review"
    wrong = h.prepare(approve_preparation_id="wgi_someone_else", approve_finding_ids=("healing-1",))
    assert wrong["reason"] == "findings_need_review"

    solved = h.prepare(
        approve_preparation_id=review["preparationId"], approve_finding_ids=("healing-1",)
    )

    assert (solved["state"], solved["preparationId"]) == ("accepted", review["preparationId"])
    assert len(h.ingest.calls) == 1  # every Solve again resumed the one preparation
    job = h.latest_job()
    assert _cad(job)["preparation"]["approvals"] == [
        {"preparation_id": review["preparationId"], "finding_id": "healing-1"}
    ]
    report = json.loads(h.store.get_ingest(review["preparationId"])["record_json"])["report_sha256"]
    assert h.submitted[-1].geometry.acknowledged_findings == [f"{report}:healing-1"]
    # And a later Solve again of a refused sibling starts from the approvals recorded so far.
    assert _cad(job)["preparation"]["blocking_finding_ids"] == ["healing-1"]


def test_solve_again_refuses_what_it_should_and_retry_reaches_it(h: JobsHarness) -> None:
    _received(h)
    _second_return(h, "cmd-2")
    revision = _revision(h.store, _setup())
    # Still being prepared: nothing to solve again.
    h.blocked = "An update restart is pending."
    preparing = _accept_direct(h)
    with pytest.raises(JobConflictError, match="still being prepared"):
        h._loop.run(h.runtime.solve_cad_again(preparing))
    with pytest.raises(JobConflictError, match="still being prepared"):
        h._loop.run(h.runtime.retry(preparing))
    h.blocked = None
    _settle(h)
    bound = _row(h, preparing)
    assert bound["status"] == "queued"
    # A job that has a request is retried as any job is, never "solved again".
    with pytest.raises(JobConflictError, match="already has a solve request"):
        h._loop.run(h.runtime.solve_cad_again(preparing))
    # A return WG rejected as invalid is not solved again: it is sent again from CAD.
    bundle_path, _manifest = _write_return(h.workspace, "moved.wgreturn", step=b"moved")
    _accept(h.store, "cmd-bad", bundle_path, "sha256:" + "f" * 64)
    rejected = h.prepare("cmd-bad", setup_revision_id=revision)
    assert (rejected["state"], rejected["reason"]) == ("rejected", "snapshot_invalid")
    with pytest.raises(JobConflictError, match="."):
        h._loop.run(h.runtime.solve_cad_again(h._latest["cmd-bad"]))
    # A refused, solvable one is solved again by ``retry`` (POST /jobs/{id}/retry).
    h.submit_error = RuntimeError("database is locked")
    refused = h.prepare("cmd-2", setup_revision_id=revision)
    assert refused["reason"] == "submission_refused"
    child = h._loop.run(h.runtime.retry(h._latest["cmd-2"]))
    assert child != h._latest["cmd-2"]
    h._latest["cmd-2"] = child
    _settle(h)
    assert _row(h, child)["status"] == "queued"


# -- restart recovery --------------------------------------------------------------------


def test_a_preparation_a_lane_held_when_wg_stopped_ends_refused_interrupted(h: JobsHarness) -> None:
    _received(h)
    revision = _revision(h.store, _setup())

    def stopped() -> None:
        raise _Kill()

    h.ingest.during = stopped
    held = h.prepare(setup_revision_id=revision)  # the lane dies with the process
    assert h.latest_job()["status"] == "preparing" and held["state"] == "processing"
    job_id = h.latest_job()["id"]

    h.restart()

    row = _row(h, job_id)
    assert row["status"] == "error"
    assert _cad(row)["refusal"] == {"code": "interrupted", "message": INTERRUPTED_MESSAGE}
    assert h.summary()["reason"] == "interrupted"
    # Solve again solves it, once.
    h.ingest.during = None
    assert h.prepare(setup_revision_id=revision)["state"] == "accepted"
    assert len(h.submitted) == 1


def test_a_job_no_lane_held_is_prepared_by_the_next_start(h: JobsHarness) -> None:
    _received(h)
    revision = _revision(h.store, _setup())
    h.blocked = "An update restart is pending."
    job_id = _accept_direct(h, setup_revision_id=revision)
    assert _row(h, job_id)["status"] == "preparing" and h.ingest.calls == []
    h.blocked = None
    h._latest.clear()

    h.restart()  # the next process: the lane takes up what no lane held
    _settle(h)

    row = _row(h, job_id)
    assert (row["status"], row["run_number"]) == ("queued", 1)
    assert len(h.ingest.calls) == 1 and len(h.submitted) == 1


# -- the update-restart latch ----------------------------------------------------------------


def test_a_job_accepted_while_a_restart_is_approved_waits_untouched(h: JobsHarness) -> None:
    _received(h)
    revision = _revision(h.store, _setup())
    before = h.store.get_operation("cmd-1")
    h.blocked = "Waveguide Generator is about to restart to install 0.3.4."

    job_id = _accept_direct(h, setup_revision_id=revision)

    row = _row(h, job_id)
    # Not claimed, not meshed, not bound: it says why, and nothing else changed.
    assert (row["status"], row["started_at"], row["stage"]) == (
        "preparing", None, STAGE_WAITING_FOR_RESTART,
    )
    assert row["stage_message"] == h.blocked
    assert h.ingest.calls == [] and h.submitted == []
    assert h.store.get_operation("cmd-1") == before
    view = job_operation_view(row)
    assert (view["state"], view["reason"], view["message"]) == (
        "needs_user_input", "update_restart_pending", h.blocked,
    )

    h.blocked = None  # the restart is called off
    _settle(h)

    assert _row(h, job_id)["status"] == "queued" and len(h.ingest.calls) == 1


def test_a_solve_the_restart_held_is_taken_up_again_when_the_latch_comes_down(
    h: JobsHarness,
) -> None:
    _received(h)
    revision = _revision(h.store, _setup())

    def approve() -> None:
        h.blocked = "An update restart is pending."
        h.ingest.during = None

    h.ingest.during = approve

    held = h.prepare(setup_revision_id=revision)

    assert (held["state"], held["reason"]) == ("needs_user_input", "update_restart_pending")
    job = h.latest_job()
    # Handed back, not ended: it keeps what it prepared, and no lane holds it.
    assert (job["status"], job["started_at"]) == ("preparing", None)
    assert _cad(job)["preparation"]["setup_revision_id"] == revision
    assert h.submitted == []

    assert h.release_latch() == ["cmd-1"]

    assert _row(h, job["id"])["status"] == "queued"
    assert len(h.ingest.calls) == 1  # its preparation was resumed
    # The same after a restart of the process instead of a called-off latch.
    _second_return(h, "cmd-2")
    h.blocked = "An update restart is pending."
    parked = _accept_direct(h, "cmd-2", setup_revision_id=revision)
    h.blocked = None
    h.restart()
    _settle(h)
    assert _row(h, parked)["status"] == "queued"


def test_a_stopped_job_the_restart_held_is_never_prepared_again(h: JobsHarness) -> None:
    _received(h)
    revision = _revision(h.store, _setup())

    def approve() -> None:
        h.blocked = "An update restart is pending."
        h.ingest.during = None

    h.ingest.during = approve
    assert h.prepare(setup_revision_id=revision)["reason"] == "update_restart_pending"
    calls = len(h.ingest.calls)
    h.dismiss()  # the user stops the waiting solve

    assert h.release_latch() == []
    h.restart()
    _settle(h)

    assert h.latest_job()["status"] == "cancelled"
    assert len(h.ingest.calls) == calls and h.submitted == []


def test_a_job_keeps_the_axis_its_press_showed_across_a_restart_and_solve_again(
    h: JobsHarness,
) -> None:
    _received(h)
    refused = h.prepare(setup_revision_id="wgs_gone", expected_frame_axis="+z")
    assert refused["reason"] == "setup_required"
    assert h.held_axis() == "+z"
    h.restart()
    assert h.held_axis() == "+z"  # durable: the job's own intent

    # A Solve again that names none is held to it; one that names another replaces it.
    revision = _revision(h.store, _setup())
    h.submit_error = RuntimeError("database is locked")
    assert h.prepare(setup_revision_id=revision)["reason"] == "submission_refused"
    assert h.held_axis() == "+z"
    solved = h.prepare(expected_frame_axis="+x")
    assert solved["state"] == "accepted"
    assert h.held_axis() == "+x"  # the bound job records the axis its press showed


def test_the_lane_prepares_two_at_a_time_and_never_waits_behind_a_solve(h: JobsHarness) -> None:
    for name in ("cmd-2", "cmd-3"):
        _second_return(h, name)
    _received(h)
    revision = _revision(h.store, _setup())
    gate = threading.Event()
    lock = threading.Lock()
    active = {"now": 0, "most": 0}

    def hold_while_meshing() -> None:
        with lock:
            active["now"] += 1
            active["most"] = max(active["most"], active["now"])
        gate.wait(timeout=20)
        with lock:
            active["now"] -= 1

    h.ingest.during = hold_while_meshing
    # A solve is running: the scheduler is busy, and meshing must not wait for it.
    h.runtime._running.add("a-solve-that-is-running")
    jobs = [_accept_direct(h, name, setup_revision_id=revision) for name in ("cmd-1", "cmd-2", "cmd-3")]
    for _ in range(200):
        if active["now"] == 2:
            break
        threading.Event().wait(0.02)
    assert active["now"] == 2  # two at once; the third waits for a place in the lane
    gate.set()
    _settle(h)
    h.runtime._running.discard("a-solve-that-is-running")

    assert active["most"] == 2
    assert [_row(h, job)["status"] for job in jobs] == ["queued"] * 3
    assert sorted(_row(h, job)["run_number"] for job in jobs) == [1, 2, 3]


# -- what a job says about itself --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "cad", "expected"),
    [
        ("preparing", {}, ("received", "received", None)),
        ("queued", {"setup": {"origin": "user"}}, ("accepted", "submitted", None)),
        ("running", {}, ("accepted", "submitted", None)),
        ("complete", {}, ("accepted", "submitted", None)),
        ("cancelled", {}, ("cancelled", None, None)),
        (
            "error",
            {"refusal": {"code": "findings_need_review", "message": "Review it."},
             "preparation": {"preparation_id": "wgi_1"}},
            ("needs_user_input", "ready", "findings_need_review"),
        ),
        (
            "error",
            {"refusal": {"code": "setup_required", "message": "Choose them."}, "last_stage": "validating"},
            ("needs_user_input", "validating", "setup_required"),
        ),
        (
            "error",
            {"refusal": {"code": "snapshot_invalid", "message": "Changed."}},
            ("rejected", None, "snapshot_invalid"),
        ),
        (
            "error",
            {"refusal": {"code": "interrupted", "message": INTERRUPTED_MESSAGE}},
            ("needs_user_input", None, "interrupted"),
        ),
    ],
)
def test_a_job_reads_in_the_operations_vocabulary(
    status: str, cad: dict[str, Any], expected: tuple[str, str | None, str | None]
) -> None:
    row = {
        "id": "job-1", "status": status, "stage": "received" if status == "preparing" else status,
        "started_at": None, "task_metadata": {"cad": {"operation_id": "cmd-1", **cad}},
    }

    view = job_operation_view(row)

    assert (view["state"], view["stage"], view["reason"]) == expected
    assert view["operationId"] == "cmd-1"
    assert (view["jobId"] is not None) == (status in {"queued", "running", "complete"})


def test_a_job_solved_with_wgs_defaults_says_so_in_the_operations_words() -> None:
    from server.cadlink.default_setup import DEFAULT_SETTINGS_NOTE

    view = job_operation_view({
        "id": "job-1", "status": "queued", "stage": "queued", "started_at": None,
        "task_metadata": {"cad": {
            "operation_id": "cmd-1",
            "setup": {"revision_id": "wgs_1", "origin": "wg_defaults"},
            "frame": {"axis": "+z", "provenance": "automatic"},
        }},
    })

    assert (view["state"], view["message"], view["setupDefaults"]) == (
        "accepted", DEFAULT_SETTINGS_NOTE, True,
    )
    assert view["frameAxisAutomatic"] == "+z" and view["setupRevisionId"] == "wgs_1"


# -- retention --------------------------------------------------------------------------------


def test_the_state_a_cad_solve_needs_is_kept_while_it_can_still_run(h: JobsHarness) -> None:
    """A preparing job, and a refused one the user can answer, hold the captured document."""

    state = "sha256:" + "5" * 64
    _received(h)
    h.ingest.findings = [{"id": "healing-1", "kind": "healing-performed", "blocking": True}]
    revision = _revision(h.store, _setup())
    waiting = h.prepare(setup_revision_id=revision)
    assert waiting["reason"] == "findings_need_review"
    assert h.held_return_states() == [state]  # refused, and still answerable

    # Continued by another job: the new one holds it, and the first no longer does.
    h.ingest.findings = []
    parent = h.latest_job()["id"]
    h.blocked = "An update restart is pending."
    child = h._loop.run(h.runtime.solve_cad_again(
        parent, approve_preparation_id=waiting["preparationId"], approve_finding_ids=("healing-1",)
    ))
    assert h.held_return_states() == [state]  # the child, preparing, holds it
    assert _cad(_row(h, child))["return_state_hash"] == state  # carried before it ran
    h.blocked = None
    _settle(h)
    assert _row(h, child)["status"] == "queued"
    assert h.jobs_store.get_job_row(parent)["status"] == "error"
    # Bound and queued: the ordinary rule holds it (no document capture in the stand-in record,
    # so what remains is the carried state on the queued job, which the store does not read).
    assert h.held_return_states() == []

    # Nothing rejected or stopped holds anything.
    bundle_path, _manifest = _write_return(h.workspace, "bad.wgreturn", step=b"bad")
    _accept(h.store, "cmd-bad", bundle_path, "sha256:" + "f" * 64)
    h.prepare("cmd-bad", setup_revision_id=revision)
    assert h.held_return_states() == []


# -- logs -----------------------------------------------------------------------------------------


def test_each_step_logs_its_job_and_operation(h: JobsHarness, caplog: pytest.LogCaptureFixture) -> None:
    _received(h, sized=False)

    def logged(*parts: str) -> bool:
        return any(
            all(part in record.getMessage() for part in parts)
            for record in caplog.records
            if record.name == "server.jobs.cad_preparation"
        )

    with caplog.at_level(logging.INFO, logger="server.jobs.cad_preparation"):
        assert h.prepare()["reason"] == "setup_required"
        job = h.latest_job()["id"]
        assert h.prepare(setup_revision_id=_revision(h.store, _setup()))["state"] == "accepted"
        second = h.latest_job()["id"]

    assert logged(job, "cmd-1", "the lane took it")
    assert logged(job, "cmd-1", "refused (setup_required)", "Choose")
    assert second != job
    for stage in ("preparing-mesh", "ready"):
        assert logged(second, "cmd-1", f"stage {stage}")
    assert logged(second, "cmd-1", "bound and queued")


def test_a_refusal_logs_its_message_unless_it_is_a_rejection(
    h: JobsHarness, caplog: pytest.LogCaptureFixture
) -> None:
    _received(h)
    h.submit_error = Refused("BEAT CUDA solves CAD returns only in Accurate. Choose Accurate, or Metal / AUTO.")
    with caplog.at_level(logging.INFO, logger="server.jobs.cad_preparation"):
        h.prepare(setup_revision_id=_revision(h.store, _setup(engine="metal")))

    refused = [r.getMessage() for r in caplog.records if "refused (" in r.getMessage()]
    assert len(refused) == 1
    assert "engine_cannot_solve_return" in refused[0] and "Choose Accurate, or Metal / AUTO" in refused[0]


def test_an_unexpected_failure_ends_the_job_refused_and_never_strands_it(h: JobsHarness) -> None:
    _received(h)
    h.ingest.error = KeyError("an unexpected failure")

    summary = h.prepare(setup_revision_id=_revision(h.store, _setup()))

    job = h.latest_job()
    assert (job["status"], summary["reason"]) == ("error", "preparation_failed")
    assert "Preparing this request failed unexpectedly" in _cad(job)["refusal"]["message"]
    assert "Press Solve now to try again." in summary["message"]
    # The intent it ended with can be solved again, and the lane is free for it.
    h.ingest.error = None
    assert h.prepare(setup_revision_id=_revision(h.store, _setup()))["state"] == "accepted"


def test_solve_again_intent_holds_the_axis_and_replaces_only_what_is_named() -> None:
    parent = {
        "id": "job-1",
        "config_json": CadSolveIntent(
            "cmd-1", "wgreturn/a.wgreturn", "sha256:" + "1" * 64, "wgr_1",
            setup_revision_id="wgs_old", frame_axis="+z", label="run", submission_key="cad-solve:cmd-1",
        ).to_config(),
    }

    plain = solve_again_intent(parent)
    named = solve_again_intent(
        parent, setup_revision_id="wgs_new", frame_axis="-y",
        approve_preparation_id="wgi_1", approve_finding_ids=("f",), submit=False,
    )

    assert (plain.frame_axis, plain.label, plain.submission_key, plain.parent_job_id) == (
        "+z", "run", "cad-solve:cmd-1", "job-1",
    )
    assert plain.setup_revision_id is None  # the recorded setup travels in the job's record
    assert (named.setup_revision_id, named.frame_axis, named.submit) == ("wgs_new", "-y", False)
    assert named.approvals == {"preparation_id": "wgi_1", "finding_ids": ["f"]}
    with pytest.raises(ValueError, match="never had a request"):
        solve_again_intent({"id": "job-2", "config_json": {"design": {}}})
