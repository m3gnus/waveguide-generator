"""WG's half of Fusion delivery, version 3.

WG publishes each return request and handoff as one file with ``schemaVersion``
3 -- no single-slot marker and no twin -- and advertises version 3 in its
capability file. It refuses an add-in whose heartbeat reports less, and at
every start it removes what an older WG left for its add-in
(docs/architecture/CAD-OPERATIONS.md, "Delivery version" and "WG-produced
Fusion requests").

The add-in is played by ``Addin``, which takes files the way that contract
tells a version-3 add-in to. The contract strings are spelled out here rather
than imported, so a rename on WG's side shows up as a failure.
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

from server.cadlink.fusion_return import publish_return_request
from server.cadlink.solve_command import collect_solve_deliveries
from server.cadlink.store import CadLinkStore
from server.exports.cad_handoff import UPDATE_TARGET_REQUIRED, publish_fusion_handoff


CAPABILITIES = "wg-capabilities.json"
SEQUENCE = "deliverySequence"
RETURNS = "return"
HANDOFFS = "handoff"
# kind -> (per-request folder, the single slot a WG before version 3 wrote)
KINDS = {
    RETURNS: (".fusion-return-requests", ".fusion-return-request.json"),
    HANDOFFS: (".fusion-handoffs", ".fusion-handoff.json"),
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


class Addin:
    """An add-in that follows delivery version 3.

    One pass takes the request files in sequence order: claim by renaming to a
    hidden name, run, delete the claim. Files of another schema, or without a
    sequence, are left alone. ``after_claim`` runs after each claim, before the
    request runs.
    """

    def __init__(self, ipc: Path, session_id: str = SESSION) -> None:
        self.ipc = ipc
        self.session_id = session_id
        self.ran: list[str] = []

    def take(self, kind: str, after_claim: Callable[[str], object] | None = None) -> list[str]:
        folder = self.ipc / KINDS[kind][0]
        ran: list[str] = []

        def order(path: Path) -> tuple[int, str]:
            return (_sequence_of(_read(path)) or 0, path.name)

        def pending() -> list[Path]:
            # Listed afresh after every request, as the real add-in's next tick
            # does: a request published while one ran is taken in this pass.
            return [
                path for path in sorted(_requests(folder), key=order)
                if path.stem not in self.ran
            ]

        while candidates := pending():
            path = candidates[0]
            payload = _read(path)
            if (
                not isinstance(payload, dict)
                or payload.get("schemaVersion") != 3
                or payload.get("target") != "fusion360"
                or not payload.get("requestId")
                or payload.get("operationId") != payload.get("requestId")
                or _sequence_of(payload) is None
            ) or (kind == RETURNS and payload.get("sessionId") != self.session_id):
                self.ran.append(path.stem)  # left alone, and not looked at again
                continue
            request_id = str(payload["requestId"])
            claim = path.with_name(f".wglink-claim-{request_id}.json")
            os.rename(path, claim)
            if after_claim is not None:
                after_claim(request_id)
            self.ran.append(request_id)
            ran.append(request_id)
            claim.unlink()
        return ran


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


def _ipc(data_dir: Path) -> Path:
    return data_dir / "ipc" / "wglink"


def _folder(kind: str, data_dir: Path) -> Path:
    return _ipc(data_dir) / KINDS[kind][0]


def _publish(
    kind: str,
    data_dir: Path,
    workspace: Path,
    n: int,
    session: str = SESSION,
    *,
    instance: str | None = "instance-a",
):
    """Publish request number ``n`` of this kind; return what was published.

    A return request returns its request id; a handoff its ``PublishedRequest``.
    """

    if kind == RETURNS:
        _path, request_id = publish_return_request(
            data_dir,
            CadLinkStore.for_data_dir(data_dir),
            session_id=session,
            design_id="wgd_a",
            document_id="fusion:doc-a",
            instance_id="instance-a",
            expected_return_state_hash=f"sha256:state-{n}",
        )
        return request_id
    bundle = workspace / "wglink" / f"horn-{n}.wglink"
    bundle.mkdir(parents=True, exist_ok=True)
    return publish_fusion_handoff(
        data_dir,
        workspace,
        CadLinkStore.for_data_dir(data_dir),
        {
            "bundlePath": str(bundle),
            "bundleId": f"wgb_{n}",
            "exportId": f"wge_{n}",
            "sequence": n,
            "identity": {"designId": "wgd_a"},
        },
        expected_document_id="fusion:doc-a" if instance else None,
        expected_instance_id=instance,
        expected_return_state_hash=f"sha256:state-{n}" if instance else None,
    )


def _request_id(published) -> str:
    return published if isinstance(published, str) else published.request_id


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


# -- the capability file --------------------------------------------------------


def test_wg_advertises_its_delivery_versions_at_startup(
    data_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The suite turns the request consumer off; a WG that runs it advertises it.
    monkeypatch.delenv("WG2_CAD_DELIVERY", raising=False)
    _run_cadlink_startup(data_dir, workspace)

    # ``sourceIdentity`` tells the add-in it may declare source-identity-v1;
    # ``liveProtocol`` that this WG serves the live session protocol. The WG
    # request inbox reads schema 4 (a Send as well as a Solve); Fusion-bound
    # requests stay version 3 (M1 transfer contract, C3).
    assert _read(_ipc(data_dir) / CAPABILITIES) == {
        "schemaVersion": 1,
        "producer": "waveguide-generator",
        "solveCommandDelivery": 4,
        "fusionRequestDelivery": 3,
        "sourceIdentity": 1,
        "liveProtocol": 1,
        "documentUp": 1,
        "automaticDomain": 1,
    }


def test_wg_whose_consumer_is_off_does_not_advertise_solve_delivery(
    data_dir: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WG2_CAD_DELIVERY", "0")
    _run_cadlink_startup(data_dir, workspace)

    advertised = _read(_ipc(data_dir) / CAPABILITIES)
    assert "solveCommandDelivery" not in advertised
    assert advertised["fusionRequestDelivery"] == 3
    assert [path.name for path in _ipc(data_dir).iterdir() if path.name.endswith(".tmp")] == []


def test_a_solve_command_the_addin_writes_after_startup_is_one_operation(
    data_dir: Path, workspace: Path
) -> None:
    _run_cadlink_startup(data_dir, workspace)
    body = b'{"document": {}}'
    bundle = workspace / "wgreturn" / "speaker.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "wgreturn.json").write_bytes(body)
    folder = _ipc(data_dir) / ".wg-solve-requests"
    folder.mkdir(parents=True)
    written = folder / "cmd-1.json"
    written.write_text(json.dumps({
        "schemaVersion": 3,
        "target": "waveguide-generator",
        "commandId": "cmd-1",
        "operationId": "cmd-1",
        "returnId": "wgr_1",
        "bundlePath": "wgreturn/speaker.wgreturn",
        "manifestSha256": f"sha256:{hashlib.sha256(body).hexdigest()}",
        "requestedAt": "2026-09-13T01:00:00Z",
    }), encoding="utf-8")

    store = CadLinkStore.for_data_dir(data_dir)
    try:
        assert collect_solve_deliveries(data_dir, store) is None
        waiting = store.list_operations(states={"received"}, oldest_first=True)
        assert [row["operation_id"] for row in waiting] == ["cmd-1"]
        assert not written.exists()
        assert len(store.list_operations()) == 1
    finally:
        store.close()


# -- WG-produced requests: one file each ------------------------------------------


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_each_request_is_one_version_3_file_and_nothing_else(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    request_id = _request_id(_publish(kind, data_dir, workspace, 1))

    assert _files(kind, data_dir) == [request_id]
    payload = _read(_folder(kind, data_dir) / f"{request_id}.json")
    assert payload["schemaVersion"] == 3
    assert payload["requestId"] == payload["operationId"] == request_id
    assert payload[SEQUENCE] == 1
    # No single slot, no twin, no record.
    assert not (_ipc(data_dir) / KINDS[kind][1]).exists()
    assert [path.name for path in _folder(kind, data_dir).iterdir()] == [f"{request_id}.json"]


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_the_sequence_keeps_rising_while_requests_wait(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    first = _request_id(_publish(kind, data_dir, workspace, 1, instance="instance-a"))
    second = _request_id(_publish(kind, data_dir, workspace, 2, instance="instance-b"))

    folder = _folder(kind, data_dir)
    assert _read(folder / f"{first}.json")[SEQUENCE] == 1
    assert _read(folder / f"{second}.json")[SEQUENCE] == 2


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_version_3_addin_runs_each_request_once(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    addin = Addin(_ipc(data_dir))
    first = _request_id(_publish(kind, data_dir, workspace, 1, instance="instance-a"))
    second = _request_id(_publish(kind, data_dir, workspace, 2, instance="instance-b"))

    assert addin.take(kind) == [first, second]
    assert addin.take(kind) == []
    assert _files(kind, data_dir) == []


@pytest.mark.parametrize("kind", [RETURNS, HANDOFFS])
def test_a_publish_while_the_addin_runs_a_request_loses_nothing(
    kind: str, data_dir: Path, workspace: Path
) -> None:
    addin = Addin(_ipc(data_dir))
    first = _request_id(_publish(kind, data_dir, workspace, 1))
    published: list[str] = []

    def publish_the_next(_running: str) -> None:
        if not published:
            published.append(_request_id(_publish(kind, data_dir, workspace, 2)))

    assert addin.take(kind, after_claim=publish_the_next) == [first, published[0]]


def test_return_requests_for_an_earlier_session_are_withdrawn(
    data_dir: Path, workspace: Path
) -> None:
    old = _publish(RETURNS, data_dir, workspace, 1, session="session-old")
    new = _publish(RETURNS, data_dir, workspace, 2, session="session-new")

    assert _files(RETURNS, data_dir) == [new]
    assert old != new


# -- supersession: an unstarted update of the same link --------------------------


def test_a_newer_update_of_the_same_link_withdraws_one_not_yet_started(
    data_dir: Path, workspace: Path, caplog
) -> None:
    older = _publish(HANDOFFS, data_dir, workspace, 1, instance="instance-a")
    other_link = _publish(HANDOFFS, data_dir, workspace, 2, instance="instance-b")
    insert = _publish(HANDOFFS, data_dir, workspace, 3, instance=None)

    with caplog.at_level(logging.INFO):
        newer = _publish(HANDOFFS, data_dir, workspace, 4, instance="instance-a")

    assert newer.withdrawn == (older.request_id,)
    assert sorted(_files(HANDOFFS, data_dir)) == sorted(
        [other_link.request_id, insert.request_id, newer.request_id]
    )
    assert any(
        older.request_id in record.getMessage() and "superseded" in record.getMessage()
        for record in caplog.records
    )


def test_an_update_the_addin_has_already_claimed_is_never_superseded(
    data_dir: Path, workspace: Path
) -> None:
    older = _publish(HANDOFFS, data_dir, workspace, 1, instance="instance-a")
    started: list[object] = []

    def publish_while_running(_running: str) -> None:
        if not started:
            started.append(_publish(HANDOFFS, data_dir, workspace, 2, instance="instance-a"))

    ran = Addin(_ipc(data_dir)).take(HANDOFFS, after_claim=publish_while_running)

    assert started[0].withdrawn == ()
    assert ran == [older.request_id, started[0].request_id]


def test_inserts_are_never_superseded(data_dir: Path, workspace: Path) -> None:
    first = _publish(HANDOFFS, data_dir, workspace, 1, instance=None)
    second = _publish(HANDOFFS, data_dir, workspace, 2, instance=None)

    assert second.withdrawn == ()
    assert sorted(_files(HANDOFFS, data_dir)) == sorted([first.request_id, second.request_id])


def test_an_update_must_name_its_document_and_baseline(
    data_dir: Path, workspace: Path
) -> None:
    bundle = workspace / "wglink" / "horn-1.wglink"
    bundle.mkdir(parents=True)

    with pytest.raises(ValueError) as refused:
        publish_fusion_handoff(
            data_dir,
            workspace,
            CadLinkStore.for_data_dir(data_dir),
            {"bundlePath": str(bundle), "bundleId": "wgb_1", "exportId": "wge_1"},
            expected_document_id="fusion:doc-a",
            expected_instance_id="instance-a",
            expected_return_state_hash=None,
        )

    assert str(refused.value) == UPDATE_TARGET_REQUIRED
    assert _files(HANDOFFS, data_dir) == []


def test_a_return_request_must_name_its_baseline(data_dir: Path) -> None:
    with pytest.raises(ValueError, match="has not reported the model's state"):
        publish_return_request(
            data_dir,
            CadLinkStore.for_data_dir(data_dir),
            session_id=SESSION,
            design_id="wgd_a",
            document_id="fusion:doc-a",
            instance_id="instance-a",
            expected_return_state_hash=None,
        )


# -- what an older WG left behind ------------------------------------------------


def _older_wg_left(data_dir: Path, workspace: Path) -> dict[str, Path]:
    """What a WG with delivery version 2 left in the folder when it stopped."""

    ipc = _ipc(data_dir)
    left: dict[str, Path] = {}
    for kind, (folder_name, slot_name) in KINDS.items():
        folder = ipc / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        body = {"target": "fusion360", "requestId": f"old-{kind}", "operationId": f"old-{kind}",
                SEQUENCE: 1}
        (folder / f"old-{kind}.json").write_text(json.dumps({**body, "schemaVersion": 2}))
        (ipc / slot_name).write_text(json.dumps({**body, "schemaVersion": 1}))
        (folder / ".legacy-slot.json").write_text(json.dumps({"operationId": f"old-{kind}", SEQUENCE: 1}))
        left[f"{kind}-file"] = folder / f"old-{kind}.json"
        left[f"{kind}-slot"] = ipc / slot_name
        left[f"{kind}-record"] = folder / ".legacy-slot.json"
    return left


def test_startup_removes_what_an_older_wg_left_and_keeps_current_requests(
    data_dir: Path, workspace: Path, caplog
) -> None:
    left = _older_wg_left(data_dir, workspace)
    current = _request_id(_publish(HANDOFFS, data_dir, workspace, 5))
    claim = _folder(HANDOFFS, data_dir) / ".wglink-claim-req-9-abc.json"
    claim.write_text("{}")

    with caplog.at_level(logging.INFO):
        _run_cadlink_startup(data_dir, workspace)

    assert [name for name, path in left.items() if path.exists()] == []
    assert _files(HANDOFFS, data_dir) == [current]
    # The add-in's own claim is the add-in's to settle.
    assert claim.exists()
    assert any("older WG left" in record.getMessage() for record in caplog.records)
