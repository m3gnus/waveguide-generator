"""The browser-to-Fusion handoff is scoped, complete, atomic, and exact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.exports.cad_handoff import (
    HANDOFFS_DIRECTORY,
    UPDATE_TARGET_REQUIRED,
    publish_fusion_handoff,
)


def _result(bundle: Path) -> dict[str, object]:
    return {
        "bundlePath": str(bundle),
        "bundleId": "wgb_01KZV700000000000000000000",
        "exportId": "wge_01KZV700000000000000000000",
        "sequence": 4,
        "identity": {"designId": "wgd_01KZV700000000000000000000"},
    }


def test_publish_fusion_handoff_announces_the_completed_bundle(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    bundle = workspace / "wglink" / "horn.wglink"
    bundle.mkdir(parents=True)

    published = publish_fusion_handoff(
        data_dir,
        workspace,
        _result(bundle),
        expected_document_id="fusion:doc-a",
        expected_instance_id="instance-b",
        expected_return_state_hash="sha256:return-state",
    )

    folder = data_dir / "ipc" / "wglink" / HANDOFFS_DIRECTORY
    assert published.path == folder / f"{published.request_id}.json"
    assert published.withdrawn == ()
    payload = json.loads(published.path.read_text())
    assert payload == {
        "schemaVersion": 3,
        "requestId": published.request_id,
        "operationId": published.request_id,
        "deliverySequence": 1,
        "target": "fusion360",
        "bundlePath": str(bundle),
        "bundleId": "wgb_01KZV700000000000000000000",
        "exportId": "wge_01KZV700000000000000000000",
        "sequence": 4,
        "designId": "wgd_01KZV700000000000000000000",
        "expectedDocumentId": "fusion:doc-a",
        "expectedInstanceId": "instance-b",
        "expectedReturnStateHash": "sha256:return-state",
        "requestedAt": payload["requestedAt"],
    }
    assert payload["requestedAt"].endswith("Z")
    # Staged under a hidden .tmp name and renamed: nothing else is left.
    assert [path.name for path in folder.iterdir()] == [published.path.name]
    assert not (data_dir / "ipc" / "wglink" / ".fusion-handoff.json").exists()


def test_two_inserts_are_two_requests(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    bundle = workspace / "wglink" / "horn.wglink"
    bundle.mkdir(parents=True)
    first = _result(bundle)
    earlier = publish_fusion_handoff(data_dir, workspace, first)

    later = publish_fusion_handoff(data_dir, workspace, {**first, "exportId": "wge_new", "sequence": 5})

    assert earlier.path.exists() and later.path.exists()
    assert json.loads(later.path.read_text())["deliverySequence"] == 2


def test_an_update_without_its_document_or_baseline_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bundle = workspace / "wglink" / "horn.wglink"
    bundle.mkdir(parents=True)

    for document_id, state in (("fusion:doc-a", None), (None, "sha256:state")):
        with pytest.raises(ValueError) as refused:
            publish_fusion_handoff(
                tmp_path / "data",
                workspace,
                _result(bundle),
                expected_document_id=document_id,
                expected_instance_id="instance-b",
                expected_return_state_hash=state,
            )
        assert str(refused.value) == UPDATE_TARGET_REQUIRED
    assert not (tmp_path / "data" / "ipc" / "wglink" / HANDOFFS_DIRECTORY).exists()


def test_handoff_refuses_a_bundle_outside_the_selected_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "wglink").mkdir(parents=True)
    outside = tmp_path / "outside.wglink"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside the selected workspace"):
        publish_fusion_handoff(tmp_path / "data", workspace, _result(outside))
    assert not (tmp_path / "data" / "ipc" / "wglink" / HANDOFFS_DIRECTORY).exists()
