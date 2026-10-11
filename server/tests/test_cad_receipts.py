"""Send discovery without jobs or a frontend coordinator."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from server.cadlink.intake import returns_folder
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
    assert receipt_page(store)["receipts"][0]["send_id"] == "send"
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
    payload = receipt_page(store)["receipts"][0]
    with store._lock, store._transaction() as conn:
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


def bad_delivery(tmp_path):
    data = tmp_path / 'data'
    inbox = data / 'ipc' / 'wglink' / '.wg-solve-requests'
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / 'bad.json').write_text(json.dumps({'schemaVersion': 4, 'target': 'waveguide-generator',
                                              'kind': 'receive_snapshot', 'commandId': 'bad'}))
    return data, inbox


def reset_collector(monkeypatch):
    from server.cadlink import solve_command as commands
    for name in ('_refused_claims', '_unreadable_reported'):
        monkeypatch.setattr(commands, name, set())
    for name in ('_pending_refusal_acks', '_ack_failures', '_admission_waits', '_unreadable_waits', '_retention_waits'):
        monkeypatch.setattr(commands, name, {})
    return commands


def test_refusal_is_durable_before_push_ack_and_consumption(store, tmp_path, monkeypatch):
    commands = reset_collector(monkeypatch)
    store.initialize()
    data, inbox = bad_delivery(tmp_path)
    events = []
    durable = store.make_durable
    def sync():
        events.append('durable')
        durable()
    monkeypatch.setattr(store, 'make_durable', sync)
    acknowledgement = commands.write_acknowledgement
    def ack(*args, **kwargs):
        assert events == ['durable', 'push']
        events.append('ack')
        return acknowledgement(*args, **kwargs)
    monkeypatch.setattr(commands, 'write_acknowledgement', ack)
    collect_solve_deliveries(data, store, refuse=lambda _: events.append('push'))
    assert events == ['durable', 'push', 'ack']
    assert not list(inbox.iterdir())


def test_failed_refusal_durability_keeps_claim_unacknowledged(store, tmp_path, monkeypatch):
    commands = reset_collector(monkeypatch)
    store.initialize()
    data, inbox = bad_delivery(tmp_path)
    def fail():
        raise RuntimeError('checkpoint busy')
    monkeypatch.setattr(store, 'make_durable', fail)
    pushed = []
    ack = []
    monkeypatch.setattr(commands, 'write_acknowledgement', lambda *a, **k: ack.append(a))
    with pytest.raises(RuntimeError, match='checkpoint busy'):
        collect_solve_deliveries(data, store, refuse=pushed.append)
    assert len(list(inbox.iterdir())) == 1
    assert not pushed and not ack


def test_interrupted_refusal_replays_original_after_restart_and_distinct_same_slot_claim(store, tmp_path, monkeypatch):
    commands = reset_collector(monkeypatch)
    data, inbox = bad_delivery(tmp_path)
    pushed = []
    monkeypatch.setattr(commands, '_acknowledge', lambda *a, **k: False)
    collect_solve_deliveries(data, store, refuse=pushed.append)
    first = receipt_page(store)
    reset_collector(monkeypatch)
    path = store.db_path
    store.close()
    reopened = CadLinkStore(path)
    try:
        collect_solve_deliveries(data, reopened, refuse=pushed.append)
        assert receipt_page(reopened) == first
        assert pushed == [first['receipts'][0]['refusal']] * 2
        # Two new claims of the same original filename in the same clock second
        # must remain distinct in health's at/file/id dedupe too.
        monkeypatch.setattr(commands, 'utc_now', lambda: '2026-10-11T12:00:00Z')
        bad_delivery(tmp_path)
        collect_solve_deliveries(data, reopened, refuse=pushed.append)
        bad_delivery(tmp_path)
        collect_solve_deliveries(data, reopened, refuse=pushed.append)
        rows = receipt_page(reopened)['receipts']
        assert len(rows) == 3
        assert len({(r['refusal']['at'], r['refusal']['file'], r['send_id']) for r in rows}) == 3
    finally:
        reopened.close()


def test_admission_ack_failures_do_not_evict_intake_log(store, tmp_path, monkeypatch):
    commands = reset_collector(monkeypatch)
    data, inbox = bad_delivery(tmp_path)
    (inbox / 'bad.json').write_text(json.dumps({'schemaVersion': 3, 'target': 'waveguide-generator',
        'commandId': 'solve', 'operationId': 'solve', 'returnId': 'wgr_1', 'bundlePath': 'wgreturn/model.wgreturn',
        'manifestSha256': 'sha256:' + 'a' * 64, 'requestedAt': '2026-10-11T12:00:00Z'}))
    monkeypatch.setattr(commands, 'RETENTION_PASSES', 1)
    monkeypatch.setattr(commands, 'write_acknowledgement', lambda *a, **k: False)
    for i in range(19):
        record_refusal(store, {'at': '2026-10-11T12:00:00Z', 'file': f'prior-{i}', 'operationId': None, 'reason': 'invalid'})
    pushed = []
    for i in range(25):
        monkeypatch.setattr(commands, 'utc_now', lambda i=i: f'2026-10-11T12:00:{i:02d}Z')
        collect_solve_deliveries(data, store, accept_solve=lambda _: False, refuse=pushed.append)
    rows = receipt_page(store)['receipts']
    assert len(rows) == 20
    assert sum(r['send_id'] == 'solve' for r in rows) == 1
    assert pushed == [pushed[0]] * 25
    assert len(list(inbox.iterdir())) == 1


def test_unreadable_refusal_retries_persistence_after_threshold(store, tmp_path, monkeypatch):
    from server.cadlink import receipts
    commands = reset_collector(monkeypatch)
    data, inbox = bad_delivery(tmp_path)
    claim = inbox / (commands.CLAIM_PREFIX + 'existing.json')
    (inbox / 'bad.json').rename(claim)
    monkeypatch.setattr(commands, '_read_payload', lambda *a, **k: commands._Unreadable('held'))
    monkeypatch.setattr(commands, 'UNREADABLE_PASSES', 1)
    original = receipts.record_refusal
    def fail(*a, **k):
        raise RuntimeError('receipt write failed')
    monkeypatch.setattr(receipts, 'record_refusal', fail)
    with pytest.raises(RuntimeError, match='receipt write failed'):
        collect_solve_deliveries(data, store)
    monkeypatch.setattr(receipts, 'record_refusal', original)
    pushed = []
    collect_solve_deliveries(data, store, refuse=pushed.append)
    collect_solve_deliveries(data, store, refuse=pushed.append)
    assert len(pushed) == 1 and len(receipt_page(store)['receipts']) == 1
    assert claim.exists()


def test_evicted_pending_claim_replays_without_reentering_log(store):
    original = record_refusal(store, {'at': '2026-10-11T12:00:00Z', 'file': 'pending', 'operationId': None, 'reason': 'invalid'}, claim_id='pending')
    for i in range(20):
        record_refusal(store, {'at': '2026-10-11T12:00:00Z', 'file': str(i), 'operationId': None, 'reason': 'invalid'})
    before = receipt_page(store)
    assert record_refusal(store, {**original, 'at': '2026-10-11T12:01:00Z'}, claim_id='pending') == original
    assert receipt_page(store) == before


def test_feed_identity_is_durable_stable_and_database_scoped(store, tmp_path, monkeypatch):
    calls = []
    durable = store.make_durable
    monkeypatch.setattr(store, 'make_durable', lambda: (calls.append('durable'), durable()))
    identity = receipt_page(store)['feed_id']
    assert calls == ['durable']
    path = store.db_path
    store.close()
    reopened = CadLinkStore(path)
    other = CadLinkStore(tmp_path / 'other.db')
    try:
        assert receipt_page(reopened)['feed_id'] == identity
        assert receipt_page(other)['feed_id'] != identity
    finally:
        reopened.close()
        other.close()


def test_backfill_skips_existing_and_malformed_rows_and_discovers_later_settlement(store, monkeypatch, caplog):
    from server.cadlink import receipts
    accept(store, 'existing')
    accept(store, 'broken', settle=False)
    accept(store, 'later', settle=False)
    with store._lock, store._transaction() as conn:
        conn.execute("UPDATE cad_operations SET state = 'accepted', snapshot_json = 'not json' WHERE operation_id IN ('existing', 'broken')")
    path = store.db_path
    store.close()
    projected = []
    original = receipts.record_operation
    def observe(conn, row):
        projected.append(row['operation_id'])
        original(conn, row)
    monkeypatch.setattr(receipts, 'record_operation', observe)
    reopened = CadLinkStore(path)
    try:
        assert receipt_page(reopened)['receipts'][0]['send_id'] == 'existing'
        assert 'existing' not in projected
        assert 'Skipping malformed terminal Send broken' in caplog.text
        with reopened._lock, reopened._transaction() as conn:
            conn.execute("UPDATE cad_operations SET state = 'accepted' WHERE operation_id = 'later'")
        assert [r['send_id'] for r in receipt_page(reopened)['receipts']] == ['existing', 'later']
    finally:
        reopened.close()


def test_http_caches_inventory_and_hashes_across_pages_but_refreshes_changed_source(store, tmp_path, monkeypatch):
    from pathlib import Path
    import hashlib
    body = json.dumps({'document': {}, 'sources': [], 'instances': []}).encode()
    root = tmp_path / 'workspace'
    for name in ('one', 'two'):
        accept(store, name)
        source = root / 'wgreturn' / f'{name}.wgreturn'
        source.mkdir(parents=True)
        (source / 'wgreturn.json').write_bytes(body)
    with store._lock, store._transaction() as conn:
        for row in conn.execute('SELECT cursor, payload_json FROM cad_send_receipts').fetchall():
            payload = json.loads(row['payload_json'])
            payload['manifest_sha256'] = 'sha256:' + hashlib.sha256(body).hexdigest()
            conn.execute('UPDATE cad_send_receipts SET payload_json = ? WHERE cursor = ?', (json.dumps(payload), row['cursor']))
    scans, hashes = [], []
    inventory, read = returns_folder.return_inventory, Path.read_bytes
    def scan(path):
        scans.append(path)
        return inventory(path)
    def reading(path):
        if path.name == 'wgreturn.json':
            hashes.append(path)
        return read(path)
    monkeypatch.setattr(returns_folder, 'return_inventory', scan)
    monkeypatch.setattr(Path, 'read_bytes', reading)
    app = application(store, root)
    one = get(app, limit=1)
    two = get(app, after=one['cursor'], limit=1)
    get(app, limit=1)
    get(app, after=one['cursor'], limit=1)
    assert len(scans) == 1 and len(hashes) == 2
    assert two['receipts'][0]['outcome'] == 'accepted'
    (root / 'wgreturn' / 'two.wgreturn' / 'wgreturn.json').write_bytes(body + b' ')
    assert get(app, after=one['cursor'], limit=1)['receipts'][0]['reason'] == 'source_changed'
    assert len(scans) == 2 and len(hashes) == 3



def test_distinct_claims_of_same_file_at_identical_clock_time_have_distinct_health_keys(store, monkeypatch):
    from datetime import datetime, timezone
    from server.cadlink import receipts
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(receipts, 'datetime', FrozenClock)
    refusal = {'at': '2026-10-11T12:00:00Z', 'file': 'bad.json', 'operationId': None, 'reason': 'invalid'}
    first = record_refusal(store, refusal, claim_id='first')
    second = record_refusal(store, refusal, claim_id='second')
    assert first['at'] != second['at']
    assert len(receipt_page(store)['receipts']) == 2



def test_claim_uuid_is_synced_before_receipt_persistence(store, tmp_path, monkeypatch):
    from server.cadlink import receipts
    commands = reset_collector(monkeypatch)
    if commands.os.name == 'nt':
        pytest.skip('directory fsync is a POSIX facility')
    data, inbox = bad_delivery(tmp_path)
    synced = []
    fsync = commands.os.fsync
    def sync(fd):
        import stat
        if stat.S_ISDIR(commands.os.fstat(fd).st_mode):
            synced.append(fd)
        fsync(fd)
    monkeypatch.setattr(commands.os, 'fsync', sync)
    original = receipts.record_refusal
    def persist(*a, **k):
        assert synced, 'claim rename must be synced before persistence'
        return original(*a, **k)
    monkeypatch.setattr(receipts, 'record_refusal', persist)
    collect_solve_deliveries(data, store)
    assert not list(inbox.iterdir())


def test_feed_identity_checkpoint_failure_remains_retryable(store, monkeypatch):
    sync = store.make_durable
    calls = []
    def unavailable():
        calls.append('failed')
        raise RuntimeError('checkpoint busy')
    monkeypatch.setattr(store, 'make_durable', unavailable)
    with pytest.raises(RuntimeError, match='checkpoint busy'):
        receipt_page(store)
    identity = store._connect().execute('SELECT feed_id FROM cad_receipt_meta').fetchone()[0]
    def durable():
        calls.append('durable')
        sync()
    monkeypatch.setattr(store, 'make_durable', durable)
    assert receipt_page(store)['feed_id'] == identity
    assert calls == ['failed', 'durable']
