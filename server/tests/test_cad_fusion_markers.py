"""WG's half of the mixed-version matrix for Fusion-bound requests.

WG publishes each return request and handoff twice under one request id: as its
own file, which a current add-in reads, and in the legacy single slot, which an
add-in that predates per-request files reads. It also advertises, in a
capability file, that it reads per-command solve files.

The add-in is played by two fakes. ``OldAddin`` reads only the legacy slots,
with the checks the add-in's watcher makes before per-request files existed.
``NewAddin`` follows "WG-produced Fusion requests" in
docs/architecture/CAD-OPERATIONS.md. The contract strings are spelled out here
rather than imported, so a rename on WG's side shows up as a failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import pytest

from server.cadlink.fusion_return import RETURN_REQUEST_FILENAME, publish_return_request
from server.cadlink.solve_command import collect_solve_deliveries, oldest_pending_solve_command
from server.cadlink.store import CadLinkStore
from server.exports.cad_handoff import HANDOFF_FILENAME, publish_fusion_handoff


CAPABILITIES = "wg-capabilities.json"
SLOT_RECORD = ".legacy-slot.json"
SEQUENCE = "deliverySequence"
RETURNS = "return"
HANDOFFS = "handoff"
# kind -> (legacy slot, per-request folder, the id an old add-in acknowledges by)
KINDS = {
    RETURNS: (RETURN_REQUEST_FILENAME, ".fusion-return-requests", "requestId"),
    HANDOFFS: (HANDOFF_FILENAME, ".fusion-handoffs", "exportId"),
}
SESSION = "session-a"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _requests(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        path for path in folder.iterdir()
        if path.is_file() and path.suffix == ".json" and not path.name.startswith(".")
    )


def _sequence_of(payload: Any) -> int | None:
    value = payload.get(SEQUENCE) if isinstance(payload, dict) else None
    valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    return value if valid else None


class OldAddin:
    """An add-in that reads only the legacy slots.

    It runs a slot's request when the slot is schema 1 for Fusion (and, for a
    return request, names its session), then deletes the slot only if it still
    holds the id it ran. ``ran`` records each request's WG request id, which the
    real add-in never reads: it is here so the test can say which request ran.
    ``before_ack`` runs between running a request and acknowledging it.
    """

    def __init__(self, ipc: Path, session_id: str = SESSION) -> None:
        self.ipc = ipc
        self.session_id = session_id
        self.ran: list[str] = []

    def take(self, kind: str, before_ack: Callable[[], object] | None = None) -> str | None:
        slot_name, _folder, ack_key = KINDS[kind]
        slot = self.ipc / slot_name
        payload = _read(slot)
        if (
            not isinstance(payload, dict)
            or payload.get("schemaVersion") != 1
            or payload.get("target") != "fusion360"
            or not payload.get(ack_key)
        ):
            return None
        if kind == RETURNS and payload.get("sessionId") != self.session_id:
            return None
        ran = str(payload.get("requestId") or payload[ack_key])
        self.ran.append(ran)
        if before_ack is not None:
            before_ack()
        current = _read(slot)
        if isinstance(current, dict) and current.get(ack_key) == payload[ack_key]:
            slot.unlink()
        return ran


class NewAddin:
    """An add-in that follows the per-request contract.

    One pass: discard what a legacy reader already took, then take per-request
    files in sequence order. It never deletes a slot that names an
    ``operationId``: that is a twin, which only a legacy reader takes. A slot
    without one comes from a WG that predates per-request files, and is the
    only copy; it is deleted only while it still holds the request that ran.
    Files without a sequence are left alone. ``after_claim`` runs after each
    claim, before the request runs; ``before_slot_delete`` runs before that
    check.
    """

    def __init__(self, ipc: Path, session_id: str = SESSION) -> None:
        self.ipc = ipc
        self.session_id = session_id
        self.ran: list[str] = []
        self.discarded: list[str] = []

    def take(
        self,
        kind: str,
        after_claim: Callable[[str], object] | None = None,
        before_slot_delete: Callable[[], object] | None = None,
    ) -> list[str]:
        slot_name, folder_name, ack_key = KINDS[kind]
        slot = self.ipc / slot_name
        folder = self.ipc / folder_name
        ran: list[str] = []

        record = _read(folder / SLOT_RECORD)
        twin_id = record.get("operationId") if isinstance(record, dict) else None
        last = _sequence_of(record)
        twin = folder / f"{twin_id}.json"
        if twin_id and last is not None and not os.path.lexists(slot) and twin.exists():
            for path in _requests(folder):
                sequence = _sequence_of(_read(path))
                if path == twin or (sequence is not None and sequence <= last):
                    path.unlink()
                    self.discarded.append(path.stem)

        def order(path: Path) -> tuple[int, str]:
            return (_sequence_of(_read(path)) or 0, path.name)

        for path in sorted(_requests(folder), key=order):
            payload = _read(path)
            if (
                not isinstance(payload, dict)
                or payload.get("schemaVersion") != 2
                or payload.get("target") != "fusion360"
                or not payload.get("requestId")
                or payload.get("operationId") != payload.get("requestId")
            ):
                continue
            if _sequence_of(payload) is None:
                continue
            if kind == RETURNS and payload.get("sessionId") != self.session_id:
                continue
            request_id = str(payload["requestId"])
            claim = path.with_name(f".claim-{request_id}.json")
            os.rename(path, claim)
            if after_claim is not None:
                after_claim(request_id)
            if request_id not in self.ran:
                self.ran.append(request_id)
                ran.append(request_id)
            claim.unlink()

        payload = _read(slot)
        if (
            isinstance(payload, dict)
            and payload.get("schemaVersion") == 1
            and payload.get("target") == "fusion360"
            and not payload.get("operationId")
            and (kind != RETURNS or payload.get("sessionId") == self.session_id)
        ):
            request_id = str(payload.get("requestId") or payload.get(ack_key))
            if request_id not in self.ran:
                self.ran.append(request_id)
                ran.append(request_id)
            if before_slot_delete is not None:
                before_slot_delete()
            current = _read(slot)
            if (
                isinstance(current, dict)
                and not current.get("operationId")
                and current.get(ack_key) == payload.get(ack_key)
            ):
                slot.unlink()
        return ran


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


def _ipc(data_dir: Path) -> Path:
    return data_dir / "ipc" / "wglink"


def _slot(kind: str, data_dir: Path) -> Path:
    return _ipc(data_dir) / KINDS[kind][0]


def _folder(kind: str, data_dir: Path) -> Path:
    return _ipc(data_dir) / KINDS[kind][1]


def _publish(kind: str, data_dir: Path, workspace: Path, n: int, session: str = SESSION) -> str:
    """Publish request number ``n`` of this kind and return its request id."""

    if kind == RETURNS:
        _marker, request_id = publish_return_request(
            data_dir,
            session_id=session,
            design_id="wgd_a",
            document_id="fusion:doc-a",
            instance_id="instance-a",
            expected_return_state_hash=f"sha256:state-{n}",
        )
        return request_id
    bundle = workspace / "wglink" / f"horn-{n}.wglink"
    bundle.mkdir(parents=True, exist_ok=True)
    marker = publish_fusion_handoff(
        data_dir,
        workspace,
        {
            "bundlePath": str(bundle),
            "bundleId": f"wgb_{n}",
            "exportId": f"wge_{n}",
            "sequence": n,
            "identity": {"designId": "wgd_a"},
        },
    )
    request_id = _read(marker).get("requestId")
    assert request_id, "the legacy twin carries the request id"
    return str(request_id)


def _files(kind: str, data_dir: Path) -> list[str]:
    return [path.stem for path in _requests(_folder(kind, data_dir))]


def _run_cadlink_startup(data_dir: Path, workspace: Path) -> None:
    """Run the startup work the CAD Link routes register, and nothing else.

    The whole startup also refreshes the Fusion add-in and warms native
    workers, which a unit test must not do.
    """

    from server.app import create_app

    application = create_app(data_dir=data_dir, workspace_dir=workspace / "runs")
    handlers = [
        handler for handler in application.router.on_startup
        if getattr(handler, "__name__", "") == "advertise_fusion_delivery_on_startup"
    ]
    assert len(handlers) == 1, "CAD Link registers one startup step for Fusion delivery"
    for handler in handlers:
        result = handler()
        if asyncio.iscoroutine(result):
            asyncio.run(result)


# -- the capability file ------------------------------------------------------


def test_wg_advertises_at_startup_that_it_reads_per_command_solve_files(
    data_dir: Path, workspace: Path
) -> None:
    _run_cadlink_startup(data_dir, workspace)

    payload = _read(_ipc(data_dir) / CAPABILITIES)
    assert payload == {
        "schemaVersion": 1,
        "producer": "waveguide-generator",
        "solveCommandDelivery": 2,
        "fusionRequestDelivery": 2,
    }
    assert [path.name for path in _ipc(data_dir).iterdir() if path.name.endswith(".tmp")] == []


def _addin_writes_solve_command(ipc: Path, command_id: str, bundle: str, manifest: str) -> Path:
    """Write a solve command the way the contract tells a current add-in to.

    Per-command files only when WG advertises ``solveCommandDelivery`` >= 2;
    the legacy slot otherwise.
    """

    capability = _read(ipc / CAPABILITIES)
    advertised = isinstance(capability, dict) and isinstance(
        capability.get("solveCommandDelivery"), int
    ) and capability["solveCommandDelivery"] >= 2
    payload = {
        "schemaVersion": 2 if advertised else 1,
        "target": "waveguide-generator",
        "commandId": command_id,
        "returnId": "wgr_1",
        "bundlePath": bundle,
        "manifestSha256": manifest,
        "requestedAt": "2026-09-13T01:00:00Z",
    }
    if advertised:
        folder = ipc / ".wg-solve-requests"
        folder.mkdir(parents=True, exist_ok=True)
        payload["operationId"] = command_id
        path = folder / f"{command_id}.json"
    else:
        ipc.mkdir(parents=True, exist_ok=True)
        path = ipc / ".wg-solve-request.json"
    staging = path.with_name(f".{path.name}.tmp")
    staging.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(staging, path)
    return path


@pytest.mark.parametrize("wg_started", [True, False], ids=["advertised", "not-advertised"])
def test_a_solve_command_is_one_operation_in_whichever_format_the_capability_selects(
    data_dir: Path, workspace: Path, wg_started: bool
) -> None:
    if wg_started:
        _run_cadlink_startup(data_dir, workspace)
    body = b'{"document": {}}'
    bundle = workspace / "wgreturn" / "speaker.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "wgreturn.json").write_bytes(body)
    manifest = f"sha256:{hashlib.sha256(body).hexdigest()}"

    written = _addin_writes_solve_command(
        _ipc(data_dir), "cmd-1", "wgreturn/speaker.wgreturn", manifest
    )
    assert (written.parent.name == ".wg-solve-requests") is wg_started

    store = CadLinkStore.for_data_dir(data_dir)
    try:
        assert collect_solve_deliveries(data_dir, store) is None
        pending = oldest_pending_solve_command(store)
        assert pending is not None and pending.command_id == "cmd-1"
        assert not written.exists()
        assert len(store.list_operations()) == 1
    finally:
        store.close()


# -- WG-produced requests: payloads ---------------------------------------------


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_each_request_is_one_file_plus_a_legacy_twin_under_the_same_id(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    request_id = _publish(kind, data_dir, workspace, 1)

    per_request = _read(_folder(kind, data_dir) / f"{request_id}.json")
    twin = _read(_slot(kind, data_dir))
    assert isinstance(per_request, dict), "the request has its own file"
    assert per_request["schemaVersion"] == 2
    assert per_request["requestId"] == per_request["operationId"] == request_id
    assert per_request[SEQUENCE] == 1
    assert twin == {**per_request, "schemaVersion": 1}
    assert _read(_folder(kind, data_dir) / SLOT_RECORD) == {
        "operationId": request_id,
        SEQUENCE: 1,
    }
    leftovers = [
        path.name for path in (_ipc(data_dir), _folder(kind, data_dir))
        for path in path.iterdir() if path.name.endswith(".tmp")
    ]
    assert leftovers == []


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_the_sequence_keeps_rising_after_requests_are_taken(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    _publish(kind, data_dir, workspace, 1)
    new = NewAddin(_ipc(data_dir))
    assert len(new.take(kind)) == 1
    assert _files(kind, data_dir) == []

    second = _publish(kind, data_dir, workspace, 2)

    assert _read(_folder(kind, data_dir) / f"{second}.json")[SEQUENCE] == 2


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_the_legacy_twin_is_written_after_its_file_and_withdraws_it_on_failure(
    kind: str, data_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot_name = KINDS[kind][0]
    seen: list[list[str]] = []
    real_replace = os.replace

    def failing_replace(source, destination, *args, **kwargs):
        if Path(destination).name == slot_name:
            seen.append(_files(kind, data_dir))
            raise OSError("disk full")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        _publish(kind, data_dir, workspace, 1)

    assert len(seen) == 1 and len(seen[0]) == 1, "the request's file exists before its twin"
    assert _files(kind, data_dir) == [], "a request without its twin is withdrawn"
    assert not _slot(kind, data_dir).exists()
    assert not (_folder(kind, data_dir) / SLOT_RECORD).exists()


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_record_a_reader_holds_open_is_retried_and_a_lasting_failure_is_only_logged(
    kind: str,
    data_dir: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("server.cadlink.fusion_delivery._WRITE_RETRY_SECONDS", 0)
    real_replace = os.replace
    refusals = {"left": 3}

    def busy_replace(source, destination, *args, **kwargs):
        if Path(destination).name == SLOT_RECORD and refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError("the file is open in another process")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", busy_replace)
    first = _publish(kind, data_dir, workspace, 1)
    assert refusals["left"] == 0
    assert _read(_folder(kind, data_dir) / SLOT_RECORD) == {"operationId": first, SEQUENCE: 1}

    refusals["left"] = 1000
    with caplog.at_level(logging.WARNING, logger="server.cadlink.fusion_delivery"):
        second = _publish(kind, data_dir, workspace, 2)
    assert "Could not record" in caplog.text
    assert _read(_slot(kind, data_dir))["operationId"] == second, "the request is delivered"
    assert _read(_folder(kind, data_dir) / SLOT_RECORD) == {"operationId": first, SEQUENCE: 1}


def test_return_requests_for_an_earlier_session_are_withdrawn(
    data_dir: Path, workspace: Path
) -> None:
    earlier = _publish(RETURNS, data_dir, workspace, 1, session="session-old")
    current = _publish(RETURNS, data_dir, workspace, 2, session="session-new")

    assert earlier != current
    assert _files(RETURNS, data_dir) == [current]


# -- the matrix: new WG with an old add-in ---------------------------------------


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_new_wg_with_an_old_addin_runs_each_request_once_across_an_upgrade(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    old = OldAddin(_ipc(data_dir))
    first = _publish(kind, data_dir, workspace, 1)
    assert old.take(kind) == first

    second = _publish(kind, data_dir, workspace, 2)
    # The old add-in left the first request's own file behind. Publishing the
    # next one discards it: the slot is empty, so a legacy reader ran it.
    assert _files(kind, data_dir) == [second]
    assert old.take(kind) == second
    assert old.ran == [first, second]

    # The add-in is upgraded before WG publishes again.
    new = NewAddin(_ipc(data_dir))
    assert new.take(kind) == []
    assert new.discarded == [second]
    assert _files(kind, data_dir) == []


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_requests_an_old_addin_never_saw_are_discarded_with_the_one_it_ran(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    first = _publish(kind, data_dir, workspace, 1)
    second = _publish(kind, data_dir, workspace, 2)
    old = OldAddin(_ipc(data_dir))
    # The first was replaced in the slot before the old add-in looked.
    assert old.take(kind) == second
    assert _files(kind, data_dir) == sorted([first, second])

    third = _publish(kind, data_dir, workspace, 3)

    assert _files(kind, data_dir) == [third]


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_request_an_old_addin_ran_while_wg_replaced_the_slot_goes_with_the_next(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    old = OldAddin(_ipc(data_dir))
    first = _publish(kind, data_dir, workspace, 1)
    published: list[str] = []

    # WG publishes the second request while the old add-in runs the first. Its
    # acknowledgement then finds another id in the slot, and deletes nothing.
    assert old.take(kind, before_ack=lambda: published.append(
        _publish(kind, data_dir, workspace, 2)
    )) == first
    second = published[0]
    assert _read(_slot(kind, data_dir))["operationId"] == second
    assert _files(kind, data_dir) == sorted([first, second])

    assert old.take(kind) == second
    third = _publish(kind, data_dir, workspace, 3)

    assert old.ran == [first, second]
    assert _files(kind, data_dir) == [third]


# -- the matrix: new WG with a new add-in ----------------------------------------


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_new_wg_with_a_new_addin_runs_each_request_once(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    """No supersession policy is applied here: that is not settled yet."""

    first = _publish(kind, data_dir, workspace, 1)
    second = _publish(kind, data_dir, workspace, 2)
    new = NewAddin(_ipc(data_dir))

    assert new.take(kind) == [first, second]
    assert new.take(kind) == []
    assert new.discarded == []
    assert _files(kind, data_dir) == []
    # The twin stays: only a legacy reader takes it, and it never runs here.
    assert _read(_slot(kind, data_dir))["operationId"] == second

    third = _publish(kind, data_dir, workspace, 3)
    assert _files(kind, data_dir) == [third]
    assert new.take(kind) == [third]
    assert new.ran == [first, second, third]


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_publish_while_a_new_addin_takes_a_request_loses_nothing(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    first = _publish(kind, data_dir, workspace, 1)
    published: list[str] = []
    new = NewAddin(_ipc(data_dir))

    assert new.take(kind, after_claim=lambda _id: published.append(
        _publish(kind, data_dir, workspace, 2)
    )) == [first]
    second = published[0]

    assert new.take(kind) == [second]
    assert new.discarded == []
    _publish(kind, data_dir, workspace, 3)
    assert new.discarded == []
    assert len(_files(kind, data_dir)) == 1


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_request_a_new_addin_is_still_holding_is_never_discarded(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    first = _publish(kind, data_dir, workspace, 1)
    second = _publish(kind, data_dir, workspace, 2)
    # The new add-in has taken the second request; the first waits for the user.
    (_folder(kind, data_dir) / f"{second}.json").unlink()

    third = _publish(kind, data_dir, workspace, 3)
    _run_cadlink_startup(data_dir, workspace)

    assert _files(kind, data_dir) == sorted([first, third])
    assert _read(_slot(kind, data_dir))["operationId"] == third


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_startup_retires_a_twin_whose_file_a_new_addin_took(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    _publish(kind, data_dir, workspace, 1)
    assert len(NewAddin(_ipc(data_dir)).take(kind)) == 1
    assert _slot(kind, data_dir).exists()

    _run_cadlink_startup(data_dir, workspace)

    assert not _slot(kind, data_dir).exists()
    # Nothing a legacy reader could have taken: the next publish discards nothing.
    fourth = _publish(kind, data_dir, workspace, 4)
    assert _files(kind, data_dir) == [fourth]
    assert _read(_folder(kind, data_dir) / f"{fourth}.json")[SEQUENCE] == 2


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_startup_discards_by_sequence_not_by_clock(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    folder = _folder(kind, data_dir)
    older = _publish(kind, data_dir, workspace, 1)
    ran = _publish(kind, data_dir, workspace, 2)
    assert OldAddin(_ipc(data_dir)).take(kind) == ran
    # A later request whose file carries an older time (the clock stepped
    # back, or the folder was restored), and one without a sequence.
    later = folder / "later-request.json"
    later.write_text(json.dumps({"schemaVersion": 2, SEQUENCE: 3}), encoding="utf-8")
    stat = (folder / f"{older}.json").stat()
    os.utime(later, ns=(stat.st_atime_ns, stat.st_mtime_ns - 60_000_000_000))
    (folder / "unsequenced.json").write_text(json.dumps({"schemaVersion": 2}), encoding="utf-8")
    assert _files(kind, data_dir) == sorted([older, ran, "later-request", "unsequenced"])

    _run_cadlink_startup(data_dir, workspace)

    assert _files(kind, data_dir) == ["later-request", "unsequenced"]


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_lagging_record_is_repaired_before_wg_empties_the_slot(
    kind: str, data_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("server.cadlink.fusion_delivery._WRITE_RETRY_SECONDS", 0)
    first = _publish(kind, data_dir, workspace, 1)
    real_replace = os.replace

    def busy_replace(source, destination, *args, **kwargs):
        if Path(destination).name == SLOT_RECORD:
            raise PermissionError("the file is open in another process")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", busy_replace)
    second = _publish(kind, data_dir, workspace, 2)
    monkeypatch.setattr(os, "replace", real_replace)
    record = _folder(kind, data_dir) / SLOT_RECORD
    assert _read(record)["operationId"] == first, "the record lags the slot"
    # A per-request reader took the second request; the first is unclaimed.
    (_folder(kind, data_dir) / f"{second}.json").unlink()

    _run_cadlink_startup(data_dir, workspace)
    _run_cadlink_startup(data_dir, workspace)

    assert _read(record) == {"operationId": second, SEQUENCE: 2}
    assert not _slot(kind, data_dir).exists(), "the taken twin is retired"
    assert _files(kind, data_dir) == [first], "the unrun request survives"
    new = NewAddin(_ipc(data_dir))
    assert new.take(kind) == [first]
    assert new.discarded == []
    third = _publish(kind, data_dir, workspace, 3)
    assert _read(_folder(kind, data_dir) / f"{third}.json")[SEQUENCE] == 3


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_record_wg_cannot_repair_keeps_the_slot_and_every_unrun_request(
    kind: str, data_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("server.cadlink.fusion_delivery._WRITE_RETRY_SECONDS", 0)
    first = _publish(kind, data_dir, workspace, 1)
    real_replace = os.replace

    def busy_replace(source, destination, *args, **kwargs):
        if Path(destination).name == SLOT_RECORD:
            raise PermissionError("the file is open in another process")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", busy_replace)
    second = _publish(kind, data_dir, workspace, 2)
    (_folder(kind, data_dir) / f"{second}.json").unlink()

    _run_cadlink_startup(data_dir, workspace)
    _run_cadlink_startup(data_dir, workspace)

    assert _read(_slot(kind, data_dir))["operationId"] == second, "not retired unrecorded"
    assert _files(kind, data_dir) == [first]


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_file_the_twin_failure_must_withdraw_is_retried_while_a_reader_holds_it(
    kind: str, data_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("server.cadlink.fusion_delivery._WRITE_RETRY_SECONDS", 0)
    slot_name = KINDS[kind][0]
    real_replace = os.replace
    real_unlink = Path.unlink
    held = {"left": 3}

    def failing_replace(source, destination, *args, **kwargs):
        if Path(destination).name == slot_name:
            raise OSError("disk full")
        return real_replace(source, destination, *args, **kwargs)

    def held_unlink(self, *args, **kwargs):
        if self.parent.name == KINDS[kind][1] and self.suffix == ".json" and held["left"]:
            held["left"] -= 1
            raise PermissionError("the file is open in another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    monkeypatch.setattr(Path, "unlink", held_unlink)
    with pytest.raises(OSError, match="disk full"):
        _publish(kind, data_dir, workspace, 1)

    assert held["left"] == 0
    assert _files(kind, data_dir) == []


# -- the matrix: old WG with a new add-in ----------------------------------------


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_slot_without_an_operation_id_is_the_only_copy_and_runs_once(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    """A WG that predates per-request files writes only the slot, as below."""

    ipc = _ipc(data_dir)
    ipc.mkdir(parents=True)
    payload: dict[str, Any] = {"schemaVersion": 1, "target": "fusion360"}
    if kind == RETURNS:
        payload.update(requestId="req-old-wg", sessionId=SESSION)
    else:
        payload.update(bundlePath="b", bundleId="wgb_1", exportId="wge_old_wg")
    (ipc / KINDS[kind][0]).write_text(json.dumps(payload), encoding="utf-8")
    new = NewAddin(ipc)

    expected = "req-old-wg" if kind == RETURNS else "wge_old_wg"
    assert new.take(kind) == [expected]
    assert new.take(kind) == []
    assert not (ipc / KINDS[kind][0]).exists()


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_newer_wg_publishing_while_a_reader_finishes_an_older_slot_loses_nothing(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    ipc = _ipc(data_dir)
    ipc.mkdir(parents=True)
    payload: dict[str, Any] = {"schemaVersion": 1, "target": "fusion360"}
    if kind == RETURNS:
        payload.update(requestId="req-old-wg", sessionId=SESSION)
    else:
        payload.update(bundlePath="b", bundleId="wgb_1", exportId="wge_old_wg")
    (ipc / KINDS[kind][0]).write_text(json.dumps(payload), encoding="utf-8")
    new = NewAddin(ipc)
    published: list[str] = []

    new.take(kind, before_slot_delete=lambda: published.append(
        _publish(kind, data_dir, workspace, 1)
    ))
    newer = published[0]

    assert _read(_slot(kind, data_dir))["operationId"] == newer, "the new twin survives"
    assert new.take(kind) == [newer]
    assert new.discarded == []
    _run_cadlink_startup(data_dir, workspace)
    assert new.discarded == []
