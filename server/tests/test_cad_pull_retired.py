"""Pull from WG (WG asking Fusion to send its model back) is retired.

The user sends the model with Send to WG in Fusion. What an earlier WG left
behind -- unfinished ``request_return`` rows -- is settled as cancelled at
startup, with that remedy, and never stops WG.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from server.app import create_app
from server.cadlink.fusion.fusion_outcomes import (
    RETIRED_RETURN_MESSAGE, retire_return_requests, settle_from_heartbeat,
)
from server.cadlink.ipc import ipc_folder
from server.cadlink.store import CadLinkStore
from server.tests.test_cad_fusion_outcomes import _accept, _heartbeat
from cad_legacy_rows import request_cancel


def _return_row(store: CadLinkStore, operation_id: str, state: str) -> None:
    _accept(store, operation_id=operation_id, kind="request_return")
    if state == "received":
        return
    assert store.claim(operation_id, 0) == 1
    if state == "live-claim":
        # A claim an older WG's HTTP session took, long ago.
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with store._lock, store._transaction() as conn:
            conn.execute("UPDATE cad_operations SET stage = 'adapter-received', updated_at = ?, claim_json = ? "
                         "WHERE operation_id = ?",
                         (old, json.dumps({"liveSessionId": "old", "claimId": "claim"}), operation_id))
    elif state == "cancel_requested":
        assert request_cancel(store, operation_id)["state"] == "cancel_requested"


@pytest.mark.parametrize("state", ["received", "processing", "live-claim", "cancel_requested"])
def test_unfinished_pull_is_cancelled_with_the_send_to_wg_remedy(tmp_path, state):
    store = CadLinkStore.for_data_dir(tmp_path)
    _return_row(store, "pull-1", state)

    assert retire_return_requests(store) == 1
    row = store.get_operation("pull-1")
    assert row["state"] == "cancelled"
    if state != "cancel_requested":  # a dismissal stands as the user's own cancel
        assert json.loads(row["outcome_json"])["message"] == RETIRED_RETURN_MESSAGE
    assert "Send to WG in Fusion" in RETIRED_RETURN_MESSAGE
    assert retire_return_requests(store) == 0
    store.close()
    reopened = CadLinkStore.for_data_dir(tmp_path)
    assert reopened.get_operation("pull-1")["state"] == "cancelled"
    reopened.close()


def test_retirement_leaves_finished_pulls_and_other_requests_alone(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    _accept(store, operation_id="pull-done", kind="request_return")
    store.record_outcome("pull-done", 0, "rejected", reason="adapter_failed")
    _accept(store, operation_id="update-1")
    before = (store.get_operation("pull-done"), store.get_operation("update-1"))

    assert retire_return_requests(store) == 0
    assert (store.get_operation("pull-done"), store.get_operation("update-1")) == before
    store.close()


def test_a_row_that_cannot_be_settled_never_stops_the_others(tmp_path, monkeypatch, caplog):
    store = CadLinkStore.for_data_dir(tmp_path)
    _return_row(store, "pull-bad", "received")
    _return_row(store, "pull-good", "received")
    original = store.record_outcome

    def record(operation_id, *args, **kwargs):
        if operation_id == "pull-bad":
            raise ValueError("unreadable row")
        return original(operation_id, *args, **kwargs)

    monkeypatch.setattr(store, "record_outcome", record)
    assert retire_return_requests(store) == 1
    assert store.get_operation("pull-good")["state"] == "cancelled"
    assert store.get_operation("pull-bad")["state"] == "received"
    assert "pull-bad" in caplog.text
    store.close()


def test_fusion_outcomes_for_a_pull_are_no_longer_recorded(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    _return_row(store, "pull-1", "received")
    heartbeat = _heartbeat(
        recent=[{"channel": "returnRequest", "requestId": "pull-1", "outcome": "applied"}],
        last={"channel": "returnRequest", "correlationId": "pull-1", "outcome": "failed"},
    )
    assert settle_from_heartbeat(store, heartbeat, ipc_folder(tmp_path)) == 0
    assert store.get_operation("pull-1")["state"] == "received"
    store.close()


def test_startup_settles_unfinished_pulls(tmp_path):
    app = create_app(data_dir=tmp_path)
    try:
        _return_row(app.state.cadlink_store, "pull-1", "processing")
        handler = next(h for h in app.router.on_startup
                       if h.__name__ == "advertise_fusion_delivery_on_startup")
        asyncio.run(handler())
        row = app.state.cadlink_store.get_operation("pull-1")
        assert row["state"] == "cancelled"
        assert json.loads(row["outcome_json"])["message"] == RETIRED_RETURN_MESSAGE
    finally:
        app.state.cadlink_store.close()


def test_the_pull_routes_are_gone(tmp_path):
    app = create_app(data_dir=tmp_path)
    try:
        paths = {getattr(route, "path", "") for route in app.routes}
        assert "/api/cadlink/request-fusion-return" not in paths
        assert "/api/cadlink/return-requests/{request_id}" not in paths
        assert not any("return-request" in path for path in app.openapi()["paths"])
    finally:
        app.state.cadlink_store.close()
