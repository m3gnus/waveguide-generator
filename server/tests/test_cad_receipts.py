"""Send discovery without jobs or a frontend coordinator."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from server.cadlink.operations import request_digest
from server.cadlink.receipts import receipt_page, record_refusal
from server.cadlink.receipts_api import list_receipts, router
from server.cadlink.solve_command import collect_solve_deliveries
from server.cadlink.store import CadLinkStore, STORE_FORMAT_VERSION


@pytest.fixture
def store(tmp_path):
    registry = CadLinkStore(tmp_path / "cadlink.db")
    yield registry
    registry.close()


def accept(store, id, *, settle=True, refused=False):
    inputs = {"bundle_path": f"wgreturn/{id}.wgreturn", "manifest_sha256": "sha256:" + "a" * 64}
    row, result = store.accept_operation(id, "receive_snapshot", request_digest("receive_snapshot", {}, inputs),
                                       {}, inputs, snapshot={"manifest_sha256": inputs["manifest_sha256"],
                                       "project_lineage_id": "project-a", "document_name": id})
    assert result == "created"
    if settle:
        finish(store, id, refused=refused)
    return row


def finish(store, id, *, refused=False):
    generation = store.claim(id, 0)
    return store.record_outcome(id, generation, "rejected" if refused else "accepted",
                               reason="snapshot_invalid" if refused else None,
                               outcome={"message": "bad model" if refused else "kept"})


def test_feed_walks_more_than_fifty_in_terminal_order_without_jobs(store):
    for i in range(123):
        accept(store, f"send-{i}")
    page = receipt_page(store)
    assert len(page["receipts"]) == 50 and page["has_more"]
    second = receipt_page(store, after=page["cursor"])
    third = receipt_page(store, after=second["cursor"])
    items = page["receipts"] + second["receipts"] + third["receipts"]
    assert [item["send_id"] for item in items] == [f"send-{i}" for i in range(123)]
    assert len({item["receipt_id"] for item in items}) == 123
    assert [item["cursor"] for item in items] == sorted(item["cursor"] for item in items)
    assert all(item["ingest_id"] is None and item["outcome"] == "accepted" for item in items)
    assert all(row["job_id"] is None for row in store.list_operations(limit=200))
    assert not third["has_more"]
    assert receipt_page(store, after=third["cursor"])["receipts"] == []


def test_receipt_projects_snapshot_and_project_design_identity(store):
    store.initialize()
    with store._lock, store._transaction() as conn:
        conn.execute(
            "INSERT INTO designs(design_id, lineage_id, edit_version, design_hash, "
            "snapshot_text, filename, created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
            ("design-a", "project-a", "hash", "{}", "speaker.wg", "2026-10-11T12:00:00Z", "2026-10-11T12:00:00Z"),
        )
    accept(store, "send")
    item = receipt_page(store)["receipts"][0]
    assert item["receipt_id"] == "send:send"
    assert item["send_id"] == "send"
    assert item["project_id"] == "project-a"
    assert item["design_ids"] == ["design-a"]
    assert item["bundle_path"] == "wgreturn/send.wgreturn"
    assert item["manifest_sha256"] == "sha256:" + "a" * 64
    assert item["document_name"] == "send"
    assert item["retained_copy"] is True
    assert item["send_sequence"] > 0


def test_late_settlement_is_after_cursor_not_lost_by_acceptance_order(store):
    accept(store, "early", settle=False)
    accept(store, "late")
    cursor = receipt_page(store)["cursor"]
    finish(store, "early")
    late_receipt = receipt_page(store)["receipts"][0]
    early_receipt = receipt_page(store, after=cursor)["receipts"][0]
    assert early_receipt["send_id"] == "early"
    assert early_receipt["cursor"] > late_receipt["cursor"]
    assert early_receipt["send_sequence"] < late_receipt["send_sequence"]


def test_cursor_and_receipt_identity_survive_reopen_and_overlap(store):
    accept(store, "one")
    accept(store, "two", refused=True)
    before = receipt_page(store, limit=1)
    path = store.db_path
    store.close()
    reopened = CadLinkStore(path)
    try:
        assert receipt_page(reopened, limit=1) == before
        tail = receipt_page(reopened, after=before["cursor"])
        assert [item["send_id"] for item in tail["receipts"]] == ["two"]
        assert tail["receipts"][0]["outcome"] == "refused"
        assert tail["receipts"][0]["reason"] == "snapshot_invalid"
        assert reopened._connect().execute("PRAGMA user_version").fetchone()[0] == STORE_FORMAT_VERSION
        accept(reopened, "three")
        assert receipt_page(reopened, after=tail["cursor"])["receipts"][0]["send_id"] == "three"
    finally:
        reopened.close()


def test_receipt_commits_atomically_with_outcome(store, monkeypatch):
    from server.cadlink import receipts
    accept(store, "send", settle=False)
    generation = store.claim("send", 0)
    def fail(*args):
        raise RuntimeError("receipt write failed")
    monkeypatch.setattr(receipts, "record_operation", fail)
    with pytest.raises(RuntimeError, match="receipt write failed"):
        store.record_outcome("send", generation, "accepted")
    assert store.get_operation("send")["state"] == "processing"
    assert receipt_page(store)["receipts"] == []


def test_reopen_projects_terminal_sends_written_by_an_older_app(store):
    accept(store, "send", settle=False)
    with store._lock, store._transaction() as conn:
        conn.execute("UPDATE cad_operations SET state = 'accepted' WHERE operation_id = 'send'")
    assert receipt_page(store)["receipts"] == []
    path = store.db_path
    store.close()
    reopened = CadLinkStore(path)
    try:
        assert receipt_page(reopened)["receipts"][0]["send_id"] == "send"
    finally:
        reopened.close()


def test_intake_refusal_without_operation_is_retained_deduplicated_and_recovered(store):
    refusal = {"at": "2026-10-11T12:00:00Z", "file": "bad.json", "operationId": None, "reason": "invalid request"}
    record_refusal(store, refusal)
    record_refusal(store, refusal)
    path = store.db_path
    store.close()
    reopened = CadLinkStore(path)
    try:
        page = receipt_page(reopened)
        assert len(page["receipts"]) == 1
        assert page["receipts"][0]["refusal"] == refusal
        assert reopened.list_operations() == []
    finally:
        reopened.close()


def test_real_intake_persists_refusal_before_push_or_file_removal(store, tmp_path):
    data = tmp_path / "data"
    inbox = data / "ipc" / "wglink" / ".wg-solve-requests"
    inbox.mkdir(parents=True)
    (inbox / "bad.json").write_text(json.dumps({"schemaVersion": 4, "target": "waveguide-generator",
                                               "kind": "receive_snapshot", "commandId": "bad"}))
    seen = []
    def push(refusal):
        seen.append(refusal)
        assert receipt_page(store)["receipts"][0]["refusal"] == dict(refusal)
    collect_solve_deliveries(data, store, refuse=push)
    assert len(seen) == 1
    assert len(receipt_page(store)["receipts"]) == 1
    assert store.get_operation("bad") is None
    assert not (inbox / "bad.json").exists()


def test_intake_refusal_log_is_bounded_without_reusing_or_rewinding_cursor(store):
    for i in range(25):
        record_refusal(store, {"at": "2026-10-11T12:00:00Z", "file": f"bad-{i}.json",
                               "operationId": None, "reason": "bad request"})
    page = receipt_page(store)
    assert len(page["receipts"]) == 20
    assert page["receipts"][0]["refusal"]["file"] == "bad-5.json"
    accept(store, "after-refusals")
    tail = receipt_page(store, after=page["cursor"])
    assert tail["receipts"][0]["send_id"] == "after-refusals"
    assert tail["cursor"] > page["cursor"]


def get(app, *, after=0, limit=50):
    route = next(route for route in app.routes if route.path == "/api/cadlink/receipts")
    assert route.endpoint is list_receipts
    return asyncio.run(list_receipts(SimpleNamespace(app=app), after=after, limit=limit))


def application(store, workspace):
    app = FastAPI()
    app.state.cadlink_store = store
    app.state.cad_workspace = SimpleNamespace(selected_path=lambda: workspace)
    app.include_router(router)
    return app


def test_http_projects_refused_missing_unreadable_and_retained_copy(store, tmp_path):
    accept(store, "refused", refused=True)
    accept(store, "missing")
    accept(store, "unreadable")
    source = tmp_path / "workspace" / "wgreturn" / "unreadable.wgreturn"
    source.mkdir(parents=True)
    (source / "wgreturn.json").write_text("not json")
    app = application(store, tmp_path / "workspace")
    page = get(app, limit=2)
    assert [item["outcome"] for item in page["receipts"]] == ["refused", "missing_source"]
    assert page["receipts"][1]["retained_copy"] is True
    tail = get(app, after=page["cursor"])
    assert tail["receipts"][0]["outcome"] == "unreadable"
    app.state.cad_workspace = SimpleNamespace(selected_path=lambda: None)
    retained = get(app)["receipts"]
    assert [item["outcome"] for item in retained] == ["refused", "retained_copy", "retained_copy"]
    assert retained[1]["reason"] == "folder_not_selected"
    assert receipt_page(store)["receipts"][1]["outcome"] == "accepted"


def test_http_reuses_readable_inventory_and_refuses_changed_manifest(store, tmp_path):
    import hashlib
    accept(store, "send")
    source = tmp_path / "workspace" / "wgreturn" / "send.wgreturn"
    source.mkdir(parents=True)
    body = json.dumps({"document": {}, "sources": [], "instances": []}).encode()
    (source / "wgreturn.json").write_bytes(body)
    # Set the accepted fixture's digest to this source's exact manifest.
    with store._lock, store._transaction() as conn:
        payload = receipt_page(store)["receipts"][0]
        payload.pop("cursor")
        payload["manifest_sha256"] = "sha256:" + hashlib.sha256(body).hexdigest()
        conn.execute("UPDATE cad_send_receipts SET payload_json = ?", (json.dumps(payload),))
    app = application(store, tmp_path / "workspace")
    assert get(app)["receipts"][0]["outcome"] == "accepted"
    (source / "wgreturn.json").write_bytes(body + b" ")
    assert get(app)["receipts"][0]["reason"] == "source_changed"


@pytest.mark.parametrize("after,limit", [(-1, 50), (0, 0), (0, 201)])
def test_feed_rejects_invalid_cursor_and_limit(store, after, limit):
    with pytest.raises(ValueError):
        receipt_page(store, after=after, limit=limit)
