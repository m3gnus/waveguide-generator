from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from server.cadlink import fusion_delivery
from server.cadlink.fusion_delivery import expire_unstarted_insert_handoffs
from server.cadlink.fusion_delivery import recover_staged_fusion_requests
from server.cadlink.operations import request_digest
from server.cadlink.fusion_return import publish_return_request
from server.cadlink.store import CadLinkStore
from server.exports.cad_handoff import publish_fusion_handoff


def _store(data_dir: Path) -> CadLinkStore:
    return CadLinkStore.for_data_dir(data_dir)


def _bundle(workspace: Path, name: str = "horn.wglink") -> Path:
    bundle = workspace / "wglink" / name
    bundle.mkdir(parents=True, exist_ok=True)
    return bundle


def _result(bundle: Path, export_id: str = "wge_1") -> dict[str, object]:
    return {
        "bundlePath": str(bundle),
        "bundleId": "wgb_1",
        "exportId": export_id,
        "sequence": 1,
        "identity": {"designId": "wgd_1"},
    }


def test_capabilities_advertise_source_identity_without_changing_delivery() -> None:
    """``sourceIdentity`` and ``liveProtocol`` are additive: the schema and the
    Fusion-bound delivery version stay put. Solve delivery is 4: the WG request
    inbox reads requests that name their kind (M1 transfer contract, C3)."""

    assert fusion_delivery.capabilities() == {
        "schemaVersion": 1,
        "producer": "waveguide-generator",
        "solveCommandDelivery": 4,
        "fusionRequestDelivery": 3,
        "sourceIdentity": 1,
        "liveProtocol": 1,
    }
    # An integer, as the add-in reads every capability value; never a bool.
    assert type(fusion_delivery.capabilities()["sourceIdentity"]) is int
    assert type(fusion_delivery.capabilities()["liveProtocol"]) is int


def test_publishing_a_return_request_records_its_operation_before_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    store = _store(data_dir)
    original = store.accept_operation
    observed: list[bool] = []

    def accept(*args, **kwargs):
        observed.append(
            not (data_dir / "ipc" / "wglink" / ".fusion-return-requests" / "return-1.json").exists()
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "accept_operation", accept)
    path, request_id = publish_return_request(
        data_dir,
        store,
        session_id="session-a",
        design_id="wgd_1",
        document_id="fusion:doc-1",
        instance_id="instance-1",
        expected_return_state_hash="sha256:state-1",
        request_id="return-1",
    )

    assert observed == [True]
    assert path.is_file() and request_id == "return-1"
    row = store.get_operation(request_id)
    assert row is not None and row["kind"] == "request_return" and row["state"] == "received"


def test_a_republished_request_id_with_another_target_is_a_conflict(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = _store(data_dir)
    first, _ = publish_return_request(
        data_dir,
        store,
        session_id="session-a",
        design_id="wgd_1",
        document_id="fusion:doc-1",
        instance_id="instance-1",
        expected_return_state_hash="sha256:state-1",
        request_id="return-1",
    )
    before = first.read_bytes()

    with pytest.raises(ValueError, match="another operation"):
        publish_return_request(
            data_dir,
            store,
            session_id="session-a",
            design_id="wgd_1",
            document_id="fusion:doc-2",
            instance_id="instance-1",
            expected_return_state_hash="sha256:state-1",
            request_id="return-1",
        )

    assert first.read_bytes() == before


def test_a_terminal_operation_is_never_republished(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = _store(data_dir)
    path, _ = publish_return_request(
        data_dir,
        store,
        session_id="session-a",
        design_id="wgd_1",
        document_id="fusion:doc-1",
        instance_id="instance-1",
        expected_return_state_hash="sha256:state-1",
        request_id="return-1",
    )
    path.unlink()  # Fusion claimed and completed the visible delivery.
    store.record_outcome("return-1", 0, "accepted")

    recovered_path, _ = publish_return_request(
        data_dir,
        store,
        session_id="session-a",
        design_id="wgd_1",
        document_id="fusion:doc-1",
        instance_id="instance-1",
        expected_return_state_hash="sha256:state-1",
        request_id="return-1",
    )

    assert recovered_path == path
    assert not recovered_path.exists()


def test_a_superseded_update_is_recorded_cancelled_superseded(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    store = _store(data_dir)
    older = publish_fusion_handoff(
        data_dir,
        workspace,
        store,
        _result(_bundle(workspace, "old.wglink"), "wge_old"),
        expected_document_id="fusion:doc-1",
        expected_instance_id="instance-1",
        expected_return_state_hash="sha256:old",
        request_id="update-old",
    )
    newer = publish_fusion_handoff(
        data_dir,
        workspace,
        store,
        _result(_bundle(workspace, "new.wglink"), "wge_new"),
        expected_document_id="fusion:doc-1",
        expected_instance_id="instance-1",
        expected_return_state_hash="sha256:new",
        request_id="update-new",
    )

    assert newer.withdrawn == (older.request_id,)
    row = store.get_operation(older.request_id)
    assert row is not None and (row["state"], row["reason"]) == ("cancelled", "superseded")
    assert "update-new" in json.loads(row["outcome_json"])["message"]


def test_a_return_for_an_old_session_is_recorded_cancelled(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = _store(data_dir)
    _, old_id = publish_return_request(
        data_dir,
        store,
        session_id="session-old",
        design_id="wgd_1",
        document_id="fusion:doc-1",
        instance_id="instance-1",
        expected_return_state_hash="sha256:state-1",
        request_id="return-old",
    )
    publish_return_request(
        data_dir,
        store,
        session_id="session-new",
        design_id="wgd_1",
        document_id="fusion:doc-1",
        instance_id="instance-1",
        expected_return_state_hash="sha256:state-1",
        request_id="return-new",
    )

    row = store.get_operation(old_id)
    assert row is not None and (row["state"], row["reason"]) == (
        "cancelled", "session_changed"
    )


def test_an_unstarted_insert_expires_and_is_never_delivered(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    store = _store(data_dir)
    published = publish_fusion_handoff(
        data_dir,
        workspace,
        store,
        _result(_bundle(workspace)),
        request_id="insert-old",
    )
    body = json.loads(published.path.read_text())
    now = datetime.now(timezone.utc)
    body["requestedAt"] = (now - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    published.path.write_text(json.dumps(body), encoding="utf-8")

    expired = expire_unstarted_insert_handoffs(
        data_dir,
        now=now,
        record_expired=lambda operation_id: _record_expired(store, operation_id),
    )

    assert expired == (published.request_id,)
    assert not published.path.exists()
    row = store.get_operation(published.request_id)
    assert row is not None and (row["state"], row["reason"]) == ("cancelled", "expired")


def _record_expired(store: CadLinkStore, operation_id: str) -> None:
    row = store.get_operation(operation_id)
    assert row is not None
    store.record_outcome(
        operation_id,
        int(row["attempt_generation"]),
        "cancelled",
        reason="expired",
        outcome={"message": "expired"},
    )


@pytest.mark.parametrize("active_document", ["fusion:doc-live", None])
def test_an_insert_names_its_destination_document_or_a_new_one(
    tmp_path: Path, active_document: str | None
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    if active_document:
        marker = data_dir / "ipc" / "wglink" / ".fusion-status.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({
            "schemaVersion": 1,
            "cadApplication": "fusion360",
            "deliveryVersion": 3,
            "sessionId": "session-a",
            "adapterVersion": "0.3.3",
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "document": {"id": active_document, "name": "Speaker", "links": []},
        }))
    published = publish_fusion_handoff(
        data_dir,
        workspace,
        _store(data_dir),
        _result(_bundle(workspace)),
        request_id="insert-1",
    )

    destination = json.loads(published.path.read_text())["destination"]
    assert destination == (
        {"kind": "document", "value": active_document}
        if active_document
        else {"kind": "new_document", "value": published.request_id}
    )


def test_the_v3_handoff_keys_are_unchanged_for_the_pinned_addin(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    published = publish_fusion_handoff(
        data_dir,
        workspace,
        _store(data_dir),
        _result(_bundle(workspace)),
        request_id="insert-1",
    )
    payload = json.loads(published.path.read_text())

    assert {
        "schemaVersion", "target", "requestId", "operationId", "deliverySequence",
        "bundleId", "exportId", "bundlePath",
    } <= set(payload)
    assert payload["schemaVersion"] == 3
    assert payload["target"] == "fusion360"
    assert payload["operationId"] == payload["requestId"]


def test_store_failure_publishes_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    store = _store(data_dir)

    def fail(*_args, **_kwargs):
        raise OSError("registry unavailable")

    monkeypatch.setattr(store, "accept_operation", fail)
    with pytest.raises(OSError, match="registry unavailable"):
        publish_return_request(
            data_dir,
            store,
            session_id="session-a",
            design_id="wgd_1",
            document_id="fusion:doc-1",
            instance_id="instance-1",
            expected_return_state_hash="sha256:state-1",
            request_id="return-1",
        )
    assert not (data_dir / "ipc" / "wglink" / ".fusion-return-requests" / "return-1.json").exists()


def test_file_failure_does_not_leave_a_received_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    store = _store(data_dir)

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(fusion_delivery.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        publish_return_request(
            data_dir,
            store,
            session_id="session-a",
            design_id="wgd_1",
            document_id="fusion:doc-1",
            instance_id="instance-1",
            expected_return_state_hash="sha256:state-1",
            request_id="return-1",
        )
    row = store.get_operation("return-1")
    assert row is not None and (row["state"], row["reason"]) == (
        "cancelled", "publication_failed"
    )


def test_failed_compensation_leaves_a_staged_request_startup_can_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    store = _store(data_dir)
    real_replace = fusion_delivery.os.replace

    monkeypatch.setattr(
        fusion_delivery.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        store,
        "record_outcome",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("registry unavailable")),
    )
    with pytest.raises(OSError, match="disk full"):
        publish_return_request(
            data_dir,
            store,
            session_id="session-a",
            design_id="wgd_1",
            document_id="fusion:doc-1",
            instance_id="instance-1",
            expected_return_state_hash="sha256:state-1",
            request_id="return-1",
        )
    folder = data_dir / "ipc" / "wglink" / ".fusion-return-requests"
    assert not (folder / "return-1.json").exists()
    assert any(path.name.endswith(".tmp") for path in folder.iterdir())

    monkeypatch.setattr(fusion_delivery.os, "replace", real_replace)
    assert recover_staged_fusion_requests(
        data_dir, lookup_operation=store.get_operation
    ) == ("return-1",)
    assert (folder / "return-1.json").is_file()


def test_failed_expiry_recording_restores_the_request(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    store = _store(data_dir)
    published = publish_fusion_handoff(
        data_dir,
        workspace,
        store,
        _result(_bundle(workspace)),
        request_id="insert-old",
    )
    body = json.loads(published.path.read_text())
    now = datetime.now(timezone.utc)
    body["requestedAt"] = (now - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    published.path.write_text(json.dumps(body), encoding="utf-8")

    assert expire_unstarted_insert_handoffs(
        data_dir,
        now=now,
        record_expired=lambda _operation_id: (_ for _ in ()).throw(
            OSError("registry unavailable")
        ),
    ) == ()
    assert published.path.is_file()
    assert store.get_operation("insert-old")["state"] == "received"


def test_new_document_destination_value_is_the_operation_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    target = {
        "destination": {"kind": "new_document", "value": "another-id"},
        "export_id": "wge_1",
    }
    with pytest.raises(ValueError, match="must equal its operation id"):
        store.accept_operation(
            "insert-1",
            "insert_link",
            request_digest("insert_link", target, {}),
            target,
            {},
        )
