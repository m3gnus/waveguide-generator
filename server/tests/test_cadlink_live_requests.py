"""Fusion-bound requests over HTTP: long poll, claim, progress and completion.

``docs/reference/CADLINK-LIVE-PROTOCOL.md`` section 7 is the contract. Every test
runs a real application (``create_app``) on a real data directory with a
registered live session. Requests are published through WG's own publish paths
(``publish_return_request``, ``publish_fusion_handoff``) into the real request
folders, and file-mode claims are made the way the pinned add-in makes them: a
rename to ``.wglink-claim-<requestId>-<hex>.json``. A WG restart is a second
``create_app`` against the same data directory; a crash is an exception that
escapes the route between two steps, after which the old store is closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import shutil
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from server.app import create_app
from server.cadlink import fusion_delivery, fusion_return
from server.cadlink.fusion_outcomes import settle_from_heartbeat
from server.cadlink.fusion_return import publish_return_request
from server.cadlink.live import registry as live_registry
from server.cadlink.operations import RECEIVE_SNAPSHOT, normalize_request, request_digest
from server.cadlink.store import STORE_FORMAT_VERSION, CadLinkStore
from server.exports import cad_handoff
from server.exports.cad_handoff import publish_fusion_handoff
from server.tests.test_app_batch_e import Response
from server.tests.test_cadlink_live_session import (
    INSTALLATION,
    JSON,
    LIVE,
    OTHER_INSTALLATION,
    SHUTDOWN,
    STARTUP,
    Recorder,
    _code,
    _register,
    _registration,
    _run_handlers,
    _session_headers,
)


REQUESTS = f"{LIVE}/requests"
SESSION = "fusion-session-1"
OTHER_SESSION = "fusion-session-2"
DOCUMENT = "fusion:doc-1"
STATE_HASH = "sha256:state-1"
EXPORT = "wge_1"
WAIT = 15.0


class _Crash(BaseException):
    """A process that stops between two steps: nothing after it runs."""


# -- harness -------------------------------------------------------------------


class Wg:
    """One started WG on a data directory, with its WGLink folder."""

    def __init__(self, application, data_dir: Path, workspace: Path) -> None:
        self.app = application
        self.data_dir = data_dir
        self.workspace = workspace
        self.client = Recorder(application)

    @property
    def store(self) -> CadLinkStore:
        return self.app.state.cadlink_store

    @property
    def ipc(self) -> Path:
        return fusion_delivery.ipc_folder(self.data_dir)

    # -- sessions ---------------------------------------------------------------

    def token(self, *, installation: str = INSTALLATION, adapter_session: str = SESSION) -> str:
        response = _register(
            self.client,
            _registration(self.app, installation=installation, adapterSessionId=adapter_session),
        )
        assert response.status_code == 201, response.text
        return response.json()["sessionToken"]

    # -- HTTP -------------------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Response:
        return asyncio.run(asgi(self.app, method, path, query=query, headers=headers, body=body))

    def poll(
        self, token: str | None, wait: object = 0, *, installation: str | None = INSTALLATION,
        extra: dict[str, str] | None = None,
    ) -> Response:
        return self.call(
            "GET", REQUESTS, query=f"waitSeconds={wait}",
            headers={**_session_headers(token, installation), **(extra or {})},
        )

    def post(
        self, token: str | None, path: str, body: object, *, installation: str | None = INSTALLATION,
        extra: dict[str, str] | None = None, raw: bytes | None = None,
    ) -> Response:
        payload = raw if raw is not None else json.dumps(body).encode()
        return self.call(
            "POST", path, headers={**JSON, **_session_headers(token, installation), **(extra or {})},
            body=payload,
        )

    def claim(self, token: str | None, operation_id: str, generation: int = 0, claim_id: str | None = None, **kw: Any) -> Response:
        body = {"attemptGeneration": generation, "claimId": claim_id or str(uuid.uuid4())}
        return self.post(token, f"{REQUESTS}/{operation_id}/claim", body, **kw)

    def progress(self, token: str | None, operation_id: str, generation: int, stage: str, **kw: Any) -> Response:
        return self.post(
            token, f"{REQUESTS}/{operation_id}/progress",
            {"attemptGeneration": generation, "stage": stage}, **kw,
        )

    def complete(self, token: str | None, operation_id: str, generation: int, outcome: str, **fields: Any) -> Response:
        kw = {name: fields.pop(name) for name in ("installation", "extra", "raw") if name in fields}
        return self.post(
            token, f"{REQUESTS}/{operation_id}/complete",
            {"attemptGeneration": generation, "outcome": outcome, **fields}, **kw,
        )

    # -- requests ---------------------------------------------------------------

    def publish_return(self, operation_id: str, *, session: str = SESSION) -> None:
        publish_return_request(
            self.data_dir, self.store, session_id=session, design_id="wgd_1",
            document_id=DOCUMENT, instance_id="instance-1",
            expected_return_state_hash=STATE_HASH, request_id=operation_id,
        )

    def publish_update(self, operation_id: str, *, instance: str = "instance-1") -> None:
        bundle = self.workspace / "wglink" / "bundle-1"
        bundle.mkdir(parents=True, exist_ok=True)
        publish_fusion_handoff(
            self.data_dir, self.workspace, self.store,
            {"bundlePath": str(bundle), "exportId": EXPORT, "bundleId": "wgb_1", "identity": {"designId": "wgd_1"}},
            expected_document_id=DOCUMENT, expected_instance_id=instance,
            expected_return_state_hash=STATE_HASH, request_id=operation_id,
        )

    def directory(self, operation_id: str) -> Path:
        row = self.row(operation_id)
        channel = fusion_delivery.RETURN_REQUESTS if row["kind"] == "request_return" else fusion_delivery.HANDOFFS
        return self.ipc / channel.directory

    def visible(self, operation_id: str) -> Path:
        return self.directory(operation_id) / f"{operation_id}.json"

    def hidden(self, operation_id: str) -> list[Path]:
        return sorted(self.directory(operation_id).glob(f".{operation_id}.json.*.tmp"))

    def file_claim(self, operation_id: str) -> Path:
        """The pinned add-in's claim: rename the request to a hidden claim name."""

        claim = self.directory(operation_id) / f".wglink-claim-{operation_id}-{uuid.uuid4().hex[:12]}.json"
        os.rename(self.visible(operation_id), claim)
        return claim

    def row(self, operation_id: str) -> dict[str, Any]:
        row = self.store.get_operation(operation_id)
        assert row is not None
        return row


async def asgi(
    application, method: str, path: str, *, query: str = "", headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> Response:
    """One ASGI call with a query string (the shared test client sends none)."""

    sent: list[dict[str, Any]] = []
    delivered = False
    finished = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        # A client that stays connected until it has its answer.
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    raw_headers = [(b"host", b"127.0.0.1:3100")] + [
        (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in (headers or {}).items()
    ]
    try:
        await application(
            {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                "raw_path": path.encode("ascii"), "query_string": query.encode("ascii"),
                "root_path": "", "headers": raw_headers, "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 3100),
            },
            receive,
            send,
        )
    finally:
        finished.set()
    start = next(message for message in sent if message["type"] == "http.response.start")
    return Response(
        status_code=start["status"],
        body=b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"),
    )


@contextmanager
def wg(tmp_path: Path) -> Iterator[Wg]:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    application = create_app(data_dir=data_dir, advertised_port=3100)
    application.state.cad_workspace.select(workspace)
    _run_handlers(application, "startup", STARTUP)
    try:
        yield Wg(application, data_dir, workspace)
    finally:
        _run_handlers(application, "shutdown", SHUTDOWN)
        application.state.cadlink_store.close()


def _in_thread(target) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = target()
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def _joined(thread: threading.Thread, box: dict[str, Any], wait: float = WAIT) -> Any:
    thread.join(wait)
    assert not thread.is_alive(), "a concurrent call never finished"
    if "error" in box:
        raise box["error"]
    return box["value"]


def _parked(app: Wg, count: int = 1) -> None:
    """Wait until ``count`` long polls wait on this data directory."""

    from server.cadlink.live import wake

    deadline = time.monotonic() + WAIT
    while wake.waiting(app.data_dir) < count:
        assert time.monotonic() < deadline, "the long poll never started waiting"
        time.sleep(0.01)


def _crashes(call) -> None:
    """The call stopped with the simulated crash (possibly inside an exception group)."""

    try:
        call()
    except BaseException as exc:  # noqa: BLE001 - inspected below
        pending = [exc]
        while pending:
            current = pending.pop()
            if isinstance(current, _Crash):
                return
            pending.extend(getattr(current, "exceptions", ()))
            if current.__cause__ is not None:
                pending.append(current.__cause__)
        raise
    raise AssertionError("the simulated crash did not happen")


def _claim_json(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["claim_json"])


def _heartbeat(*, links=None, applying=None, recent=None, last=None) -> dict[str, Any]:
    return {
        "deliveryVersion": 3,
        "document": {"id": DOCUMENT, "links": links or [], "applyingOperation": applying},
        "diagnostics": {"recentOutcomes": recent or [], "lastRequest": last},
    }


def _restart(app: Wg):
    """Stop the store the way a killed process does, and start WG again."""

    app.store.close()
    return wg(app.data_dir.parent)


def _snapshot_row(store: CadLinkStore, operation_id: str) -> None:
    target, inputs = normalize_request(
        RECEIVE_SNAPSHOT, {}, {"bundle_path": "wgreturn/x.wgreturn", "manifest_sha256": "sha256:" + "0" * 64}
    )
    store.accept_operation(operation_id, RECEIVE_SNAPSHOT, request_digest(RECEIVE_SNAPSHOT, target, inputs), target, inputs)


# -- long poll -----------------------------------------------------------------


def test_a_published_update_is_offered_with_its_generation_and_exact_file_json(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        response = app.poll(token)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "requests": [
                {
                    "operationId": "update-1",
                    "kind": "update_link",
                    "attemptGeneration": 0,
                    "request": json.loads(app.visible("update-1").read_text(encoding="utf-8")),
                }
            ]
        }
        # An offer changes nothing: the file stays, the row stays received.
        assert app.visible("update-1").is_file() and app.row("update-1")["state"] == "received"


def test_offers_are_ordered_by_delivery_sequence_then_id(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("u-b", instance="instance-b")
        app.publish_update("u-a", instance="instance-a")
        app.publish_return("r-z")
        offered = [(item["request"]["deliverySequence"], item["operationId"]) for item in app.poll(token).json()["requests"]]
        assert offered == [(1, "r-z"), (1, "u-b"), (2, "u-a")]


def test_a_return_request_is_offered_only_to_its_adapter_session(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        app.publish_return("return-1", session=OTHER_SESSION)
        mine = app.token()
        assert app.poll(mine).json() == {"requests": []}
        other = app.token(installation=OTHER_INSTALLATION, adapter_session=OTHER_SESSION)
        offered = app.poll(other, installation=OTHER_INSTALLATION).json()["requests"]
        assert [item["operationId"] for item in offered] == ["return-1"]


def test_non_fusion_kinds_and_non_received_rows_are_never_offered(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        _snapshot_row(app.store, "snapshot-1")
        app.publish_update("cancelled-1", instance="i-1")
        app.store.request_cancel("cancelled-1")
        assert app.visible("cancelled-1").is_file()  # a leftover file is not an offer
        app.publish_update("processing-1", instance="i-2")
        app.file_claim("processing-1")
        assert app.store.claim("processing-1", 0) == 1
        app.publish_update("file-claimed-1", instance="i-3")
        app.file_claim("file-claimed-1")  # still received: the add-in holds it
        app.publish_update("offered-1", instance="i-4")
        assert [item["operationId"] for item in app.poll(token).json()["requests"]] == ["offered-1"]


def test_a_long_poll_wakes_at_once_when_a_request_is_published(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        started = time.monotonic()
        thread, box = _in_thread(lambda: app.poll(token, 20))
        _parked(app)
        app.publish_update("update-1")
        response = _joined(thread, box)
        assert response.status_code == 200
        assert [item["operationId"] for item in response.json()["requests"]] == ["update-1"]
        # Well inside the rescan interval: the publish woke the poll.
        assert time.monotonic() - started < 1.5


def test_a_long_poll_in_a_new_event_loop_still_wakes(tmp_path: Path) -> None:
    """Each request here runs on its own loop; nothing is bound to the first one."""

    with wg(tmp_path) as app:
        token = app.token()
        assert app.poll(token, 0).status_code == 200
        thread, box = _in_thread(lambda: app.poll(token, 20))
        _parked(app)
        started = time.monotonic()
        app.publish_return("return-1")
        assert [item["operationId"] for item in _joined(thread, box).json()["requests"]] == ["return-1"]
        assert time.monotonic() - started < 1.5


def test_a_missed_wake_is_recovered_by_the_rescan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from server.cadlink.live import requests as live_requests
    from server.cadlink.live import wake

    monkeypatch.setattr(wake, "notify", lambda data_dir: None)
    with wg(tmp_path) as app:
        token = app.token()
        thread, box = _in_thread(lambda: app.poll(token, 20))
        _parked(app)
        started = time.monotonic()
        app.publish_update("update-1")
        response = _joined(thread, box)
        assert [item["operationId"] for item in response.json()["requests"]] == ["update-1"]
        assert time.monotonic() - started < live_requests.RESCAN_SECONDS + 1.5


def test_an_empty_long_poll_ends_at_its_bound_without_holding_a_transaction(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        started = time.monotonic()
        thread, box = _in_thread(lambda: app.poll(token, 3))
        _parked(app)
        # While it waits: no connection of this store is inside a transaction ...
        assert all(not connection.in_transaction for connection in list(app.store._connections))

        # ... another writer of this store commits at once ...
        def write() -> float:
            begun = time.monotonic()
            _snapshot_row(app.store, "snapshot-during-poll")
            return time.monotonic() - begun

        writer, written = _in_thread(write)
        assert _joined(writer, written, 2.0) < 1.0
        assert app.row("snapshot-during-poll")["state"] == "received"
        # ... so does a writer on its own connection, which needs the database's write lock ...
        outside = sqlite3.connect(str(app.store.db_path), timeout=1.0)
        try:
            outside.execute("BEGIN IMMEDIATE")
            outside.execute("UPDATE cad_operations SET updated_at = updated_at WHERE operation_id = 'snapshot-during-poll'")
            outside.commit()
        finally:
            outside.close()
        # ... and the publisher's lock is free.
        assert fusion_delivery._LOCK.acquire(timeout=1.0)
        fusion_delivery._LOCK.release()
        response = _joined(thread, box)
        elapsed = time.monotonic() - started
        assert response.status_code == 200 and response.json() == {"requests": []}
        assert 3.0 <= elapsed < 3.0 + 2.0


def test_a_long_poll_ends_401_when_its_session_is_superseded_while_waiting(tmp_path: Path) -> None:
    from server.cadlink.live import requests as live_requests

    with wg(tmp_path) as app:
        token = app.token()
        thread, box = _in_thread(lambda: app.poll(token, 20))
        _parked(app)
        started = time.monotonic()
        app.token()  # the same installation registers again
        response = _joined(thread, box)
        assert response.status_code == 401 and _code(response) == "session_superseded"
        assert time.monotonic() - started < live_requests.RESCAN_SECONDS + 1.5


def test_a_long_poll_ends_401_when_its_session_expires_while_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr(live_registry, "_now", lambda: now[0])
    with wg(tmp_path) as app:
        token = app.token()
        thread, box = _in_thread(lambda: app.poll(token, 20))
        _parked(app)
        now[0] += live_registry.IDLE_TIMEOUT_SECONDS + 1
        response = _joined(thread, box)
        assert response.status_code == 401 and _code(response) == "token_expired"


def test_a_poll_never_returns_an_offer_to_a_session_that_expires_during_the_file_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink.live import requests as live_requests

    now = [1_000.0]
    monkeypatch.setattr(live_registry, "_now", lambda: now[0])
    original = live_requests._offers

    def slow_scan(*args):
        offers = original(*args)
        now[0] += live_registry.IDLE_TIMEOUT_SECONDS + 1
        return offers

    monkeypatch.setattr(live_requests, "_offers", slow_scan)
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-during-expiry")
        response = app.poll(token)
        assert response.status_code == 401 and _code(response) == "token_expired"


def test_a_long_poll_ends_503_when_wg_stops_while_waiting(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        thread, box = _in_thread(lambda: app.poll(token, 20))
        _parked(app)
        started = time.monotonic()
        _run_handlers(app.app, "shutdown", SHUTDOWN)
        response = _joined(thread, box)
        assert response.status_code == 503 and _code(response) == "store_busy"
        assert time.monotonic() - started < 1.5


@pytest.mark.parametrize("wait", ["26", "-1", "abc", "1.5", "true"])
def test_wait_seconds_outside_0_to_25_is_400_without_echo(tmp_path: Path, wait: str) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        response = app.poll(token, wait)
        assert response.status_code == 400 and _code(response) == "invalid_request"
        assert wait not in json.dumps(response.json()["error"].get("details"))


def test_a_long_poll_without_token_with_another_installation_or_with_an_origin_is_refused_at_once(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        started = time.monotonic()
        cases = [
            (app.poll(None, 20), 401, "session_unknown"),
            (app.poll("x" + token, 20), 401, "session_unknown"),
            (app.poll(token, 20, installation=OTHER_INSTALLATION), 401, "installation_mismatch"),
            (app.poll(token, 20, installation=None), 401, "installation_mismatch"),
            (app.poll(token, 20, extra={"Origin": "http://127.0.0.1:3100"}), 403, "origin_not_allowed"),
            (app.poll(None, "bad"), 401, "session_unknown"),
        ]
        for response, status, code in cases:
            assert response.status_code == status and _code(response) == code
        assert time.monotonic() - started < 2.0


# -- claim ---------------------------------------------------------------------


def test_a_claim_hides_the_file_then_claims_the_store_then_deletes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink.live import requests as live_requests

    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        document = json.loads(app.visible("update-1").read_text(encoding="utf-8"))
        seen: dict[str, Any] = {}
        real_claim = app.store.claim_fusion_request
        real_delete = live_requests._delete_claimed_file

        def claim(*args: Any, **kwargs: Any):
            seen["at_store_claim"] = (app.visible("update-1").exists(), [p.name for p in app.hidden("update-1")])
            return real_claim(*args, **kwargs)

        def delete(path: Path):
            seen["at_delete"] = app.row("update-1")["state"]
            return real_delete(path)

        monkeypatch.setattr(app.store, "claim_fusion_request", claim)
        monkeypatch.setattr(live_requests, "_delete_claimed_file", delete)
        claim_id = str(uuid.uuid4())
        response = app.claim(token, "update-1", 0, claim_id)
        assert response.status_code == 200, response.text
        assert response.json() == {"attemptGeneration": 1, "request": document}
        # The file was hidden before the store was touched, under the name the
        # heartbeat reader treats as a delivery in flight ...
        assert seen["at_store_claim"] == (False, [f".update-1.json.live-{claim_id}.tmp"])
        # ... and deleted only once the claim was committed.
        assert seen["at_delete"] == "processing"
        row = app.row("update-1")
        assert (row["state"], row["stage"], row["attempt_generation"]) == ("processing", "adapter-received", 1)
        claim_record = _claim_json(row)
        session = app.app.state.live_registry._sessions
        assert claim_record["installationId"] == INSTALLATION
        assert claim_record["liveSessionId"] in session
        assert claim_record["claimId"] == claim_id
        assert claim_record["attemptGeneration"] == 1
        assert claim_record["request"] == document
        assert not app.visible("update-1").exists() and app.hidden("update-1") == []
        assert app.poll(token).json() == {"requests": []}


def test_the_same_claim_id_replays_the_stored_request_after_the_file_is_gone(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_return("return-1")
        first = app.claim(token, "return-1", 0, "claim-1")
        assert first.status_code == 200
        assert not app.visible("return-1").exists()
        again = app.claim(token, "return-1", 0, "claim-1")
        assert again.status_code == 200 and again.json() == first.json()
        assert app.row("return-1")["attempt_generation"] == 1
        # A replay after the operation finished still answers: the client can close its journal.
        assert app.complete(token, "return-1", 1, "applied").status_code == 200
        assert app.claim(token, "return-1", 0, "claim-1").json() == first.json()


def test_a_replay_survives_a_wg_restart(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        first = app.claim(token, "update-1", 0, "claim-1")
        assert first.status_code == 200
    with wg(tmp_path) as again:
        token = again.token()
        replay = again.claim(token, "update-1", 0, "claim-1")
        assert replay.status_code == 200 and replay.json() == first.json()


def test_another_claim_id_installation_or_generation_is_409_already_claimed(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        other = app.token(installation=OTHER_INSTALLATION)
        app.publish_update("update-1")
        assert app.claim(token, "update-1", 0, "claim-1").status_code == 200
        for response in (
            app.claim(token, "update-1", 0, "claim-2"),
            app.claim(token, "update-1", 1, "claim-2"),
            app.claim(other, "update-1", 0, "claim-1", installation=OTHER_INSTALLATION),
            app.claim(token, "update-1", 1, "claim-1"),
        ):
            assert response.status_code == 409 and _code(response) == "already_claimed"
        assert app.row("update-1")["attempt_generation"] == 1


def test_a_claim_of_a_file_the_addin_claimed_first_is_409_claimed_elsewhere(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        claimed = app.file_claim("update-1")
        response = app.claim(token, "update-1", 0)
        assert response.status_code == 409 and _code(response) == "claimed_elsewhere"
        row = app.row("update-1")
        assert (row["state"], row["attempt_generation"], row["claim_json"]) == ("received", 0, None)
        assert claimed.is_file() and app.hidden("update-1") == []


def test_a_stale_generation_restores_the_visible_request_and_is_409_stale_attempt(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        before = app.visible("update-1").read_bytes()
        response = app.claim(token, "update-1", 3)
        assert response.status_code == 409 and _code(response) == "stale_attempt"
        assert app.visible("update-1").read_bytes() == before and app.hidden("update-1") == []
        row = app.row("update-1")
        assert (row["state"], row["attempt_generation"], row["claim_json"]) == ("received", 0, None)
        assert [item["operationId"] for item in app.poll(token).json()["requests"]] == ["update-1"]


def test_a_failed_store_claim_on_a_cancelled_operation_deletes_its_file_instead_of_restoring_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        real = app.store.claim_fusion_request

        def cancelled_meanwhile(*args: Any, **kwargs: Any):
            app.store.request_cancel("update-1")
            return real(*args, **kwargs)

        monkeypatch.setattr(app.store, "claim_fusion_request", cancelled_meanwhile)
        response = app.claim(token, "update-1", 0)
        assert response.status_code == 409 and _code(response) == "stale_attempt"
        assert not app.visible("update-1").exists() and app.hidden("update-1") == []
        assert app.row("update-1")["state"] == "cancelled"


def test_a_released_file_claim_of_a_processing_operation_is_not_claimed_live_and_keeps_its_file(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        claim = app.file_claim("update-1")
        settle_from_heartbeat(app.store, _heartbeat(), app.ipc)  # the vanished file started an attempt
        os.rename(claim, app.visible("update-1"))  # the add-in's release_claim
        before = app.visible("update-1").read_bytes()
        response = app.claim(token, "update-1", 1)
        assert response.status_code == 409 and _code(response) == "stale_attempt"
        row = app.row("update-1")
        assert (row["state"], row["attempt_generation"], row["claim_json"]) == ("processing", 1, None)
        assert app.visible("update-1").read_bytes() == before and app.hidden("update-1") == []


def test_a_busy_store_during_the_claim_restores_the_file_and_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")

        def busy(*args: Any, **kwargs: Any):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(app.store, "claim_fusion_request", busy)
        response = app.claim(token, "update-1", 0)
        assert response.status_code == 503 and _code(response) == "store_busy"
        assert response.json()["error"]["retryable"] is True
        assert app.visible("update-1").is_file() and app.hidden("update-1") == []
        assert app.row("update-1")["state"] == "received"


def test_a_claim_of_a_return_for_another_adapter_session_is_409_session_mismatch_and_renames_nothing(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        app.publish_return("return-1", session=OTHER_SESSION)
        token = app.token()
        response = app.claim(token, "return-1", 0)
        assert response.status_code == 409 and _code(response) == "session_mismatch"
        assert app.visible("return-1").is_file() and app.hidden("return-1") == []
        assert app.row("return-1")["state"] == "received"


def test_a_claim_of_an_unknown_or_non_fusion_operation_is_404(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        _snapshot_row(app.store, "snapshot-1")
        for operation_id in ("no-such-op", "snapshot-1"):
            response = app.claim(token, operation_id, 0)
            assert response.status_code == 404 and _code(response) == "operation_unknown"
        for response in (
            app.progress(token, "no-such-op", 1, "queuedForFusion"),
            app.complete(token, "snapshot-1", 1, "applied"),
        ):
            assert response.status_code == 404 and _code(response) == "operation_unknown"


def test_invalid_claim_bodies_are_400_without_echo(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        path = f"{REQUESTS}/update-1/claim"
        for body in (
            {"attemptGeneration": "0", "claimId": "c1"},
            {"attemptGeneration": -1, "claimId": "c1"},
            {"attemptGeneration": 0, "claimId": "bad claim id!"},
            {"attemptGeneration": 0},
            {"attemptGeneration": 0, "claimId": "c1", "sessionToken": "secret-value"},
        ):
            response = app.post(token, path, body)
            assert response.status_code == 400 and _code(response) == "invalid_request"
            assert "secret-value" not in response.text and "bad claim id!" not in response.text
        assert app.visible("update-1").is_file()


def test_heartbeat_settlement_never_claims_a_request_while_a_live_claim_holds_it_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        entered, release = threading.Event(), threading.Event()
        real = app.store.claim_fusion_request

        def held(*args: Any, **kwargs: Any):
            entered.set()
            assert release.wait(WAIT)
            return real(*args, **kwargs)

        monkeypatch.setattr(app.store, "claim_fusion_request", held)
        thread, box = _in_thread(lambda: app.claim(token, "update-1", 0))
        assert entered.wait(WAIT)
        assert not app.visible("update-1").exists()
        assert settle_from_heartbeat(app.store, _heartbeat(), app.ipc) == 0
        assert app.row("update-1")["state"] == "received"
        release.set()
        response = _joined(thread, box)
        assert response.status_code == 200 and response.json()["attemptGeneration"] == 1


# -- crash windows and restarts (section 7.2) ----------------------------------


def test_a_crash_before_the_store_claim_restores_the_visible_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        original = app.visible("update-1").read_bytes()

        def crash(*args: Any, **kwargs: Any):
            raise _Crash()

        monkeypatch.setattr(app.store, "claim_fusion_request", crash)
        _crashes(lambda: app.claim(token, "update-1", 0, "claim-1"))
        assert not app.visible("update-1").exists() and len(app.hidden("update-1")) == 1
        assert app.row("update-1")["state"] == "received"
        with _restart(app) as again:
            assert again.visible("update-1").read_bytes() == original
            assert again.hidden("update-1") == []
            token = again.token()
            offered = again.poll(token).json()["requests"]
            assert [(item["operationId"], item["attemptGeneration"]) for item in offered] == [("update-1", 0)]
            # The file transport works on it as before.
            claim = again.file_claim("update-1")
            assert claim.is_file()


def test_a_crash_before_the_store_claim_of_an_operation_cancelled_since_is_not_resurrected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")

        def crash(*args: Any, **kwargs: Any):
            raise _Crash()

        monkeypatch.setattr(app.store, "claim_fusion_request", crash)
        _crashes(lambda: app.claim(token, "update-1", 0, "claim-1"))
        assert app.store.request_cancel("update-1")["state"] == "cancelled"
        with _restart(app) as again:
            assert not again.visible("update-1").exists() and again.hidden("update-1") == []
            assert again.row("update-1")["state"] == "cancelled"
            assert again.poll(again.token()).json() == {"requests": []}


def test_a_crash_between_claim_and_delete_leaves_no_visible_request_after_startup_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink.live import requests as live_requests

    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        document = json.loads(app.visible("update-1").read_text(encoding="utf-8"))

        def crash(path: Path):
            raise _Crash()

        monkeypatch.setattr(live_requests, "_delete_claimed_file", crash)
        _crashes(lambda: app.claim(token, "update-1", 0, "claim-1"))
        assert len(app.hidden("update-1")) == 1 and not app.visible("update-1").exists()
        monkeypatch.undo()
        with _restart(app) as again:
            assert not again.visible("update-1").exists() and again.hidden("update-1") == []
            row = again.row("update-1")
            assert (row["state"], row["attempt_generation"]) == ("processing", 1)
            token = again.token()
            assert again.poll(token).json() == {"requests": []}
            replay = again.claim(token, "update-1", 0, "claim-1")
            assert replay.status_code == 200
            assert replay.json() == {"attemptGeneration": 1, "request": document}


# -- mixed file and live claims ------------------------------------------------


def test_a_live_claim_first_leaves_nothing_for_a_file_claim(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        assert app.claim(token, "update-1", 0).status_code == 200
        directory = app.ipc / fusion_delivery.HANDOFFS.directory
        assert fusion_delivery._request_files(directory) == []
        with pytest.raises(FileNotFoundError):
            app.file_claim("update-1")


def test_concurrent_file_and_live_claims_of_one_request_claim_it_exactly_once(tmp_path: Path) -> None:
    rounds = 20
    winners = {"file": 0, "live": 0}
    with wg(tmp_path) as app:
        token = app.token()
        for index in range(rounds):
            operation_id = f"race-{index}"
            app.publish_update(operation_id, instance=f"instance-{index}")
            barrier = threading.Barrier(2)

            def by_file() -> bool:
                barrier.wait(WAIT)
                try:
                    app.file_claim(operation_id)
                except FileNotFoundError:
                    return False
                return True

            def by_live() -> Response:
                barrier.wait(WAIT)
                return app.claim(token, operation_id, 0)

            file_thread, file_box = _in_thread(by_file)
            live_thread, live_box = _in_thread(by_live)
            file_won = _joined(file_thread, file_box)
            live = _joined(live_thread, live_box)
            live_won = live.status_code == 200
            assert file_won != live_won, (index, live.text)
            row = app.row(operation_id)
            if live_won:
                winners["live"] += 1
                assert (row["state"], row["attempt_generation"]) == ("processing", 1)
                assert not list(app.directory(operation_id).glob(f".wglink-claim-{operation_id}-*.json"))
            else:
                winners["file"] += 1
                assert live.status_code == 409 and _code(live) == "claimed_elsewhere"
                assert (row["state"], row["attempt_generation"], row["claim_json"]) == ("received", 0, None)
            assert not app.visible(operation_id).exists() and app.hidden(operation_id) == []
    assert sum(winners.values()) == rounds


def test_a_file_claimed_operation_refuses_live_progress_and_completion_claimed_elsewhere(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        app.file_claim("update-1")
        settle_from_heartbeat(app.store, _heartbeat(), app.ipc)  # the vanished file starts an attempt
        assert app.row("update-1")["attempt_generation"] == 1
        for response in (
            app.progress(token, "update-1", 1, "queuedForFusion"),
            app.complete(token, "update-1", 1, "refused"),
        ):
            assert response.status_code == 409 and _code(response) == "claimed_elsewhere"
        assert app.row("update-1")["state"] == "processing"


# -- progress ------------------------------------------------------------------


def _claimed(app: Wg, token: str, operation_id: str = "update-1", *, kind: str = "update") -> int:
    if kind == "update":
        app.publish_update(operation_id)
    else:
        app.publish_return(operation_id)
    response = app.claim(token, operation_id, 0)
    assert response.status_code == 200, response.text
    return response.json()["attemptGeneration"]


def test_progress_is_recorded_durably_one_stage_at_a_time(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        assert app.row("update-1")["stage"] == "adapter-received"
        queued = app.progress(token, "update-1", generation, "queuedForFusion")
        assert queued.status_code == 200, queued.text
        assert queued.json()["operation"]["stage"] == "queued-for-fusion"
        assert "alreadyRecorded" not in queued.json()
        repeat = app.progress(token, "update-1", generation, "queuedForFusion")
        assert repeat.status_code == 200 and repeat.json()["alreadyRecorded"] is True
    with wg(tmp_path) as again:
        assert again.row("update-1")["stage"] == "queued-for-fusion"
        token = again.token()
        executing = again.progress(token, "update-1", generation, "executing")
        assert executing.status_code == 200 and executing.json()["operation"]["stage"] == "executing"
        assert again.row("update-1")["state"] == "processing"
        done = again.complete(token, "update-1", generation, "applied", evidence={"operationId": "update-1", "exportId": EXPORT})
        assert done.status_code == 200
        row = again.row("update-1")
        assert (row["state"], row["stage"]) == ("accepted", "executing")


def test_progress_that_skips_or_goes_back_is_409_stage_out_of_order(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        skipped = app.progress(token, "update-1", generation, "executing")
        assert skipped.status_code == 409 and _code(skipped) == "stage_out_of_order"
        assert app.row("update-1")["stage"] == "adapter-received"
        assert app.progress(token, "update-1", generation, "queuedForFusion").status_code == 200
        assert app.progress(token, "update-1", generation, "executing").status_code == 200
        back = app.progress(token, "update-1", generation, "queuedForFusion")
        assert back.status_code == 409 and _code(back) == "stage_out_of_order"
        assert app.row("update-1")["stage"] == "executing"
        unknown = app.progress(token, "update-1", generation, "completed")
        assert unknown.status_code == 400 and _code(unknown) == "invalid_request"


def test_progress_or_completion_for_an_obsolete_attempt_is_409_stale_attempt(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token, "return-1", kind="return")
        assert app.store.claim("return-1", generation) == generation + 1  # WG took the attempt over
        for response in (
            app.progress(token, "return-1", generation, "queuedForFusion"),
            app.complete(token, "return-1", generation, "applied"),
            app.progress(token, "return-1", generation + 5, "queuedForFusion"),
        ):
            assert response.status_code == 409 and _code(response) == "stale_attempt"
        row = app.row("return-1")
        assert (row["state"], row["stage"], row["attempt_generation"]) == ("processing", "adapter-received", 2)


def test_an_obsolete_attempt_cannot_revive_a_cancelled_operation(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token, "return-1", kind="return")
        taken = app.store.claim("return-1", generation)
        app.store.record_outcome("return-1", taken, "cancelled", reason="superseded")
        for response in (
            app.progress(token, "return-1", generation, "queuedForFusion"),
            app.complete(token, "return-1", generation, "applied"),
            app.complete(token, "return-1", generation, "superseded"),
        ):
            assert response.status_code == 409 and _code(response) == "stale_attempt"
        row = app.row("return-1")
        assert (row["state"], row["reason"]) == ("cancelled", "superseded")


def test_a_dismissed_return_records_cancelled_whatever_its_attempt_reports(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token, "return-1", kind="return")
        assert app.store.request_cancel("return-1")["state"] == "cancel_requested"
        done = app.complete(token, "return-1", generation, "applied")
        assert done.status_code == 200 and done.json()["operation"]["state"] == "cancelled"
        again = app.complete(token, "return-1", generation, "applied")
        assert again.status_code == 200 and again.json()["alreadyRecorded"] is True
        assert app.row("return-1")["state"] == "cancelled"


def test_completion_after_a_token_refresh_and_a_new_registration_is_accepted(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        refreshed = app.client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        token = refreshed.json()["sessionToken"]
        assert app.progress(token, "update-1", generation, "queuedForFusion").status_code == 200
        token = app.token()  # a new session of the same installation
        response = app.complete(token, "update-1", generation, "refused")
        assert response.status_code == 200 and response.json()["operation"]["state"] == "rejected"


def test_progress_or_completion_from_another_installation_is_409_claimed_elsewhere(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        other = app.token(installation=OTHER_INSTALLATION)
        generation = _claimed(app, token)
        for response in (
            app.progress(other, "update-1", generation, "queuedForFusion", installation=OTHER_INSTALLATION),
            app.complete(other, "update-1", generation, "refused", installation=OTHER_INSTALLATION),
        ):
            assert response.status_code == 409 and _code(response) == "claimed_elsewhere"
        row = app.row("update-1")
        assert (row["state"], row["stage"]) == ("processing", "adapter-received")


def test_stale_or_wrong_sessions_origins_and_malformed_unauthenticated_bodies_change_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1_000.0]
    monkeypatch.setattr(live_registry, "_now", lambda: now[0])
    with wg(tmp_path) as app:
        token = app.token()
        app.publish_update("update-1")
        other = "update-2"
        app.publish_update(other, instance="instance-2")
        generation = app.claim(token, other, 0).json()["attemptGeneration"]
        origin = {"Origin": "http://127.0.0.1:3100"}
        cases = [
            (app.claim(None, "update-1"), 401, "session_unknown"),
            (app.claim("x" + token, "update-1"), 401, "session_unknown"),
            (app.claim(token, "update-1", installation=OTHER_INSTALLATION), 401, "installation_mismatch"),
            (app.claim(token, "update-1", extra=origin), 403, "origin_not_allowed"),
            (app.post(None, f"{REQUESTS}/update-1/claim", None, raw=b"{not json"), 401, "session_unknown"),
            (app.post(None, f"{REQUESTS}/{other}/progress", None, raw=b"[]"), 401, "session_unknown"),
            (app.post(None, f"{REQUESTS}/{other}/complete", None, raw=b'{"x": 1}'), 401, "session_unknown"),
            (app.progress(token, other, generation, "queuedForFusion", extra=origin), 403, "origin_not_allowed"),
            (app.complete("x" + token, other, generation, "refused"), 401, "session_unknown"),
        ]
        for response, status, code in cases:
            assert response.status_code == status and _code(response) == code, response.text
        now[0] += live_registry.SESSION_LIFETIME_SECONDS + 1
        expired = app.claim(token, "update-1")
        assert expired.status_code == 401 and _code(expired) == "token_expired"
        for response in (
            app.progress(token, other, generation, "queuedForFusion"),
            app.complete(token, other, generation, "refused"),
        ):
            assert response.status_code == 401  # the expired session is gone now
        assert app.visible("update-1").is_file() and app.row("update-1")["state"] == "received"
        row = app.row(other)
        assert (row["state"], row["stage"]) == ("processing", "adapter-received")


def test_request_routes_are_not_restart_latched_and_still_authenticate_first(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        app.app.state.update_restart.refusal = lambda: "An update restart is pending."
        unauthenticated = app.claim(None, "update-1")
        assert unauthenticated.status_code == 401 and _code(unauthenticated) == "session_unknown"
        token = app.token()
        app.publish_update("update-1")
        assert [item["operationId"] for item in app.poll(token).json()["requests"]] == ["update-1"]
        generation = app.claim(token, "update-1", 0).json()["attemptGeneration"]
        assert app.progress(token, "update-1", generation, "queuedForFusion").status_code == 200
        assert app.complete(token, "update-1", generation, "refused").status_code == 200


# -- completion ----------------------------------------------------------------


_EVIDENCE = {"operationId": "update-1", "exportId": EXPORT}


def test_an_applied_or_reconciled_mutation_needs_its_exact_evidence(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        for outcome in ("applied", "reconciled"):
            for evidence in (
                None,
                {"operationId": "update-1", "exportId": "wge_other"},
                {"operationId": "other-op", "exportId": EXPORT},
            ):
                fields = {} if evidence is None else {"evidence": evidence}
                response = app.complete(token, "update-1", generation, outcome, **fields)
                assert response.status_code == 400 and _code(response) == "invalid_request"
        row = app.row("update-1")
        assert (row["state"], row["outcome_json"]) == ("processing", None)
        done = app.complete(token, "update-1", generation, "applied", evidence=_EVIDENCE)
        assert done.status_code == 200
        outcome = json.loads(app.row("update-1")["outcome_json"])
        assert outcome["reconciled"] is True
        assert outcome["evidence"] == {"operation_id": "update-1", "export_id": EXPORT}


def test_outcomes_that_do_not_apply_to_a_return_are_400(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token, "return-1", kind="return")
        for outcome, fields in (
            ("reconciled", {}),
            ("recoveryRequired", {}),
            ("applied", {"evidence": {"operationId": "return-1", "exportId": EXPORT}}),
            ("unknown", {}),
        ):
            response = app.complete(token, "return-1", generation, outcome, **fields)
            assert response.status_code == 400 and _code(response) == "invalid_request"
        assert app.row("return-1")["state"] == "processing"


# (live outcome, fields, kind, the heartbeat that reports the same thing)
_MAPPED = [
    ("applied", {}, "return", _heartbeat(last={"channel": "returnRequest", "correlationId": "op-1", "outcome": "applied"})),
    ("refused", {}, "return", _heartbeat(last={"channel": "returnRequest", "correlationId": "op-1", "outcome": "refused"})),
    ("superseded", {}, "return", _heartbeat(recent=[{"channel": "returnRequest", "requestId": "op-1", "outcome": "superseded"}])),
    ("discarded", {}, "return", _heartbeat(recent=[{"channel": "returnRequest", "requestId": "op-1", "outcome": "discarded"}])),
    ("applied", {"evidence": {"operationId": "op-1", "exportId": EXPORT}}, "update",
     _heartbeat(links=[{"operationId": "op-1", "exportId": EXPORT}])),
    ("reconciled", {"evidence": {"operationId": "op-1", "exportId": EXPORT}}, "update",
     _heartbeat(recent=[{"channel": "handoff", "requestId": "op-1", "outcome": "reconciled"}])),
    ("refused", {}, "update", _heartbeat(last={"channel": "handoff", "correlationId": "op-1", "outcome": "refused"})),
    ("superseded", {}, "update", _heartbeat(recent=[{"channel": "handoff", "requestId": "op-1", "outcome": "superseded"}])),
    ("discarded", {}, "update", _heartbeat(recent=[{"channel": "handoff", "requestId": "op-1", "outcome": "discarded"}])),
    ("recoveryRequired", {}, "update", _heartbeat(recent=[{"channel": "handoff", "requestId": "op-1", "outcome": "recoveryRequired"}])),
]


def _settled(row: dict[str, Any]) -> tuple[Any, ...]:
    outcome = json.loads(row["outcome_json"]) if row["outcome_json"] else {}
    return row["state"], row["reason"], outcome.get("reconciled"), outcome.get("evidence")


@pytest.mark.parametrize("outcome, fields, kind, heartbeat", _MAPPED)
def test_each_completion_is_recorded_as_the_heartbeat_records_the_same_outcome(
    tmp_path: Path, outcome: str, fields: dict[str, Any], kind: str, heartbeat: dict[str, Any]
) -> None:
    with wg(tmp_path / "live") as app:
        token = app.token()
        generation = _claimed(app, token, "op-1", kind=kind)
        response = app.complete(token, "op-1", generation, outcome, **fields)
        assert response.status_code == 200, response.text
        live = _settled(app.row("op-1"))
        # The heartbeat, reporting the same outcome later, settles nothing twice.
        assert settle_from_heartbeat(app.store, heartbeat, app.ipc) == 0
        assert _settled(app.row("op-1")) == live
    with wg(tmp_path / "file") as app:
        token = app.token()
        _claimed(app, token, "op-1", kind=kind)
        assert settle_from_heartbeat(app.store, heartbeat, app.ipc) == 1
        assert _settled(app.row("op-1")) == live


def test_failed_is_rejected_adapter_failed_unless_a_mutation_was_executing(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token, "return-1", kind="return")
        assert app.progress(token, "return-1", generation, "queuedForFusion").status_code == 200
        assert app.progress(token, "return-1", generation, "executing").status_code == 200
        assert app.complete(token, "return-1", generation, "failed", message="Export failed.").status_code == 200
        row = app.row("return-1")
        assert (row["state"], row["reason"], json.loads(row["outcome_json"])["message"]) == (
            "rejected", "adapter_failed", "Export failed.",
        )
        queued = _claimed(app, token, "update-q")
        assert app.progress(token, "update-q", queued, "queuedForFusion").status_code == 200
        assert app.complete(token, "update-q", queued, "failed").status_code == 200
        assert (app.row("update-q")["state"], app.row("update-q")["reason"]) == ("rejected", "adapter_failed")
        app.publish_update("update-x", instance="instance-x")
        executing = app.claim(token, "update-x", 0).json()["attemptGeneration"]
        assert app.progress(token, "update-x", executing, "queuedForFusion").status_code == 200
        assert app.progress(token, "update-x", executing, "executing").status_code == 200
        assert app.complete(token, "update-x", executing, "failed").status_code == 200
        assert app.row("update-x")["state"] == "recovery_required"


def test_the_same_outcome_again_is_already_recorded_and_another_is_409_outcome_conflict(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        first = app.complete(token, "update-1", generation, "refused")
        assert first.status_code == 200 and "alreadyRecorded" not in first.json()
        updated_at = app.row("update-1")["updated_at"]
        again = app.complete(token, "update-1", generation, "refused")
        assert again.status_code == 200 and again.json()["alreadyRecorded"] is True
        assert again.json()["operation"] == first.json()["operation"]
        conflict = app.complete(token, "update-1", generation, "superseded")
        assert conflict.status_code == 409 and _code(conflict) == "outcome_conflict"
        late = app.progress(token, "update-1", generation, "queuedForFusion")
        assert late.status_code == 409 and _code(late) == "stale_attempt"
        row = app.row("update-1")
        assert (row["state"], row["reason"], row["updated_at"]) == ("rejected", "adapter_refused", updated_at)


def test_a_heartbeat_settled_operation_answers_a_matching_live_completion_already_recorded(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        settle_from_heartbeat(
            app.store, _heartbeat(last={"channel": "handoff", "correlationId": "update-1", "outcome": "refused"}), app.ipc
        )
        assert app.row("update-1")["state"] == "rejected"
        same = app.complete(token, "update-1", generation, "refused")
        assert same.status_code == 200 and same.json()["alreadyRecorded"] is True
        other = app.complete(token, "update-1", generation, "discarded")
        assert other.status_code == 409 and _code(other) == "outcome_conflict"


def test_a_recovery_required_mutation_is_settled_by_its_evidence_and_nothing_else(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        assert app.complete(token, "update-1", generation, "recoveryRequired").status_code == 200
        again = app.complete(token, "update-1", generation, "recoveryRequired")
        assert again.status_code == 200 and again.json()["alreadyRecorded"] is True
        refused = app.complete(token, "update-1", generation, "refused")
        assert refused.status_code == 409 and _code(refused) == "outcome_conflict"
        settled = app.complete(token, "update-1", generation, "reconciled", evidence=_EVIDENCE)
        assert settled.status_code == 200 and settled.json()["operation"]["state"] == "accepted"


def test_claims_progress_and_outcomes_publish_operation_events_without_claim_fields(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        events: list[dict[str, Any]] = []
        app.app.state.jobs_runtime = SimpleNamespace(events=SimpleNamespace(publish=events.append))
        token = app.token()
        generation = _claimed(app, token)
        app.progress(token, "update-1", generation, "queuedForFusion")
        app.complete(token, "update-1", generation, "refused")
        assert [(e["kind"], e["operation"]["state"], e["operation"]["stage"]) for e in events] == [
            ("cadOperation", "processing", "adapter-received"),
            ("cadOperation", "processing", "queued-for-fusion"),
            ("cadOperation", "rejected", "queued-for-fusion"),
        ]
        rendered = json.dumps(events)
        assert "claim" not in rendered and INSTALLATION not in rendered and token not in rendered


# -- store ---------------------------------------------------------------------


def test_claim_json_is_added_to_an_existing_database_without_changing_its_format(tmp_path: Path) -> None:
    store = CadLinkStore.for_data_dir(tmp_path)
    _snapshot_row(store, "before-1")
    path = store.db_path
    store.close()
    with sqlite3.connect(str(path)) as conn:
        conn.execute("ALTER TABLE cad_operations DROP COLUMN claim_json")
        before = conn.execute("SELECT * FROM cad_operations").fetchall()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
    reopened = CadLinkStore.for_data_dir(tmp_path)
    reopened.initialize()
    reopened.close()
    with sqlite3.connect(str(path)) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(cad_operations)")]
        assert "claim_json" in columns and "cad_frame_confirmations" in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert conn.execute("PRAGMA user_version").fetchone()[0] == STORE_FORMAT_VERSION == 11
        after = conn.execute(f"SELECT {', '.join(columns[:-1])}, claim_json FROM cad_operations").fetchall()
        assert [tuple(row[:-1]) for row in after] == [tuple(row) for row in before]
        assert all(row[-1] is None for row in after)


def test_no_token_proof_or_session_secret_is_ever_written_to_the_store(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        generation = _claimed(app, token)
        app.complete(token, "update-1", generation, "refused")
        secret = app.app.state.live_registry.secret
        claim = _claim_json(app.row("update-1"))
        assert set(claim) == {"installationId", "liveSessionId", "claimId", "attemptGeneration", "claimedAt", "request"}
        app.store.close()
        files = [path for path in app.data_dir.rglob("*") if path.is_file()]
        assert any(path.suffix == ".db" or ".db-" in path.name for path in files)
        assert all(token.encode() not in path.read_bytes() for path in files)
        # The secret lives in the endpoint file by design, and nowhere else.
        assert all(
            secret.encode() not in path.read_bytes() for path in files if path.name != "wg-endpoint.json"
        )


# -- the file transport is unchanged ------------------------------------------


def test_request_files_are_byte_identical_with_or_without_a_live_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001 - datetime's signature
            return fixed

    monkeypatch.setattr(fusion_return, "datetime", Frozen)
    monkeypatch.setattr(cad_handoff, "datetime", Frozen)
    files: dict[str, dict[str, bytes]] = {}
    for mode in ("file-only", "live"):
        # The same paths both times: a handoff names its bundle's absolute path.
        shutil.rmtree(tmp_path / "data", ignore_errors=True)
        with wg(tmp_path) as app:
            if mode == "live":
                token = app.token()
                assert app.poll(token).status_code == 200
            app.publish_update("update-1")
            app.publish_return("return-1")
            if mode == "live":
                assert len(app.poll(token).json()["requests"]) == 2
            files[mode] = {
                # as_posix: the expected names below are spelled with "/", and
                # Windows spells a relative path with "\".
                path.relative_to(app.ipc).as_posix(): path.read_bytes()
                for path in app.ipc.rglob("*.json")
                if path.parent != app.ipc
            }
    assert files["file-only"] == files["live"]
    assert set(files["live"]) == {".fusion-handoffs/update-1.json", ".fusion-return-requests/return-1.json"}
