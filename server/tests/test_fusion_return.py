from __future__ import annotations

import json

from server.cadlink.fusion_return import RETURN_REQUESTS_DIRECTORY, publish_return_request


def test_return_request_is_machine_local_and_targets_the_addin_session(tmp_path) -> None:
    path, request_id = publish_return_request(
        tmp_path,
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
