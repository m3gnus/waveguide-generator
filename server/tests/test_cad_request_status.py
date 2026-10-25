"""The request-status file Fusion follows (``server.cadlink.joblane.request_status``).

One record per request, next to its schema-1 receipt, projected from the
request's latest job by one owner, written atomically and only on change.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

from server.cadlink import wglink_protocol as protocol
from server.cadlink.fusion import fusion_delivery
from server.cadlink.intake import solve_command
from server.cadlink.intake.solve_command import (
    REQUEST_STATUS_RETENTION_SECONDS,
    SOLVE_ACKS_DIRECTORY,
    prune_acknowledgements,
    write_acknowledgement,
)
from server.cadlink.joblane import request_status
from server.cadlink.joblane.request_status import (
    PROGRESS_INTERVAL_S,
    RequestStatusPublisher,
    project_request_status,
)
from server.jobs.cad_preparation import job_cad_state

from cad_backends import JobsHarness
from test_cad_preparation import _received, _revision, _setup
from test_job_cad_lane import _row, _second_return

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = "sha256:" + "a" * 64


@pytest.fixture
def h(tmp_path: Path):
    harness = JobsHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def incidents(monkeypatch):
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(request_status, "record_incident",
                        lambda kind, phase, **kwargs: recorded.append({"kind": kind, "phase": phase, **kwargs}))
    return recorded


def _acks(h: JobsHarness) -> Path:
    return h.data_dir / "ipc/wglink" / SOLVE_ACKS_DIRECTORY


def _status(h: JobsHarness, request_id: str = "cmd-1") -> dict[str, Any] | None:
    path = _acks(h) / f"{request_id}.status.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _receipt(h: JobsHarness, request_id: str = "cmd-1", **kwargs: Any) -> None:
    job = h.latest_job(request_id) if kwargs.get("kind", "prepare_and_solve") == "prepare_and_solve" \
        and kwargs.get("outcome", "accepted") == "accepted" else None
    outcome = kwargs.pop("outcome", "accepted")
    kwargs.setdefault("kind", "prepare_and_solve")
    assert write_acknowledgement(
        h.data_dir, request_id, outcome, job_id=job["id"] if job else None,
        manifest_sha256=MANIFEST, **kwargs,
    )


def _queued(h: JobsHarness) -> dict[str, Any]:
    _received(h)
    h.prepare(setup_revision_id=_revision(h.store, _setup()))
    job = h.latest_job()
    assert job["status"] == "queued"
    _receipt(h)
    return job


def _set(h: JobsHarness, job_id: str, event: str = "updated", **fields: Any) -> None:
    changed, _event = h.jobs_store.update_job_with_event(job_id, fields, event, {})
    assert changed


def _held(h: JobsHarness) -> dict[str, Any]:
    """A request whose preparation stopped before binding (no settings yet): an intent job."""

    _received(h, sized=False)
    h.prepare()
    job = h.latest_job()
    assert job["status"] == "held" and job["config_json"]["type"] == "cad_intent"
    _receipt(h)
    return job


def _refuse(h: JobsHarness, job: dict[str, Any], code: str, message: str, *, status: str = "error") -> None:
    metadata = job["task_metadata"]
    metadata["cad"].pop("hold", None)
    metadata["cad"]["refusal"] = {"code": code, "message": message}
    _set(h, job["id"], status=status, stage=status, error_message=message,
         task_metadata_json=json.dumps(metadata))


def _publisher(h: JobsHarness, clock: Clock | None = None) -> RequestStatusPublisher:
    return RequestStatusPublisher(h.data_dir, h.store, clock=clock or Clock())


def _words(record: dict[str, Any]) -> str:
    text = protocol.request_status_text(protocol.validate_request_status(record, request_id="cmd-1"))
    return " ".join([text["headline"], text["detail"] or "", *text["options"],
                     *(action["label"] for action in text["actions"])])


# -- the projection, state by state ------------------------------------------------


def test_a_solve_moves_preparing_queued_solving_solved_and_each_record_validates(h, incidents) -> None:
    job = _queued(h)
    clock = Clock()
    publisher = _publisher(h, clock)
    _set(h, job["id"], status="preparing")
    publisher.tick(h.jobs_store)
    states = [_status(h)["state"]]
    _set(h, job["id"], status="queued")
    publisher.tick(h.jobs_store)
    states.append(_status(h)["state"])
    _set(h, job["id"], status="running", progress=0.4)
    publisher.tick(h.jobs_store)
    states.append(_status(h)["state"])
    assert _status(h)["progress"] == {"fraction": 0.4}
    _set(h, job["id"], "completed", status="complete", progress=1.0)
    publisher.tick(h.jobs_store)
    record = _status(h)
    states.append(record["state"])
    assert states == ["preparing", "queued", "solving", "solved"]
    assert record["result"]["jobId"] == job["id"] and record["job"]["jobId"] == job["id"]
    assert (record["contract"], record["schemaVersion"], record["requestId"], record["operationId"]) == (
        "wg-request-status", 1, "cmd-1", "cmd-1")
    assert record["documentName"] == "Tritonia speaker" and record["manifestSha256"] == MANIFEST
    assert record["seq"] == 4
    assert protocol.validate_request_status(record, request_id="cmd-1") is not None
    assert "Tritonia speaker · Solved" in _words(record)
    assert incidents == []


def test_a_real_question_needs_you_with_wgs_own_next_step_and_options(h) -> None:
    job = _held(h)
    _refuse(h, job, "mesh_too_large", "This model meshes to 28,944 triangles; the limit is 22,000.", status="held")
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    state = job_cad_state(_row(h, job["id"]))
    assert record["state"] == "needsYou"
    assert record["prompt"] == {"code": "mesh_too_large", "nextStep": state["next_step"],
                                "options": state["options"], "answerIn": "wg"}
    assert record["prompt"]["options"][0] == {"id": "solve_anyway", "label": "Solve anyway"}
    assert "28,944" not in json.dumps(record)


def test_a_legacy_mesh_ceiling_row_is_still_the_same_question(h) -> None:
    """C2: rows stored before mesh_too_large had its own code read through the one legacy reader."""

    job = _held(h)
    _refuse(h, job, "preparation_failed", "This model meshes to 28,944 triangles; the limit is 22,000.")
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "needsYou" and record["prompt"]["code"] == "mesh_too_large"
    assert record["prompt"]["options"][0]["id"] == "solve_anyway"


@pytest.mark.parametrize("status", ["held", "error"])
def test_held_rows_and_legacy_error_holds_both_need_you(h, status) -> None:
    """C9: a schema-7 ``held`` row and a schema-6 ``error`` + NEEDS_USER_INPUT row read alike."""

    job = _held(h)
    _refuse(h, job, "findings_need_review", "Review finding-123 before solving.", status=status)
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "needsYou" and record["prompt"]["code"] == "findings_need_review"
    assert [option["id"] for option in record["prompt"]["options"]] == ["review", "dismiss"]
    assert "finding-123" not in json.dumps(record)


@pytest.mark.parametrize("code", ["preparation_failed", "submission_refused", "interrupted", "snapshot_invalid"])
def test_wgs_own_preparation_failures_could_not_finish_calmly_never_need_you(h, incidents, code) -> None:
    """C3: internal failures are not questions; their detail stays with the developers."""

    job = _held(h)
    raw = "Traceback (most recent call last): gmsh crashed in worker.py line 12"
    _refuse(h, job, code, raw)
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "couldNotFinish" and record["phase"] == "preparing"
    assert record["failure"] == {"recorded": True}
    assert "gmsh" not in json.dumps(record) and "Traceback" not in json.dumps(record)
    assert "could not prepare Tritonia speaker. This has been recorded for the developers." in _words(record)
    # Recorded where it was diagnosed, not a second time here.
    assert incidents == []


def test_a_failure_the_user_can_act_on_says_what_to_do(h) -> None:
    job = _held(h)
    _refuse(h, job, "snapshot_changed", "raw detail")
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "couldNotFinish" and record["failure"]["recorded"] is False
    assert record["failure"]["message"] == "Tritonia speaker changed after Fusion asked WG to solve it."
    assert record["failure"]["nextStep"] == "Send it again from Fusion."
    assert "raw detail" not in json.dumps(record)


def test_a_solve_that_errors_could_not_finish_and_is_recorded_once_for_the_developers(h, incidents) -> None:
    job = _queued(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    _set(h, job["id"], "failed", status="error", error_message="BEM assembly diverged at 4 kHz")
    publisher.tick(h.jobs_store)
    publisher.tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "couldNotFinish" and record["phase"] == "solving"
    assert record["failure"] == {"recorded": True} and "diverged" not in json.dumps(record)
    assert "could not finish solving Tritonia speaker" in _words(record)
    assert len(incidents) == 1
    incident = incidents[0]
    assert (incident["kind"], incident["ids"]["job_id"], incident["ids"]["request_id"]) == (
        "cad-solve-failure", job["id"], "cmd-1")
    assert incident["evidence"]["error"] == "BEM assembly diverged at 4 kHz"
    assert incident["redaction_names"] == ["Tritonia speaker"]
    # A restarted WG reads the file and records nothing again.
    _publisher(h).tick(h.jobs_store)
    assert len(incidents) == 1


def test_a_stopped_solve_and_a_replaced_one_say_which(h) -> None:
    job = _queued(h)
    publisher = _publisher(h)
    _set(h, job["id"], "cancelled", status="cancelled")
    publisher.tick(h.jobs_store)
    assert (_status(h)["state"], _status(h)["cancelReason"]) == ("cancelled", "stopped")
    metadata = _row(h, job["id"])["task_metadata"]
    metadata["cad"]["superseded_by"] = "job-newer"
    _set(h, job["id"], task_metadata_json=json.dumps(metadata))
    publisher.tick(h.jobs_store)
    assert _status(h)["cancelReason"] == "replaced"


def test_a_dismissed_hold_reads_dismissed_and_a_removed_solved_run_stays_solved(h) -> None:
    job = _held(h)
    publisher = _publisher(h)
    _refuse(h, job, "setup_required", "Settings", status="held")
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "needsYou"
    h._loop.run(h.runtime.dismiss_cad_solve("cmd-1"))
    publisher.tick(h.jobs_store)
    assert (_status(h)["state"], _status(h)["cancelReason"]) == ("cancelled", "dismissed")
    # Pure: a finished request keeps its state when its run is removed later.
    solved = {"contract": "wg-request-status", "state": "solved", "seq": 3, "result": {"jobId": "j", "runNumber": 2}}
    receipt = {"commandId": "cmd-1", "outcome": "accepted", "kind": "prepare_and_solve", "jobId": "j"}
    kept = project_request_status(receipt, None, None, None, previous=solved)
    assert kept["state"] == "solved" and kept["result"] == {"jobId": "j", "runNumber": 2} and "seq" not in kept


def test_a_solve_again_child_is_followed_through_the_chain(h) -> None:
    job = _held(h)
    _refuse(h, job, "mesh_too_large", "This model meshes to 28,944 triangles.", status="held")
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    child = h._loop.run(h.runtime.solve_cad_again(job["id"], mesh_limit_override=True))
    publisher.tick(h.jobs_store)
    record = _status(h)
    assert record["job"]["jobId"] == child and record["state"] in {"preparing", "queued"}


def test_send_refusal_and_send_acceptance_need_no_job(h) -> None:
    _received(h)
    _receipt(h, "cmd-send", kind="receive_snapshot")
    _receipt(h, "cmd-bad", outcome="refused", reason="This file is not a request WG can read: not JSON.")
    _publisher(h).tick(None)
    assert _status(h, "cmd-send")["state"] == "accepted"
    refused = _status(h, "cmd-bad")
    assert refused["state"] == "refused" and "JSON" not in json.dumps(refused)


def test_a_solved_record_names_the_solved_mesh_and_the_symmetry_fallback() -> None:
    receipt = {"commandId": "cmd-1", "outcome": "accepted", "kind": "prepare_and_solve", "jobId": "job-9"}
    job = {"id": "job-9", "status": "complete", "run_number": 89, "config_json": {"type": "solve"},
           "mesh_stats": {"triangle_count": 19812}, "task_metadata": {"cad": {"operation_id": "cmd-1"}}}
    ingest = {"symmetry": {"domain_planes": ["XZ", "YZ"]},
              "findings": [{"kind": "symmetry-cut-unverified", "solved_domain": "full"}]}
    record = project_request_status(receipt, None, job, {}, ingest=ingest)
    assert record["result"] == {"jobId": "job-9", "runNumber": 89,
                                "mesh": {"trianglesSolved": 19812, "domainKind": "quarter", "solvedFraction": 0.25}}
    assert record["notice"] == "fullModel"
    assert protocol.validate_request_status({**record, "seq": 1})["result"]["mesh"]["domainKind"] == "quarter"
    healed = project_request_status(receipt, None, job, {}, ingest={"findings": [{"kind": "healing-performed"}]})
    assert healed["notice"] == "healed"
    assert healed["result"]["mesh"] == {"trianglesSolved": 19812, "domainKind": "full", "solvedFraction": 1.0}


# -- the publisher -----------------------------------------------------------------


def test_a_status_is_written_on_change_only_and_counts_its_writes(h) -> None:
    job = _queued(h)
    publisher = _publisher(h)
    assert publisher.tick(h.jobs_store) == 1
    assert publisher.tick(h.jobs_store) == 0
    before = (_acks(h) / "cmd-1.status.json").stat().st_mtime_ns
    _set(h, job["id"], stage_message="still queued")  # an event, no change of status
    assert publisher.tick(h.jobs_store) == 0
    assert (_acks(h) / "cmd-1.status.json").stat().st_mtime_ns == before
    assert _status(h)["seq"] == 1


def test_progress_is_coalesced_but_a_change_of_state_is_written_at_once(h) -> None:
    job = _queued(h)
    clock = Clock()
    publisher = _publisher(h, clock)
    _set(h, job["id"], status="running", progress=0.1)
    publisher.tick(h.jobs_store)
    _set(h, job["id"], progress=0.2)
    clock.now += 0.5
    assert publisher.tick(h.jobs_store) == 0
    assert _status(h)["progress"]["fraction"] == 0.1
    clock.now += PROGRESS_INTERVAL_S
    # No new event: the deferred progress is written once its interval passed.
    assert publisher.tick(h.jobs_store) == 1
    assert _status(h)["progress"]["fraction"] == 0.2
    _set(h, job["id"], progress=0.3)
    clock.now += 0.1
    publisher.tick(h.jobs_store)
    _set(h, job["id"], "completed", status="complete", progress=1.0)
    clock.now += 0.1
    assert publisher.tick(h.jobs_store) == 1
    assert _status(h)["state"] == "solved"


def test_a_lost_event_cursor_reconciles_every_request(h, monkeypatch) -> None:
    job = _queued(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    h.jobs_store.update_job(job["id"], status="running", progress=0.5)  # no event at all
    monkeypatch.setattr(h.jobs_store, "replay_events", lambda cursor: None)
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "solving"


def test_the_sweep_catches_a_change_no_event_announced(h) -> None:
    job = _queued(h)
    clock = Clock()
    publisher = _publisher(h, clock)
    publisher.tick(h.jobs_store)
    h.jobs_store.update_job(job["id"], status="running", progress=0.5)
    clock.now += 1
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "queued"
    clock.now += request_status.SWEEP_INTERVAL_S
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "solving"


def test_a_restarted_wg_continues_the_record_without_rewriting_it(h) -> None:
    job = _queued(h)
    _publisher(h).tick(h.jobs_store)
    path = _acks(h) / "cmd-1.status.json"
    before = path.read_bytes()
    assert _publisher(h).tick(h.jobs_store) == 0
    assert path.read_bytes() == before
    _set(h, job["id"], status="running", progress=0.2)
    restarted = _publisher(h)
    restarted.tick(h.jobs_store)
    assert (_status(h)["state"], _status(h)["seq"]) == ("solving", 2)


def test_a_request_received_while_wg_runs_is_picked_up(h) -> None:
    job = _queued(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    _second_return(h, "cmd-2")
    write_acknowledgement(h.data_dir, "cmd-2", "accepted", kind="receive_snapshot")
    publisher.tick(h.jobs_store)
    assert _status(h, "cmd-2")["state"] == "accepted"
    assert job["id"]


def test_a_held_status_file_is_retried_and_no_staging_file_is_left(h, monkeypatch) -> None:
    _queued(h)
    real_replace = os.replace
    failures: list[str] = []

    def replace(source, destination, *args, **kwargs):
        if str(destination).endswith(".status.json") and len(failures) < 3:
            failures.append(str(destination))
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "replace", replace)
    monkeypatch.setattr(solve_command, "_HELD_RETRY_SECONDS", 0)
    _publisher(h).tick(h.jobs_store)
    assert len(failures) == 3 and _status(h)["state"] == "queued"
    assert sorted(path.name for path in _acks(h).iterdir()) == ["cmd-1.json", "cmd-1.status.json"]


def test_a_status_that_cannot_be_written_keeps_the_last_one_and_tries_again(h, monkeypatch) -> None:
    job = _queued(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    real_replace = os.replace
    blocked = {"on": True}

    def replace(source, destination, *args, **kwargs):
        if blocked["on"] and str(destination).endswith(".status.json"):
            raise PermissionError(13, "held")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "replace", replace)
    monkeypatch.setattr(solve_command, "_HELD_RETRY_SECONDS", 0)
    _set(h, job["id"], status="running", progress=0.2)
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "queued"
    blocked["on"] = False
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "solving"
    assert sorted(path.name for path in _acks(h).iterdir()) == ["cmd-1.json", "cmd-1.status.json"]


# -- retention ---------------------------------------------------------------------


def _age(path: Path, seconds_ago: float, now: float) -> None:
    os.utime(path, (now - seconds_ago, now - seconds_ago))


def test_retention_keeps_requests_under_way_and_prunes_finished_pairs_together(tmp_path) -> None:
    data_dir = tmp_path / "data"
    folder = data_dir / "ipc/wglink" / SOLVE_ACKS_DIRECTORY
    now = 2_000_000_000.0
    for name, state in (("running", "solving"), ("held", "needsYou"), ("done", "solved"), ("fresh", "solved")):
        write_acknowledgement(data_dir, name, "accepted", job_id="job", kind="prepare_and_solve")
        (folder / f"{name}.status.json").write_text(json.dumps({"contract": "wg-request-status", "state": state}))
    write_acknowledgement(data_dir, "old-receipt", "accepted", job_id="job", kind="prepare_and_solve")
    old = REQUEST_STATUS_RETENTION_SECONDS + 10
    for name in ("running", "held", "done"):
        for suffix in (".json", ".status.json"):
            _age(folder / f"{name}{suffix}", old, now)
    _age(folder / "fresh.json", old, now)  # the pair's newest write counts
    _age(folder / "fresh.status.json", 3600, now)
    _age(folder / "old-receipt.json", 25 * 3600, now)

    assert prune_acknowledgements(data_dir, now=now) == 3
    assert sorted(path.name for path in folder.iterdir()) == [
        "fresh.json", "fresh.status.json", "held.json", "held.status.json",
        "running.json", "running.status.json",
    ]
    # The cap drops the oldest finished request, never one under way.
    assert prune_acknowledgements(data_dir, now=now, max_files=1) == 2
    assert sorted(path.name for path in folder.iterdir()) == [
        "held.json", "held.status.json", "running.json", "running.status.json",
    ]


# -- compatibility -----------------------------------------------------------------


def _released_reader():
    spec = importlib.util.spec_from_file_location("wglink_032_ack_reader", FIXTURES / "wglink-0.3.2-ack-reader.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_a_released_add_in_still_reads_the_receipt_and_never_sees_the_status(h) -> None:
    job = _queued(h)
    _publisher(h).tick(h.jobs_store)
    ipc = h.data_dir / "ipc/wglink"
    (ipc / "wg-capabilities.json").write_text(json.dumps(fusion_delivery.capabilities()))
    reader = _released_reader()
    assert reader.wg_acknowledges(ipc) is True
    assert reader.read_acknowledgement(ipc, "cmd-1", kind="prepare_and_solve", manifest_sha256=MANIFEST) == (
        "accepted", None)
    assert (ipc / SOLVE_ACKS_DIRECTORY / "cmd-1.status.json").exists()
    # The receipt's bytes are those schema 1 always wrote: sorted keys, two spaces.
    receipt_path = ipc / SOLVE_ACKS_DIRECTORY / "cmd-1.json"
    receipt = json.loads(receipt_path.read_text())
    assert receipt_path.read_bytes() == (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    assert set(receipt) == {"schemaVersion", "commandId", "operationId", "outcome", "reason", "jobId",
                            "digest", "manifestSha256", "kind", "at"}
    assert receipt["schemaVersion"] == 1 and receipt["jobId"] == job["id"]


def test_the_capability_is_advertised_only_while_the_consumer_runs() -> None:
    assert fusion_delivery.capabilities()["requestStatus"] == 1
    assert "requestStatus" not in fusion_delivery.capabilities(solve_delivery=False)


def test_every_state_wg_can_write_reads_without_jargon(h) -> None:
    records = [
        {"state": state, "kind": "prepare_and_solve", "documentName": "Tritonia speaker", **extra}
        for state, extra in (
            ("preparing", {}), ("queued", {}), ("solving", {"progress": {"fraction": 0.3}}),
            ("needsYou", {"prompt": {"code": "mesh_too_large", "nextStep": "Change the mesh resolution.",
                                     "options": [{"id": "solve_anyway", "label": "Solve anyway"}]}}),
            ("solved", {"result": {"jobId": "j", "runNumber": 3}, "notice": "cutModel"}),
            ("couldNotFinish", {"phase": "preparing", "failure": {"recorded": True}}),
            ("couldNotFinish", {"phase": "solving", "failure": {"recorded": True}}),
            ("cancelled", {"cancelReason": "dismissed"}), ("refused", {}),
        )
    ] + [{"state": "accepted", "kind": "receive_snapshot", "documentName": None}]
    for record in records:
        full = {"contract": "wg-request-status", "schemaVersion": 1, "seq": 1, "requestId": "cmd-1", **record}
        assert protocol.user_text_violations(_words(full)) == [], record
    # The next steps WG's holds and failed preparations put in a record pass too.
    from server.cadlink.operations import QUESTION_CODES, hold_details, preparation_failure, USER_ACTION_CODES

    for code in QUESTION_CODES:
        hold = hold_details(code, "")
        assert protocol.user_text_violations(" ".join(
            [hold["next_step"], *(option["label"] for option in hold["options"])])) == [], code
    for code in USER_ACTION_CODES:
        failure = preparation_failure(code, "Tritonia speaker")
        assert protocol.user_text_violations(f"{failure['message']} {failure['next_step'] or ''}") == [], code


def test_the_delivery_loop_step_publishes_and_reports_each_failure_once(h, caplog) -> None:
    import asyncio
    from types import SimpleNamespace

    from server.cadlink.api import _publish_request_status

    _queued(h)
    state = SimpleNamespace(data_dir=h.data_dir, cadlink_store=h.store,
                            jobs_runtime=SimpleNamespace(store=h.jobs_store))
    assert asyncio.run(_publish_request_status(state, None)) is None
    assert _status(h)["state"] == "queued"
    assert isinstance(state.cad_request_status, RequestStatusPublisher)

    def broken(_job_store):
        raise RuntimeError("database is locked")

    state.cad_request_status.tick = broken
    with caplog.at_level("WARNING"):
        error = asyncio.run(_publish_request_status(state, None))
        assert asyncio.run(_publish_request_status(state, error)) == error
    assert len([r for r in caplog.records if "request status" in r.getMessage()]) == 1


def test_a_job_that_is_not_a_fusion_requests_is_looked_up_once(h, monkeypatch) -> None:
    job = _queued(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    metadata = _row(h, job["id"])["task_metadata"]
    metadata["cad"]["operation_id"] = "manual-solve:abc"
    h.jobs_store.update_job(job["id"], task_metadata_json=json.dumps(metadata))
    publisher._job_requests.clear()
    lookups: list[str] = []
    real = h.jobs_store.get_job_row
    monkeypatch.setattr(h.jobs_store, "get_job_row", lambda job_id: lookups.append(job_id) or real(job_id))
    for progress in (0.1, 0.2, 0.3):
        _set(h, job["id"], status="running", progress=progress)
        publisher.tick(h.jobs_store)
    assert lookups.count(job["id"]) == 1
