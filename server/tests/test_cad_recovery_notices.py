from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.cadlink.fusion_outcomes import settle_from_heartbeat
from server.cadlink.operations import request_digest
from server.cadlink.recovery_api import (
    dismiss_recovery_notice, get_recovery_notices, post_dismiss_recovery_notice, recovery_notices,
)
from server.cadlink.store import CadLinkStore


def accept(store, operation_id="mutation", kind="update_link"):
    target = {"document_id": "fusion:active", "design_id": "design", "instance_id": "instance",
              "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:baseline"}} if kind == "update_link" else {"export_id": "export", "destination": {"kind": "new_document", "value": operation_id}}
    inputs = {"export_id": "export"} if kind == "update_link" else {}
    row, _ = store.accept_operation(operation_id, kind, request_digest(kind, target, inputs), target, inputs)
    return row


def report(tmp_path, *, document_id="fusion:active", applying="mutation", links=None):
    value = {"schemaVersion": 1, "cadApplication": "fusion360", "deliveryVersion": 3,
             "updatedAt": datetime.now(timezone.utc).isoformat(),
             "document": {"id": document_id, "name": "Active document", "links": links or [],
                          "applyingOperation": {"operationId": applying} if applying else None}}
    marker = tmp_path / "ipc" / "wglink" / ".fusion-status.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(value))
    return value


def test_restart_restores_insert_document_even_without_notice_read(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store, kind="insert_link")
    heartbeat = report(tmp_path)
    settle_from_heartbeat(store, heartbeat, tmp_path / "ipc" / "wglink")
    store.close()
    (tmp_path / "ipc" / "wglink" / ".fusion-status.json").unlink()
    reopened = CadLinkStore.for_data_dir(tmp_path)
    notice, = recovery_notices(reopened, tmp_path).notices
    assert notice.id == "mutation" and notice.document_id == "fusion:active"
    assert notice.document_name == "Active document" and not notice.active_document
    reopened.close()


def test_evidence_alone_settles_recovery_and_requires_exact_ids(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    report(tmp_path)
    assert len(recovery_notices(store, tmp_path).notices) == 1
    for document_id, export_id in (("fusion:other", "export"), ("fusion:active", "wrong")):
        report(tmp_path, document_id=document_id, applying=None, links=[{"operationId": "mutation", "exportId": export_id}])
        assert len(recovery_notices(store, tmp_path).notices) == 1
        assert store.get_operation("mutation")["state"] == "recovery_required"
    report(tmp_path, applying=None, links=[{"operationId": "mutation", "exportId": "export"}])
    assert recovery_notices(store, tmp_path).notices == []
    assert store.get_operation("mutation")["state"] == "accepted"
    store.close()


def test_dismissal_is_idempotent_durable_and_does_not_claim_repair(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    report(tmp_path)
    assert recovery_notices(store, tmp_path).notices
    first = dismiss_recovery_notice(store, "mutation")
    with store._lock:
        at = store._connect().execute("SELECT dismissed_at FROM cad_recovery_notices").fetchone()[0]
    assert dismiss_recovery_notice(store, "mutation") == first
    assert first.dismissed and not first.repaired
    assert store.get_operation("mutation")["state"] == "recovery_required"
    assert store._connect().execute("SELECT dismissed_at FROM cad_recovery_notices").fetchone()[0] == at
    version = store._connect().execute("PRAGMA user_version").fetchone()[0]
    assert version == 11
    store.close()
    store = CadLinkStore.for_data_dir(tmp_path)
    assert recovery_notices(store, tmp_path).notices == []
    assert store.get_operation("mutation")["state"] == "recovery_required"
    # Dismissed is still unresolved, and later evidence remains authoritative.
    report(tmp_path, applying=None, links=[{"operationId": "mutation", "exportId": "export"}])
    assert recovery_notices(store, tmp_path).notices == []
    assert store.get_operation("mutation")["state"] == "accepted"
    store.close()


def test_dismissal_before_binding_cannot_be_revived(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store, kind="insert_link")
    dismiss_recovery_notice(store, "mutation")
    report(tmp_path)
    assert recovery_notices(store, tmp_path).notices == []
    store.close()
    store = CadLinkStore.for_data_dir(tmp_path)
    assert recovery_notices(store, tmp_path).notices == []
    store.close()


@pytest.mark.parametrize("destination", ["document", "new_document"])
def test_insert_binding_and_settlement_stay_in_the_persisted_document(tmp_path, destination):
    store = CadLinkStore.for_data_dir(tmp_path)
    target = {"export_id": "export", "destination": {
        "kind": destination, "value": "fusion:active" if destination == "document" else "mutation"}}
    store.accept_operation("mutation", "insert_link", request_digest("insert_link", target, {}), target, {})
    if destination == "document":
        # The first matching mark in a foreign document cannot bind this Insert.
        report(tmp_path, document_id="fusion:other")
        recovery_notices(store, tmp_path)
        saved = store._connect().execute("SELECT document_id FROM cad_recovery_notices WHERE notice_id = 'mutation'").fetchone()
        assert saved is None or saved["document_id"] != "fusion:other"
        assert store.get_operation("mutation")["state"] != "recovery_required"
    report(tmp_path)
    assert recovery_notices(store, tmp_path).notices[0].document_id == "fusion:active"
    store.close()
    store = CadLinkStore.for_data_dir(tmp_path)
    # Even exact operation/export ids in another document cannot hide recovery.
    foreign = report(tmp_path, document_id="fusion:other", links=[{"operationId": "mutation", "exportId": "export"}])
    foreign["document"]["name"] = "Foreign document"
    (tmp_path / "ipc" / "wglink" / ".fusion-status.json").write_text(json.dumps(foreign))
    notice, = recovery_notices(store, tmp_path).notices
    assert notice.document_id == "fusion:active" and not notice.active_document
    assert notice.document_name == "Active document"
    assert store.get_operation("mutation")["state"] == "recovery_required"
    report(tmp_path, applying=None, links=[{"operationId": "mutation", "exportId": "export"}])
    assert recovery_notices(store, tmp_path).notices == []
    assert store.get_operation("mutation")["state"] == "accepted"
    store.close()


def test_command_driven_last_report_stays_loud_but_never_settles_or_claims(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    report(tmp_path)
    assert recovery_notices(store, tmp_path).notices[0].active_document
    accept(store, operation_id="pending")
    value = report(tmp_path, links=[{"operationId": "mutation", "exportId": "export"}])
    value["diagnostics"] = {"activation": {"automaticCoordination": False}}
    value["updatedAt"] = "2020-01-01T00:00:00Z"
    marker = tmp_path / "ipc" / "wglink" / ".fusion-status.json"
    marker.write_text(json.dumps(value))
    assert recovery_notices(store, tmp_path).notices[0].active_document
    assert store.get_operation("mutation")["state"] == "recovery_required"
    assert store.get_operation("pending")["state"] == "received"
    value["updatedAt"] = "2999-01-01T00:00:00Z"
    marker.write_text(json.dumps(value))
    assert not recovery_notices(store, tmp_path).notices[0].active_document
    value["document"]["links"] = []
    value["updatedAt"] = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    marker.write_text(json.dumps(value))
    assert not recovery_notices(store, tmp_path).notices[0].active_document
    store.close()


@pytest.mark.parametrize("applying", [None, "later-operation"])
def test_historical_recovery_is_quiet_when_its_mark_is_gone(tmp_path, applying):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    report(tmp_path)
    assert recovery_notices(store, tmp_path).notices[0].active_document
    report(tmp_path, applying=applying)
    notice, = recovery_notices(store, tmp_path).notices
    assert notice.document_id == "fusion:active" and not notice.active_document
    assert store.get_operation("mutation")["state"] == "recovery_required"
    store.close()


def test_dismissal_flushes_the_committed_tombstone_before_success(tmp_path, monkeypatch):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    def flush():
        assert not store._connect().in_transaction
        assert store._connect().execute("SELECT dismissed_at FROM cad_recovery_notices").fetchone()[0]
        raise OSError("checkpoint failed")
    durable = Mock(side_effect=flush)
    monkeypatch.setattr(store, "make_durable", durable)
    with pytest.raises(OSError, match="checkpoint failed"):
        dismiss_recovery_notice(store, "mutation")
    durable.assert_called_once()
    durable.side_effect = None
    assert dismiss_recovery_notice(store, "mutation").dismissed
    assert durable.call_count == 2
    store.close()


@pytest.mark.parametrize("notice_id", ["unknown", "return"])
def test_dismissal_rejects_ids_without_an_accepted_mutation(tmp_path, notice_id):
    store = CadLinkStore.for_data_dir(tmp_path)
    target = {"document_id": "fusion:active", "design_id": "design", "instance_id": "instance",
              "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:baseline"}}
    store.accept_operation("return", "request_return", request_digest("request_return", target, {}), target, {})
    recovery_notices(store, tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=store)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(post_dismiss_recovery_notice(notice_id, request))
    assert error.value.status_code == 404
    assert store._connect().execute("SELECT COUNT(*) FROM cad_recovery_notices").fetchone()[0] == 0
    store.close()


def test_evidence_request_directories_match_the_delivery_channels(tmp_path):
    import ast
    from server.cadlink.fusion_outcomes import _visible_request
    # Read the constants without importing their live-dependent publisher.
    source = Path(__file__).parents[1] / "cadlink" / "fusion_delivery.py"
    channels = {node.targets[0].id: ast.literal_eval(node.value.args[0])
                for node in ast.parse(source.read_text()).body
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"HANDOFFS", "RETURN_REQUESTS"}}
    for kind, channel in (("insert_link", "HANDOFFS"), ("update_link", "HANDOFFS"), ("request_return", "RETURN_REQUESTS")):
        assert _visible_request(tmp_path, {"kind": kind, "operation_id": "named"}) == tmp_path / channels[channel] / "named.json"


def test_only_active_document_notices_are_loud_and_stale_status_is_quiet(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    report(tmp_path)
    assert recovery_notices(store, tmp_path).notices[0].active_document
    report(tmp_path, document_id="fusion:other", applying=None)
    assert not recovery_notices(store, tmp_path).notices[0].active_document
    value = report(tmp_path)
    value["updatedAt"] = "2020-01-01T00:00:00Z"
    (tmp_path / "ipc" / "wglink" / ".fusion-status.json").write_text(json.dumps(value))
    assert not recovery_notices(store, tmp_path).notices[0].active_document
    store.close()


def test_routes_read_status_file_without_live_layer(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    report(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(cadlink_store=store, data_dir=tmp_path)))
    assert asyncio.run(get_recovery_notices(request)).notices[0].active_document
    response = asyncio.run(post_dismiss_recovery_notice("mutation", request))
    assert response.dismissed and not response.repaired
    assert asyncio.run(get_recovery_notices(request)).notices == []
    # Neither direct nor transitive recovery imports include the live layer.
    import ast
    from server.cadlink import recovery_api, fusion_outcomes
    for module in (recovery_api, fusion_outcomes):
        tree = ast.parse(Path(module.__file__).read_text())
        assert not any(isinstance(node, ast.ImportFrom) and node.module and "live" in node.module.split(".") for node in ast.walk(tree))
    store.close()


def test_recovery_imports_and_routes_work_when_live_imports_are_forbidden(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    accept(store)
    report(tmp_path)
    store.close()
    script = """
import importlib.abc
import sys
from pathlib import Path
class NoLive(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'server.cadlink.live' or fullname.startswith('server.cadlink.live.'):
            raise AssertionError('Recovery imported the live transport')
sys.meta_path.insert(0, NoLive())
from server.cadlink.recovery_api import recovery_notices, dismiss_recovery_notice
from server.cadlink.store import CadLinkStore
store = CadLinkStore.for_data_dir(Path(sys.argv[1]))
assert recovery_notices(store, Path(sys.argv[1])).notices[0].id == 'mutation'
assert dismiss_recovery_notice(store, 'mutation').dismissed
assert not recovery_notices(store, Path(sys.argv[1])).notices
store.close()
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_notice_read_does_not_claim_an_unrelated_return_request(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    target = {"document_id": "fusion:active", "design_id": "design", "instance_id": "instance",
              "expected_baseline": {"kind": "document_signature_hash", "value": "sha256:baseline"}}
    store.accept_operation("return", "request_return", request_digest("request_return", target, {}), target, {})
    report(tmp_path, applying=None)
    assert recovery_notices(store, tmp_path).notices == []
    assert store.get_operation("return")["state"] == "received"
    store.close()
