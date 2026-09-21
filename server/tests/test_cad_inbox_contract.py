"""The WG request inbox under the M1 transfer contract: C4 visibility, C5 idempotence, C7 push.

A request reaches WG by file (the inbox) or live (``POST .../deliveries``), in
either order or both. Whatever the path, one ``operationId`` with one digest is
one operation; a different request under the same id is refused visibly and the
stored operation is left as it is. Every row the inbox creates, recovers or
refuses is pushed on the jobs channel, and a refusal with no row of its own is
kept for the CAD Link panel as well.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.cadlink import preparation, solve_command
from server.cadlink.delivery_status import RECENT_REFUSALS, DeliveryStatus, delivery_status
from server.cadlink.operations import PREPARE_AND_SOLVE, RECEIVE_SNAPSHOT
from server.cadlink.solve_command import collect_solve_deliveries
from server.tests.test_cad_inbox_stall import FIXTURES, V4_NO_KIND, V4_SNAPSHOT, V4_SOLVE, app_for, drop, fixture
from server.tests.test_cad_preparation import _write_return
from server.tests.test_cadlink_live_deliveries import _snapshot_digest, wg


def _live_body(payload: dict[str, Any]) -> dict[str, Any]:
    """The same request as a live delivery body: the fields the route takes."""

    body = {key: payload[key] for key in ("operationId", "kind", "bundlePath", "manifestSha256", "requestedAt")}
    if payload["kind"] == PREPARE_AND_SOLVE:
        body["returnId"] = payload["returnId"]
    return body


def _snapshot_request(bundle_path: str, manifest: str) -> dict[str, Any]:
    return fixture(V4_SNAPSHOT, bundlePath=bundle_path, manifestSha256=manifest)


def _solve_request(bundle_path: str, manifest: str) -> dict[str, Any]:
    return fixture(V4_SOLVE, bundlePath=bundle_path, manifestSha256=manifest)


# -- C5: one id, one digest, one operation --------------------------------------


@pytest.mark.parametrize("make", [_snapshot_request, _solve_request], ids=["snapshot", "solve"])
def test_file_then_file_is_one_operation(tmp_path: Path, make) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        request = make(bundle_path, manifest)
        drop(app.data_dir, request)
        app.run_pass()
        before = app.row(request["operationId"])
        drop(app.data_dir, request)
        app.run_pass()
        assert app.ids() == [request["operationId"]]
        after = app.row(request["operationId"])
        assert after["request_digest"] == before["request_digest"]
        assert after["attempt_generation"] >= before["attempt_generation"]


@pytest.mark.parametrize("make", [_snapshot_request, _solve_request], ids=["snapshot", "solve"])
def test_file_then_live_is_one_operation(tmp_path: Path, make) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        request = make(bundle_path, manifest)
        drop(app.data_dir, request)
        app.run_pass()
        response = app.deliver(app.token(), _live_body(request))
        assert response.status_code == 200, response.text
        assert response.json()["result"] == "recovered"
        assert app.ids() == [request["operationId"]]


@pytest.mark.parametrize("make", [_snapshot_request, _solve_request], ids=["snapshot", "solve"])
def test_live_then_file_is_one_operation_and_one_start(tmp_path: Path, make) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        request = make(bundle_path, manifest)
        response = app.deliver(app.token(), _live_body(request))
        assert response.status_code == 200 and response.json()["result"] == "created", response.text
        drop(app.data_dir, request)
        started = app.run_pass()
        assert app.ids() == [request["operationId"]]
        # A solve is started once, and a snapshot is never prepared.
        assert started == ([request["operationId"]] if request["kind"] == PREPARE_AND_SOLVE else [])
        # The loop tracks what it started (``running``); nothing is started twice.
        assert app.run_pass(running=set(started)) == []


def test_a_v4_snapshot_file_is_settled_as_it_is_taken(tmp_path: Path) -> None:
    """A Send by file is accepted and settled, as the live route settles one."""

    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        request = _snapshot_request(bundle_path, manifest)
        drop(app.data_dir, request)
        assert app.run_pass() == []
        row = app.row(request["operationId"])
        assert row["kind"] == RECEIVE_SNAPSHOT
        assert row["state"] == "accepted"
        assert row["request_digest"] == _snapshot_digest(bundle_path, manifest)
        # The page opens it from its event, so the event names where it is.
        assert preparation.operation_summary(row)["snapshot"]["bundlePath"] == bundle_path


def test_only_a_send_names_a_bundle_path(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        drop(app.data_dir, _solve_request(bundle_path, manifest))
        app.run_pass()
        row = app.row(fixture(V4_SOLVE)["operationId"])
        assert "bundlePath" not in preparation.operation_summary(row)["snapshot"]


def test_the_same_id_as_another_kind_is_refused_and_leaves_the_operation(tmp_path: Path) -> None:
    with wg(tmp_path) as app:
        bundle_path, manifest = _write_return(app.workspace)
        solve = _solve_request(bundle_path, manifest)
        drop(app.data_dir, solve)
        app.run_pass()
        before = app.row(solve["operationId"])
        snapshot = {**_snapshot_request(bundle_path, manifest), "commandId": solve["commandId"], "operationId": solve["operationId"]}
        drop(app.data_dir, snapshot)
        refused: list[dict[str, Any]] = []
        collect_solve_deliveries(app.data_dir, app.store, refuse=refused.append)
        assert app.row(solve["operationId"]) == before
        assert [item["operationId"] for item in refused] == [solve["operationId"]]
        assert refused[0]["reason"]


# -- C4 and C7: every refusal and every arrival reaches the page ----------------


def test_a_pass_pushes_arrivals_and_keeps_refusals_for_the_panel(tmp_path: Path) -> None:
    """Through the application's own context: the jobs channel and the status route."""

    from server.cadlink.api import _preparation_context, get_delivery_status

    application, data_dir, workspace = app_for(tmp_path)
    bundle_path, manifest = _write_return(workspace)
    drop(data_dir, _solve_request(bundle_path, manifest))
    drop(data_dir, fixture(V4_NO_KIND))
    events = application.state.jobs_runtime.events
    pushed: list[dict[str, Any]] = []

    async def one_pass() -> dict[str, Any]:
        original = events.publish
        events.publish = pushed.append
        try:
            ctx = _preparation_context(application.state, workspace_root=workspace.resolve())
            await preparation.run_delivery_pass(ctx, spawn=lambda _id, coroutine: coroutine.close())
            await asyncio.sleep(0)  # the pushes are scheduled onto this loop
            return await get_delivery_status(SimpleNamespace(app=application))
        finally:
            events.publish = original

    status = asyncio.run(one_pass())
    kinds = [message["kind"] for message in pushed]
    assert "cadOperation" in kinds and "cadInboxRefusal" in kinds
    arrived = [m["operation"] for m in pushed if m["kind"] == "cadOperation"]
    assert arrived[0]["operationId"] == fixture(V4_SOLVE)["commandId"]
    assert arrived[0]["state"] == "received"
    refusal = next(m["refusal"] for m in pushed if m["kind"] == "cadInboxRefusal")
    assert refusal["operationId"] == fixture(V4_NO_KIND)["commandId"]
    assert status["recentRefusals"][0]["operationId"] == fixture(V4_NO_KIND)["commandId"]
    application.state.cadlink_store.close()


def test_the_real_application_always_has_a_channel_to_push_on(tmp_path: Path) -> None:
    """C7 gap (ii): ``publish`` is a no-op only without a jobs runtime, and the
    application always mounts one before the CAD Link routes. Without it the
    status route still serves refusals, and rows are listed on reconnect."""

    application, _data_dir, _workspace = app_for(tmp_path)
    assert getattr(application.state.jobs_runtime, "events", None) is not None
    bare = SimpleNamespace()
    delivery_status(bare).refused({"operationId": "x", "file": "x.json", "reason": "r", "at": "t"})
    assert delivery_status(bare).snapshot()["recentRefusals"][0]["operationId"] == "x"
    application.state.cadlink_store.close()


def test_the_refusal_list_is_bounded_newest_first() -> None:
    status = DeliveryStatus()
    for index in range(RECENT_REFUSALS + 5):
        status.refused({"operationId": f"op-{index}", "file": "f", "reason": "r", "at": "t"})
    kept = status.snapshot()["recentRefusals"]
    assert len(kept) == RECENT_REFUSALS
    assert kept[0]["operationId"] == f"op-{RECENT_REFUSALS + 4}"


def test_the_fixtures_are_the_add_in_writers_output() -> None:
    """Guard against editing the fixtures into something the add-in never wrote."""

    for name in (V4_SOLVE, V4_SNAPSHOT, V4_NO_KIND):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        assert payload["schemaVersion"] == 4 and payload["target"] == "waveguide-generator"
        assert payload["operationId"] == payload["commandId"]
    assert "returnId" not in fixture(V4_SNAPSHOT)
    assert isinstance(fixture(V4_SOLVE)["returnId"], str)
    assert "kind" not in fixture(V4_NO_KIND)
    assert solve_command.KINDED_SCHEMA_VERSION == 4
