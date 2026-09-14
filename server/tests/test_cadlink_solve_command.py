from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
import sqlite3

import pytest

from server.cadlink.operations import request_digest
from server.cadlink.preparation import retain_operation_snapshot
from server.cadlink.solve_command import (
    SOLVE_REQUESTS_DIRECTORY,
    SolveOutcomeConflict,
    _deliveries,
    collect_solve_deliveries,
    ledger_entry,
    record_outcome,
    solve_command_request,
)
from server.cadlink.store import CadLinkStore

from test_cad_preparation import Harness, _write_return


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def store(data_dir):
    registry = CadLinkStore.for_data_dir(data_dir)
    yield registry
    registry.close()


def _write_command(data_dir, bundle_path: str, manifest_sha256: str, command_id="cmd-1"):
    """One solve command as the add-in writes it: its own version-3 file."""

    folder = data_dir / "ipc" / "wglink" / SOLVE_REQUESTS_DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    # The file name is the producer's convention; WG reads the id inside. An id
    # too long for a file name gets a short one.
    name = command_id if len(command_id) <= 64 else hashlib.sha256(command_id.encode()).hexdigest()
    (folder / f"{name}.json").write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "target": "waveguide-generator",
                "commandId": command_id,
                "operationId": command_id,
                "returnId": "wgr_1",
                "bundlePath": bundle_path,
                "manifestSha256": manifest_sha256,
                "requestedAt": "2026-08-14T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def _held(data_dir):
    """The oldest solve command WG still holds on disk, or None."""

    return next((delivery.command for delivery in _deliveries(data_dir)), None)


def _write_bundle(workspace, name="speaker.wgreturn", body=b'{"document": {}}') -> str:
    bundle = workspace / "wgreturn" / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "wgreturn.json").write_bytes(body)
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _raw(data_dir, command_id):
    with closing(sqlite3.connect(data_dir / "db" / "cadlink.db")) as conn:
        return conn.execute(
            "SELECT * FROM cad_operations WHERE operation_id = ?", (command_id,)
        ).fetchone()


def _waiting(store) -> list[str]:
    """The solve operations the delivery loop starts, in the order it starts them."""

    rows = store.list_operations(kind="prepare_and_solve", states={"received"}, oldest_first=True)
    return [str(row["operation_id"]) for row in rows]


def _collect(harness: Harness):
    """One delivery pass as the backend runs it: retain, record, acknowledge."""

    return collect_solve_deliveries(
        harness.data_dir,
        harness.store,
        retain=lambda operation_id: retain_operation_snapshot(
            harness.store, harness.data_dir, harness.workspace.resolve(), operation_id
        ),
    )


def _rewrite_manifest(harness: Harness, bundle_path: str) -> None:
    """Change a return's evidence after Fusion asked for it, keeping it a valid return."""

    path = harness.workspace / bundle_path / "wgreturn.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["document"]["name"] = "changed"
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_a_matching_command_is_accepted_as_an_operation(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)

    assert collect_solve_deliveries(data_dir, store) is None

    row = store.get_operation("cmd-1")
    assert (row["kind"], row["state"]) == ("prepare_and_solve", "received")
    assert json.loads(row["inputs_json"])["bundle_path"] == "wgreturn/speaker.wgreturn"
    assert _waiting(store) == ["cmd-1"]
    assert _held(data_dir) is None


def test_a_bundle_that_changed_after_the_command_is_refused_when_prepared(tmp_path) -> None:
    harness = Harness(tmp_path)
    bundle_path, manifest = _write_return(harness.workspace)
    _write_command(harness.data_dir, bundle_path, manifest)
    # Fusion published, then something rewrote the evidence underneath it.
    _rewrite_manifest(harness, bundle_path)

    _collect(harness)
    summary = harness.prepare()

    assert (summary["state"], summary["reason"]) == ("rejected", "snapshot_invalid")
    assert "changed after Fusion asked" in summary["message"]
    # The delivery is one-shot, while the store keeps the terminal answer.
    assert ledger_entry(harness.store, "cmd-1")["state"] == "refused"
    assert _held(harness.data_dir) is None
    assert harness.submitted == []


def test_a_newer_return_does_not_cancel_a_command_for_an_older_one(
    tmp_path, data_dir, store
) -> None:
    """Explicit solve requests stay separate (CAD-OPERATIONS.md, "Ordering").

    The command names its bundle and that bundle's digest; both still match,
    so a return that arrived later -- from this document or another -- is no
    reason to refuse it.
    """

    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace, name="older.wgreturn")
    older = workspace / "wgreturn" / "older.wgreturn"
    newer = workspace / "wgreturn" / "newer.wgreturn"
    _write_bundle(workspace, name="newer.wgreturn", body=b'{"document": {"name": "B"}}')
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    _write_command(data_dir, "wgreturn/older.wgreturn", digest)

    assert collect_solve_deliveries(data_dir, store) is None

    assert json.loads(store.get_operation("cmd-1")["inputs_json"])["bundle_path"] == (
        "wgreturn/older.wgreturn"
    )
    assert _waiting(store) == ["cmd-1"]
    assert ledger_entry(store, "cmd-1") is None


def test_queued_commands_for_two_returns_are_each_kept_in_turn(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    first = _write_bundle(workspace, name="project-a.wgreturn")
    second = _write_bundle(
        workspace, name="project-b.wgreturn", body=b'{"document": {"name": "B"}}'
    )
    os.utime(workspace / "wgreturn" / "project-a.wgreturn", (1, 1))
    os.utime(workspace / "wgreturn" / "project-b.wgreturn", (2, 2))
    _write_command(data_dir, "wgreturn/project-a.wgreturn", first, command_id="cmd-a")
    assert collect_solve_deliveries(data_dir, store) is None
    _write_command(data_dir, "wgreturn/project-b.wgreturn", second, command_id="cmd-b")

    # The older command stays first in line, and B's newer return does not
    # take its place.
    assert collect_solve_deliveries(data_dir, store) is None
    assert _waiting(store) == ["cmd-a", "cmd-b"]

    record_outcome(store, "cmd-a", state="accepted", job_id="job-a")
    assert _waiting(store) == ["cmd-b"]
    assert ledger_entry(store, "cmd-a")["state"] == "accepted"


def test_a_command_pointing_outside_the_workspace_is_refused_when_prepared(tmp_path) -> None:
    harness = Harness(tmp_path)
    (harness.workspace / "wgreturn").mkdir(parents=True)
    _write_command(harness.data_dir, "../../elsewhere/evil.wgreturn", "sha256:whatever")

    _collect(harness)
    summary = harness.prepare()

    assert (summary["state"], summary["reason"]) == ("rejected", "snapshot_invalid")
    assert _held(harness.data_dir) is None
    assert harness.ingest.calls == [] and harness.submitted == []


def test_an_accepted_command_replays_its_job_instead_of_submitting_again(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    record_outcome(store, "cmd-1", state="accepted", job_id="job-7")

    result = collect_solve_deliveries(data_dir, store)

    assert result["outcome"]["state"] == "accepted"
    assert result["outcome"]["jobId"] == "job-7"
    assert _held(data_dir) is None
    assert _waiting(store) == []


def test_a_blocked_command_is_not_written_to_the_ledger(store) -> None:
    # Only terminal outcomes are recordable: a blocked command stays so the
    # user can satisfy the gate and run the same request.
    assert ledger_entry(store, "cmd-1") is None
    with pytest.raises(ValueError, match="blocked"):
        record_outcome(store, "cmd-1", state="blocked")
    assert ledger_entry(store, "cmd-1") is None


def test_a_second_different_outcome_is_rejected_and_the_first_survives(data_dir, store) -> None:
    first = record_outcome(store, "cmd-1", state="accepted", job_id="job-7")
    before = _raw(data_dir, "cmd-1")

    with pytest.raises(SolveOutcomeConflict) as refused:
        record_outcome(store, "cmd-1", state="refused", reason="Dismissed in the CAD Link panel.")
    assert refused.value.existing == first
    # A different job for the same accepted command conflicts as well.
    with pytest.raises(SolveOutcomeConflict):
        record_outcome(store, "cmd-1", state="accepted", job_id="job-8")

    assert ledger_entry(store, "cmd-1") == first
    assert _raw(data_dir, "cmd-1") == before


def test_repeating_the_same_outcome_is_idempotent(data_dir, store) -> None:
    first = record_outcome(store, "cmd-1", state="accepted", job_id="job-7")
    before = _raw(data_dir, "cmd-1")

    assert record_outcome(store, "cmd-1", state="accepted", job_id="job-7") == first
    assert _raw(data_dir, "cmd-1") == before


def test_an_outcome_recorded_for_a_delivered_request_keeps_its_identity(tmp_path) -> None:
    harness = Harness(tmp_path)
    bundle_path, manifest = _write_return(harness.workspace)
    _write_command(harness.data_dir, bundle_path, manifest)
    _rewrite_manifest(harness, bundle_path)
    command = _held(harness.data_dir)

    _collect(harness)
    harness.prepare()

    row = harness.row()
    assert (row["kind"], row["state"], row["legacy"]) == ("prepare_and_solve", "rejected", 0)
    assert row["request_digest"] == request_digest(
        "prepare_and_solve", *solve_command_request(command)
    )


def test_an_outcome_for_a_request_wg_no_longer_holds_is_kept_without_identity(store) -> None:
    entry = record_outcome(store, "cmd-9", state="accepted", job_id="job-9")

    row = store.get_operation("cmd-9")
    assert (row["legacy"], row["request_digest"], row["job_id"]) == (1, None, "job-9")
    assert ledger_entry(store, "cmd-9") == entry


def _accept_as_delivered(store, data_dir, manifest_sha256: str) -> None:
    """Persist cmd-1 as a delivery of wgreturn/speaker.wgreturn with this manifest."""

    _write_command(data_dir, "wgreturn/speaker.wgreturn", manifest_sha256)
    target, inputs = solve_command_request(_held(data_dir))
    store.accept_operation(
        "cmd-1", "prepare_and_solve", request_digest("prepare_and_solve", target, inputs),
        target, inputs,
    )


def test_an_outcome_carrying_a_different_request_is_refused_and_writes_nothing(
    data_dir, store
) -> None:
    _accept_as_delivered(store, data_dir, "sha256:" + "a" * 64)
    before = _raw(data_dir, "cmd-1")
    _write_command(data_dir, "wgreturn/speaker.wgreturn", "sha256:" + "b" * 64)

    with pytest.raises(SolveOutcomeConflict) as refused:
        record_outcome(
            store, "cmd-1", state="refused", reason="bundle changed",
            command=_held(data_dir),
        )

    assert refused.value.existing["state"] == "refused"
    assert "different request" in refused.value.existing["reason"]
    assert _raw(data_dir, "cmd-1") == before


def test_delivery_refuses_a_conflicting_file_and_leaves_the_operation(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    stored_manifest = _write_bundle(workspace)
    _accept_as_delivered(store, data_dir, stored_manifest)
    before = _raw(data_dir, "cmd-1")
    # The same command id arrives again, naming a manifest this bundle does
    # not have: WG refuses that delivery, and the operation it already holds
    # is neither answered with nor rewritten.
    _write_command(data_dir, "wgreturn/speaker.wgreturn", "sha256:" + "b" * 64)

    # The delivery is refused and removed. The operation holding the id is
    # unfinished, so its refusal is no answer: it would end the operation.
    assert collect_solve_deliveries(data_dir, store) is None
    assert _held(data_dir) is None
    assert _raw(data_dir, "cmd-1") == before
    assert _waiting(store) == ["cmd-1"]


def test_delivery_never_replays_an_outcome_to_a_different_request(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    _accept_as_delivered(store, data_dir, _write_bundle(workspace))
    record_outcome(
        store, "cmd-1", state="accepted", job_id="job-7", command=_held(data_dir)
    )
    before = _raw(data_dir, "cmd-1")
    other = _write_bundle(workspace, name="other.wgreturn", body=b'{"document": {"name": "other"}}')
    _write_command(data_dir, "wgreturn/other.wgreturn", other)

    result = collect_solve_deliveries(data_dir, store)

    assert result["outcome"]["state"] == "refused"
    assert result["outcome"]["jobId"] is None
    assert "different request" in result["outcome"]["reason"]
    assert _held(data_dir) is None
    assert _raw(data_dir, "cmd-1") == before


def test_delivery_never_replaces_a_held_request_with_a_different_one(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    # An unfinished operation holds cmd-1 for another manifest; the bundle on
    # disk is valid for the new request, which would otherwise be accepted.
    _accept_as_delivered(store, data_dir, "sha256:" + "a" * 64)
    held_digest = store.get_operation("cmd-1")["request_digest"]
    _write_command(data_dir, "wgreturn/speaker.wgreturn", _write_bundle(workspace))

    assert collect_solve_deliveries(data_dir, store) is None

    # The different request is removed and never accepted. The held one is not
    # refused for it: its preparation, from its own retained snapshot, decides.
    assert _held(data_dir) is None
    row = store.get_operation("cmd-1")
    assert (row["request_digest"], row["state"]) == (held_digest, "received")


def test_delivery_refuses_a_command_id_that_names_another_kind(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    target = {
        "document_id": "urn:doc",
        "design_id": "wgd_1",
        "instance_id": "wgi_1",
        "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:base"},
    }
    inputs = {"export_id": "wge_1"}
    store.accept_operation(
        "cmd-1", "update_link", request_digest("update_link", target, inputs), target, inputs
    )
    before = _raw(data_dir, "cmd-1")
    _write_command(data_dir, "wgreturn/speaker.wgreturn", _write_bundle(workspace))

    result = collect_solve_deliveries(data_dir, store)

    assert result["outcome"]["state"] == "refused"
    assert "different CAD operation" in result["outcome"]["reason"]
    assert _held(data_dir) is None
    assert _raw(data_dir, "cmd-1") == before


def test_a_long_command_id_still_round_trips(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    long_id = "cmd-" + "x" * 300
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest, command_id=long_id)

    assert collect_solve_deliveries(data_dir, store) is None
    entry = record_outcome(
        store, long_id, state="accepted", job_id="job-1", command=_held(data_dir)
    )
    assert entry["jobId"] == "job-1"
    # The pass consumed the file; Fusion delivering the command again gets
    # the recorded outcome back.
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest, command_id=long_id)
    assert collect_solve_deliveries(data_dir, store)["outcome"] == entry


def test_a_delivered_commands_first_outcome_stands_and_keeps_its_identity(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    assert collect_solve_deliveries(data_dir, store) is None

    accepted = record_outcome(store, "cmd-1", state="accepted", job_id="job-7")
    assert (accepted["state"], accepted["jobId"]) == ("accepted", "job-7")
    # The delivery named the command, so its row keeps its identity.
    assert store.get_operation("cmd-1")["legacy"] == 0

    with pytest.raises(SolveOutcomeConflict) as late:
        record_outcome(store, "cmd-1", state="refused", reason="Dismissed.")
    assert (late.value.existing["state"], late.value.existing["jobId"]) == ("accepted", "job-7")
    assert ledger_entry(store, "cmd-1")["jobId"] == "job-7"
