"""The heartbeat over HTTP and ``/fusion-status`` reading either transport.

``docs/reference/CADLINK-LIVE-PROTOCOL.md`` section 6 is the contract. The file
transport is exercised through real ``.fusion-status.json`` files with the
pinned add-in's fields; the live transport through real application routes and
a registered session (the harness of ``test_cadlink_live_session.py``).
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from server.cadlink import addin_update, fusion_delivery, fusion_status
from server.cadlink import api as cadlink_api
from server.cadlink.api import FusionStatusRequest
from server.cadlink.identity import design_hash
from server.cadlink.live import registry as live_registry
from server.cadlink.operations import request_digest
from server.cadlink.store import CadLinkStore
from server.tests.test_cadlink_live_session import (
    INSTALLATION,
    JSON,
    LIVE,
    OTHER_INSTALLATION,
    Clock,
    Recorder,
    _code,
    _register,
    _registration,
    _session_headers,
    started,
)


HEARTBEAT = f"{LIVE}/heartbeat"
STATUS = "/api/cadlink/fusion-status"
ADAPTER_SESSION = "fusion-session-1"
STATUS_BODY = {"design": {"formula": "OSSE"}}
T0 = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Wall:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def wall(monkeypatch: pytest.MonkeyPatch) -> Wall:
    """One wall clock for heartbeat freshness, at POST and at selection."""

    fake = Wall()
    monkeypatch.setattr(fusion_status, "_utc_now", fake)
    return fake


@pytest.fixture(autouse=True)
def quiet_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status poll without the process probe or an activation pass."""

    async def no_activation(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(cadlink_api, "poll_activation", no_activation)
    monkeypatch.setattr(cadlink_api, "fusion_process_running", lambda: False)


def _current_hash() -> str:
    return design_hash(FusionStatusRequest.model_validate(STATUS_BODY).design)


def _link(**updates: Any) -> dict[str, Any]:
    """One link as the pinned add-in's ``write_fusion_status`` writes it."""

    link: dict[str, Any] = {
        "instanceId": "instance-1",
        "bundlePath": "/workspace/wglink/horn.wglink",
        "designId": "wgd_1",
        "lineageId": "wgl_1",
        "editVersion": "1",
        "designHash": _current_hash(),
        "designName": "Horn",
        "linkName": None,
        "formula": "OSSE",
        "configPresent": True,
        "parameterCount": 3,
        "parameterDriftCount": 0,
        "driftedParameters": [],
        "localBodyState": "unmodified",
        "bodyFingerprintHash": None,
        "documentSignatureHash": "sha256:doc",
        "documentBodyCount": 1,
        "sourceStateHash": None,
        "exportId": "wge_1",
        "exportSequence": "1",
    }
    link.update(updates)
    return link


def _heartbeat(
    *,
    updated_at: datetime = T0,
    session_id: str = ADAPTER_SESSION,
    links: list[dict[str, Any]] | None = None,
    document: bool = True,
    document_id: str = "fusion:doc-1",
    applying: dict[str, Any] | None = None,
    delivery: int | None = 3,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The pinned add-in's heartbeat object, field for field."""

    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "cadApplication": "fusion360",
        "sessionId": session_id,
        "adapterVersion": "0.3.3",
        "workspaceRoot": None,
        "updatedAt": _iso(updated_at),
        "document": (
            {"name": "Speaker", "id": document_id, "links": list(links or [])} if document else None
        ),
    }
    if delivery is not None:
        payload["deliveryVersion"] = delivery
    if document and applying is not None:
        payload["document"]["applyingOperation"] = applying
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    return payload


def _write_file(data_dir: Path, payload: dict[str, Any]) -> Path:
    folder = fusion_delivery.ipc_folder(data_dir, create=True)
    marker = folder / fusion_status.FUSION_STATUS_FILENAME
    marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return marker


def _remove_file(data_dir: Path) -> None:
    (fusion_delivery.ipc_folder(data_dir) / fusion_status.FUSION_STATUS_FILENAME).unlink(missing_ok=True)


def _session(client: Recorder, application, **overrides: Any) -> dict[str, Any]:
    response = _register(client, _registration(application, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _post(
    client: Recorder,
    token: str | None,
    payload: object,
    *,
    installation: str | None = INSTALLATION,
    extra: dict[str, str] | None = None,
    raw: bytes | None = None,
):
    headers = {**JSON, **_session_headers(token, installation), **(extra or {})}
    body = raw if raw is not None else json.dumps(payload).encode()
    return client.request("POST", HEARTBEAT, headers=headers, body=body)


def _status(client: Recorder, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.request(
        "POST", STATUS, headers=JSON, body=json.dumps(body or STATUS_BODY).encode()
    )
    assert response.status_code == 200, response.text
    return response.json()


def _without_transport(status: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in status.items() if key != "heartbeatTransport"}


# -- the route -----------------------------------------------------------------


def test_a_live_heartbeat_is_the_status_when_no_file_exists(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        assert _status(client)["state"] == "closed"
        assert _status(client)["heartbeatTransport"] is None
        response = _post(client, token, _heartbeat(links=[_link()]))
        assert response.status_code == 204, response.text
        assert response.body == b""
        status = _status(client)
        assert status["state"] == "current"
        assert status["heartbeatTransport"] == "live"
        assert status["sessionId"] == ADAPTER_SESSION
        assert not (fusion_delivery.ipc_folder(tmp_path) / fusion_status.FUSION_STATUS_FILENAME).exists()


PARITY_CASES = {
    "no_document": dict(document=False),
    "not_linked": dict(links=[]),
    "current": dict(links=[_link()]),
    "stale": dict(links=[_link(localBodyState="modified", parameterDriftCount=1, driftedParameters=["L"])]),
    "instance_selection_required": dict(links=[_link(), _link(instanceId="instance-2")]),
    "recovery_required": dict(
        links=[],
        applying={"operationId": "update-1", "kind": "update", "instanceId": "instance-1",
                  "exportId": "wge_1", "phase": "applied"},
    ),
}


@pytest.mark.parametrize("case", sorted(PARITY_CASES))
def test_live_and_file_heartbeats_give_identical_status_for_the_same_payload(
    tmp_path: Path, wall: Wall, case: str
) -> None:
    payload = _heartbeat(**PARITY_CASES[case])
    file_dir, live_dir = tmp_path / "file", tmp_path / "live"
    file_dir.mkdir()
    live_dir.mkdir()
    wall.now = T0 + timedelta(seconds=5)

    with started(file_dir) as application:
        _write_file(file_dir, payload)
        by_file = _status(Recorder(application))
    with started(live_dir) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        assert _post(client, token, copy.deepcopy(payload)).status_code == 204
        by_live = _status(client)

    assert by_file["heartbeatTransport"] == "file"
    assert by_live["heartbeatTransport"] == "live"
    assert _without_transport(by_live) == _without_transport(by_file)
    expected_state = "no_document" if case == "no_document" else (
        "not_linked" if case == "recovery_required" else case
    )
    assert by_live["state"] == expected_state
    if case == "recovery_required":
        assert by_live["recoveryRequired"]["operationId"] == "update-1"


def test_a_fresh_live_heartbeat_wins_over_a_fresh_file_heartbeat(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        _write_file(tmp_path, _heartbeat(document_id="fusion:file-doc"))
        assert _status(client)["documentId"] == "fusion:file-doc"
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc")).status_code == 204
        wall.now = T0 + timedelta(seconds=10)
        # The file was written later, and still loses to a fresh live heartbeat.
        _write_file(tmp_path, _heartbeat(document_id="fusion:file-doc", updated_at=wall.now))
        status = _status(client)
        assert status["documentId"] == "fusion:live-doc"
        assert status["heartbeatTransport"] == "live"


def test_a_live_heartbeat_that_aged_out_falls_back_to_a_fresh_file_heartbeat(
    tmp_path: Path, wall: Wall
) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc")).status_code == 204
        wall.now = T0 + fusion_status.FUSION_STATUS_TTL + timedelta(seconds=1)
        # Live aged out, file stale too: none.
        _write_file(tmp_path, _heartbeat(document_id="fusion:file-doc", updated_at=T0))
        both_stale = _status(client)
        assert both_stale["state"] == "closed" and both_stale["heartbeatTransport"] is None
        # A fresh file is selected once the live one is stale.
        _write_file(tmp_path, _heartbeat(document_id="fusion:file-doc", updated_at=wall.now))
        status = _status(client)
        assert status["documentId"] == "fusion:file-doc"
        assert status["heartbeatTransport"] == "file"
        # And a fresh live heartbeat takes over again.
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc", updated_at=wall.now)).status_code == 204
        assert _status(client)["heartbeatTransport"] == "live"


def test_a_heartbeat_already_stale_when_posted_is_409_heartbeat_stale_and_not_recorded(
    tmp_path: Path, wall: Wall
) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        old = T0 - fusion_status.FUSION_STATUS_TTL - timedelta(seconds=1)
        response = _post(client, token, _heartbeat(updated_at=old))
        assert response.status_code == 409 and _code(response) == "heartbeat_stale"
        assert response.json()["error"]["stage"] == "cadlink-live"
        assert application.state.live_registry.current_heartbeat() is None
        assert _status(client)["state"] == "closed"


def test_a_future_updated_at_beyond_skew_is_409_heartbeat_stale(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        future = T0 + timedelta(minutes=1, seconds=1)
        response = _post(client, token, _heartbeat(updated_at=future))
        assert response.status_code == 409 and _code(response) == "heartbeat_stale"
        assert application.state.live_registry.current_heartbeat() is None
        # Within the skew it is accepted, exactly as the file reader accepts it.
        assert _post(client, token, _heartbeat(updated_at=T0 + timedelta(seconds=59))).status_code == 204


def test_a_heartbeat_for_another_adapter_session_is_409_session_mismatch(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        for session_id in ("fusion-session-2", None):
            payload = _heartbeat(session_id="x")
            payload["sessionId"] = session_id
            response = _post(client, token, payload)
            assert response.status_code == 409 and _code(response) == "session_mismatch"
        payload = _heartbeat()
        del payload["sessionId"]
        response = _post(client, token, payload)
        assert response.status_code == 409 and _code(response) == "session_mismatch"
        assert application.state.live_registry.current_heartbeat() is None
        assert _status(client)["state"] == "closed"


def test_a_heartbeat_below_delivery_version_3_is_409_addin_outdated(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        for delivery in (2, None):
            response = _post(client, token, _heartbeat(delivery=delivery))
            assert response.status_code == 409 and _code(response) == "addin_outdated"
        payload = _heartbeat()
        payload["deliveryVersion"] = True
        response = _post(client, token, payload)
        assert response.status_code == 409 and _code(response) == "addin_outdated"
        assert application.state.live_registry.current_heartbeat() is None


def test_an_invalid_heartbeat_is_400_without_echoing_input(tmp_path: Path, wall: Wall) -> None:
    marker = "private-value-7f3a"
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        bodies = [
            b"{not json " + marker.encode(),
            json.dumps([marker]).encode(),
            json.dumps({**_heartbeat(), "schemaVersion": marker}).encode(),
            json.dumps({**_heartbeat(), "cadApplication": marker}).encode(),
            json.dumps({**_heartbeat(), "updatedAt": marker}).encode(),
            json.dumps({**_heartbeat(), "updatedAt": "2026-09-17T10:00:00"}).encode(),
        ]
        for body in bodies:
            response = _post(client, token, None, raw=body)
            assert response.status_code == 400, (body, response.text)
            assert _code(response) == "invalid_request"
            assert marker.encode() not in response.body
            assert b"2026-09-17T10:00:00" not in response.body
        assert application.state.live_registry.current_heartbeat() is None


def test_a_heartbeat_without_token_header_or_with_an_origin_is_refused(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        payload = _heartbeat(links=[_link()])
        for kwargs, status, code in (
            (dict(token=None), 401, "session_unknown"),
            (dict(token="not-a-token"), 401, "session_unknown"),
            (dict(token=token, installation=None), 401, "installation_mismatch"),
            (dict(token=token, installation=OTHER_INSTALLATION), 401, "installation_mismatch"),
            (dict(token=token, extra={"Origin": "http://127.0.0.1:3100"}), 403, "origin_not_allowed"),
        ):
            token_arg = kwargs.pop("token")
            response = _post(client, token_arg, payload, **kwargs)
            assert response.status_code == status and _code(response) == code, (kwargs, response.text)
        # ``Origin: null`` and foreign origins are already refused by the global guard.
        for origin in ("null", "https://example.com"):
            assert _post(client, token, payload, extra={"Origin": origin}).status_code == 403
        # Unauthenticated and malformed: the token answers before the body.
        for raw in (b"{not json", b"[]", b'{"schemaVersion": "x"}'):
            response = _post(client, None, None, raw=raw)
            assert response.status_code == 401 and _code(response) == "session_unknown"
        assert application.state.live_registry.current_heartbeat() is None
        assert _status(client)["state"] == "closed"


def test_an_oversized_heartbeat_is_413_and_not_recorded(tmp_path: Path, wall: Wall) -> None:
    from server.app import MAX_LIVE_HEARTBEAT_BODY_BYTES

    assert MAX_LIVE_HEARTBEAT_BODY_BYTES == fusion_status._MAX_STATUS_BYTES == 256 * 1024
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        # Above the 64 KiB default of the other live routes, within the file cap: accepted.
        roomy = _heartbeat(diagnostics={"padding": "x" * (128 * 1024)})
        assert _post(client, token, roomy).status_code == 204
        application.state.live_registry._heartbeat = None
        padded = _heartbeat(diagnostics={"padding": "x" * (256 * 1024)})
        body = json.dumps(padded).encode()
        response = _post(client, token, None, raw=body, extra={"Content-Length": str(len(body))})
        assert response.status_code == 413 and _code(response) == "request_too_large"
        assert response.json()["error"]["stage"] == "cadlink-live"
        response = _post(client, token, None, raw=body)
        assert response.status_code == 413 and _code(response) == "request_too_large"
        assert application.state.live_registry.current_heartbeat() is None


def test_a_late_older_heartbeat_never_replaces_a_newer_one(tmp_path: Path, wall: Wall) -> None:
    wall.now = T0 + timedelta(seconds=10)
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        newer = _heartbeat(document_id="fusion:newer", updated_at=T0 + timedelta(seconds=8))
        older = _heartbeat(document_id="fusion:older", updated_at=T0 + timedelta(seconds=5))
        assert _post(client, token, newer).status_code == 204
        # Arrives late: not an error, and ignored.
        response = _post(client, token, older)
        assert response.status_code == 204 and response.body == b""
        assert application.state.live_registry.current_heartbeat()["document"]["id"] == "fusion:newer"
        assert _status(client)["documentId"] == "fusion:newer"
        # The same updatedAt again replaces it; a later one too.
        same = _heartbeat(document_id="fusion:same", updated_at=T0 + timedelta(seconds=8))
        assert _post(client, token, same).status_code == 204
        assert _status(client)["documentId"] == "fusion:same"
        later = _heartbeat(document_id="fusion:later", updated_at=T0 + timedelta(seconds=9))
        assert _post(client, token, later).status_code == 204
        assert _status(client)["documentId"] == "fusion:later"


def test_the_registry_hands_out_copies_of_the_live_heartbeat(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        assert _post(client, token, _heartbeat(links=[_link()])).status_code == 204
        registry = application.state.live_registry
        first = registry.current_heartbeat()
        first["sessionId"] = "changed"
        first["document"]["id"] = "fusion:changed"
        first["document"]["links"].clear()
        second = registry.current_heartbeat()
        assert second == _heartbeat(links=[_link()])
        assert second is not first
        status = _status(client)
        assert status["documentId"] == "fusion:doc-1" and status["state"] == "current"


# -- sessions ------------------------------------------------------------------


def test_ending_the_session_drops_its_live_heartbeat_at_once(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        _write_file(tmp_path, _heartbeat(document_id="fusion:file-doc"))
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc")).status_code == 204
        assert _status(client)["heartbeatTransport"] == "live"
        assert client.request("DELETE", f"{LIVE}/sessions/current", headers=_session_headers(token)).status_code == 204
        assert application.state.live_registry.current_heartbeat() is None
        # Dropped, not merely hidden: the ended session's payload is not kept.
        assert application.state.live_registry._heartbeat is None
        status = _status(client)
        assert status["heartbeatTransport"] == "file"
        assert status["documentId"] == "fusion:file-doc"


def test_an_expired_or_superseded_session_heartbeat_is_not_read(
    tmp_path: Path, wall: Wall, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(live_registry, "_now", clock)
    monkeypatch.setattr(live_registry, "_wall", clock)
    with started(tmp_path) as application:
        client = Recorder(application)
        first = _session(client, application)["sessionToken"]
        assert _post(client, first, _heartbeat()).status_code == 204
        # Superseded by a new registration of the same installation.
        second = _session(client, application)["sessionToken"]
        assert application.state.live_registry.current_heartbeat() is None
        assert application.state.live_registry._heartbeat is None
        assert _status(client)["heartbeatTransport"] is None
        response = _post(client, first, _heartbeat())
        assert response.status_code == 401 and _code(response) == "session_superseded"

        # Heartbeats keep the session from idling out...
        for _ in range(20):
            clock.now += live_registry.HEARTBEAT_INTERVAL_SECONDS
            assert _post(client, second, _heartbeat()).status_code == 204
        assert _status(client)["heartbeatTransport"] == "live"
        # ...and without them it expires, taking its heartbeat with it, even
        # while the payload's own timestamp would still pass (the wall clock stands).
        clock.now += live_registry.IDLE_TIMEOUT_SECONDS + 1
        assert application.state.live_registry.current_heartbeat() is None
        assert _status(client)["heartbeatTransport"] is None
        response = _post(client, second, _heartbeat())
        assert response.status_code == 401 and _code(response) == "token_expired"


def test_another_apps_live_heartbeat_is_never_selected(tmp_path: Path, wall: Wall) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    with started(left) as a, started(right) as b:
        client_a = Recorder(a)
        token = _session(client_a, a)["sessionToken"]
        assert _post(client_a, token, _heartbeat()).status_code == 204
        assert _status(client_a)["heartbeatTransport"] == "live"
        assert _status(Recorder(b))["heartbeatTransport"] is None
        assert fusion_status.select_heartbeat(right) == (None, None)
        # A's token is not B's session.
        response = _post(Recorder(b), token, _heartbeat())
        assert response.status_code == 401 and _code(response) == "session_unknown"

    # A stopped app on the same data directory: its registry is gone, the file wins.
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc")).status_code == 204
    _write_file(tmp_path, _heartbeat(document_id="fusion:file-doc"))
    payload, transport = fusion_status.select_heartbeat(tmp_path)
    assert transport == "file" and payload["document"]["id"] == "fusion:file-doc"


def test_a_wg_restart_reads_the_file_heartbeat_before_the_addin_registers_again(
    tmp_path: Path, wall: Wall
) -> None:
    with started(tmp_path) as first:
        client = Recorder(first)
        token = _session(client, first)["sessionToken"]
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc")).status_code == 204
    _write_file(tmp_path, _heartbeat(document_id="fusion:file-doc"))
    with started(tmp_path) as second:
        client = Recorder(second)
        status = _status(client)
        assert status["heartbeatTransport"] == "file"
        assert status["documentId"] == "fusion:file-doc"
        # The old token is refused until the add-in registers again.
        response = _post(client, token, _heartbeat(document_id="fusion:live-doc"))
        assert response.status_code == 401 and _code(response) == "session_unknown"
        assert _status(client)["heartbeatTransport"] == "file"
        token = _session(client, second)["sessionToken"]
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc")).status_code == 204
        assert _status(client)["documentId"] == "fusion:live-doc"


# -- one selection per status call, settlement ---------------------------------


def test_one_status_call_settles_and_reports_from_the_same_heartbeat(
    tmp_path: Path, wall: Wall, monkeypatch: pytest.MonkeyPatch
) -> None:
    selections: list[tuple[Any, Any]] = []
    real_select = fusion_status.select_heartbeat

    def counting_select(*args: Any, **kwargs: Any):
        selected = real_select(*args, **kwargs)
        selections.append(selected)
        return selected

    settled: list[Any] = []
    real_settle = cadlink_api.settle_from_heartbeat

    def settle_then_flip(store, heartbeat, ipc, **kwargs):
        settled.append(heartbeat)
        # Between settlement and the status read, the add-in rewrites its file
        # and the selected heartbeat ages past the freshness limit.
        _write_file(tmp_path, _heartbeat(document_id="fusion:flipped", links=[_link()], updated_at=wall.now))
        result = real_settle(store, heartbeat, ipc, **kwargs)
        wall.now = wall.now + fusion_status.FUSION_STATUS_TTL + timedelta(seconds=1)
        return result

    monkeypatch.setattr(fusion_status, "select_heartbeat", counting_select)
    monkeypatch.setattr(cadlink_api, "select_heartbeat", counting_select)
    monkeypatch.setattr(cadlink_api, "settle_from_heartbeat", settle_then_flip)
    with started(tmp_path) as application:
        client = Recorder(application)
        selections.clear()  # startup's own settlement pass
        _write_file(tmp_path, _heartbeat(document_id="fusion:first", links=[]))
        status = _status(client)
        assert len(selections) == 1
        assert len(settled) == 1 and settled[0] is selections[0][0]
        assert status["documentId"] == "fusion:first"
        assert status["state"] == "not_linked"  # not "closed": measured at the selection's instant
        assert status["heartbeatTransport"] == "file"

        # The same holds with a live heartbeat selected.
        selections.clear()
        settled.clear()
        token = _session(client, application)["sessionToken"]
        live = _heartbeat(document_id="fusion:live", links=[], updated_at=wall.now)
        assert _post(client, token, live).status_code == 204
        status = _status(client)
        assert len(selections) == 1 and len(settled) == 1
        assert settled[0] is selections[0][0]
        assert status["documentId"] == "fusion:live" and status["heartbeatTransport"] == "live"
        assert status["state"] == "not_linked"


def _accept_update(store: CadLinkStore, data_dir: Path, operation_id: str = "update-1") -> None:
    target = {
        "document_id": "fusion:doc-1",
        "design_id": "wgd_1",
        "instance_id": "instance-1",
        "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:doc"},
    }
    inputs = {"export_id": "wge_2"}
    _row, result = store.accept_operation(
        operation_id, "update_link", request_digest("update_link", target, inputs), target, inputs
    )
    assert result == "created"
    published = fusion_delivery.publish_fusion_request(
        data_dir, fusion_delivery.HANDOFFS,
        {"target": "fusion360", "exportId": "wge_2", "expectedDocumentId": "fusion:doc-1",
         "expectedInstanceId": "instance-1"},
        operation_id,
    )
    # The add-in claims the request file: it is gone from the handoff folder.
    published.path.unlink()


def _run_transport(
    data_dir: Path, transport: str, payloads: list[dict[str, Any] | None], wall: Wall
) -> list[tuple[str, Any]]:
    """Feed each heartbeat through one transport; after each, the row state and the
    status's ``recoveryRequired``. ``None`` is heartbeat loss (file removed, or
    nothing posted)."""

    observed: list[tuple[str, Any]] = []
    for index, payload in enumerate(payloads):
        # Every step is a new WG start on the same data directory: a restart
        # forgets the live session, so heartbeat loss on the live transport is
        # simply not posting again.
        with started(data_dir) as application:
            client = Recorder(application)
            store: CadLinkStore = application.state.cadlink_store
            if index == 0:
                _accept_update(store, data_dir)
            if transport == "live" and payload is not None:
                token = _session(client, application)["sessionToken"]
                assert _post(client, token, payload).status_code == 204
            elif transport == "file":
                if payload is None:
                    _remove_file(data_dir)
                else:
                    _write_file(data_dir, payload)
            status = _status(client)
            row = store.get_operation("update-1")
            observed.append((str(row["state"]), status["recoveryRequired"]))
    return observed


def test_a_recovery_required_mark_survives_heartbeat_loss_and_restart_on_either_transport(
    tmp_path: Path, wall: Wall
) -> None:
    applying = {"operationId": "update-1", "kind": "update", "instanceId": "instance-1",
                "exportId": "wge_2", "phase": "applied"}
    payloads = [_heartbeat(applying=applying), None, _heartbeat(), None]
    results = {}
    for transport in ("file", "live"):
        data_dir = tmp_path / transport
        data_dir.mkdir()
        results[transport] = _run_transport(data_dir, transport, copy.deepcopy(payloads), wall)
    assert results["live"] == results["file"]
    states = [state for state, _ in results["file"]]
    assert states == ["recovery_required"] * 4
    assert results["file"][0][1]["operationId"] == "update-1"
    assert results["file"][2][1] is None


def test_live_heartbeat_settles_operations_like_the_file_heartbeat(tmp_path: Path, wall: Wall) -> None:
    evidence = _heartbeat(links=[_link(operationId="update-1", exportId="wge_2")])
    outcomes = {}
    for transport in ("file", "live"):
        data_dir = tmp_path / transport
        data_dir.mkdir()
        with started(data_dir) as application:
            client = Recorder(application)
            store: CadLinkStore = application.state.cadlink_store
            _accept_update(store, data_dir)
            if transport == "file":
                _write_file(data_dir, evidence)
            else:
                token = _session(client, application)["sessionToken"]
                assert _post(client, token, copy.deepcopy(evidence)).status_code == 204
            _status(client)
            row = store.get_operation("update-1")
            outcomes[transport] = (row["state"], json.loads(row["outcome_json"]))
    assert outcomes["live"] == outcomes["file"]
    assert outcomes["live"][0] == "accepted"
    assert outcomes["live"][1]["evidence"] == {"operation_id": "update-1", "export_id": "wge_2"}


def test_reconcile_settles_from_the_live_heartbeat(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        store: CadLinkStore = application.state.cadlink_store
        _accept_update(store, tmp_path)
        assert store.claim("update-1", int(store.get_operation("update-1")["attempt_generation"])) is not None
        token = _session(client, application)["sessionToken"]
        evidence = _heartbeat(links=[_link(operationId="update-1", exportId="wge_2")])
        assert _post(client, token, evidence).status_code == 204
        response = client.request("POST", "/api/cadlink/operations/update-1/reconcile", headers=JSON, body=b"")
        assert response.status_code == 200, response.text
        assert store.get_operation("update-1")["state"] == "accepted"


# -- other readers ---------------------------------------------------------------


def test_a_live_heartbeat_blocks_wglink_activation_like_a_file_heartbeat(
    tmp_path: Path, wall: Wall, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addin_update, "fusion_process_state", lambda: "closed")
    with started(tmp_path) as application:
        client = Recorder(application)
        assert addin_update._live_fusion_state(tmp_path) == "closed"
        token = _session(client, application)["sessionToken"]
        assert _post(client, token, _heartbeat()).status_code == 204
        assert addin_update._live_fusion_state(tmp_path) == "running"


def test_the_insert_destination_uses_the_live_heartbeat_document(tmp_path: Path, wall: Wall) -> None:
    from server.exports.cad_handoff import publish_fusion_handoff

    workspace = tmp_path / "workspace"
    bundle = workspace / "wglink" / "horn.wglink"
    bundle.mkdir(parents=True)
    result = {"bundlePath": str(bundle), "bundleId": "wgb_1", "exportId": "wge_1",
              "sequence": 1, "identity": {"designId": "wgd_1"}}
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        assert _post(client, token, _heartbeat(document_id="fusion:live-doc")).status_code == 204
        published = publish_fusion_handoff(
            tmp_path, workspace, application.state.cadlink_store, result, request_id="insert-1"
        )
        destination = json.loads(published.path.read_text())["destination"]
        assert destination == {"kind": "document", "value": "fusion:live-doc"}


def test_the_return_request_reads_the_live_session_id(tmp_path: Path, wall: Wall) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        assert _post(client, token, _heartbeat(links=[_link()])).status_code == 204
        status = fusion_status.read_fusion_status(
            tmp_path, current_design_hash="", current_formula="", design_id="wgd_1",
            instance_id="instance-1",
        )
        assert status["sessionId"] == ADAPTER_SESSION and status["heartbeatTransport"] == "live"
        assert fusion_status.read_live_fusion_heartbeat(tmp_path)["sessionId"] == ADAPTER_SESSION


# -- the pinned add-in, file only ----------------------------------------------


@pytest.mark.parametrize(
    "variant",
    ["current", "outdated-2", "outdated-missing", "stale", "future", "no-document", "not-json", "absent"],
)
def test_a_file_only_addin_reads_the_same_with_or_without_a_live_session(
    tmp_path: Path, wall: Wall, variant: str
) -> None:
    """The pinned add-in never posts: registering a session changes nothing it sees."""

    def write(data_dir: Path) -> None:
        if variant == "absent":
            return
        if variant == "not-json":
            fusion_delivery.ipc_folder(data_dir, create=True).joinpath(
                fusion_status.FUSION_STATUS_FILENAME
            ).write_text("{", encoding="utf-8")
            return
        payload = {
            "current": _heartbeat(links=[_link()]),
            "outdated-2": _heartbeat(links=[_link()], delivery=2),
            "outdated-missing": _heartbeat(links=[_link()], delivery=None),
            "stale": _heartbeat(updated_at=T0 - timedelta(seconds=21)),
            "future": _heartbeat(updated_at=T0 + timedelta(seconds=61)),
            "no-document": _heartbeat(document=False),
        }[variant]
        _write_file(data_dir, payload)

    plain, with_session = tmp_path / "plain", tmp_path / "session"
    plain.mkdir()
    with_session.mkdir()
    write(plain)
    write(with_session)
    with started(plain) as application:
        before = _status(Recorder(application))
    with started(with_session) as application:
        client = Recorder(application)
        _session(client, application)
        after = _status(client)
    assert after == before
    assert before["heartbeatTransport"] == (
        "file" if variant in {"current", "outdated-2", "outdated-missing", "no-document"} else None
    )
    if variant.startswith("outdated"):
        assert before["state"] == "addin_outdated"


def test_status_without_a_started_live_service_reads_the_file(tmp_path: Path, wall: Wall) -> None:
    _write_file(tmp_path, _heartbeat(links=[_link()]))
    assert live_registry.registry_for(tmp_path) is None
    assert fusion_status.select_heartbeat(tmp_path)[1] == "file"
    status = fusion_status.read_fusion_status(
        tmp_path, current_design_hash=_current_hash(), current_formula="OSSE", design_id=None
    )
    assert status["state"] == "current" and status["heartbeatTransport"] == "file"


# -- plumbing --------------------------------------------------------------------


def test_the_heartbeat_route_is_a_quiet_session_route(tmp_path: Path) -> None:
    from server.app import QUIET_REQUEST_ROUTES, create_app
    from server.cadlink.live.api import SessionRoute

    assert ("POST", HEARTBEAT) in QUIET_REQUEST_ROUTES
    routes = [route for route in create_app(data_dir=tmp_path).routes if getattr(route, "path", "") == HEARTBEAT]
    assert len(routes) == 1 and isinstance(routes[0], SessionRoute)
    assert routes[0].methods == {"POST"}


def test_heartbeats_log_at_debug_only(tmp_path: Path, wall: Wall, caplog: pytest.LogCaptureFixture) -> None:
    with started(tmp_path) as application:
        client = Recorder(application)
        token = _session(client, application)["sessionToken"]
        with caplog.at_level(logging.DEBUG, logger="wg.requests"):
            assert _post(client, token, _heartbeat()).status_code == 204
        lines = [r for r in caplog.records if r.name == "wg.requests" and HEARTBEAT in r.getMessage()]
        assert lines and all(r.levelno == logging.DEBUG for r in lines)
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="wg.requests"):
            assert _post(client, None, _heartbeat()).status_code == 401
        refused = [r for r in caplog.records if r.name == "wg.requests" and HEARTBEAT in r.getMessage()]
        assert refused and all(r.levelno == logging.INFO for r in refused)
        assert all(token not in r.getMessage() for r in caplog.records)
