"""Solve-command delivery through the CAD operation store.

Fusion delivers each solve command as its own file (delivery version 3). The
command id is the operation identity, and a consumer deletes only the file it
consumed, after the store holds the operation. What an older WGLink writes --
the single slot, or a version-2 file -- is refused with the remedy.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from server.cadlink.api import (
    SolveCommandOutcome,
    _pending_solve_command,
    post_solve_command_outcome,
)
from server.cadlink.operations import prepare_and_solve_request, request_digest
from server.cadlink.store import CadLinkStore


LEGACY = ".wg-solve-request.json"
V2_DIR = ".wg-solve-requests"
OLDER_ADDIN = "older than this Waveguide Generator"


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def store(data_dir):
    registry = CadLinkStore.for_data_dir(data_dir)
    yield registry
    registry.close()


def _ipc(data_dir) -> Path:
    folder = data_dir / "ipc" / "wglink"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _bundle(workspace, name="speaker.wgreturn", body=b'{"document": {}}') -> tuple[str, str]:
    bundle = workspace / "wgreturn" / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "wgreturn.json").write_bytes(body)
    return f"wgreturn/{name}", f"sha256:{hashlib.sha256(body).hexdigest()}"


def _payload(
    command_id, bundle_path, manifest, *, schema=3, requested_at="2026-09-13T01:00:00Z", **extra
):
    payload = {
        "schemaVersion": schema,
        "target": "waveguide-generator",
        "commandId": command_id,
        "returnId": "wgr_1",
        "bundlePath": bundle_path,
        "manifestSha256": manifest,
        "requestedAt": requested_at,
    }
    payload.update(extra)
    return payload


def _atomic_write(path: Path, payload) -> None:
    # As the add-in writes: a temporary file beside the target, then a replace.
    temporary = path.with_name(f"{path.name}.writing")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _legacy(data_dir, command_id, bundle_path, manifest, **kwargs) -> Path:
    """The single slot a WGLink older than delivery version 3 wrote."""

    path = _ipc(data_dir) / LEGACY
    _atomic_write(path, _payload(command_id, bundle_path, manifest, schema=1, **kwargs))
    return path


def _file(data_dir, command_id, bundle_path, manifest, *, schema=3, **kwargs) -> Path:
    folder = _ipc(data_dir) / V2_DIR
    folder.mkdir(exist_ok=True)
    path = folder / f"{command_id}.json"
    _atomic_write(path, _payload(command_id, bundle_path, manifest, schema=schema, **kwargs))
    return path


def _raw(data_dir, command_id):
    with closing(sqlite3.connect(data_dir / "db" / "cadlink.db")) as conn:
        return conn.execute(
            "SELECT * FROM cad_operations WHERE operation_id = ?", (command_id,)
        ).fetchone()


def _operation_ids(data_dir) -> list[str]:
    with closing(sqlite3.connect(data_dir / "db" / "cadlink.db")) as conn:
        rows = conn.execute("SELECT operation_id FROM cad_operations ORDER BY operation_id")
        return [row[0] for row in rows]


def _delivery_files(data_dir) -> list[str]:
    """Every solve delivery or claim file WG could still act on."""

    folder = _ipc(data_dir)
    names = [path.name for path in folder.iterdir() if path.name.startswith(".wg-solve")]
    requests = folder / V2_DIR
    if requests.is_dir():
        names += [f"{V2_DIR}/{path.name}" for path in requests.iterdir()]
    return sorted(name for name in names if name != V2_DIR)


def _poll(data_dir, workspace, store):
    return _pending_solve_command(data_dir, workspace.resolve(), store)


def _report(data_dir, store, command_id, state, **fields):
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(data_dir=str(data_dir), cadlink_store=store))
    )
    return asyncio.run(
        post_solve_command_outcome(
            SolveCommandOutcome(commandId=command_id, state=state, **fields), request
        )
    )


def test_a_newer_marker_written_during_the_acknowledgement_survives(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    folder = _ipc(data_dir).resolve()
    original_unlink = Path.unlink
    injected: list[str] = []

    def unlink_after_a_newer_marker_lands(self, *args, **kwargs):
        # Fusion writes cmd-2 into the single slot between the consumer's read
        # of cmd-1 and its delete.
        if not injected and folder in Path(self).resolve().parents:
            injected.append(str(self))
            _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:05Z")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_after_a_newer_marker_lands)

    first = _poll(data_dir, workspace, store)
    assert first["command"]["commandId"] == "cmd-1"
    _report(data_dir, store, "cmd-1", "accepted", jobId="job-1")
    assert injected, "the consumer never deleted a delivery file"

    second = _poll(data_dir, workspace, store)

    assert second["command"] is not None, "cmd-2 was deleted by cmd-1's acknowledgement"
    assert (second["command"]["commandId"], second["outcome"]) == ("cmd-2", None)
    assert store.get_operation("cmd-1")["state"] == "accepted"
    assert store.get_operation("cmd-2")["state"] == "received"


def test_one_command_delivered_twice_is_one_operation(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    first = _poll(data_dir, workspace, store)
    assert (first["command"]["commandId"], first["outcome"]) == ("cmd-1", None)
    _file(data_dir, "cmd-1", bundle_path, manifest, operationId="cmd-1")

    result = _poll(data_dir, workspace, store)

    assert (result["command"]["commandId"], result["outcome"]) == ("cmd-1", None)
    # The response shape is the one the frontend has always read.
    assert set(result["command"]) == {
        "commandId", "returnId", "bundlePath", "manifestSha256", "requestedAt",
    }
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert _delivery_files(data_dir) == []
    _report(data_dir, store, "cmd-1", "accepted", jobId="job-1")
    # Nothing is left to execute a second time.
    assert _poll(data_dir, workspace, store) == {"command": None}


def test_a_duplicate_after_the_outcome_replays_it_instead_of_running_again(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    _report(data_dir, store, "cmd-1", "accepted", jobId="job-1")
    before = _raw(data_dir, "cmd-1")
    _file(data_dir, "cmd-1", bundle_path, manifest)

    result = _poll(data_dir, workspace, store)

    assert (result["outcome"]["state"], result["outcome"]["jobId"]) == ("accepted", "job-1")
    assert _delivery_files(data_dir) == []
    assert _raw(data_dir, "cmd-1") == before
    assert _poll(data_dir, workspace, store) == {"command": None}


def test_a_different_request_under_a_held_id_is_refused_and_leaves_the_operation(
    data_dir, workspace, store, caplog
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    before = _raw(data_dir, "cmd-1")
    conflicting = _file(data_dir, "cmd-1", bundle_path, "sha256:" + "b" * 64)

    with caplog.at_level(logging.WARNING):
        result = _poll(data_dir, workspace, store)

    # The conflicting file is removed and its refusal recorded.
    assert not conflicting.exists()
    assert any(
        "cmd-1" in record.getMessage() and "different request" in record.getMessage()
        for record in caplog.records
    )
    assert _raw(data_dir, "cmd-1") == before
    # The operation holding the id is unfinished, so it is still what is handed
    # out: a refusal under its id would end it for the client.
    assert (result["command"]["manifestSha256"], result["outcome"]) == (manifest, None)


def test_a_refused_conflict_never_ends_the_held_command_for_the_client(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    # The client's rule: an outcome under a command id ends that command, and
    # an ended command handed out again is ignored.
    ended: set[str] = set()

    def client_poll():
        result = _poll(data_dir, workspace, store)
        if result["command"] is not None and result["outcome"] is not None:
            ended.add(result["command"]["commandId"])
        return result

    assert client_poll()["command"]["commandId"] == "cmd-1"
    _file(data_dir, "cmd-1", bundle_path, "sha256:" + "b" * 64)
    _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:05Z")

    assert client_poll()["command"]["commandId"] == "cmd-1"
    assert "cmd-1" not in ended
    _report(data_dir, store, "cmd-1", "accepted", jobId="job-1")
    after = client_poll()

    assert (after["command"]["commandId"], after["outcome"]) == ("cmd-2", None)
    assert "cmd-2" not in ended


def test_a_crash_between_claim_and_persist_is_recovered_on_the_next_poll(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    marker = _file(data_dir, "cmd-1", bundle_path, manifest)
    real_accept = CadLinkStore.accept_operation

    def backend_stops(self, *args, **kwargs):
        raise RuntimeError("backend stopped")

    monkeypatch.setattr(CadLinkStore, "accept_operation", backend_stops)
    with pytest.raises(RuntimeError, match="backend stopped"):
        _poll(data_dir, workspace, store)
    # Claimed, not yet persisted: the slot is free for Fusion and the claim
    # still holds the request.
    assert not marker.exists()
    assert len(_delivery_files(data_dir)) == 1
    assert store.get_operation("cmd-1") is None

    monkeypatch.setattr(CadLinkStore, "accept_operation", real_accept)
    result = _poll(data_dir, workspace, store)

    assert (result["command"]["commandId"], result["outcome"]) == ("cmd-1", None)
    assert _delivery_files(data_dir) == []
    assert _operation_ids(data_dir) == ["cmd-1"]


def test_a_failed_delete_after_persisting_recovers_the_same_operation(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    original_unlink = Path.unlink
    refused: list[str] = []

    def held_open_once(self, *args, **kwargs):
        if not refused:
            refused.append(str(self))
            raise PermissionError(13, "The process cannot access the file")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held_open_once)

    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    assert refused and len(_delivery_files(data_dir)) == 1
    result = _poll(data_dir, workspace, store)

    assert (result["command"]["commandId"], result["outcome"]) == ("cmd-1", None)
    assert _delivery_files(data_dir) == []
    assert _operation_ids(data_dir) == ["cmd-1"]


def test_a_marker_the_writer_still_holds_is_retried_on_the_next_poll(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    marker = _file(data_dir, "cmd-1", bundle_path, manifest)
    real_rename = os.rename
    held: list[str] = []

    def rename_while_held(source, destination, *args, **kwargs):
        # Windows refuses to rename a file another process has open.
        if not held:
            held.append(str(source))
            raise PermissionError(13, "The process cannot access the file")
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename_while_held)

    assert _poll(data_dir, workspace, store) == {"command": None}
    assert marker.exists()
    assert store.get_operation("cmd-1") is None
    result = _poll(data_dir, workspace, store)

    assert held
    assert (result["command"]["commandId"], result["outcome"]) == ("cmd-1", None)
    assert not marker.exists()


def test_two_per_command_files_are_surfaced_oldest_first_and_neither_is_lost(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    # The age order is cmd-2, cmd-1, cmd-3: neither name order, nor the write
    # order, nor newest first.
    _file(data_dir, "cmd-3", bundle_path, manifest, requested_at="2026-09-13T01:00:10Z")
    _file(data_dir, "cmd-1", bundle_path, manifest, requested_at="2026-09-13T01:00:05Z")
    _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:00Z")

    first = _poll(data_dir, workspace, store)
    assert (first["command"]["commandId"], first["outcome"]) == ("cmd-2", None)
    # A command that is waiting on the user stays first in line: a later
    # request never takes its place.
    _report(data_dir, store, "cmd-2", "blocked")
    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-2"
    _report(data_dir, store, "cmd-2", "accepted", jobId="job-2")

    second = _poll(data_dir, workspace, store)
    assert (second["command"]["commandId"], second["outcome"]) == ("cmd-1", None)
    _report(data_dir, store, "cmd-1", "accepted", jobId="job-1")

    third = _poll(data_dir, workspace, store)

    assert (third["command"]["commandId"], third["outcome"]) == ("cmd-3", None)
    assert _operation_ids(data_dir) == ["cmd-1", "cmd-2", "cmd-3"]
    assert _delivery_files(data_dir) == []


def test_an_operation_accepted_earlier_stays_ahead_of_a_later_delivery(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest, requested_at="2026-09-13T02:00:00Z")
    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    # Delivered afterwards, even though its producer stamped an older time.
    _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:00Z")

    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    assert _operation_ids(data_dir) == ["cmd-1", "cmd-2"]


def test_files_wg_cannot_read_are_left_in_place_and_block_nothing(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    folder = _ipc(data_dir) / V2_DIR
    folder.mkdir()
    future = folder / "cmd-9.json"
    future.write_text(json.dumps(_payload("cmd-9", bundle_path, manifest, schema=4)))
    torn = folder / "cmd-8.json"
    torn.write_text("{", encoding="utf-8")
    mismatched = _file(data_dir, "cmd-7", bundle_path, manifest, operationId="cmd-other")
    staging = folder / ".cmd-6.json.tmp"
    staging.write_text(json.dumps(_payload("cmd-6", bundle_path, manifest, schema=2)))
    _file(data_dir, "cmd-1", bundle_path, manifest)

    result = _poll(data_dir, workspace, store)

    assert (result["command"]["commandId"], result["outcome"]) == ("cmd-1", None)
    assert future.exists() and torn.exists() and mismatched.exists() and staging.exists()
    assert _operation_ids(data_dir) == ["cmd-1"]


def test_an_outcome_for_a_held_operation_ignores_a_conflicting_file_still_waiting(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    # A conflicting redelivery lands before the browser reports its result.
    _file(data_dir, "cmd-1", bundle_path, "sha256:" + "c" * 64)

    entry = _report(data_dir, store, "cmd-1", "accepted", jobId="job-1")

    assert (entry["state"], entry["jobId"], entry["cleared"]) == ("accepted", "job-1", True)
    assert "conflict" not in entry
    row = store.get_operation("cmd-1")
    assert row["request_digest"] == request_digest(
        "prepare_and_solve",
        *prepare_and_solve_request(
            return_id="wgr_1", bundle_path=bundle_path, manifest_sha256=manifest
        ),
    )
    # The waiting file is then refused as a delivery in its own right.
    refused = _poll(data_dir, workspace, store)
    assert "different request" in refused["outcome"]["reason"]
    assert store.get_operation("cmd-1")["job_id"] == "job-1"


def test_operations_can_be_listed_oldest_first(store) -> None:
    for operation_id in ("cmd-z", "cmd-y", "cmd-x"):
        target, inputs = prepare_and_solve_request(
            return_id="", bundle_path=f"wgreturn/{operation_id}.wgreturn",
            manifest_sha256="sha256:1",
        )
        store.accept_operation(
            operation_id, "prepare_and_solve",
            request_digest("prepare_and_solve", target, inputs), target, inputs,
        )

    listed = store.list_operations(oldest_first=True)

    assert [row["operation_id"] for row in listed] == ["cmd-z", "cmd-y", "cmd-x"]


@pytest.mark.parametrize("writer", ["single-slot", "version-2-file"])
def test_a_command_from_an_older_addin_is_refused_with_the_remedy(
    data_dir, workspace, store, writer
) -> None:
    """Decision 4: the legacy route is gone, and nothing is silently dropped.

    A WGLink older than delivery version 3 writes the single slot, or a
    version-2 file. WG answers it with a refusal that names the remedy, which
    the browser shows, and records it; it is never run.
    """

    bundle_path, manifest = _bundle(workspace)
    if writer == "single-slot":
        delivered = _legacy(data_dir, "cmd-old", bundle_path, manifest)
    else:
        delivered = _file(data_dir, "cmd-old", bundle_path, manifest, schema=2)

    result = _poll(data_dir, workspace, store)

    assert result["command"]["commandId"] == "cmd-old"
    assert result["outcome"]["state"] == "refused"
    assert OLDER_ADDIN in result["outcome"]["reason"]
    assert "Restart Fusion" in result["outcome"]["reason"]
    assert not delivered.exists() and _delivery_files(data_dir) == []
    assert store.get_operation("cmd-old")["state"] == "rejected"
    assert _poll(data_dir, workspace, store) == {"command": None}


def test_an_older_addins_repeat_of_a_held_command_does_not_end_it(
    data_dir, workspace, store
) -> None:
    """Refusing the old format must not refuse an operation WG already holds."""

    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    _legacy(data_dir, "cmd-1", bundle_path, manifest)

    result = _poll(data_dir, workspace, store)

    assert (result["command"]["commandId"], result["outcome"]) == ("cmd-1", None)
    assert store.get_operation("cmd-1")["state"] == "received"


def test_a_command_whose_job_already_exists_is_reconciled_not_handed_out_again(
    data_dir, workspace, store
) -> None:
    """The parked-command 409 (WP-14's compatibility edge), closed at the source.

    The browser created the job under ``cad-solve:<commandId>`` and its report
    never arrived -- a lost acknowledgement, a reload, an upgrade. Handing the
    command out again made the browser submit it again; a client that builds
    the request differently (``engine: 'auto'`` where an older one sent
    ``'metal'``) then met ``submission_key_conflict`` and stayed parked. The job
    is the command's outcome: WG records it and answers with it.
    """

    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, workspace, store)["command"]["commandId"] == "cmd-1"
    jobs = {"cad-solve:cmd-1": "job-7"}

    result = _pending_solve_command(data_dir, workspace.resolve(), store, jobs.get)

    assert result["command"]["commandId"] == "cmd-1"
    assert (result["outcome"]["state"], result["outcome"]["jobId"]) == ("accepted", "job-7")
    assert store.get_operation("cmd-1")["job_id"] == "job-7"
    # Nothing is handed out again, and a later report of the same job agrees.
    assert _pending_solve_command(data_dir, workspace.resolve(), store, jobs.get) == {
        "command": None
    }
    assert _report(data_dir, store, "cmd-1", "accepted", jobId="job-7")["state"] == "accepted"


def test_a_command_with_no_job_under_its_key_is_handed_out_as_before(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)

    result = _pending_solve_command(data_dir, workspace.resolve(), store, {}.get)

    assert (result["command"]["commandId"], result["outcome"]) == ("cmd-1", None)
