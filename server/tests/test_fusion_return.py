from __future__ import annotations

import json
import sys

import pytest

from server.cadlink.fusion_return import RETURN_REQUESTS_DIRECTORY, publish_return_request
from server.cadlink.store import CadLinkStore


def test_return_request_is_machine_local_and_targets_the_addin_session(tmp_path) -> None:
    path, request_id = publish_return_request(
        tmp_path,
        CadLinkStore.for_data_dir(tmp_path),
        session_id="session-a",
        design_id="wgd_a",
        document_id="fusion:doc-a",
        instance_id="instance-a",
        expected_return_state_hash="sha256:return-state",
    )
    assert path == tmp_path / "ipc" / "wglink" / RETURN_REQUESTS_DIRECTORY / f"{request_id}.json"
    payload = json.loads(path.read_text())
    assert payload["schemaVersion"] == 3
    assert payload["target"] == "fusion360"
    assert payload["sessionId"] == "session-a"
    assert payload["designId"] == "wgd_a"
    assert payload["documentId"] == "fusion:doc-a"
    assert payload["instanceId"] == "instance-a"
    assert payload["expectedReturnStateHash"] == "sha256:return-state"
    assert payload["requestId"] == payload["operationId"] == request_id
    assert not (tmp_path / "ipc" / "wglink" / ".fusion-return-request.json").exists()


def test_a_published_request_logs_its_request_id(tmp_path, caplog) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="server.cadlink.fusion_delivery"):
        _path, request_id = publish_return_request(
            tmp_path,
            CadLinkStore.for_data_dir(tmp_path),
            session_id="session-a",
            design_id="wgd_a",
            document_id="fusion:doc-a",
            instance_id="instance-a",
            expected_return_state_hash="sha256:return-state",
        )

    assert any(
        request_id in record.getMessage() and "delivery sequence 1" in record.getMessage()
        for record in caplog.records
        if record.name == "server.cadlink.fusion_delivery"
    )


def _request(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    path, request_id = publish_return_request(
        tmp_path, store, session_id="session-a", design_id="wgd_a",
        document_id="fusion:doc-a", instance_id="instance-a",
        expected_return_state_hash="sha256:state", request_id="pull-a",
    )
    return store, path, request_id


def _report(tmp_path, request_id="pull-a", outcome="refused", **changes):
    from datetime import datetime, timezone

    report = {
        "schemaVersion": 1, "cadApplication": "fusion360", "deliveryVersion": 3,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "diagnostics": {"lastRequest": {"channel": "returnRequest", "correlationId": request_id,
                                         "outcome": outcome}, "recentOutcomes": []},
        **changes,
    }
    marker = tmp_path / "ipc" / "wglink" / ".fusion-status.json"
    marker.write_text(json.dumps(report), encoding="utf-8")
    return marker


def test_file_refusal_is_correlated_persisted_once_and_survives_reopen(tmp_path, monkeypatch):
    from server.cadlink.fusion_return import read_return_request

    # Reading a return outcome never consults the live registry or wakes it.
    registry = sys.modules.get("server.cadlink.live.registry")
    if registry is not None:
        monkeypatch.setattr(registry, "registry_for", lambda *_: (_ for _ in ()).throw(AssertionError("live read")))
    store, _path, request_id = _request(tmp_path)
    marker = _report(tmp_path)
    first = read_return_request(tmp_path, store, request_id)
    assert first == {"requestId": request_id, "state": "rejected", "reason": "adapter_refused",
                     "message": "Fusion refused this request."}
    row = store.get_operation(request_id)
    _report(tmp_path, outcome="applied")
    assert read_return_request(tmp_path, store, request_id) == first
    assert store.get_operation(request_id)["updated_at"] == row["updated_at"]
    marker.unlink()
    store.close()
    reopened = CadLinkStore.for_data_dir(tmp_path)
    assert read_return_request(tmp_path, reopened, request_id) == first
    reopened.close()


def test_recent_file_outcome_survives_last_request_slot_being_overwritten(tmp_path):
    from server.cadlink.fusion_return import read_return_request

    store, _path, request_id = _request(tmp_path)
    _report(tmp_path, diagnostics={"lastRequest": {"channel": "handoff", "correlationId": "other", "outcome": "applied"},
                                 "recentOutcomes": [{"channel": "returnRequest", "requestId": request_id,
                                                     "outcome": "refused"}]})
    assert read_return_request(tmp_path, store, request_id)["state"] == "rejected"
    store.close()


def test_old_command_time_file_can_report_exact_request_outcome_without_claiming_presence(tmp_path):
    from server.cadlink.fusion_return import read_return_request

    store, _path, request_id = _request(tmp_path)
    _report(tmp_path, updatedAt="2026-01-01T00:00:00Z")
    assert read_return_request(tmp_path, store, request_id)["state"] == "rejected"
    store.close()


@pytest.mark.parametrize("outcome,state,reason", [
    ("applied", "accepted", None), ("failed", "rejected", "adapter_failed"),
    ("superseded", "cancelled", "superseded"), ("discarded", "cancelled", "adapter_not_started"),
])
def test_file_return_outcomes_are_exact_and_acceptance_is_only_adapter_completion(tmp_path, outcome, state, reason):
    from server.cadlink.fusion_return import read_return_request

    store, _path, request_id = _request(tmp_path)
    _report(tmp_path, outcome=outcome)
    result = read_return_request(tmp_path, store, request_id)
    assert result["state"] == state and result["reason"] == reason
    assert "bundle" not in result  # Geometry is proved only by the returns listing.
    store.close()


@pytest.mark.parametrize("changes", [
    {"schemaVersion": 2}, {"cadApplication": "other"}, {"deliveryVersion": 2},
    {"deliveryVersion": "3"}, {"deliveryVersion": True}, {"updatedAt": "2999-01-01T00:00:00Z"},
    {"updatedAt": "invalid"}, {"diagnostics": []},
    {"diagnostics": {"lastRequest": {"channel": "handoff", "correlationId": "pull-a", "outcome": "refused"}}},
    {"diagnostics": {"lastRequest": {"channel": "returnRequest", "correlationId": "other", "outcome": "refused"}}},
    {"diagnostics": {"lastRequest": {"channel": "returnRequest", "correlationId": "pull-a", "outcome": "unknown"}}},
])
def test_invalid_or_uncorrelated_file_evidence_never_settles_the_wait(tmp_path, changes):
    from server.cadlink.fusion_return import read_return_request

    store, path, request_id = _request(tmp_path)
    _report(tmp_path, **changes)
    assert read_return_request(tmp_path, store, request_id)["state"] == "received"
    assert path.exists()  # A read never consumes or republishes the request.
    store.close()


@pytest.mark.parametrize("file_kind", ["missing", "malformed", "too large", "symlink"])
def test_missing_or_unsafe_file_is_not_an_outcome(tmp_path, file_kind):
    from server.cadlink.fusion_return import read_return_request

    store, _path, request_id = _request(tmp_path)
    marker = tmp_path / "ipc" / "wglink" / ".fusion-status.json"
    if file_kind == "malformed":
        marker.write_text("[")
    if file_kind == "too large":
        marker.write_text(" " * (256 * 1024 + 1))
    if file_kind == "symlink":
        source = tmp_path / "report.json"
        _report(tmp_path).rename(source)
        marker.symlink_to(source)
    assert read_return_request(tmp_path, store, request_id)["state"] == "received"
    store.close()


def test_exact_http_read_without_coordinator_live_session_or_job(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.cadlink.fusion_return import router

    store, _path, request_id = _request(tmp_path)
    _report(tmp_path)
    app = FastAPI()
    app.state.data_dir = tmp_path
    app.state.cadlink_store = store
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(f"/api/cadlink/return-requests/{request_id}")
        assert response.status_code == 200 and response.json()["state"] == "rejected"
        assert client.get("/api/cadlink/return-requests/unknown").status_code == 404
    store.close()
