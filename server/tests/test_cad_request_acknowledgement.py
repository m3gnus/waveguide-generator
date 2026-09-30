"""The request acknowledgement file (handoff H9, WG half).

WG publishes the outcome of every request in its inbox as
``<ipc>/.wg-solve-acks/<commandId>.json`` before it deletes the request, so a
producer can tell "WG accepted it" from "WG refused it" once the request file
is gone. Publishing changes nothing about what is accepted or refused.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.cadlink import fusion_delivery, solve_command
from server.cadlink.solve_command import (
    ACK_FAILURE_PASSES,
    ACK_MAX_FILES,
    record_outcome,
    SOLVE_ACKS_DIRECTORY,
    collect_solve_deliveries,
    prune_acknowledgements,
    write_acknowledgement,
)
from server.cadlink.store import CadLinkStore

from test_cad_solve_delivery import (
    _bundle,
    _delivery_files,
    _file,
    _finish,
    _ipc,
    _operation_ids,
    OLDER_ADDIN,
)


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


@pytest.fixture(autouse=True)
def _fresh_state():
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims", "_pending_refusal_acks", "_ack_failures"):
        getattr(solve_command, name).clear()
    solve_command._last_ack_prune = 0.0
    yield
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims", "_pending_refusal_acks", "_ack_failures"):
        getattr(solve_command, name).clear()


def _acks(data_dir) -> Path:
    return _ipc(data_dir) / SOLVE_ACKS_DIRECTORY


def _ack(data_dir, command_id):
    path = _acks(data_dir) / f"{command_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _claims(data_dir) -> list[str]:
    return [name for name in _delivery_files(data_dir) if "claim" in name]


def _poll(data_dir, store):
    """Collect with the admission receipt a production jobs pass provides."""
    def admit(operation_id):
        row = store.get_operation(operation_id)
        if row is not None and row["state"] == "received":
            _finish(store, operation_id, "job-" + operation_id.removeprefix("cmd-"))
        return True
    return collect_solve_deliveries(data_dir, store, accept_solve=admit)


def _send_file(data_dir, bundle_path, manifest):
    path = _file(data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="receive_snapshot")
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["returnId"]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_the_capability_is_advertised_while_the_consumer_runs() -> None:
    assert fusion_delivery.capabilities()["solveAcknowledgement"] == 1
    assert "solveAcknowledgement" not in fusion_delivery.capabilities(solve_delivery=False)


def test_an_accepted_request_is_acknowledged_before_its_file_is_deleted(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    seen: list[dict | None] = []
    real = solve_command._acknowledge

    def delete_after_looking(path, **kwargs):
        seen.append(_ack(data_dir, "cmd-1"))
        return real(path, **kwargs)

    monkeypatch.setattr(solve_command, "_acknowledge", delete_after_looking)

    assert _poll(data_dir, store) is None

    assert len(seen) == 1 and seen[0] is not None, "the request was deleted before WG recorded its outcome"
    ack = _ack(data_dir, "cmd-1")
    assert ack["outcome"] == "accepted" and ack["commandId"] == "cmd-1"
    assert ack["operationId"] == "cmd-1" and ack["reason"] is None and ack["at"]
    assert ack["digest"] == store.get_operation("cmd-1")["request_digest"]
    assert _delivery_files(data_dir) == []
    # The acknowledgement folder is not an inbox WG consumes.
    assert _poll(data_dir, store) is None
    assert _operation_ids(data_dir) == ["cmd-1"]


def test_an_older_addins_request_is_refused_in_its_acknowledgement(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-old", bundle_path, manifest, schema=2)

    _poll(data_dir, store)

    ack = _ack(data_dir, "cmd-old")
    assert ack["outcome"] == "refused" and OLDER_ADDIN in ack["reason"]
    assert _delivery_files(data_dir) == []


def test_a_request_wg_can_identify_but_not_accept_is_refused_in_its_acknowledgement(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-bad", bundle_path, manifest, schema=4, kind="explode")

    _poll(data_dir, store)

    ack = _ack(data_dir, "cmd-bad")
    assert ack["outcome"] == "refused" and "explode" in ack["reason"]
    assert _delivery_files(data_dir) == []
    assert store.get_operation("cmd-bad") is None


def test_a_file_that_names_no_usable_id_leaves_no_acknowledgement(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-x", bundle_path, manifest, schema=4, kind="explode")
    (_ipc(data_dir) / ".wg-solve-requests" / "cmd-x.json").write_text(
        json.dumps({"schemaVersion": 4, "target": "waveguide-generator",
                    "commandId": "../escape", "bundlePath": "b", "manifestSha256": "m",
                    "kind": "explode"}),
        encoding="utf-8",
    )

    _poll(data_dir, store)

    assert not _acks(data_dir).exists() or not list(_acks(data_dir).iterdir())
    assert not (_ipc(data_dir) / "escape.json").exists()
    assert not (_ipc(data_dir).parent / "escape.json").exists()


def test_a_conflicting_copy_is_refused_but_never_replaces_the_operations_acknowledgement(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    _poll(data_dir, store)
    _file(data_dir, "cmd-1", bundle_path, "sha256:" + "b" * 64)

    _poll(data_dir, store)

    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    assert store.get_operation("cmd-1")["state"] == "accepted"
    assert _delivery_files(data_dir) == []


def test_a_conflict_never_writes_a_refusal_under_an_id_the_store_holds(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    _poll(data_dir, store)
    (_acks(data_dir) / "cmd-1.json").unlink()
    _file(data_dir, "cmd-1", bundle_path, "sha256:" + "b" * 64)

    _poll(data_dir, store)

    # The operation's own state, describing the original request.
    ack = _ack(data_dir, "cmd-1")
    assert ack["outcome"] == "accepted" and ack["manifestSha256"] == manifest


def test_an_invalid_copy_under_a_held_id_never_downgrades_the_accepted_acknowledgement(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="prepare_and_solve")
    _poll(data_dir, store)
    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    _file(data_dir, "cmd-1", bundle_path, manifest, schema=4, kind="explode")

    _poll(data_dir, store)

    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    assert store.get_operation("cmd-1")["state"] == "accepted"
    assert _delivery_files(data_dir) == []


def test_every_acknowledgement_names_the_request_it_answers(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-s", bundle_path, manifest, schema=4, kind="prepare_and_solve")
    _file(data_dir, "cmd-bad", bundle_path, manifest, schema=4, kind="explode")
    _file(data_dir, "cmd-old", bundle_path, manifest, schema=2)

    _poll(data_dir, store)

    solve = _ack(data_dir, "cmd-s")
    assert (solve["manifestSha256"], solve["kind"]) == (manifest, "prepare_and_solve")
    bad = _ack(data_dir, "cmd-bad")
    assert (bad["manifestSha256"], bad["kind"]) == (manifest, "explode")
    old = _ack(data_dir, "cmd-old")
    assert old["manifestSha256"] == manifest and old["kind"] == "prepare_and_solve"


def test_a_snapshot_rejected_at_acceptance_is_acknowledged_as_refused(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)

    def reject(operation_id):
        record_outcome(store, operation_id, state="refused", reason="The snapshot is not valid.")
        return solve_command.RETAIN_INVALID

    collect_solve_deliveries(data_dir, store, retain=reject)

    ack = _ack(data_dir, "cmd-1")
    assert ack["outcome"] == "refused" and "not valid" in ack["reason"]
    assert _delivery_files(data_dir) == []


def test_the_acceptance_is_made_durable_before_its_acknowledgement_is_written(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    order: list[str] = []
    real_durable = CadLinkStore.make_durable

    def durable(self):
        order.append(f"durable(ack={'yes' if _ack(data_dir, 'cmd-1') else 'no'})")
        return real_durable(self)

    monkeypatch.setattr(CadLinkStore, "make_durable", durable)

    _poll(data_dir, store)

    assert order == ["durable(ack=no)"]
    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"


def test_an_acceptance_that_cannot_be_made_durable_keeps_the_request(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)

    def broken(self):
        raise OSError("disk")

    monkeypatch.setattr(CadLinkStore, "make_durable", broken)
    _poll(data_dir, store)

    assert _ack(data_dir, "cmd-1") is None and len(_delivery_files(data_dir)) == 1


def test_the_acknowledgement_folder_is_flushed_after_the_replace(
    data_dir, monkeypatch
) -> None:
    if os.name == "nt":
        pytest.skip("directories are not flushed on Windows")
    synced: list[bool] = []
    real_fsync = os.fsync

    def fsync(descriptor):
        synced.append(True)
        return real_fsync(descriptor)

    monkeypatch.setattr(solve_command.os, "fsync", fsync)
    write_acknowledgement(data_dir, "cmd-1", "accepted", job_id="job-1")
    # The staged file, then the folder.
    assert len(synced) == 2


def test_an_unusable_acknowledgement_folder_is_given_up_once_after_a_bounded_number_of_passes(
    data_dir, workspace, store, monkeypatch, caplog
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _ipc(data_dir)
    (_ipc(data_dir) / SOLVE_ACKS_DIRECTORY).write_text("in the way", encoding="utf-8")
    _file(data_dir, "cmd-1", bundle_path, manifest)
    _file(data_dir, "cmd-2", bundle_path, "sha256:" + "b" * 64, schema=4, kind="explode")
    monkeypatch.setattr(solve_command, "ACK_FAILURE_PASSES", 3)
    reports: list[dict] = []

    with caplog.at_level("ERROR"):
        for _ in range(2):
            collect_solve_deliveries(data_dir, store, refuse=reports.append)
        assert len(_claims(data_dir)) == 2, "given up too early"
        collect_solve_deliveries(data_dir, store, refuse=reports.append)

    assert _claims(data_dir) == []
    assert store.get_operation("cmd-1") is not None
    assert len(reports) == 1, "the refusal was reported again on each pass"
    assert len([r for r in caplog.records if "giving it up" in r.getMessage()]) == 2
    assert ACK_FAILURE_PASSES >= 3


def test_an_owed_refusal_is_forgotten_when_its_claim_disappears(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-bad", bundle_path, manifest, schema=4, kind="explode")
    real_replace = os.replace

    def replace(source, destination, *args, **kwargs):
        if Path(destination).parent.name == SOLVE_ACKS_DIRECTORY:
            raise PermissionError(13, "held")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "replace", replace)
    monkeypatch.setattr(solve_command, "_HELD_RETRY_SECONDS", 0)
    _poll(data_dir, store)
    assert solve_command._pending_refusal_acks and solve_command._ack_failures
    for path in (_ipc(data_dir) / ".wg-solve-requests").iterdir():
        path.unlink()

    _poll(data_dir, store)

    assert not solve_command._pending_refusal_acks and not solve_command._ack_failures


def test_a_redelivery_replays_the_same_acknowledgement_with_the_job(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    _poll(data_dir, store)
    _finish(store, "cmd-1", "job-1")
    _file(data_dir, "cmd-1", bundle_path, manifest)

    _poll(data_dir, store)

    ack = _ack(data_dir, "cmd-1")
    assert (ack["outcome"], ack["jobId"]) == ("accepted", "job-1")
    assert _operation_ids(data_dir) == ["cmd-1"]


def test_a_crash_before_the_acknowledgement_keeps_the_request_and_recovers_one_operation(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)

    class Crash(BaseException):
        pass

    real = solve_command.write_acknowledgement

    def die(*args, **kwargs):
        raise Crash

    monkeypatch.setattr(solve_command, "write_acknowledgement", die)
    with pytest.raises(Crash):
        _poll(data_dir, store)
    # Persisted, not acknowledged: the claim still holds the request.
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert _ack(data_dir, "cmd-1") is None
    assert len(_delivery_files(data_dir)) == 1

    monkeypatch.setattr(solve_command, "write_acknowledgement", real)
    assert _poll(data_dir, store)["outcome"]["jobId"] == "job-1"

    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert store.get_operation("cmd-1")["state"] == "accepted"
    assert _delivery_files(data_dir) == []


def test_an_acknowledgement_that_cannot_be_written_keeps_the_request_until_it_can(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    real_replace = os.replace
    blocked = {"on": True}

    def replace(source, destination, *args, **kwargs):
        if blocked["on"] and Path(destination).parent.name == SOLVE_ACKS_DIRECTORY:
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "replace", replace)
    monkeypatch.setattr(solve_command, "_HELD_RETRY_SECONDS", 0)

    assert _poll(data_dir, store) is None
    assert _ack(data_dir, "cmd-1") is None
    assert len(_delivery_files(data_dir)) == 1
    assert [p.name for p in _acks(data_dir).iterdir()] == [], "a staging file was left behind"

    blocked["on"] = False
    assert _poll(data_dir, store)["outcome"]["jobId"] == "job-1"

    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert _delivery_files(data_dir) == []


def test_a_windows_style_replace_failure_is_retried(data_dir, monkeypatch) -> None:
    real_replace = os.replace
    failures: list[str] = []

    def replace(source, destination, *args, **kwargs):
        if len(failures) < 3:
            failures.append(str(destination))
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "replace", replace)
    monkeypatch.setattr(solve_command, "_HELD_RETRY_SECONDS", 0)

    assert write_acknowledgement(data_dir, "cmd-1", "accepted", job_id="job-1") is True

    assert len(failures) == 3
    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    assert [p.name for p in _acks(data_dir).iterdir()] == ["cmd-1.json"]


def test_a_refusal_whose_acknowledgement_cannot_be_written_keeps_its_claim_and_is_reported_once(
    data_dir, workspace, store, monkeypatch
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-bad", bundle_path, manifest, schema=4, kind="explode")
    real_replace = os.replace
    blocked = {"on": True}
    reports: list[dict] = []

    def replace(source, destination, *args, **kwargs):
        if blocked["on"] and Path(destination).parent.name == SOLVE_ACKS_DIRECTORY:
            raise PermissionError(13, "held")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(solve_command.os, "replace", replace)
    monkeypatch.setattr(solve_command, "_HELD_RETRY_SECONDS", 0)

    for _ in range(3):
        collect_solve_deliveries(data_dir, store, refuse=reports.append)
    assert len(reports) == 1 and len(_delivery_files(data_dir)) == 1

    blocked["on"] = False
    collect_solve_deliveries(data_dir, store, refuse=reports.append)

    assert _ack(data_dir, "cmd-bad")["outcome"] == "refused"
    assert _delivery_files(data_dir) == []
    assert len(reports) == 1


def test_acknowledgements_past_their_retention_are_pruned_and_the_folder_is_bounded(
    data_dir,
) -> None:
    for index in range(4):
        write_acknowledgement(data_dir, f"cmd-{index}", "accepted", job_id=f"job-{index}")
    folder = _acks(data_dir)
    base = 1_000_000.0
    for index in range(4):
        os.utime(folder / f"cmd-{index}.json", (base + index, base + index))
    stale_temp = folder / ".cmd-9.abc.tmp"
    stale_temp.write_text("{", encoding="utf-8")
    os.utime(stale_temp, (base, base))

    # Nothing is old yet.
    assert prune_acknowledgements(data_dir, now=base + 10, retention_seconds=100) == 0
    # cmd-0 and cmd-1 and the temp file are past retention.
    assert prune_acknowledgements(data_dir, now=base + 101.5, retention_seconds=100) == 3
    assert sorted(p.name for p in folder.iterdir()) == ["cmd-2.json", "cmd-3.json"]
    # The cap drops the oldest first.
    assert prune_acknowledgements(data_dir, now=base + 102, retention_seconds=100, max_files=1) == 1
    assert [p.name for p in folder.iterdir()] == ["cmd-3.json"]
    assert ACK_MAX_FILES >= 100


def test_the_delivery_pass_prunes_at_most_once_per_interval(
    data_dir, workspace, store, monkeypatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(solve_command, "prune_acknowledgements", lambda directory, **_: calls.append(directory) or 0)
    moment = {"now": 100.0}
    monkeypatch.setattr(solve_command, "_now", lambda: moment["now"])

    _poll(data_dir, store)
    _poll(data_dir, store)
    assert len(calls) == 1
    moment["now"] += solve_command.ACK_PRUNE_INTERVAL_SECONDS + 1
    _poll(data_dir, store)
    assert len(calls) == 2


def test_a_command_id_that_names_no_safe_file_is_not_acknowledged_and_not_held(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    long_id = "cmd-" + "x" * 300
    _file(data_dir, long_id[:100], bundle_path, manifest, commandId=long_id)

    assert _poll(data_dir, store) is None

    assert _delivery_files(data_dir) == []
    assert store.get_operation(long_id) is not None
    assert not _acks(data_dir).exists() or not list(_acks(data_dir).iterdir())


def test_make_durable_checkpoints_the_log(data_dir, store, workspace) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    _poll(data_dir, store)
    store.make_durable()
    # Nothing is left uncheckpointed.
    with store._lock:
        busy, log_frames, checkpointed = store._connect().execute(
            "PRAGMA wal_checkpoint(PASSIVE)"
        ).fetchone()
    assert busy == 0 and checkpointed == log_frames


def test_a_reader_pinning_the_log_stops_the_acknowledgement_and_the_claim_is_never_given_up(
    data_dir, workspace, store, monkeypatch
) -> None:
    import sqlite3

    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    monkeypatch.setattr(solve_command, "ACK_FAILURE_PASSES", 2)
    monkeypatch.setattr("server.cadlink.store.DURABLE_BUSY_TIMEOUT_MS", 20)
    store.initialize()
    # Land the row, then keep its frames un-checkpointed while a reader pins the log.
    reader = sqlite3.connect(str(store.db_path))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM cad_operations").fetchone()
    try:
        # A write after the reader's snapshot: the checkpoint cannot finish.
        for _ in range(4):
            _poll(data_dir, store)
            assert _ack(data_dir, "cmd-1") is None
            assert len(_claims(data_dir)) == 1, "the claim was consumed without a durable operation"
    finally:
        reader.rollback()
        reader.close()

    _poll(data_dir, store)

    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    assert _claims(data_dir) == []


def test_a_busy_checkpoint_result_raises(store, monkeypatch) -> None:
    import sqlite3

    store.initialize()
    real = store._connect()

    class Busy:
        def execute(self, sql, *args):
            if "wal_checkpoint" in sql:
                class Result:
                    def fetchone(self_inner):
                        return (1, 5, 0)
                return Result()
            return real.execute(sql, *args)

    monkeypatch.setattr(store, "_connect", lambda: Busy())
    with pytest.raises(sqlite3.OperationalError, match="durable"):
        store.make_durable()


@pytest.mark.parametrize("job_id", [None, ""])
@pytest.mark.parametrize("reason", [None, "Cancelled before admission."])
@pytest.mark.parametrize("kind", [None, "prepare_and_solve"])
def test_acknowledgement_writer_refuses_solve_acceptance_without_a_job(data_dir, job_id, reason, kind):
    assert write_acknowledgement(data_dir, "cmd-1", "accepted", job_id=job_id, reason=reason, kind=kind)
    ack = _ack(data_dir, "cmd-1")
    assert ack["outcome"] == "refused"
    assert ack["jobId"] == job_id
    assert ack["reason"] == (reason or "WG did not admit this request to a job.")


def test_acknowledgement_writer_accepts_a_retained_send_without_a_job(data_dir):
    assert write_acknowledgement(data_dir, "cmd-1", "accepted", kind="receive_snapshot")
    ack = _ack(data_dir, "cmd-1")
    assert (ack["outcome"], ack["kind"], ack["jobId"], ack["reason"]) == (
        "accepted", "receive_snapshot", None, None,
    )


def test_acknowledgement_writer_preserves_a_sends_retention_failure(data_dir):
    reason = "WG could not verify this snapshot. Send it again from Fusion."
    assert write_acknowledgement(data_dir, "cmd-1", "refused", kind="receive_snapshot", reason=reason)
    ack = _ack(data_dir, "cmd-1")
    assert (ack["outcome"], ack["kind"], ack["jobId"], ack["reason"]) == (
        "refused", "receive_snapshot", None, reason,
    )


@pytest.mark.parametrize("invalid_manifest", [False, True])
def test_send_acknowledgement_follows_snapshot_retention(data_dir, workspace, store, monkeypatch, invalid_manifest):
    from server.cadlink.preparation import SNAPSHOT_INVALID_MESSAGE, settle_snapshot_operation
    from server.cadlink.ingest import retained_snapshot_path
    from test_cad_preparation import _write_return

    bundle_path, manifest = _write_return(workspace)
    if invalid_manifest:
        manifest = "sha256:" + "0" * 64
    _send_file(data_dir, bundle_path, manifest)
    real = solve_command.write_acknowledgement
    seen = []

    def write_after_retention(*args, **kwargs):
        row = store.get_operation("cmd-1")
        seen.append((row["state"], row["snapshot_json"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(solve_command, "write_acknowledgement", write_after_retention)
    collect_solve_deliveries(
        data_dir, store,
        retain=lambda op: settle_snapshot_operation(store, data_dir, workspace, op),
    )
    ack = _ack(data_dir, "cmd-1")
    assert ack["kind"] == "receive_snapshot" and ack["jobId"] is None
    assert len(seen) == 1
    if invalid_manifest:
        assert seen == [("rejected", None)]
        assert (ack["outcome"], ack["reason"]) == ("refused", SNAPSHOT_INVALID_MESSAGE)
    else:
        assert seen[0][0] == "accepted" and seen[0][1] is not None
        retained = json.loads(seen[0][1])
        assert retained_snapshot_path(data_dir, retained["manifest_sha256"]).is_dir()
        assert (ack["outcome"], ack["reason"]) == ("accepted", None)
    assert _delivery_files(data_dir) == []


def test_send_without_retention_is_never_acknowledged_accepted(data_dir, workspace, store):
    bundle_path, manifest = _bundle(workspace)
    _send_file(data_dir, bundle_path, manifest)
    collect_solve_deliveries(data_dir, store)
    ack = _ack(data_dir, "cmd-1")
    assert (ack["outcome"], ack["jobId"], ack["reason"]) == (
        "refused", None, "WG has not retained this snapshot.",
    )
    assert store.get_operation("cmd-1")["snapshot_json"] is None


def test_collection_without_admission_never_acknowledges_accepted(data_dir, workspace, store):
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    collect_solve_deliveries(data_dir, store)
    ack = _ack(data_dir, "cmd-1")
    assert ack["outcome"] == "refused" and ack["jobId"] is None
    assert ack["reason"] and store.get_operation("cmd-1")["state"] == "received"
