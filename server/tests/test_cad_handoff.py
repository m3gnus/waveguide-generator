"""The browser-to-Fusion delivery marker is scoped, complete, and atomic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.exports.cad_handoff import HANDOFF_FILENAME, publish_fusion_handoff


def _result(bundle: Path) -> dict[str, object]:
    return {
        "bundlePath": str(bundle),
        "bundleId": "wgb_01KZV700000000000000000000",
        "exportId": "wge_01KZV700000000000000000000",
        "sequence": 4,
    }


def test_publish_fusion_handoff_announces_the_completed_bundle(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bundle = workspace / "wglink" / "horn.wglink"
    bundle.mkdir(parents=True)

    marker = publish_fusion_handoff(workspace, _result(bundle))

    assert marker == workspace / "wglink" / HANDOFF_FILENAME
    payload = json.loads(marker.read_text())
    assert payload == {
        "schemaVersion": 1,
        "target": "fusion360",
        "bundlePath": str(bundle),
        "bundleId": "wgb_01KZV700000000000000000000",
        "exportId": "wge_01KZV700000000000000000000",
        "sequence": 4,
        "requestedAt": payload["requestedAt"],
    }
    assert payload["requestedAt"].endswith("Z")
    assert list(marker.parent.glob(f"{HANDOFF_FILENAME}.*")) == []


def test_a_new_send_atomically_replaces_the_previous_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    bundle = workspace / "wglink" / "horn.wglink"
    bundle.mkdir(parents=True)
    first = _result(bundle)
    publish_fusion_handoff(workspace, first)

    second = {**first, "exportId": "wge_new", "sequence": 5}
    marker = publish_fusion_handoff(workspace, second)

    payload = json.loads(marker.read_text())
    assert payload["exportId"] == "wge_new"
    assert payload["sequence"] == 5


def test_handoff_refuses_a_bundle_outside_the_selected_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "wglink").mkdir(parents=True)
    outside = tmp_path / "outside.wglink"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside the selected workspace"):
        publish_fusion_handoff(workspace, _result(outside))
    assert not (workspace / "wglink" / HANDOFF_FILENAME).exists()
