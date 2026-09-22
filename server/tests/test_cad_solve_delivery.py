"""Solve-command delivery through the CAD operation store.

Fusion delivers each solve command as its own file (delivery version 3). The
command id is the operation identity, and a consumer deletes only the file it
consumed, after the store holds the operation. What an older WGLink writes --
the single slot, or a version-2 file -- is refused with the remedy. The
backend's delivery loop is the one consumer: each pass collects the files
(``collect_solve_deliveries``) and prepares what it accepted
(``run_delivery_pass``).
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
from typing import Any

import pytest

from server.cadlink.operations import prepare_and_solve_request, request_digest
from server.cadlink.preparation import (
    DeliveryPassReporter,
    PreparationContext,
    reconcile_with_jobs,
    run_delivery_pass,
)
from server.cadlink import solve_command
from server.cadlink.solve_command import collect_solve_deliveries, record_outcome
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


def _poll(data_dir, store):
    """One collection pass of the backend's delivery loop."""

    return collect_solve_deliveries(data_dir, store)


def _waiting(store) -> list[str]:
    """Unfinished solve operations, in the order WG accepted them."""

    rows = store.list_operations(
        kind="prepare_and_solve",
        states={"received", "processing", "needs_user_input"},
        oldest_first=True,
    )
    return [str(row["operation_id"]) for row in rows]


def _finish(store, command_id, job_id):
    """The operation's job exists: its outcome, as the backend records it."""

    return record_outcome(store, command_id, state="accepted", job_id=job_id)


def test_a_newer_marker_written_during_the_acknowledgement_survives(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    original_unlink = os.unlink
    injected: list[str] = []

    def unlink_after_a_newer_marker_lands(path, *args, **kwargs):
        # Fusion writes cmd-2 between the consumer's read of cmd-1 and its delete.
        if not injected and Path(path).name.startswith(solve_command.CLAIM_PREFIX):
            injected.append(str(path))
            _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:05Z")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "unlink", unlink_after_a_newer_marker_lands)

    assert _poll(data_dir, store) is None
    assert injected, "the consumer never deleted a delivery file"
    assert _waiting(store) == ["cmd-1"]
    _finish(store, "cmd-1", "job-1")

    assert _poll(data_dir, store) is None

    assert store.get_operation("cmd-2") is not None, "cmd-2 was deleted by cmd-1's acknowledgement"
    assert store.get_operation("cmd-1")["state"] == "accepted"
    assert store.get_operation("cmd-2")["state"] == "received"


def test_one_command_delivered_twice_is_one_operation(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, store) is None
    assert _waiting(store) == ["cmd-1"]
    _file(data_dir, "cmd-1", bundle_path, manifest, operationId="cmd-1")

    assert _poll(data_dir, store) is None

    assert _waiting(store) == ["cmd-1"]
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert _delivery_files(data_dir) == []
    _finish(store, "cmd-1", "job-1")
    # Nothing is left to execute a second time.
    assert _waiting(store) == []
    assert _poll(data_dir, store) is None


def test_a_duplicate_after_the_outcome_replays_it_instead_of_running_again(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, store) is None
    _finish(store, "cmd-1", "job-1")
    before = _raw(data_dir, "cmd-1")
    _file(data_dir, "cmd-1", bundle_path, manifest)

    result = _poll(data_dir, store)

    assert (result["outcome"]["state"], result["outcome"]["jobId"]) == ("accepted", "job-1")
    # The replay names the delivery it answers.
    assert set(result["command"]) == {
        "kind", "commandId", "returnId", "bundlePath", "manifestSha256", "requestedAt",
    }
    assert _delivery_files(data_dir) == []
    assert _raw(data_dir, "cmd-1") == before
    assert _poll(data_dir, store) is None
    assert _waiting(store) == []


def test_a_different_request_under_a_held_id_is_refused_and_leaves_the_operation(
    data_dir, workspace, store, caplog
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, store) is None
    before = _raw(data_dir, "cmd-1")
    conflicting = _file(data_dir, "cmd-1", bundle_path, "sha256:" + "b" * 64)

    with caplog.at_level(logging.WARNING):
        result = _poll(data_dir, store)

    # The conflicting file is removed and its refusal logged.
    assert not conflicting.exists()
    assert any(
        "cmd-1" in record.getMessage() and "different request" in record.getMessage()
        for record in caplog.records
    )
    assert _raw(data_dir, "cmd-1") == before
    # The operation holding the id is unfinished, so the refusal is no answer:
    # it would end that operation. It is still the one the loop prepares.
    assert result is None
    assert _waiting(store) == ["cmd-1"]


def test_a_refused_conflict_never_ends_the_held_operation(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, store) is None
    _file(data_dir, "cmd-1", bundle_path, "sha256:" + "b" * 64)
    _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:05Z")

    # No answer under cmd-1's id, and cmd-2 is accepted behind it.
    assert _poll(data_dir, store) is None
    assert _waiting(store) == ["cmd-1", "cmd-2"]
    _finish(store, "cmd-1", "job-1")

    assert _waiting(store) == ["cmd-2"]
    assert store.get_operation("cmd-2")["state"] == "received"


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
        _poll(data_dir, store)
    # Claimed, not yet persisted: the slot is free for Fusion and the claim
    # still holds the request.
    assert not marker.exists()
    assert len(_delivery_files(data_dir)) == 1
    assert store.get_operation("cmd-1") is None

    monkeypatch.setattr(CadLinkStore, "accept_operation", real_accept)
    assert _poll(data_dir, store) is None

    assert _delivery_files(data_dir) == []
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert _waiting(store) == ["cmd-1"]


def test_a_failed_delete_after_persisting_recovers_the_same_operation(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    original_unlink = os.unlink
    refused: list[str] = []

    # Held for the whole of one pass, brief retries included.
    def held_open_once(path, *args, **kwargs):
        if len(refused) < solve_command._HELD_ATTEMPTS:
            refused.append(str(path))
            raise PermissionError(13, "The process cannot access the file")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "unlink", held_open_once)

    assert _poll(data_dir, store) is None
    assert refused and len(_delivery_files(data_dir)) == 1
    assert _poll(data_dir, store) is None

    assert _delivery_files(data_dir) == []
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert _waiting(store) == ["cmd-1"]


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

    assert _poll(data_dir, store) is None
    assert marker.exists()
    assert store.get_operation("cmd-1") is None
    assert _poll(data_dir, store) is None

    assert held
    assert store.get_operation("cmd-1")["state"] == "received"
    assert not marker.exists()


def test_two_per_command_files_are_accepted_oldest_first_and_neither_is_lost(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    # The age order is cmd-2, cmd-1, cmd-3: neither name order, nor the write
    # order, nor newest first.
    _file(data_dir, "cmd-3", bundle_path, manifest, requested_at="2026-09-13T01:00:10Z")
    _file(data_dir, "cmd-1", bundle_path, manifest, requested_at="2026-09-13T01:00:05Z")
    _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:00Z")

    assert _poll(data_dir, store) is None
    assert _waiting(store) == ["cmd-2", "cmd-1", "cmd-3"]
    # A command that is waiting on the user stays first in line: a later
    # request never takes its place.
    generation = store.claim("cmd-2", 0)
    store.record_outcome("cmd-2", generation, "needs_user_input", reason="setup_required")
    assert _waiting(store) == ["cmd-2", "cmd-1", "cmd-3"]
    _finish(store, "cmd-2", "job-2")
    assert _waiting(store) == ["cmd-1", "cmd-3"]
    _finish(store, "cmd-1", "job-1")

    assert _waiting(store) == ["cmd-3"]
    assert _operation_ids(data_dir) == ["cmd-1", "cmd-2", "cmd-3"]
    assert _delivery_files(data_dir) == []


def test_an_operation_accepted_earlier_stays_ahead_of_a_later_delivery(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest, requested_at="2026-09-13T02:00:00Z")
    assert _poll(data_dir, store) is None
    # Delivered afterwards, even though its producer stamped an older time.
    _file(data_dir, "cmd-2", bundle_path, manifest, requested_at="2026-09-13T01:00:00Z")

    assert _poll(data_dir, store) is None
    assert _waiting(store) == ["cmd-1", "cmd-2"]
    assert _operation_ids(data_dir) == ["cmd-1", "cmd-2"]


def test_files_wg_cannot_read_are_left_in_place_and_block_nothing(
    data_dir, workspace, store
) -> None:
    """What WG cannot identify as a request is left alone (M1 contract, C3)."""

    bundle_path, manifest = _bundle(workspace)
    folder = _ipc(data_dir) / V2_DIR
    folder.mkdir()
    newer = folder / "cmd-9.json"
    newer.write_text(json.dumps(_payload("cmd-9", bundle_path, manifest, schema=5)))
    torn = folder / "cmd-8.json"
    torn.write_text("{", encoding="utf-8")
    elsewhere = folder / "cmd-5.json"
    elsewhere.write_text(json.dumps({**_payload("cmd-5", bundle_path, manifest), "target": "fusion"}))
    staging = folder / ".cmd-6.json.tmp"
    staging.write_text(json.dumps(_payload("cmd-6", bundle_path, manifest, schema=2)))
    _file(data_dir, "cmd-1", bundle_path, manifest)

    assert _poll(data_dir, store) is None

    assert _waiting(store) == ["cmd-1"]
    assert newer.exists() and torn.exists() and elsewhere.exists() and staging.exists()
    assert _operation_ids(data_dir) == ["cmd-1"]


def test_identified_requests_wg_cannot_accept_are_refused_and_removed(
    data_dir, workspace, store
) -> None:
    """Identified but invalid: taken, refused with a reason, deleted -- never parked
    and never re-read by every pass (M1 contract, C3). None blocks a good file."""

    bundle_path, manifest = _bundle(workspace)
    no_kind = _file(data_dir, "cmd-9", bundle_path, manifest, schema=4)
    mismatched = _file(data_dir, "cmd-7", bundle_path, manifest, operationId="cmd-other")
    snapshot_with_return = _file(
        data_dir, "cmd-6", bundle_path, manifest, schema=4, kind="receive_snapshot"
    )
    unknown_kind = _file(data_dir, "cmd-4", bundle_path, manifest, schema=4, kind="update_link")
    _file(data_dir, "cmd-1", bundle_path, manifest)
    refused: list[dict] = []

    assert collect_solve_deliveries(data_dir, store, refuse=refused.append) is None

    assert _waiting(store) == ["cmd-1"]
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert not any(path.exists() for path in (no_kind, mismatched, snapshot_with_return, unknown_kind))
    assert _delivery_files(data_dir) == []
    reasons = {item["operationId"]: item["reason"] for item in refused}
    assert set(reasons) == {"cmd-9", "cmd-7", "cmd-6", "cmd-4"}
    assert "kind" in reasons["cmd-9"] and "operationId" in reasons["cmd-7"]
    assert "returnId" in reasons["cmd-6"] and "update_link" in reasons["cmd-4"]


def test_a_stale_claim_that_is_not_a_request_ends(data_dir, store) -> None:
    folder = _ipc(data_dir) / V2_DIR
    folder.mkdir()
    claim = folder / f"{solve_command.CLAIM_PREFIX}abc.json"
    claim.write_text("{", encoding="utf-8")
    refused: list[dict] = []

    collect_solve_deliveries(data_dir, store, refuse=refused.append)

    assert not claim.exists()
    assert [item["operationId"] for item in refused] == [None]


def test_a_v4_solve_and_a_v4_snapshot_are_taken_as_their_kinds(data_dir, workspace, store) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-s", bundle_path, manifest, schema=4, kind="prepare_and_solve", returnId="")
    snapshot = _payload("cmd-r", bundle_path, manifest, schema=4, kind="receive_snapshot")
    del snapshot["returnId"]
    folder = _ipc(data_dir) / V2_DIR
    _atomic_write(folder / "cmd-r.json", snapshot)
    published: list[dict] = []

    assert collect_solve_deliveries(data_dir, store, publish=published.append) is None

    assert store.get_operation("cmd-s")["kind"] == "prepare_and_solve"
    assert store.get_operation("cmd-r")["kind"] == "receive_snapshot"
    assert sorted(row["operation_id"] for row in published) == ["cmd-r", "cmd-s"]
    assert _delivery_files(data_dir) == []


def test_an_outcome_for_a_held_operation_ignores_a_conflicting_file_still_waiting(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, store) is None
    # A conflicting redelivery lands before the operation's job is recorded.
    _file(data_dir, "cmd-1", bundle_path, "sha256:" + "c" * 64)

    entry = _finish(store, "cmd-1", "job-1")

    assert (entry["state"], entry["jobId"]) == ("accepted", "job-1")
    row = store.get_operation("cmd-1")
    assert row["request_digest"] == request_digest(
        "prepare_and_solve",
        *prepare_and_solve_request(
            return_id="wgr_1", bundle_path=bundle_path, manifest_sha256=manifest
        ),
    )
    # The waiting file is then refused as a delivery in its own right.
    refused = _poll(data_dir, store)
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
    version-2 file. WG refuses it with a reason that names the remedy, and
    records that as the operation's outcome; it is never run.
    """

    bundle_path, manifest = _bundle(workspace)
    if writer == "single-slot":
        delivered = _legacy(data_dir, "cmd-old", bundle_path, manifest)
    else:
        delivered = _file(data_dir, "cmd-old", bundle_path, manifest, schema=2)

    result = _poll(data_dir, store)

    assert result["command"]["commandId"] == "cmd-old"
    assert result["outcome"]["state"] == "refused"
    assert OLDER_ADDIN in result["outcome"]["reason"]
    assert "Restart Fusion" in result["outcome"]["reason"]
    assert not delivered.exists() and _delivery_files(data_dir) == []
    assert store.get_operation("cmd-old")["state"] == "rejected"
    assert _poll(data_dir, store) is None
    assert _waiting(store) == []


def test_an_older_addins_repeat_of_a_held_command_does_not_end_it(
    data_dir, workspace, store
) -> None:
    """Refusing the old format must not refuse an operation WG already holds."""

    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, store) is None
    _legacy(data_dir, "cmd-1", bundle_path, manifest)

    assert _poll(data_dir, store) is None

    assert store.get_operation("cmd-1")["state"] == "received"
    assert _waiting(store) == ["cmd-1"]


def _one_pass(ctx: PreparationContext) -> list[str]:
    """One pass of the backend's delivery loop, waiting for what it started."""

    async def one_pass() -> list[str]:
        started: list[asyncio.Future[Any]] = []
        ids = await run_delivery_pass(
            ctx,
            spawn=lambda _operation_id, coroutine: started.append(asyncio.ensure_future(coroutine)),
        )
        await asyncio.gather(*started)
        return ids

    return asyncio.run(one_pass())


def test_a_command_whose_job_already_exists_is_reconciled_not_prepared_again(
    data_dir, workspace, store
) -> None:
    """The parked-command 409 (WP-14's compatibility edge), closed at the source.

    A job was created under ``cad-solve:<commandId>`` -- by an earlier attempt,
    or by the browser of a build before the backend owned solves -- and its
    report never arrived: a lost acknowledgement, a reload, an upgrade. The
    job is the command's outcome: the delivery loop records it before it
    prepares anything, and never submits the command again.
    """

    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    jobs = {"cad-solve:cmd-1": "job-7"}
    ctx = PreparationContext(
        store=store, data_dir=data_dir, workspace_root=workspace.resolve(),
        job_for_submission=jobs.get,
    )

    assert _one_pass(ctx) == ["cmd-1"]

    row = store.get_operation("cmd-1")
    assert (row["state"], row["job_id"]) == ("accepted", "job-7")
    # Nothing is started again, and a later record of the same job agrees.
    assert _one_pass(ctx) == []
    assert _finish(store, "cmd-1", "job-7")["state"] == "accepted"


def test_a_command_with_no_job_under_its_key_is_left_to_prepare(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    assert _poll(data_dir, store) is None
    ctx = PreparationContext(
        store=store, data_dir=data_dir, workspace_root=workspace.resolve(),
        job_for_submission={}.get,
    )

    assert reconcile_with_jobs(ctx, "cmd-1") is None

    assert store.get_operation("cmd-1")["state"] == "received"


def test_the_delivery_loop_reconciles_through_the_real_jobs_store(
    data_dir, workspace, store, tmp_path
) -> None:
    """The same, wired as the app wires it: app.state.jobs_runtime.store."""

    from server.cadlink.api import _preparation_context
    from server.jobs.store import JobStore

    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    jobs = JobStore(tmp_path / "jobs.db")
    jobs.initialize()
    now = "2026-09-13T12:00:00"
    jobs.create_job_idempotent(
        {
            "id": "job-7", "status": "queued", "created_at": now, "updated_at": now,
            "queued_at": now, "progress": 0.0, "stage": "queued", "stage_message": "queued",
            "config_json": {}, "config_summary_json": {}, "task_metadata": {},
        },
        submission_key="cad-solve:cmd-1",
        request_sha256="a" * 64,
        initial_event=("queued", {}),
    )
    state = SimpleNamespace(
        data_dir=str(data_dir),
        cadlink_store=store,
        cad_workspace=SimpleNamespace(selected_path=lambda: workspace),
        jobs_runtime=SimpleNamespace(store=jobs, submit=None),
        update_restart=None,
    )

    async def one_pass() -> list[str]:
        started: list[asyncio.Future[Any]] = []
        ids = await run_delivery_pass(
            _preparation_context(state),
            spawn=lambda _operation_id, coroutine: started.append(asyncio.ensure_future(coroutine)),
        )
        await asyncio.gather(*started)
        return ids

    assert asyncio.run(one_pass()) == ["cmd-1"]

    row = store.get_operation("cmd-1")
    assert (row["state"], row["job_id"]) == ("accepted", "job-7")
    assert asyncio.run(one_pass()) == []


# --- Why a pass started nothing -------------------------------------------
#
# The loop runs `run_delivery_pass` once a second for as long as WG is open.
# Three of its outcomes are indistinguishable from outside: an approved update
# restart stops it before anything is collected, an unselected CAD Link folder
# stops it just after, and an ordinary idle pass starts nothing because there
# is nothing to start. A consumer that has been declining a user's solve for
# hours therefore looks exactly like one with no work, which is how two real
# `prepare_and_solve` operations sat in `received` while the pass ran roughly
# 50 000 times without a line in the log.


def _noted_pass(context, **kwargs) -> tuple[list[str], list[str | None]]:
    """One pass, with the reason it started nothing."""

    notes: list[str | None] = []
    started = asyncio.run(
        run_delivery_pass(
            context,
            spawn=lambda _operation_id, coroutine: coroutine.close(),
            note=notes.append,
            **kwargs,
        )
    )
    return started, notes


def _context(data_dir, store, workspace, *, blocked: str | None = None) -> PreparationContext:
    return PreparationContext(
        store=store,
        data_dir=data_dir,
        workspace_root=workspace.resolve() if workspace is not None else None,
        submission_blocked=lambda: blocked,
    )


def test_a_pass_stopped_by_an_approved_restart_reports_that_reason(data_dir, workspace, store):
    workspace.mkdir(parents=True, exist_ok=True)
    reason = "Waveguide Generator is about to restart to install 0.3.4."

    started, notes = _noted_pass(_context(data_dir, store, workspace, blocked=reason))

    assert started == []
    assert notes == [reason]


def test_a_pass_without_a_cad_link_folder_reports_that_reason(data_dir, store):
    started, notes = _noted_pass(_context(data_dir, store, None))

    assert started == []
    assert len(notes) == 1
    assert notes[0] is not None
    assert "folder" in notes[0].lower()


def test_delivery_loop_pushes_when_addin_session_or_declaration_changes(
    data_dir, workspace, store, monkeypatch
) -> None:
    from server.cadlink import api
    from server.cadlink.fusion_status import FUSION_STATUS_FILENAME

    workspace.mkdir(parents=True)
    published: list[dict[str, Any]] = []
    events = SimpleNamespace(publish=published.append)
    runtime = SimpleNamespace(store=SimpleNamespace(), events=events, submit=None)
    app = SimpleNamespace(state=SimpleNamespace(
        cadlink_store=store,
        data_dir=str(data_dir),
        cad_workspace=SimpleNamespace(selected_path=lambda: workspace),
        jobs_runtime=runtime,
        update_restart=None,
    ))
    monkeypatch.setenv("WG2_CAD_DELIVERY", "1")
    monkeypatch.setattr(api, "_DELIVERY_INTERVAL_S", 0.001)

    async def empty_pass(*_args, **_kwargs):
        return []

    monkeypatch.setattr(api, "run_delivery_pass", empty_pass)

    marker = workspace / "ipc" / "wglink" / FUSION_STATUS_FILENAME

    async def wait_for_events(count: int) -> None:
        for _ in range(100):
            if len([item for item in published if item.get("kind") == "cadAddinStatusChanged"]) >= count:
                return
            await asyncio.sleep(0.002)
        raise AssertionError(f"did not receive {count} add-in status events: {published!r}")

    async def scenario() -> None:
        await api._deliver_solve_commands(app)()
        task = app.state.cad_delivery_task
        try:
            await wait_for_events(1)  # initial no-add-in declaration
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({
                "sessionId": "m1-session",
                "diagnostics": {"activation": {
                    "settingsKey": "automatic_coordination",
                    "automaticCoordination": False,
                    "setting": "settings",
                }},
            }), encoding="utf-8")
            await wait_for_events(2)
            marker.write_text(json.dumps({"sessionId": "pin-session"}), encoding="utf-8")
            await wait_for_events(3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert [item["kind"] for item in published if item.get("kind") == "cadAddinStatusChanged"] == [
        "cadAddinStatusChanged", "cadAddinStatusChanged", "cadAddinStatusChanged",
    ]


@pytest.mark.parametrize("setting", [[], {}])
def test_malformed_addin_declaration_does_not_stop_delivery(
    data_dir, workspace, store, monkeypatch, setting
) -> None:
    from server.cadlink import api
    from server.cadlink.fusion_status import FUSION_STATUS_FILENAME

    marker = workspace / "ipc" / "wglink" / FUSION_STATUS_FILENAME
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({
        "sessionId": "malformed-session",
        "diagnostics": {"activation": {
            "settingsKey": "automatic_coordination",
            "automaticCoordination": False,
            "setting": setting,
        }},
    }), encoding="utf-8")
    passes: list[bool] = []
    published: list[dict[str, Any]] = []
    app = SimpleNamespace(state=SimpleNamespace(
        cadlink_store=store,
        data_dir=str(data_dir),
        cad_workspace=SimpleNamespace(selected_path=lambda: workspace),
        jobs_runtime=SimpleNamespace(
            store=SimpleNamespace(), events=SimpleNamespace(publish=published.append), submit=None
        ),
        update_restart=None,
    ))
    monkeypatch.setenv("WG2_CAD_DELIVERY", "1")
    monkeypatch.setattr(api, "_DELIVERY_INTERVAL_S", 0.001)

    async def observed_pass(*_args, **_kwargs):
        passes.append(True)

    monkeypatch.setattr(api, "run_delivery_pass", observed_pass)

    async def scenario() -> None:
        await api._deliver_solve_commands(app)()
        task = app.state.cad_delivery_task
        try:
            for _ in range(100):
                if passes:
                    return
                await asyncio.sleep(0.002)
            raise AssertionError("malformed advisory declaration prevented every delivery pass")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert published == [{"v": 1, "kind": "cadAddinStatusChanged"}]


def test_addin_detector_failure_is_logged_once_and_does_not_stop_delivery(
    data_dir, workspace, store, monkeypatch, caplog
) -> None:
    from server.cadlink import api

    passes: list[bool] = []
    app = SimpleNamespace(state=SimpleNamespace(
        cadlink_store=store,
        data_dir=str(data_dir),
        cad_workspace=SimpleNamespace(selected_path=lambda: workspace),
        jobs_runtime=SimpleNamespace(store=SimpleNamespace(), events=None, submit=None),
        update_restart=None,
    ))
    monkeypatch.setenv("WG2_CAD_DELIVERY", "1")
    monkeypatch.setattr(api, "_DELIVERY_INTERVAL_S", 0.001)

    def broken_detector(_workspace):
        raise RuntimeError("broken declaration detector")

    async def observed_pass(*_args, **_kwargs):
        passes.append(True)

    monkeypatch.setattr(api, "addin_inbox_session_signature", broken_detector)
    monkeypatch.setattr(api, "run_delivery_pass", observed_pass)

    async def scenario() -> None:
        await api._deliver_solve_commands(app)()
        task = app.state.cad_delivery_task
        try:
            for _ in range(100):
                if len(passes) >= 3:
                    return
                await asyncio.sleep(0.002)
            raise AssertionError("detector failure prevented delivery passes")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    detector_logs = [
        record for record in caplog.records
        if "Detecting the CAD add-in declaration failed" in record.getMessage()
    ]
    assert len(detector_logs) == 1


def test_transient_partial_heartbeat_does_not_publish_addin_transition(
    data_dir, workspace, store, monkeypatch
) -> None:
    from server.cadlink import api
    from server.cadlink.fusion_status import FUSION_STATUS_FILENAME

    marker = workspace / "ipc" / "wglink" / FUSION_STATUS_FILENAME
    marker.parent.mkdir(parents=True)
    payload = {
        "sessionId": "stable-session",
        "diagnostics": {"activation": {
            "settingsKey": "automatic_coordination",
            "automaticCoordination": False,
            "setting": "settings",
        }},
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")
    published: list[dict[str, Any]] = []
    passes = 0
    continue_pass: asyncio.Queue[None] = asyncio.Queue()
    app = SimpleNamespace(state=SimpleNamespace(
        cadlink_store=store,
        data_dir=str(data_dir),
        cad_workspace=SimpleNamespace(selected_path=lambda: workspace),
        jobs_runtime=SimpleNamespace(
            store=SimpleNamespace(), events=SimpleNamespace(publish=published.append), submit=None
        ),
        update_restart=None,
    ))
    monkeypatch.setenv("WG2_CAD_DELIVERY", "1")
    monkeypatch.setattr(api, "_DELIVERY_INTERVAL_S", 0.001)

    async def observed_pass(*_args, **_kwargs):
        nonlocal passes
        passes += 1
        await continue_pass.get()

    monkeypatch.setattr(api, "run_delivery_pass", observed_pass)

    async def wait_for_passes(count: int) -> None:
        for _ in range(100):
            if passes >= count:
                return
            await asyncio.sleep(0.002)
        raise AssertionError(f"delivery loop stopped at {passes} passes")

    async def scenario() -> None:
        await api._deliver_solve_commands(app)()
        task = app.state.cad_delivery_task
        try:
            await wait_for_passes(1)
            assert len(published) == 1
            marker.write_text('{"sessionId":', encoding="utf-8")
            continue_pass.put_nowait(None)
            await wait_for_passes(2)
            marker.write_text(json.dumps(payload), encoding="utf-8")
            continue_pass.put_nowait(None)
            await wait_for_passes(3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert published == [{"v": 1, "kind": "cadAddinStatusChanged"}]


def test_a_pass_that_collects_and_starts_work_reports_no_reason(data_dir, workspace, store):
    """The control: a pass that actually starts an operation notes nothing.

    Without it, a `note` that simply never fired would satisfy both tests
    above by accident.
    """

    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-noted", bundle_path, manifest)

    started, notes = _noted_pass(_context(data_dir, store, workspace))

    assert started == ["cmd-noted"]
    assert notes == [None]


def test_an_idle_pass_is_not_reported_as_a_blocked_one(data_dir, workspace, store):
    """Nothing to do is not a reason; it must not read as a stopped consumer."""

    workspace.mkdir(parents=True, exist_ok=True)

    started, notes = _noted_pass(_context(data_dir, store, workspace))

    assert started == []
    assert notes == [None]


# --- Reporting a stopped consumer -----------------------------------------


def test_a_stopped_consumer_is_reported_once_not_every_second():
    reporter = DeliveryPassReporter()
    reason = "No CAD Link folder is selected."

    assert reporter.observe(reason) == reason
    assert [reporter.observe(reason) for _ in range(5)] == [None] * 5


def test_resuming_is_reported_once_and_only_after_a_stop():
    reporter = DeliveryPassReporter()

    # Never stopped: an ordinary pass says nothing at all.
    assert reporter.observe(None) is None
    assert reporter.observe(None) is None

    reporter.observe("stopped")
    assert reporter.observe(None) == DeliveryPassReporter.RESUMED
    assert reporter.observe(None) is None


def test_a_different_reason_is_reported_again():
    reporter = DeliveryPassReporter()
    reporter.observe("first")

    assert reporter.observe("second") == "second"
