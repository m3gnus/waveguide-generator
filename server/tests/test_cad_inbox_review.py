"""The inbox under the faults the independent review of ``caaf89be`` found.

- F1: a read that fails is not a request that is invalid. A claim WG could not
  read is kept and read again, bounded and visible; only bytes that were read and
  are not a request are refused and deleted.
- F2: a field WG cannot even compare (a list or object) blocks nothing.
- F5: a refused claim that cannot be deleted is reported once, and its delete
  retried quietly.
- F6: the schema-4 validation the contract requires, and a new Send never
  holding the files behind it back a pass.
- F9: a schema-3 file that names a kind other than a solve is refused.

The injected ``PermissionError`` is what Windows raises while another process
(an indexer, antivirus, a backup, the add-in) holds the file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from server.cadlink import solve_command
from server.cadlink.fusion_delivery import ipc_folder
from server.cadlink.operations import PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT
from server.cadlink.preparation import settle_snapshot_operation
from server.cadlink.solve_command import (
    CLAIM_PREFIX,
    RETAIN_TRANSIENT,
    SOLVE_REQUESTS_DIRECTORY,
    collect_solve_deliveries,
)
from server.cadlink.store import CadLinkStore
from server.tests.test_cad_preparation import _write_return

SHARING_VIOLATION = (32, "The process cannot access the file because it is being used by another process")


@pytest.fixture(autouse=True)
def fresh_waits():
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims"):
        getattr(solve_command, name, {}).clear()
    yield
    for name in ("_retention_waits", "_unreadable_waits", "_refused_claims"):
        getattr(solve_command, name, {}).clear()


@pytest.fixture
def env(tmp_path: Path):
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_dir.mkdir()
    workspace.mkdir()
    store = CadLinkStore.for_data_dir(data_dir)
    yield data_dir, workspace, store
    store.close()


def _folder(data_dir: Path) -> Path:
    folder = ipc_folder(data_dir, create=True) / SOLVE_REQUESTS_DIRECTORY
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _drop(data_dir: Path, payload: dict[str, Any], *, raw: str | None = None) -> Path:
    folder = _folder(data_dir)
    target = folder / f"{payload['commandId']}.json"
    staged = folder / f".{target.name}.tmp"
    staged.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
    staged.replace(target)
    return target


def _request(cid: str, bundle_path: str, manifest: str, *, schema: int = 3, **extra: Any) -> dict[str, Any]:
    payload = {
        "schemaVersion": schema, "target": "waveguide-generator", "commandId": cid,
        "operationId": cid, "returnId": "", "bundlePath": bundle_path,
        "manifestSha256": manifest, "requestedAt": "2026-09-21T12:00:00Z",
    }
    payload.update(extra)
    return payload


def _claims(data_dir: Path) -> list[Path]:
    return [path for path in _folder(data_dir).iterdir() if path.name.startswith(CLAIM_PREFIX)]


def _fail_reads(monkeypatch: pytest.MonkeyPatch, *, claims: bool, times: int | None) -> dict[str, int]:
    """Make reads of claims (or of unclaimed request files) raise, ``times`` times or for ever."""

    real = os.open
    count = {"n": 0}

    def open_file(path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, *args: Any) -> int:
        candidate = Path(path)
        is_claim = candidate.name.startswith(CLAIM_PREFIX)
        target = is_claim if claims else (
            candidate.parent.name == SOLVE_REQUESTS_DIRECTORY and not is_claim
        )
        if target and (times is None or count["n"] < times):
            count["n"] += 1
            raise PermissionError(*SHARING_VIOLATION)
        return real(path, flags, *args)

    monkeypatch.setattr(solve_command.os, "open", open_file)
    return count


# -- F1 -------------------------------------------------------------------------


def test_a_claim_whose_read_fails_once_is_not_lost(env, monkeypatch) -> None:
    """The review's probe: one sharing violation on the first read of the claim."""

    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    _drop(data_dir, _request("cmd-held", bundle_path, manifest))
    count = _fail_reads(monkeypatch, claims=True, times=1)
    refused: list[dict[str, Any]] = []

    collect_solve_deliveries(data_dir, store, refuse=refused.append)
    collect_solve_deliveries(data_dir, store, refuse=refused.append)

    assert count["n"] == 1
    assert store.get_operation("cmd-held") is not None, f"request lost; refusals={refused}"
    assert refused == []


def test_an_unclaimed_file_whose_read_fails_is_taken_on_a_later_pass(env, monkeypatch) -> None:
    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    path = _drop(data_dir, _request("cmd-listing", bundle_path, manifest))
    # Every read of the unclaimed file fails while it is held...
    _fail_reads(monkeypatch, claims=False, times=None)
    collect_solve_deliveries(data_dir, store)
    assert path.exists() and store.get_operation("cmd-listing") is None
    # ...and once it is released, the next pass takes it.
    monkeypatch.undo()
    collect_solve_deliveries(data_dir, store)
    assert store.get_operation("cmd-listing") is not None


def test_a_kept_claim_whose_later_read_fails_is_still_taken(env, monkeypatch) -> None:
    """A claim kept for its return is re-read every pass; one failure must not end it."""

    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    _drop(data_dir, _request("cmd-kept", bundle_path, manifest))
    outcomes = iter([RETAIN_TRANSIENT, RETAIN_TRANSIENT, "retained"])
    retain = lambda _operation_id: next(outcomes)  # noqa: E731 - a scripted retention
    collect_solve_deliveries(data_dir, store, retain=retain)
    assert len(_claims(data_dir)) == 1  # kept for its return

    count = _fail_reads(monkeypatch, claims=True, times=None)
    refused: list[dict[str, Any]] = []
    collect_solve_deliveries(data_dir, store, retain=retain, refuse=refused.append)
    assert count["n"] >= 1 and len(_claims(data_dir)) == 1 and refused == []

    monkeypatch.undo()
    collect_solve_deliveries(data_dir, store, retain=retain)
    collect_solve_deliveries(data_dir, store, retain=retain)
    assert store.get_operation("cmd-kept") is not None
    assert _claims(data_dir) == []


def test_a_claim_that_stays_unreadable_is_reported_once_and_kept(env, monkeypatch) -> None:
    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    _drop(data_dir, _request("cmd-stuck", bundle_path, manifest))
    _fail_reads(monkeypatch, claims=True, times=None)
    monkeypatch.setattr(solve_command, "UNREADABLE_PASSES", 3)
    refused: list[dict[str, Any]] = []

    for _ in range(6):
        collect_solve_deliveries(data_dir, store, refuse=refused.append)

    # Visible once it has stayed unreadable, and only once...
    assert len(refused) == 1
    assert "could not read" in refused[0]["reason"].lower()
    assert refused[0]["file"] == "cmd-stuck.json"
    # ...and never deleted: it may be a valid request that is held.
    assert len(_claims(data_dir)) == 1
    monkeypatch.undo()
    collect_solve_deliveries(data_dir, store)
    assert store.get_operation("cmd-stuck") is not None


def test_bytes_that_are_not_a_request_are_still_refused_and_removed(env) -> None:
    """The control for the four above: a claim that was read and is not a request ends."""

    data_dir, _workspace, store = env
    claim = _folder(data_dir) / f"{CLAIM_PREFIX}abc.json"
    claim.write_text("{", encoding="utf-8")
    refused: list[dict[str, Any]] = []
    collect_solve_deliveries(data_dir, store, refuse=refused.append)
    assert not claim.exists()
    assert len(refused) == 1


# -- F2 -------------------------------------------------------------------------


def test_an_uncomparable_schema_version_is_left_alone_and_blocks_nothing(env) -> None:
    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    bad = _drop(data_dir, {**_request("cmd-bad", bundle_path, manifest), "schemaVersion": [4]})
    _drop(data_dir, _request("cmd-good", bundle_path, manifest))
    collect_solve_deliveries(data_dir, store)
    assert store.get_operation("cmd-good") is not None
    assert bad.exists()


@pytest.mark.parametrize("kind", [["receive_snapshot"], {"k": 1}, 7])
def test_an_uncomparable_kind_is_refused_and_blocks_nothing(env, kind) -> None:
    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    bad = _drop(data_dir, _request("cmd-bad", bundle_path, manifest, schema=4, kind=kind))
    _drop(data_dir, _request("cmd-good", bundle_path, manifest))
    refused: list[dict[str, Any]] = []
    collect_solve_deliveries(data_dir, store, refuse=refused.append)
    assert store.get_operation("cmd-good") is not None
    assert not bad.exists()
    assert [item["operationId"] for item in refused] == ["cmd-bad"]
    assert "kind" in refused[0]["reason"]


# -- F5 -------------------------------------------------------------------------


def test_a_refused_claim_that_cannot_be_deleted_is_reported_once(env, monkeypatch) -> None:
    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    _drop(data_dir, _request("cmd-nokind", bundle_path, manifest, schema=4))
    real = Path.unlink
    held = {"on": True, "tries": 0}

    def unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name.startswith(CLAIM_PREFIX) and held["on"]:
            held["tries"] += 1
            raise PermissionError(*SHARING_VIOLATION)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    refused: list[dict[str, Any]] = []
    for _ in range(5):
        collect_solve_deliveries(data_dir, store, refuse=refused.append)

    assert [item["file"] for item in refused] == ["cmd-nokind.json"]
    # The delete is retried on every pass, not given up...
    assert held["tries"] >= 5
    # ...and succeeds, quietly, once the file is released.
    held["on"] = False
    collect_solve_deliveries(data_dir, store, refuse=refused.append)
    assert _claims(data_dir) == []
    assert len(refused) == 1


# -- F6 and F9 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"commandId": "bad id!", "operationId": "bad id!"}, "commandId"),
        ({"returnId": None}, "returnId"),
        ({"returnId": 3}, "returnId"),
    ],
    ids=["id-grammar", "no-returnId", "returnId-not-a-string"],
)
def test_a_v4_solve_the_contract_does_not_accept_is_refused(env, overrides, reason) -> None:
    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    payload = _request("cmd-v4", bundle_path, manifest, schema=4, kind=PREPARE_AND_SOLVE)
    payload.update(overrides)
    if payload.get("returnId") is None:
        del payload["returnId"]
    folder = _folder(data_dir)
    (folder / "cmd-v4.json").write_text(json.dumps(payload), encoding="utf-8")
    refused: list[dict[str, Any]] = []
    collect_solve_deliveries(data_dir, store, refuse=refused.append)
    assert store.list_operations(limit=10) == []
    assert len(refused) == 1 and reason in refused[0]["reason"]


def test_a_schema_3_file_naming_another_kind_is_refused_not_solved(env) -> None:
    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    _drop(data_dir, _request("cmd-s3", bundle_path, manifest, kind=RECEIVE_SNAPSHOT))
    refused: list[dict[str, Any]] = []
    collect_solve_deliveries(data_dir, store, refuse=refused.append)
    assert store.get_operation("cmd-s3") is None
    assert [item["operationId"] for item in refused] == ["cmd-s3"]


def test_a_schema_3_file_naming_a_solve_is_a_solve(env) -> None:
    """The control: saying what schema 3 already means is allowed."""

    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    _drop(data_dir, _request("cmd-s3", bundle_path, manifest, kind=PREPARE_AND_SOLVE))
    collect_solve_deliveries(data_dir, store)
    assert store.get_operation("cmd-s3")["kind"] == PREPARE_AND_SOLVE


def test_a_new_send_does_not_hold_the_files_behind_it_back_a_pass(env) -> None:
    """A Send settles as it is taken; its own outcome is news, not a replay to stop at."""

    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    send = _request("cmd-send", bundle_path, manifest, schema=4, kind=RECEIVE_SNAPSHOT, requestedAt="2026-09-21T11:00:00Z")
    del send["returnId"]
    _drop(data_dir, send)
    _drop(data_dir, _request("cmd-solve", bundle_path, manifest, requestedAt="2026-09-21T12:00:00Z"))

    def retain(operation_id: str) -> str:
        return settle_snapshot_operation(store, data_dir, workspace.resolve(), operation_id)

    assert collect_solve_deliveries(data_dir, store, retain=retain) is None
    assert store.get_operation("cmd-send")["state"] == "accepted"
    assert store.get_operation("cmd-solve") is not None


def test_a_brief_hold_on_the_claim_is_ridden_out_in_the_same_pass(env, monkeypatch) -> None:
    """A sharing violation lasts milliseconds: the read is retried, not deferred a pass."""

    data_dir, workspace, store = env
    bundle_path, manifest = _write_return(workspace)
    _drop(data_dir, _request("cmd-brief", bundle_path, manifest))
    _fail_reads(monkeypatch, claims=True, times=2)
    collect_solve_deliveries(data_dir, store)
    assert store.get_operation("cmd-brief") is not None
    assert _claims(data_dir) == []
