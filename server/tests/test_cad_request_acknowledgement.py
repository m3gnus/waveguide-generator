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
    ACK_MAX_FILES,
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
    _waiting,
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
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims", "_pending_refusal_acks"):
        getattr(solve_command, name).clear()
    solve_command._last_ack_prune = 0.0
    yield
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims", "_pending_refusal_acks"):
        getattr(solve_command, name).clear()


def _acks(data_dir) -> Path:
    return _ipc(data_dir) / SOLVE_ACKS_DIRECTORY


def _ack(data_dir, command_id):
    path = _acks(data_dir) / f"{command_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _poll(data_dir, store):
    return collect_solve_deliveries(data_dir, store)


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
    assert _waiting(store) == ["cmd-1"]
    assert _delivery_files(data_dir) == []


def test_a_conflict_with_no_acknowledgement_yet_is_refused_with_the_reason(
    data_dir, workspace, store
) -> None:
    bundle_path, manifest = _bundle(workspace)
    _file(data_dir, "cmd-1", bundle_path, manifest)
    _poll(data_dir, store)
    (_acks(data_dir) / "cmd-1.json").unlink()
    _file(data_dir, "cmd-1", bundle_path, "sha256:" + "b" * 64)

    _poll(data_dir, store)

    ack = _ack(data_dir, "cmd-1")
    assert ack["outcome"] == "refused" and "different request" in ack["reason"]


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
    assert _poll(data_dir, store) is None

    assert _ack(data_dir, "cmd-1")["outcome"] == "accepted"
    assert _operation_ids(data_dir) == ["cmd-1"]
    assert _waiting(store) == ["cmd-1"]
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
    assert _poll(data_dir, store) is None

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

    assert write_acknowledgement(data_dir, "cmd-1", "accepted") is True

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
        write_acknowledgement(data_dir, f"cmd-{index}", "accepted")
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
