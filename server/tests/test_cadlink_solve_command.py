from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from server.cadlink.api import (
    SolveCommandOutcome,
    _pending_solve_command,
    post_solve_command_outcome,
)
from server.cadlink.operations import request_digest
from server.cadlink.solve_command import (
    SOLVE_REQUEST_FILENAME,
    SolveOutcomeConflict,
    clear_solve_command,
    ledger_entry,
    read_solve_command,
    record_outcome,
    solve_command_request,
)
from server.cadlink.store import CadLinkStore


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def store(data_dir):
    registry = CadLinkStore.for_data_dir(data_dir)
    yield registry
    registry.close()


def _write_command(data_dir, bundle_path: str, manifest_sha256: str, command_id="cmd-1"):
    folder = data_dir / "ipc" / "wglink"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SOLVE_REQUEST_FILENAME).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "target": "waveguide-generator",
                "commandId": command_id,
                "returnId": "wgr_1",
                "bundlePath": bundle_path,
                "manifestSha256": manifest_sha256,
                "requestedAt": "2026-08-14T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )


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


def test_a_matching_command_is_actionable(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"] is None
    assert result["command"]["commandId"] == "cmd-1"
    assert result["command"]["bundlePath"] == "wgreturn/speaker.wgreturn"


def test_a_bundle_that_changed_after_the_command_is_refused(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    # Fusion published, then something rewrote the evidence underneath it.
    _write_bundle(workspace, body=b'{"document": {"name": "changed"}}')

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "refused"
    assert "changed after Fusion asked" in result["outcome"]["reason"]
    # The marker is one-shot, while the ledger keeps the terminal answer.
    assert ledger_entry(store, "cmd-1")["state"] == "refused"
    assert read_solve_command(data_dir) is None


def test_a_command_for_an_older_return_is_refused_and_cleared(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace, name="older.wgreturn")
    older = workspace / "wgreturn" / "older.wgreturn"
    newer = workspace / "wgreturn" / "newer.wgreturn"
    _write_bundle(workspace, name="newer.wgreturn")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    _write_command(data_dir, "wgreturn/older.wgreturn", digest)

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "refused"
    assert result["outcome"]["reason"] == "Superseded by a newer return from Fusion."
    assert ledger_entry(store, "cmd-1")["state"] == "refused"
    assert read_solve_command(data_dir) is None


def test_a_command_pointing_outside_the_workspace_is_refused(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "wgreturn").mkdir(parents=True)
    _write_command(data_dir, "../../elsewhere/evil.wgreturn", "sha256:whatever")

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "refused"
    assert read_solve_command(data_dir) is None


def test_an_accepted_command_replays_its_job_instead_of_submitting_again(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    record_outcome(store, "cmd-1", state="accepted", job_id="job-7")

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "accepted"
    assert result["outcome"]["jobId"] == "job-7"
    assert read_solve_command(data_dir) is None


def test_clearing_only_removes_the_command_it_names(data_dir) -> None:
    _write_command(data_dir, "wgreturn/speaker.wgreturn", "sha256:a", command_id="cmd-1")

    # A newer command must survive an acknowledgement for the older one.
    assert clear_solve_command(data_dir, "cmd-other") is False
    assert read_solve_command(data_dir).command_id == "cmd-1"
    assert clear_solve_command(data_dir, "cmd-1") is True
    assert read_solve_command(data_dir) is None


def test_a_blocked_command_is_not_written_to_the_ledger(store) -> None:
    # Only terminal outcomes are recordable; the route maps 'blocked' to a
    # no-op so the user can satisfy the gate and run the same request.
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


def test_an_outcome_recorded_while_the_request_is_held_keeps_its_identity(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    _write_bundle(workspace, body=b'{"document": {"name": "changed"}}')
    command = read_solve_command(data_dir)

    _pending_solve_command(data_dir, workspace.resolve(), store)

    row = store.get_operation("cmd-1")
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
    target, inputs = solve_command_request(read_solve_command(data_dir))
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
            command=read_solve_command(data_dir),
        )

    assert refused.value.existing["state"] == "refused"
    assert "different request" in refused.value.existing["reason"]
    assert _raw(data_dir, "cmd-1") == before


def test_polling_refuses_a_conflicting_delivery_and_leaves_the_operation(
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

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "refused"
    assert "different request" in result["outcome"]["reason"]
    assert read_solve_command(data_dir) is None
    assert _raw(data_dir, "cmd-1") == before


def test_polling_never_replays_an_outcome_to_a_different_request(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    _accept_as_delivered(store, data_dir, _write_bundle(workspace))
    record_outcome(
        store, "cmd-1", state="accepted", job_id="job-7", command=read_solve_command(data_dir)
    )
    before = _raw(data_dir, "cmd-1")
    other = _write_bundle(workspace, name="other.wgreturn", body=b'{"document": {"name": "other"}}')
    _write_command(data_dir, "wgreturn/other.wgreturn", other)

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "refused"
    assert result["outcome"]["jobId"] is None
    assert "different request" in result["outcome"]["reason"]
    assert read_solve_command(data_dir) is None
    assert _raw(data_dir, "cmd-1") == before


def test_polling_never_hands_out_a_different_request_under_a_held_id(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    # An unfinished operation holds cmd-1 for another manifest; the bundle on
    # disk is valid for the new request, which would otherwise be actionable.
    _accept_as_delivered(store, data_dir, "sha256:" + "a" * 64)
    before = _raw(data_dir, "cmd-1")
    _write_command(data_dir, "wgreturn/speaker.wgreturn", _write_bundle(workspace))

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "refused"
    assert "different request" in result["outcome"]["reason"]
    assert read_solve_command(data_dir) is None
    assert _raw(data_dir, "cmd-1") == before


def test_polling_refuses_a_command_id_that_names_another_kind(tmp_path, data_dir, store) -> None:
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

    result = _pending_solve_command(data_dir, workspace.resolve(), store)

    assert result["outcome"]["state"] == "refused"
    assert "different CAD operation" in result["outcome"]["reason"]
    assert read_solve_command(data_dir) is None
    assert _raw(data_dir, "cmd-1") == before


def test_a_long_command_id_still_round_trips(tmp_path, data_dir, store) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    long_id = "cmd-" + "x" * 300
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest, command_id=long_id)

    assert _pending_solve_command(data_dir, workspace.resolve(), store)["outcome"] is None
    entry = record_outcome(
        store, long_id, state="accepted", job_id="job-1", command=read_solve_command(data_dir)
    )
    assert entry["jobId"] == "job-1"
    assert _pending_solve_command(data_dir, workspace.resolve(), store)["outcome"] == entry


def test_the_outcome_route_answers_a_conflict_with_the_first_outcome(
    tmp_path, data_dir, store
) -> None:
    workspace = tmp_path / "workspace"
    digest = _write_bundle(workspace)
    _write_command(data_dir, "wgreturn/speaker.wgreturn", digest)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(data_dir=str(data_dir), cadlink_store=store))
    )

    accepted = asyncio.run(
        post_solve_command_outcome(
            SolveCommandOutcome(commandId="cmd-1", state="accepted", jobId="job-7"), request
        )
    )
    assert (accepted["state"], accepted["jobId"], accepted["cleared"]) == ("accepted", "job-7", True)
    assert "conflict" not in accepted
    # The request file still named the command, so its row keeps its identity.
    assert store.get_operation("cmd-1")["legacy"] == 0

    late = asyncio.run(
        post_solve_command_outcome(
            SolveCommandOutcome(commandId="cmd-1", state="refused", reason="Dismissed."), request
        )
    )
    assert late["conflict"] is True
    assert (late["state"], late["jobId"]) == ("accepted", "job-7")
    assert ledger_entry(store, "cmd-1")["jobId"] == "job-7"
