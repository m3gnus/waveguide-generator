from __future__ import annotations

import json

from server.cadlink.fusion_return import RETURN_REQUEST_FILENAME, publish_return_request


def test_return_request_is_machine_local_and_targets_the_addin_session(tmp_path) -> None:
    marker, request_id = publish_return_request(
        tmp_path,
        session_id="session-a",
        design_id="wgd_a",
        document_id="fusion:doc-a",
        instance_id="instance-a",
        expected_return_state_hash="sha256:return-state",
    )
    assert marker == tmp_path / "ipc" / "wglink" / RETURN_REQUEST_FILENAME
    payload = json.loads(marker.read_text())
    assert payload["target"] == "fusion360"
    assert payload["sessionId"] == "session-a"
    assert payload["designId"] == "wgd_a"
    assert payload["documentId"] == "fusion:doc-a"
    assert payload["instanceId"] == "instance-a"
    assert payload["expectedReturnStateHash"] == "sha256:return-state"
    assert payload["requestId"] == request_id
