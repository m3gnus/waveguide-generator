"""WG-bound deliveries over HTTP, under the file delivery's contract.

``docs/reference/CADLINK-LIVE-PROTOCOL.md`` section 8 and 9 are the contract.
Every test runs a real application (``create_app``), a registered live session,
a real WGLink folder and a real ``.wgreturn`` bundle retained into WG's own
content-addressed storage; nothing here stands in for ``retain_snapshot``. The
v3 solve files are written as the pinned add-in writes them and collected by
the real delivery pass. A WG restart is a second ``create_app`` against the same
data directory.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

import pytest

from server.cadlink import preparation, solve_command
from server.cadlink.ingest import retained_snapshot_path
from server.cadlink.operations import (
    PREPARE_AND_SOLVE,
    RECEIVE_SNAPSHOT,
    normalize_request,
    request_digest,
)
from server.cadlink.solve_command import collect_solve_deliveries, record_outcome
from server.cadlink.store import CadLinkStore
from server.tests.test_cad_preparation import _write_return
from server.tests.test_cad_solve_delivery import _delivery_files, _file
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
from server.app import create_app


DELIVERIES = f"{LIVE}/deliveries"
RECOVER = ("recover_cad_operations_on_startup",)
REQUESTED_AT = "2026-09-17T10:00:00Z"
WAIT = 10.0


# -- harness -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_waits_left() -> Iterator[None]:
    """The delivery holds are per process; no test leaves one to the next."""

    getattr(solve_command, "_live_waits", {}).clear()
    solve_command._retention_waits.clear()
    yield
    getattr(solve_command, "_live_waits", {}).clear()
    solve_command._retention_waits.clear()


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

    def token(self, **overrides: Any) -> str:
        response = _register(self.client, _registration(self.app, **overrides))
        assert response.status_code == 201, response.text
        return response.json()["sessionToken"]

    def deliver(
        self,
        token: str | None,
        body: object,
        *,
        installation: str | None = INSTALLATION,
        extra: dict[str, str] | None = None,
        raw: bytes | None = None,
    ):
        headers = {**JSON, **_session_headers(token, installation), **(extra or {})}
        payload = raw if raw is not None else json.dumps(body).encode()
        return self.client.request("POST", DELIVERIES, headers=headers, body=payload)

    def run_pass(self, *, running: set[str] | None = None) -> list[str]:
        """One pass of the backend delivery loop; returns what it started."""

        from server.cadlink.api import _preparation_context, _selected_workspace_root

        spawned: list[str] = []

        def spawn(operation_id: str, coroutine) -> None:
            spawned.append(operation_id)
            coroutine.close()  # started, not run: the start is what is counted

        async def one() -> list[str]:
            ctx = _preparation_context(
                self.app.state, workspace_root=_selected_workspace_root(self.app.state)
            )
            return await preparation.run_delivery_pass(
                ctx, spawn=spawn, running=frozenset(running or ())
            )

        started = asyncio.run(one())
        assert started == spawned
        return started

    def settle(self) -> list[str]:
        from server.cadlink.api import _preparation_context, _selected_workspace_root

        async def one() -> list[str]:
            ctx = _preparation_context(
                self.app.state, workspace_root=_selected_workspace_root(self.app.state)
            )
            return await asyncio.to_thread(preparation.settle_received_snapshots, ctx)

        return asyncio.run(one())

    def ids(self) -> list[str]:
        return [str(row["operation_id"]) for row in self.store.list_operations(limit=1000)]

    def row(self, operation_id: str = "op-1") -> dict[str, Any]:
        row = self.store.get_operation(operation_id)
        assert row is not None
        return row


@contextmanager
def wg(tmp_path: Path, *, select: bool = True, startup: tuple[str, ...] = STARTUP) -> Iterator[Wg]:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_dir.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    application = create_app(data_dir=data_dir, advertised_port=3100)
    if select:
        application.state.cad_workspace.select(workspace)
    _run_handlers(application, "startup", startup)
    try:
        yield Wg(application, data_dir, workspace)
    finally:
        _run_handlers(application, "shutdown", SHUTDOWN)
        application.state.cadlink_store.close()


def _item(
    bundle_path: str,
    manifest: str,
    operation_id: str = "op-1",
    *,
    kind: str = PREPARE_AND_SOLVE,
    requested_at: str = REQUESTED_AT,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operationId": operation_id,
        "kind": kind,
        "bundlePath": bundle_path,
        "manifestSha256": manifest,
        "requestedAt": requested_at,
    }
    if kind == PREPARE_AND_SOLVE:
        body["returnId"] = "wgr_1"
    body.update(extra)
    return body


def _snapshot_digest(bundle_path: str, manifest: str) -> str:
    target, inputs = normalize_request(
        RECEIVE_SNAPSHOT, {}, {"bundle_path": bundle_path, "manifest_sha256": manifest}
    )
    return request_digest(RECEIVE_SNAPSHOT, target, inputs)


def _retained(data_dir: Path, manifest: str) -> bool:
    return retained_snapshot_path(data_dir, manifest).is_dir()


def _raw_request(application, path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
    """One ASGI call that keeps the response headers too."""

    async def call() -> tuple[int, dict[str, str], bytes]:
        sent: list[dict[str, Any]] = []
        done = False

        async def receive() -> dict[str, Any]:
            nonlocal done
            if not done:
                done = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        raw_headers = [(b"host", b"127.0.0.1:3100")] + [
            (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()
        ]
        await application(
            {
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
                "raw_path": path.encode("ascii"), "query_string": b"", "root_path": "",
                "headers": raw_headers, "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 3100),
            },
            receive,
            send,
        )
        start = next(message for message in sent if message["type"] == "http.response.start")
        response_headers = {
            name.decode("latin-1"): value.decode("latin-1") for name, value in start["headers"]
        }
        return (
            start["status"],
            response_headers,
            b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"),
        )

    return asyncio.run(call())


class Clock:
    def __init__(self, start: float = 5_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(solve_command, "_now", fake)
    return fake


class Gate:
    """A retention held on an event, entered and released from the test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        real = preparation.retain_snapshot

        def held(*args: Any, **kwargs: Any):
            self.entered.set()
            assert self.release.wait(WAIT), "the test never released the retention"
            return real(*args, **kwargs)

        monkeypatch.setattr(preparation, "retain_snapshot", held)


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


def _joined(thread: threading.Thread, box: dict[str, Any]) -> Any:
    thread.join(WAIT)
    assert not thread.is_alive(), "a concurrent call never finished"
    if "error" in box:
        raise box["error"]
    return box["value"]


# -- a solve delivered live ----------------------------------------------------


def test_a_live_solve_delivery_is_accepted_retained_then_acknowledged(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()

        response = app.deliver(token, _item(bundle_path, manifest))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"] == "created"
        # Retained before the answer: the copy exists and the answer names it.
        assert _retained(app.data_dir, manifest)
        assert body["operation"]["operationId"] == "op-1"
        assert body["operation"]["kind"] == PREPARE_AND_SOLVE
        assert body["operation"]["state"] == "received"
        assert body["operation"]["snapshot"]["manifestSha256"] == manifest
        assert json.loads(app.row()["snapshot_json"])["manifest_sha256"] == manifest
        assert solve_command.live_held_operation_ids() == frozenset()
        # The delivery loop then starts it, once.
        assert app.run_pass() == ["op-1"]


def test_the_same_delivery_twice_is_one_operation_and_the_second_recovers(tmp_path: Path) -> None:
    """A lost response: the add-in retries the same item and gets the same operation."""

    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        first = app.deliver(token, _item(bundle_path, manifest))
        before = app.row()

        again = app.deliver(token, _item(bundle_path, manifest))

        assert first.status_code == 200 and again.status_code == 200, again.text
        assert (first.json()["result"], again.json()["result"]) == ("created", "recovered")
        assert app.ids() == ["op-1"]
        after = app.row()
        assert after["request_digest"] == before["request_digest"]
        assert after["attempt_generation"] == before["attempt_generation"] == 0
        assert again.json()["operation"]["operationId"] == "op-1"


def test_a_conflict_on_an_unfinished_solve_is_409_not_200(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 200
        before = app.row()

        response = app.deliver(token, _item(bundle_path, "sha256:" + "b" * 64))

        assert response.status_code == 409 and _code(response) == "operation_conflict"
        assert response.json()["error"]["retryable"] is False
        assert response.json()["error"]["stage"] == "cadlink-live"
        assert app.row() == before
        assert solve_command.live_held_operation_ids() == frozenset()


def test_a_conflict_on_a_finished_operation_is_409_and_leaves_it(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 200
        record_outcome(app.store, "op-1", state="accepted", job_id="job-1")
        before = app.row()

        other_kind = app.deliver(token, _item(bundle_path, manifest, kind=RECEIVE_SNAPSHOT))
        other_digest = app.deliver(token, _item(bundle_path, "sha256:" + "c" * 64))

        for response in (other_kind, other_digest):
            assert response.status_code == 409 and _code(response) == "operation_conflict"
        assert app.row() == before


def test_a_terminal_operation_redelivered_replays_its_outcome(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 200
        record_outcome(app.store, "op-1", state="accepted", job_id="job-1")

        response = app.deliver(token, _item(bundle_path, manifest))

        assert response.status_code == 200
        body = response.json()
        assert body["result"] == "recovered"
        assert (body["operation"]["state"], body["operation"]["jobId"]) == ("accepted", "job-1")
        assert app.run_pass() == []


def test_an_invalid_or_changed_return_is_acknowledged_and_preparation_rejects_it(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, _manifest = _write_return(app.workspace)
        token = app.token()

        changed = app.deliver(token, _item(bundle_path, "sha256:" + "0" * 64, "op-1"))
        outside = app.deliver(token, _item("elsewhere/speaker.wgreturn", "sha256:" + "0" * 64, "op-2"))

        for response in (changed, outside):
            assert response.status_code == 200, response.text
            assert response.json()["result"] == "created"
            assert response.json()["operation"]["snapshot"] is None
        assert solve_command.live_held_operation_ids() == frozenset()

        from server.cadlink.api import _preparation_context

        async def prepare(operation_id: str) -> dict[str, Any]:
            return await preparation.prepare_operation(
                _preparation_context(app.app.state), operation_id, preparation.PreparationInput()
            )

        for operation_id in ("op-1", "op-2"):
            summary = asyncio.run(prepare(operation_id))
            assert (summary["state"], summary["reason"]) == ("rejected", "snapshot_invalid")


# -- transient returns and the in-flight hold ----------------------------------


def test_a_delivery_held_by_503_is_never_started_by_a_pass(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        hidden = app.workspace / "not-yet"
        (app.workspace / bundle_path).rename(hidden)
        token = app.token()
        headers = {**JSON, **_session_headers(token)}

        status, response_headers, raw = _raw_request(
            app.app, DELIVERIES, headers, json.dumps(_item(bundle_path, manifest)).encode()
        )

        assert status == 503, raw
        error = json.loads(raw)["error"]
        assert (error["code"], error["retryable"], error["stage"]) == (
            "snapshot_not_readable", True, "cadlink-live",
        )
        assert response_headers.get("retry-after") == "1"
        assert app.row()["state"] == "received"
        assert solve_command.live_held_operation_ids() == frozenset({"op-1"})
        assert app.run_pass() == []

        hidden.rename(app.workspace / bundle_path)
        again = app.deliver(token, _item(bundle_path, manifest))
        assert again.status_code == 200 and again.json()["result"] == "recovered"
        assert solve_command.live_held_operation_ids() == frozenset()
        assert app.run_pass() == ["op-1"]


def test_a_transient_return_is_released_after_the_live_bound(tmp_path: Path, clock: Clock) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()

        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503
        clock.now += 29.0
        # A retry inside the bound keeps the first answer's deadline.
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503
        assert solve_command.live_held_operation_ids() == frozenset({"op-1"})
        clock.now += 1.0

        released = app.deliver(token, _item(bundle_path, manifest))

        assert released.status_code == 200, released.text
        assert released.json()["result"] == "recovered"
        assert solve_command.live_held_operation_ids() == frozenset()
        # Acknowledged without a copy: the operation waits for its return, as
        # a v3 file does after its passes.
        assert app.row()["snapshot_json"] is None
        assert app.run_pass() == ["op-1"]


def test_a_wait_the_client_never_retries_expires_at_the_bound(tmp_path: Path, clock: Clock) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503
        clock.now += 29.9
        assert app.run_pass() == []
        clock.now += 0.1

        assert app.run_pass() == ["op-1"]
        assert solve_command.live_held_operation_ids() == frozenset()


def test_an_exception_in_accept_operation_clears_the_in_flight_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        seen: list[frozenset[str]] = []

        def broken(self, *args: Any, **kwargs: Any):
            seen.append(solve_command.live_held_operation_ids())
            raise RuntimeError("accept failed")

        monkeypatch.setattr(CadLinkStore, "accept_operation", broken)

        with pytest.raises(RuntimeError, match="accept failed"):
            app.deliver(token, _item(bundle_path, manifest))

        # Held while it was being accepted, released on the way out.
        assert seen == [frozenset({"op-1"})]
        assert solve_command.live_held_operation_ids() == frozenset()


def test_store_busy_clears_the_in_flight_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()

        def busy(self, *args: Any, **kwargs: Any):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(CadLinkStore, "accept_operation", busy)
        status, headers, raw = _raw_request(
            app.app, DELIVERIES, {**JSON, **_session_headers(token)},
            json.dumps(_item(bundle_path, manifest)).encode(),
        )

        assert status == 503
        error = json.loads(raw)["error"]
        assert (error["code"], error["retryable"]) == ("store_busy", True)
        assert headers.get("retry-after") == "1"
        assert b"database is locked" not in raw
        assert solve_command.live_held_operation_ids() == frozenset()
        monkeypatch.undo()
        assert app.ids() == []


def test_live_waits_never_reach_the_file_pass_held_set(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503
        held: set[str] = set()

        collect_solve_deliveries(app.data_dir, app.store, retain=lambda _op: "retained", held=held)

        assert held == set()
        assert solve_command._retention_waits == {}


def test_file_pass_pruning_does_not_drop_a_live_wait(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503
        # A file delivery of another operation passes through and is acknowledged.
        other_path, other_manifest = _write_return(app.workspace, "other.wgreturn", step=b"STEP 2")
        _file(app.data_dir, "op-2", other_path, other_manifest)

        for _ in range(3):
            app.run_pass(running={"op-2"})

        assert solve_command.live_held_operation_ids() == frozenset({"op-1"})
        assert "op-1" not in app.run_pass(running={"op-2"})


def test_a_slow_retention_racing_a_delivery_pass_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass that collected before the delivery lists the operation while its
    snapshot is still being retained, and does not start it."""

    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        gate = Gate(monkeypatch)
        listing = threading.Event()
        real_list = CadLinkStore.list_operations
        visible: list[str] = []

        def list_while_retaining(self, **kwargs: Any):
            if kwargs.get("kind") == PREPARE_AND_SOLVE and kwargs.get("states") == {"received"}:
                listing.set()
                assert gate.entered.wait(WAIT)
                rows = real_list(self, **kwargs)
                visible.extend(str(row["operation_id"]) for row in rows)
                return rows
            return real_list(self, **kwargs)

        monkeypatch.setattr(CadLinkStore, "list_operations", list_while_retaining)
        pass_thread, pass_box = _in_thread(app.run_pass)
        assert listing.wait(WAIT)
        live_thread, live_box = _in_thread(lambda: app.deliver(token, _item(bundle_path, manifest)))

        started = _joined(pass_thread, pass_box)

        # The pass saw the committed operation and left it to its delivery.
        assert visible == ["op-1"]
        assert started == []
        gate.release.set()
        response = _joined(live_thread, live_box)
        assert response.status_code == 200 and response.json()["result"] == "created"
        monkeypatch.setattr(CadLinkStore, "list_operations", real_list)
        assert app.run_pass() == ["op-1"]


def test_the_accept_commit_racing_a_pass_listing_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pass lists right after the operation commits and before the route
    goes on: the hold must already be there, and be read after the listing."""

    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        listing = threading.Event()
        committed = threading.Event()
        pass_done = threading.Event()
        real_list = CadLinkStore.list_operations
        real_accept = CadLinkStore.accept_operation

        def gated_list(self, **kwargs: Any):
            if kwargs.get("kind") == PREPARE_AND_SOLVE and kwargs.get("states") == {"received"}:
                listing.set()
                assert committed.wait(WAIT)
            return real_list(self, **kwargs)

        def gated_accept(self, *args: Any, **kwargs: Any):
            result = real_accept(self, *args, **kwargs)
            committed.set()
            pass_done.wait(WAIT)
            return result

        monkeypatch.setattr(CadLinkStore, "list_operations", gated_list)
        monkeypatch.setattr(CadLinkStore, "accept_operation", gated_accept)

        def one_pass() -> list[str]:
            try:
                return app.run_pass()
            finally:
                pass_done.set()

        pass_thread, pass_box = _in_thread(one_pass)
        assert listing.wait(WAIT)
        live_thread, live_box = _in_thread(lambda: app.deliver(token, _item(bundle_path, manifest)))

        assert _joined(pass_thread, pass_box) == []
        response = _joined(live_thread, live_box)
        assert response.status_code == 200
        monkeypatch.undo()
        assert app.run_pass() == ["op-1"]


# -- mixed file and live delivery ----------------------------------------------


def test_a_file_then_live_delivery_with_different_requested_at_is_accepted_once(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        _file(app.data_dir, "op-1", bundle_path, manifest, requested_at="2026-09-17T09:00:00Z")
        assert app.run_pass() == ["op-1"]
        assert _delivery_files(app.data_dir) == []
        before = app.row()
        token = app.token()

        response = app.deliver(
            token, _item(bundle_path, manifest, requested_at="2026-09-17T11:30:00Z")
        )

        assert response.status_code == 200, response.text
        assert response.json()["result"] == "recovered"
        assert app.ids() == ["op-1"]
        assert app.row()["request_digest"] == before["request_digest"]
        assert app.run_pass(running={"op-1"}) == []


def test_a_live_then_file_delivery_is_accepted_once_and_prepared_once(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).json()["result"] == "created"
        digest = app.row()["request_digest"]
        # The add-in lost the response and fell back to the v3 file, same id.
        _file(app.data_dir, "op-1", bundle_path, manifest, requested_at="2026-09-17T12:00:00Z")

        assert app.run_pass() == ["op-1"]

        assert _delivery_files(app.data_dir) == []
        assert app.ids() == ["op-1"]
        assert app.row()["request_digest"] == digest
        assert app.run_pass(running={"op-1"}) == []


def test_concurrent_file_and_live_deliveries_create_one_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        _file(app.data_dir, "op-1", bundle_path, manifest, requested_at="2026-09-17T09:00:00Z")
        real_accept = CadLinkStore.accept_operation
        inside = 0
        most = 0
        results: list[str] = []
        count_lock = threading.Lock()

        def slow_accept(self, *args: Any, **kwargs: Any):
            nonlocal inside, most
            with count_lock:
                inside += 1
                most = max(most, inside)
            try:
                time.sleep(0.2)
                row, result = real_accept(self, *args, **kwargs)
                results.append(result)
                return row, result
            finally:
                with count_lock:
                    inside -= 1

        monkeypatch.setattr(CadLinkStore, "accept_operation", slow_accept)
        retain = lambda operation_id: preparation.retain_operation_snapshot(  # noqa: E731
            app.store, app.data_dir, app.workspace.resolve(), operation_id
        )
        # Both released at once, so neither reaches acceptance by being started first.
        barrier = threading.Barrier(2)

        def by_file():
            barrier.wait(WAIT)
            return collect_solve_deliveries(app.data_dir, app.store, retain=retain)

        def live():
            barrier.wait(WAIT)
            return app.deliver(token, _item(bundle_path, manifest))

        file_thread, file_box = _in_thread(by_file)
        live_thread, live_box = _in_thread(live)

        _joined(file_thread, file_box)
        response = _joined(live_thread, live_box)

        assert response.status_code == 200
        assert sorted(results) == ["created", "recovered"]
        # One consumer at a time in this process: never both inside acceptance.
        assert most == 1
        assert app.ids() == ["op-1"]
        assert _delivery_files(app.data_dir) == []
        monkeypatch.undo()
        assert app.run_pass() == ["op-1"]
        assert app.run_pass(running={"op-1"}) == []


def test_a_v3_solve_file_is_still_collected_with_no_live_session(tmp_path: Path) -> None:
    """The pinned file-only add-in: no endpoint use, no registration."""

    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        _file(app.data_dir, "op-1", bundle_path, manifest)

        assert app.run_pass() == ["op-1"]

        assert _delivery_files(app.data_dir) == []
        assert json.loads(app.row()["snapshot_json"])["manifest_sha256"] == manifest
        assert app.run_pass(running={"op-1"}) == []


# -- receive_snapshot ----------------------------------------------------------


def test_receive_snapshot_is_accepted_once_retained_and_never_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_ingest(*_args: Any, **_kwargs: Any):  # pragma: no cover - must not run
        raise AssertionError("a received snapshot is never ingested")

    monkeypatch.setattr(preparation, "ingest_bundle", no_ingest)
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        body = _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT)

        first = app.deliver(token, body)

        assert first.status_code == 200, first.text
        assert first.json()["result"] == "created"
        operation = first.json()["operation"]
        assert (operation["kind"], operation["state"]) == (RECEIVE_SNAPSHOT, "accepted")
        assert operation["snapshot"]["manifestSha256"] == manifest
        assert _retained(app.data_dir, manifest)
        row = app.row("snap-1")
        assert row["request_digest"] == _snapshot_digest(bundle_path, manifest)
        assert row["job_id"] is None and row["setup_revision_id"] is None

        again = app.deliver(token, {**body, "requestedAt": "2026-09-17T10:05:00Z"})

        assert again.status_code == 200 and again.json()["result"] == "recovered"
        assert again.json()["operation"]["state"] == "accepted"
        assert app.row("snap-1")["attempt_generation"] == row["attempt_generation"]
        assert app.ids() == ["snap-1"]
        assert app.run_pass() == []
        assert app.settle() == []


def test_a_receive_snapshot_whose_bundle_is_not_readable_is_never_accepted(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()

        response = app.deliver(token, _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT))

        assert response.status_code == 503 and _code(response) == "snapshot_not_readable"
        row = app.row("snap-1")
        assert (row["state"], row["snapshot_json"], row["attempt_generation"]) == ("received", None, 0)
        assert not _retained(app.data_dir, manifest)


def test_receive_snapshot_with_an_invalid_bundle_is_rejected_snapshot_invalid(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, _manifest = _write_return(app.workspace)
        token = app.token()

        response = app.deliver(
            token, _item(bundle_path, "sha256:" + "0" * 64, "snap-1", kind=RECEIVE_SNAPSHOT)
        )

        assert response.status_code == 200, response.text
        operation = response.json()["operation"]
        assert (operation["state"], operation["reason"]) == ("rejected", "snapshot_invalid")
        assert operation["snapshot"] is None
        assert solve_command.live_held_operation_ids() == frozenset()


def test_a_slow_snapshot_retention_racing_settlement_settles_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        listing = threading.Event()
        committed = threading.Event()
        settled = threading.Event()
        real_page = CadLinkStore.operation_page
        real_accept = CadLinkStore.accept_operation

        def gated_page(self, **kwargs: Any):
            if kwargs.get("kind") == RECEIVE_SNAPSHOT:
                listing.set()
                assert committed.wait(WAIT)
            return real_page(self, **kwargs)

        def gated_accept(self, *args: Any, **kwargs: Any):
            result = real_accept(self, *args, **kwargs)
            committed.set()
            settled.wait(WAIT)
            return result

        monkeypatch.setattr(CadLinkStore, "operation_page", gated_page)
        monkeypatch.setattr(CadLinkStore, "accept_operation", gated_accept)

        def settle() -> list[str]:
            try:
                return app.settle()
            finally:
                settled.set()

        settle_thread, settle_box = _in_thread(settle)
        assert listing.wait(WAIT)
        live_thread, live_box = _in_thread(
            lambda: app.deliver(token, _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT))
        )

        assert _joined(settle_thread, settle_box) == []
        response = _joined(live_thread, live_box)
        monkeypatch.undo()
        assert response.status_code == 200
        operation = response.json()["operation"]
        # The live delivery settled it, once: its own claim, generation 1.
        assert (operation["state"], operation["attemptGeneration"]) == ("accepted", 1)


def test_a_slow_snapshot_retention_holds_settlement_off_while_it_retains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        gate = Gate(monkeypatch)
        thread, box = _in_thread(
            lambda: app.deliver(token, _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT))
        )
        assert gate.entered.wait(WAIT)
        assert app.row("snap-1")["state"] == "received"

        assert app.settle() == []
        assert app.row("snap-1")["attempt_generation"] == 0

        gate.release.set()
        response = _joined(thread, box)
        assert response.status_code == 200 and response.json()["operation"]["state"] == "accepted"


def _crash_after_accept(app: Wg, body: dict[str, Any]) -> None:
    """WG stopped after the operation committed and before its outcome."""

    item = solve_command.DeliveredItem.from_payload(body)
    answer = solve_command.accept_delivery(app.store, item, retain=None)
    assert answer.result == "created"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("none", ("accepted", None)),
        ("missing", ("received", None)),
        ("changed", ("rejected", "snapshot_invalid")),
    ],
)
def test_a_received_snapshot_left_by_a_crash_is_settled_after_the_store_reopens(
    tmp_path: Path, change: str, expected: tuple[str, str | None]
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        _crash_after_accept(app, _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT))
        assert app.row("snap-1")["state"] == "received"
        workspace = app.workspace
    if change == "missing":
        (workspace / bundle_path).rename(workspace / "gone")
    elif change == "changed":
        manifest_file = workspace / bundle_path / "wgreturn.json"
        manifest_file.write_bytes(manifest_file.read_bytes() + b" ")

    with wg(tmp_path, select=False, startup=STARTUP + RECOVER) as restarted:
        row = restarted.row("snap-1")
        assert (row["state"], row["reason"]) == expected
        assert _retained(restarted.data_dir, manifest) == (change == "none")
        assert restarted.ids() == ["snap-1"]


def test_a_received_snapshot_without_retry_is_settled_by_the_next_delivery_pass(
    tmp_path: Path, clock: Clock
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        hidden = app.workspace / "not-yet"
        (app.workspace / bundle_path).rename(hidden)
        token = app.token()
        body = _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT)
        assert app.deliver(token, body).status_code == 503
        hidden.rename(app.workspace / bundle_path)
        # Still held by its answer: the pass leaves it to the retry.
        app.run_pass()
        assert app.row("snap-1")["state"] == "received"
        clock.now += 30.0

        app.run_pass()

        row = app.row("snap-1")
        assert row["state"] == "accepted"
        assert _retained(app.data_dir, manifest)
        # The add-in's retry, when it comes, recovers the settled operation.
        again = app.deliver(token, body)
        assert again.status_code == 200 and again.json()["operation"]["state"] == "accepted"


# -- sessions, restarts and refusals -------------------------------------------


def test_a_delivery_accepted_before_a_wg_restart_is_recovered_by_redelivery_after_it(
    tmp_path: Path,
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        old_token = app.token()
        assert app.deliver(old_token, _item(bundle_path, manifest)).json()["result"] == "created"
        digest = app.row()["request_digest"]

    with wg(tmp_path, select=False) as restarted:
        refused = restarted.deliver(old_token, _item(bundle_path, manifest))
        assert refused.status_code == 401 and _code(refused) == "session_unknown"
        new_token = restarted.token()
        assert new_token != old_token

        response = restarted.deliver(new_token, _item(bundle_path, manifest))

        assert response.status_code == 200 and response.json()["result"] == "recovered"
        assert restarted.ids() == ["op-1"]
        assert restarted.row()["request_digest"] == digest


def test_a_token_refresh_between_retries_does_not_change_the_operation_or_its_digest(
    tmp_path: Path,
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 200
        digest = app.row()["request_digest"]
        refreshed = app.client.request("POST", f"{LIVE}/sessions/refresh", headers=_session_headers(token))
        new_token = refreshed.json()["sessionToken"]

        for current in (new_token, token):  # the old one is in its grace window
            response = app.deliver(current, _item(bundle_path, manifest))
            assert response.status_code == 200 and response.json()["result"] == "recovered"

        assert app.ids() == ["op-1"]
        assert app.row()["request_digest"] == digest
        assert digest == request_digest(
            PREPARE_AND_SOLVE,
            *normalize_request(
                PREPARE_AND_SOLVE, {},
                {"return_id": "wgr_1", "bundle_path": bundle_path, "manifest_sha256": manifest},
            ),
        )


def test_a_stale_token_or_wrong_installation_header_is_401_and_accepts_nothing(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        body = _item(bundle_path, manifest)
        superseded = app.token()
        ended = app.token(installation=OTHER_INSTALLATION)
        current = app.token()
        assert app.client.request(
            "DELETE", f"{LIVE}/sessions/current", headers=_session_headers(ended, OTHER_INSTALLATION)
        ).status_code == 204

        cases = [
            (app.deliver(None, body), "session_unknown"),
            (app.deliver("not-a-token", body), "session_unknown"),
            (app.deliver(superseded, body), "session_superseded"),
            (app.deliver(ended, body, installation=OTHER_INSTALLATION), "session_unknown"),
            (app.deliver(current, body, installation=OTHER_INSTALLATION), "installation_mismatch"),
            (app.deliver(current, body, installation=None), "installation_mismatch"),
            (app.deliver(current, body, installation="bad header!"), "installation_mismatch"),
        ]

        for response, code in cases:
            assert response.status_code == 401 and _code(response) == code, response.text
        assert app.ids() == []
        assert solve_command.live_held_operation_ids() == frozenset()
        assert app.deliver(current, body).status_code == 200


def test_a_delivery_with_an_origin_header_is_403_and_accepts_nothing(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        for origin in ("http://127.0.0.1:3100", "null"):
            response = app.deliver(token, _item(bundle_path, manifest), extra={"Origin": origin})
            assert response.status_code == 403, response.text
        assert app.ids() == []


def test_an_unauthenticated_malformed_delivery_is_401_not_400(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        token = app.token()
        for raw in (b"{not json", b'{"unexpected": 1}', b"[]"):
            response = app.deliver("nope", None, raw=raw)
            assert response.status_code == 401 and _code(response) == "session_unknown"
        response = app.deliver(None, None, installation=None, raw=b"{")
        assert response.status_code == 401 and _code(response) == "installation_mismatch"
        malformed = app.deliver(token, None, raw=b"{not json")
        assert malformed.status_code == 400 and _code(malformed) == "invalid_request"
        assert app.ids() == []


def test_transport_fields_in_the_body_are_400_invalid_request_without_echo(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        secret = "transport-secret-value-1234"
        bodies = [
            {**_item(bundle_path, manifest), "sessionToken": secret},
            {**_item(bundle_path, manifest), "installationId": secret},
            {**_item(bundle_path, manifest), "attemptGeneration": 0, "claimId": secret},
            {k: v for k, v in _item(bundle_path, manifest).items() if k != "returnId"},
            {**_item(bundle_path, manifest, kind=RECEIVE_SNAPSHOT), "returnId": secret},
            {**_item(bundle_path, manifest), "kind": secret},
            {k: v for k, v in _item(bundle_path, manifest).items() if k != "requestedAt"},
            {**_item(bundle_path, manifest), "manifestSha256": ""},
            {**_item(bundle_path, manifest), "operationId": 7},
        ]
        for body in bodies:
            response = app.deliver(token, body)
            assert response.status_code == 400 and _code(response) == "invalid_request", body
            assert secret not in response.text
        assert app.ids() == []


def test_no_wglink_folder_is_409_retryable_and_accepts_nothing(tmp_path: Path) -> None:
    with wg(tmp_path, select=False) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()

        response = app.deliver(token, _item(bundle_path, manifest))

        assert response.status_code == 409 and _code(response) == "wglink_folder_not_selected"
        assert response.json()["error"]["retryable"] is True
        assert app.ids() == []
        assert solve_command.live_held_operation_ids() == frozenset()


def test_an_approved_update_restart_refuses_authenticated_deliveries_and_accepts_nothing(
    tmp_path: Path,
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        app.app.state.update_restart.refusal = lambda: "An update restart is pending."

        response = app.deliver(token, _item(bundle_path, manifest))

        assert response.status_code == 409 and _code(response) == "update_restart_pending"
        error = response.json()["error"]
        assert (error["retryable"], error["stage"]) == (True, "cadlink-live")
        assert app.ids() == []
        assert not _retained(app.data_dir, manifest)


def test_an_unauthenticated_delivery_during_a_restart_is_401_not_409(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        app.app.state.update_restart.refusal = lambda: "An update restart is pending."

        response = app.deliver("nope", _item(bundle_path, manifest))

        assert response.status_code == 401 and _code(response) == "session_unknown"


# -- review round: the bound, conflicts, takeover, paging, unavailability ------


def test_a_retry_after_the_bound_is_acknowledged_even_after_a_pass_ran(
    tmp_path: Path, clock: Clock
) -> None:
    """The delivery loop runs every second; it must not turn the add-in's retry
    at the bound into a new 30 s window."""

    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503
        clock.now += 30.0
        # No longer held: the pass starts the solve, which waits for its return.
        assert app.run_pass() == ["op-1"]

        again = app.deliver(token, _item(bundle_path, manifest))

        assert again.status_code == 200, again.text
        assert again.json()["result"] == "recovered"
        assert solve_command._live_waits == {}


def test_a_passed_deadline_is_forgotten_after_its_memory(tmp_path: Path, clock: Clock) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503
        clock.now += solve_command.LIVE_TRANSIENT_BOUND_S + solve_command.LIVE_DEADLINE_MEMORY_S - 1
        assert solve_command.live_held_operation_ids() == frozenset()
        assert "op-1" in solve_command._live_waits
        clock.now += 1

        assert solve_command.live_held_operation_ids() == frozenset()

        assert solve_command._live_waits == {}
        # A retry that late is a new delivery attempt, with a bound of its own.
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503


def test_a_conflicting_delivery_keeps_another_deliverys_transient_hold(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        token = app.token()
        assert app.deliver(token, _item(bundle_path, manifest)).status_code == 503

        conflict = app.deliver(token, _item(bundle_path, "sha256:" + "d" * 64))

        assert conflict.status_code == 409 and _code(conflict) == "operation_conflict"
        assert solve_command.live_held_operation_ids() == frozenset({"op-1"})
        assert app.run_pass() == []


def test_a_store_error_other_than_busy_is_a_500_and_releases_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()

        def broken(self, *args: Any, **kwargs: Any):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(CadLinkStore, "accept_operation", broken)

        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            app.deliver(token, _item(bundle_path, manifest))

        assert solve_command.live_held_operation_ids() == frozenset()


def _busy_once(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """The store refuses the first outcome, after the claim committed."""

    real = CadLinkStore.record_outcome
    calls = {"n": 0}

    def busy_once(self, *args: Any, **kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(CadLinkStore, "record_outcome", busy_once)
    return calls


def test_a_snapshot_left_processing_by_a_busy_store_is_healed_by_the_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        _busy_once(monkeypatch)
        body = _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT)

        first = app.deliver(token, body)

        assert first.status_code == 503 and _code(first) == "store_busy"
        row = app.row("snap-1")
        assert (row["state"], row["attempt_generation"]) == ("processing", 1)
        assert solve_command.live_held_operation_ids() == frozenset()

        again = app.deliver(token, body)

        assert again.status_code == 200, again.text
        operation = again.json()["operation"]
        assert (operation["state"], operation["attemptGeneration"]) == ("accepted", 2)


def test_a_snapshot_left_processing_by_a_busy_store_is_healed_by_the_next_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        token = app.token()
        _busy_once(monkeypatch)
        body = _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT)
        assert app.deliver(token, body).status_code == 503

        app.run_pass()

        row = app.row("snap-1")
        assert (row["state"], row["attempt_generation"]) == ("accepted", 2)
        again = app.deliver(token, body)
        assert again.status_code == 200 and again.json()["operation"]["state"] == "accepted"


def test_a_settler_that_was_taken_over_cannot_record_late(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        _crash_after_accept(app, _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT))
        stale = app.store.claim("snap-1", 0)
        assert stale == 1

        assert app.settle() == ["snap-1"]

        assert app.store.record_outcome("snap-1", stale, "rejected", reason="snapshot_invalid") is None
        row = app.row("snap-1")
        assert (row["state"], row["attempt_generation"]) == ("accepted", 2)


@pytest.mark.parametrize("change", ["present", "missing", "changed"])
def test_a_snapshot_claimed_before_a_crash_is_settled_after_the_store_reopens(
    tmp_path: Path, change: str
) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        _crash_after_accept(app, _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT))
        # The settler claimed it, and WG stopped before its outcome.
        assert app.store.claim("snap-1", 0) == 1
        workspace = app.workspace
    bundle = workspace / bundle_path
    if change == "missing":
        bundle.rename(workspace / "gone")
    elif change == "changed":
        manifest_file = bundle / "wgreturn.json"
        manifest_file.write_bytes(manifest_file.read_bytes() + b" ")

    with wg(tmp_path, select=False, startup=STARTUP + RECOVER) as restarted:
        row = restarted.row("snap-1")
        if change == "present":
            assert (row["state"], row["attempt_generation"]) == ("accepted", 2)
            assert _retained(restarted.data_dir, manifest)
        elif change == "changed":
            assert (row["state"], row["reason"]) == ("rejected", "snapshot_invalid")
        else:
            assert (row["state"], row["attempt_generation"]) == ("processing", 1)
            (workspace / "gone").rename(bundle)
            restarted.run_pass()
            row = restarted.row("snap-1")
            assert (row["state"], row["attempt_generation"]) == ("accepted", 2)


def test_every_unsettled_snapshot_is_reached_however_many_stay_unreadable(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        for index in range(preparation._SNAPSHOT_PAGE + 5):
            _crash_after_accept(
                app,
                _item(f"wgreturn/missing-{index}.wgreturn", "sha256:" + "e" * 64, f"old-{index:03d}",
                      kind=RECEIVE_SNAPSHOT),
            )
        bundle_path, manifest = _write_return(app.workspace)
        _crash_after_accept(app, _item(bundle_path, manifest, "newest", kind=RECEIVE_SNAPSHOT))

        assert app.settle() == ["newest"]

        assert app.row("newest")["state"] == "accepted"
        assert app.row("old-000")["state"] == "received"


class Wall:
    def __init__(self) -> None:
        from datetime import datetime, timezone

        self.now = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def test_a_snapshot_unreadable_for_24_hours_is_rejected_across_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    wall = Wall()
    monkeypatch.setattr(preparation, "_wall_now", wall)
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        (app.workspace / bundle_path).rename(app.workspace / "gone")
        body = _item(bundle_path, manifest, "snap-1", kind=RECEIVE_SNAPSHOT)
        _crash_after_accept(app, body)
        assert app.settle() == []
        first = app.row("snap-1")["snapshot_unreadable_since"]
        assert first == "2026-09-17T10:00:00+00:00"
        wall.now += timedelta(hours=23, minutes=59)
        assert app.settle() == []
        # A later unreadable attempt never moves the first time.
        assert app.row("snap-1")["snapshot_unreadable_since"] == first

    wall.now += timedelta(minutes=1)
    with wg(tmp_path, select=False, startup=STARTUP + RECOVER) as restarted:
        row = restarted.row("snap-1")
        assert (row["state"], row["reason"]) == ("rejected", "snapshot_unavailable")
        token = restarted.token()
        again = restarted.deliver(token, body)
        assert again.status_code == 200
        assert (again.json()["operation"]["state"], again.json()["operation"]["reason"]) == (
            "rejected", "snapshot_unavailable",
        )
