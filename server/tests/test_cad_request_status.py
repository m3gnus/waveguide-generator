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
    assert record["seq"] >= 4
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


@pytest.mark.parametrize("code", ["preparation_failed", "submission_refused", "snapshot_invalid"])
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
    kept, incident = project_request_status(receipt, None, None, None, previous=solved)
    # The run is gone: no reference to it is kept.
    assert kept["state"] == "solved" and "result" not in kept and "job" not in kept and "seq" not in kept
    assert incident is None
    assert protocol.request_status_text({**kept, "seq": 1})["actions"][0]["id"] == "openWg"


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
    record, _ = project_request_status(receipt, None, job, {}, ingest=ingest)
    assert record["result"] == {"jobId": "job-9", "runNumber": 89,
                                "mesh": {"trianglesSolved": 19812, "domainKind": "quarter", "solvedFraction": 0.25}}
    assert record["notice"] == "fullModel"
    assert protocol.validate_request_status({**record, "seq": 1})["result"]["mesh"]["domainKind"] == "quarter"
    healed, _ = project_request_status(receipt, None, job, {}, ingest={"findings": [{"kind": "healing-performed"}]})
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
    seq = _status(h)["seq"]
    _set(h, job["id"], status="running", progress=0.2)
    restarted = _publisher(h)
    restarted.tick(h.jobs_store)
    assert _status(h)["state"] == "solving" and _status(h)["seq"] > seq


def test_seq_keeps_rising_when_the_previous_file_is_unreadable(h, monkeypatch) -> None:
    job = _queued(h)
    monkeypatch.setattr(request_status, "_wall_ms", lambda: 5_000)
    _publisher(h).tick(h.jobs_store)
    assert _status(h)["seq"] == 5_000
    (_acks(h) / "cmd-1.status.json").write_text("{torn", encoding="utf-8")
    monkeypatch.setattr(request_status, "_wall_ms", lambda: 7_000)
    _set(h, job["id"], status="running", progress=0.2)
    _publisher(h).tick(h.jobs_store)
    assert _status(h)["seq"] == 7_000 and _status(h)["state"] == "solving"
    # A clock that went back still moves seq forward.
    monkeypatch.setattr(request_status, "_wall_ms", lambda: 1)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    _set(h, job["id"], status="queued")
    publisher.tick(h.jobs_store)
    assert _status(h)["seq"] == 7_001


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


def _pair(folder: Path, data_dir: Path, name: str, state: str | None, *, receipt: bool = True, raw: str | None = None) -> None:
    if receipt:
        write_acknowledgement(data_dir, name, "accepted", job_id="job", kind="prepare_and_solve")
    if raw is not None:
        (folder / f"{name}.status.json").write_text(raw, encoding="utf-8")
    elif state is not None:
        (folder / f"{name}.status.json").write_text(json.dumps({
            "contract": "wg-request-status", "schemaVersion": 1, "seq": 1, "requestId": name, "state": state}))


def test_retention_follows_each_request_and_never_drops_one_under_way(tmp_path) -> None:
    data_dir = tmp_path / "data"
    folder = data_dir / "ipc/wglink" / SOLVE_ACKS_DIRECTORY
    folder.mkdir(parents=True)
    now = 2_000_000_000.0
    for name, state in (("running", "solving"), ("held", "needsYou"), ("done", "solved"), ("fresh", "solved"),
                        ("newer", "teleporting")):
        _pair(folder, data_dir, name, state)
    _pair(folder, data_dir, "torn", None, raw="{torn")
    _pair(folder, data_dir, "waiting", None)                       # receipt, no status yet
    _pair(folder, data_dir, "orphan", "solving", receipt=False)    # status, receipt gone
    (folder / ".cmd-9.json.abc").write_text("{", encoding="utf-8")  # a staging file
    old = REQUEST_STATUS_RETENTION_SECONDS + 10
    for path in folder.iterdir():
        _age(path, old, now)
    _age(folder / "fresh.status.json", 3600, now)  # the pair's newest write counts
    _age(folder / "waiting.json", 3600, now)       # still within its grace

    removed = prune_acknowledgements(data_dir, now=now)
    names = sorted(path.name for path in folder.iterdir())
    assert names == [
        "fresh.json", "fresh.status.json", "held.json", "held.status.json",
        "newer.json", "newer.status.json",  # an unknown state: kept, but not for ever
        "running.json", "running.status.json", "waiting.json",
    ]
    assert removed == 6  # done (2), torn (2), the orphan, the staging file
    # A state this WG does not know is dropped after its own bound.
    for suffix in (".json", ".status.json"):
        _age(folder / f"newer{suffix}", solve_command.REQUEST_STATUS_UNKNOWN_STATE_SECONDS + 10, now)
    assert prune_acknowledgements(data_dir, now=now) == 2
    # The waiting receipt's grace ends too.
    _age(folder / "waiting.json", REQUEST_STATUS_RETENTION_SECONDS + 10, now)
    assert prune_acknowledgements(data_dir, now=now) == 1
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
    from server.cadlink.operations import QUESTION_CODES, hold_details, preparation_failure, USER_WORDED_FAILURE_CODES

    for code in QUESTION_CODES:
        hold = hold_details(code, "")
        assert protocol.user_text_violations(" ".join(
            [hold["next_step"], *(option["label"] for option in hold["options"])])) == [], code
    for code in USER_WORDED_FAILURE_CODES:
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


# -- review round: what reaches Fusion, and what is recorded -----------------------


def test_an_unlisted_refusal_never_shows_its_words_and_is_recorded_once(h, incidents) -> None:
    job = _held(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)  # needsYou: the publisher saw the request before
    raw = "Legacy CAD solve could not be migrated: KeyError('bundle_path')"
    _refuse(h, job, "request_payload_invalid", raw)
    publisher.tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "couldNotFinish" and record["failure"] == {"recorded": True}
    assert "KeyError" not in json.dumps(record) and "migrated" not in json.dumps(record)
    assert "recorded for the developers" in _words(record)
    assert [(item["kind"], item["evidence"]["code"]) for item in incidents] == [
        ("cad-unclassified-failure", "request_payload_invalid")]
    publisher.tick(h.jobs_store)
    assert len(incidents) == 1


def test_user_words_that_read_as_internal_detail_become_a_recorded_failure(h, incidents) -> None:
    job = _held(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    _refuse(h, job, "excitation_refused", "The bundle manifest names an operation with no gate.")
    publisher.tick(h.jobs_store)
    record = _status(h)
    assert record["failure"] == {"recorded": True} and "no gate" not in json.dumps(record)
    assert [item["kind"] for item in incidents] == ["cad-unclassified-failure"]


def test_an_interrupted_preparation_says_so_without_claiming_a_record(h, incidents) -> None:
    """WG quit, crashed or restarted while preparing: not WG's failure, nothing recorded."""

    job = _held(h)
    _refuse(h, job, "interrupted", "Preparation was interrupted because Waveguide Generator restarted "
            "before the solve was ready. Press Solve again.")
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert record["state"] == "couldNotFinish"
    assert record["failure"] == {"recorded": False,
                                 "message": "Preparing Tritonia speaker stopped when Waveguide Generator closed.",
                                 "nextStep": "Press Solve to prepare it again."}
    assert "recorded" not in _words(record) and incidents == []


def test_failures_from_before_wg_watched_are_not_recorded_again(h, incidents) -> None:
    """The first start after an upgrade (no status file yet) records nothing for old failures."""

    job = _queued(h)
    _set(h, job["id"], "failed", status="error", error_message="boom")
    _publisher(h).tick(h.jobs_store)
    assert _status(h)["state"] == "couldNotFinish" and incidents == []
    # A garbled status file is no evidence of what WG saw either.
    (_acks(h) / "cmd-1.status.json").write_text("[1, 2", encoding="utf-8")
    _publisher(h).tick(h.jobs_store)
    assert incidents == []


def test_a_request_that_arrives_and_fails_between_two_passes_is_recorded(h, incidents) -> None:
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)  # WG running, no requests yet
    job = _queued(h)
    _set(h, job["id"], "failed", status="error", error_message="boom")
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "couldNotFinish"
    assert [item["kind"] for item in incidents] == ["cad-solve-failure"]


def test_a_status_that_keeps_failing_to_write_warns_once_then_is_recorded(h, monkeypatch, caplog, incidents) -> None:
    _queued(h)
    real_replace = os.replace

    def replace(source, destination, *args, **kwargs):
        if str(destination).endswith(".status.json"):
            raise PermissionError(13, "held")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "replace", replace)
    monkeypatch.setattr(solve_command, "_HELD_RETRY_SECONDS", 0)
    publisher = _publisher(h)
    with caplog.at_level("WARNING"):
        for _ in range(request_status.WRITE_FAILURE_INCIDENT_AFTER + 5):
            publisher.tick(h.jobs_store)
    warnings = [r for r in caplog.records if "Could not write the status" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelname == "WARNING"
    assert [item["kind"] for item in incidents] == ["cad-status-write-failure"]


def test_hostile_status_files_are_read_through_the_validator(h) -> None:
    job = _queued(h)
    path = _acks(h) / "cmd-1.status.json"
    for hostile in ('{"contract": "wg-request-status", "schemaVersion": 1, "seq": 1e400, "state": ["x"]}',
                    '{"contract": "wg-request-status", "schemaVersion": 1, "requestId": "cmd-1", "seq": 3, '
                    '"state": {"a": 1}, "prompt": {"options": 5}}', "null", "[]", "\u0000"):
        path.write_text(hostile, encoding="utf-8")
        _publisher(h).tick(h.jobs_store)
        assert _status(h)["state"] == "queued" and protocol.validate_request_status(_status(h)) is not None
    assert job["id"]


def test_every_written_record_is_its_own_validated_reading(h) -> None:
    job = _queued(h)
    _set(h, job["id"], status="running", progress=0.5)
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert protocol.validate_request_status(record, request_id="cmd-1") == record


def test_a_receipt_without_a_kind_is_a_solve_everywhere(h) -> None:
    job = _queued(h)
    assert write_acknowledgement(h.data_dir, "cmd-1", "accepted", job_id=job["id"], manifest_sha256=MANIFEST)
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert (record["kind"], record["state"], record["job"]["jobId"]) == ("prepare_and_solve", "queued", job["id"])


def test_a_receipt_rewritten_in_place_is_read_again(h) -> None:
    _queued(h)
    publisher = _publisher(h)
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "queued"
    path = _acks(h) / "cmd-1.json"
    assert write_acknowledgement(h.data_dir, "cmd-1", "refused", reason="gone", manifest_sha256=MANIFEST)
    os.utime(path, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
    publisher.tick(h.jobs_store)
    assert _status(h)["state"] == "refused"


def test_a_solve_cancelled_before_admission_reads_cancelled_not_refused(h) -> None:
    _received(h)
    h.store.request_cancel("cmd-1")
    row = h.store.get_operation("cmd-1")
    _receipt(h, outcome="refused", reason="This solve was cancelled before admission.")
    _publisher(h).tick(h.jobs_store)
    record = _status(h)
    assert row["state"] in {"cancelled", "cancel_requested"}
    if row["state"] == "cancelled":
        assert (record["state"], record["cancelReason"]) == ("cancelled", "stopped")
    projected, _ = project_request_status(
        {"commandId": "cmd-1", "outcome": "refused", "kind": "prepare_and_solve"},
        {"state": "cancelled", "kind": "prepare_and_solve"}, None, None)
    assert (projected["state"], projected["cancelReason"]) == ("cancelled", "stopped")


def test_stop_ends_publishing_and_waits_for_a_tick_under_way(h) -> None:
    import threading

    _queued(h)
    publisher = _publisher(h)
    entered, release = threading.Event(), threading.Event()
    real = publisher._publish

    def slow(*args, **kwargs):
        entered.set()
        release.wait(5)
        return real(*args, **kwargs)

    publisher._publish = slow
    worker = threading.Thread(target=publisher.tick, args=(h.jobs_store,))
    worker.start()
    assert entered.wait(5)
    assert publisher.stop(timeout=0.05) is False  # still inside the tick
    release.set()
    assert publisher.stop(timeout=5) is True
    worker.join(5)
    assert publisher.tick(h.jobs_store) == 0


def test_one_request_whose_record_cannot_be_judged_never_stops_the_others(h) -> None:
    _queued(h)
    _second_return(h, "cmd-2")
    write_acknowledgement(h.data_dir, "cmd-2", "accepted", kind="receive_snapshot")
    publisher = _publisher(h)
    publisher._records["cmd-1"] = {"state": ["unhashable"]}  # as if a broken record got in
    publisher._receipts["cmd-1"] = {"schemaVersion": 1, "commandId": "cmd-1", "outcome": "accepted"}
    publisher.tick(h.jobs_store)
    assert _status(h, "cmd-2")["state"] == "accepted"
