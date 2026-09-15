from __future__ import annotations

import json

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
