"""Fusion presence and linked-design freshness contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest

from server.app import create_app
from server.cadlink.api import FusionStatusRequest, fusion_status
from server.cadlink.fusion_status import (
    FUSION_STATUS_FILENAME,
    FUSION_STATUS_TTL,
    fusion_process_running,
    read_fusion_status,
)


NOW = datetime(2026, 8, 12, 15, 30, tzinfo=timezone.utc)


def _write_status(
    workspace: Path,
    *,
    links: list[dict[str, object]] | None = None,
    document: bool = True,
    updated_at: datetime = NOW,
) -> Path:
    folder = workspace / "ipc" / "wglink"
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / FUSION_STATUS_FILENAME
    marker.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "cadApplication": "fusion360",
                "sessionId": "fusion-session-a",
                "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
                "document": (
                    {"name": "Tritonia V", "id": "fusion:doc-a", "links": links or []}
                    if document
                    else None
                ),
            }
        ),
        encoding="utf-8",
    )
    return marker


def _link(**updates: object) -> dict[str, object]:
    link: dict[str, object] = {
        "instanceId": "instance-a",
        "bundlePath": "/workspace/wglink/tritonia-v.wglink",
        "designId": "wgd_tritonia",
        "lineageId": "wgl_tritonia",
        "editVersion": "4",
        "designHash": "sha256:current",
        "designName": "Tritonia-V",
        "formula": "R-OSSE",
        "configPresent": True,
        "parameterCount": 14,
        "parameterDriftCount": 0,
        "localBodyState": "unmodified",
        "documentSignatureHash": None,
        "documentBodyCount": 0,
        "sourceStateHash": None,
        "exportId": "wge_4",
        "exportSequence": "4",
    }
    link.update(updates)
    return link


def _read(workspace: Path, **updates: object) -> dict[str, object]:
    arguments = {
        "current_design_hash": "sha256:current",
        "current_formula": "R-OSSE",
        "design_id": "wgd_tritonia",
        "now": NOW,
    }
    arguments.update(updates)
    return read_fusion_status(workspace, **arguments)  # type: ignore[arg-type]


def test_missing_stale_and_no_document_heartbeats_are_distinct(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    assert _read(workspace)["state"] == "closed"

    _write_status(
        workspace,
        document=False,
        updated_at=NOW - FUSION_STATUS_TTL - timedelta(seconds=1),
    )
    assert _read(workspace)["state"] == "closed"

    _write_status(workspace, document=False)
    result = _read(workspace)
    assert result["state"] == "no_document"
    assert result["running"] is True


def test_running_fusion_without_a_heartbeat_is_addin_offline(tmp_path: Path) -> None:
    result = _read(tmp_path / "workspace", process_running=True)
    assert result["state"] == "addin_offline"
    assert result["processRunning"] is True
    assert result["running"] is False


def test_process_probe_is_non_activating_and_platform_specific(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr("server.cadlink.fusion_status.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("server.cadlink.fusion_status.subprocess.run", fake_run)
    assert fusion_process_running(system="Darwin") is True
    assert calls == [["/usr/bin/pgrep", "-x", "Autodesk Fusion"]]


def test_active_document_reports_unlinked_current_and_stale_designs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link(designId="another")])
    assert _read(workspace)["state"] == "not_linked"

    _write_status(workspace, links=[_link()])
    current = _read(workspace)
    assert current["state"] == "current"
    assert current["documentName"] == "Tritonia V"
    assert current["fusionFormula"] == "R-OSSE"

    _write_status(
        workspace,
        links=[_link(designHash="sha256:old", formula="OSSE")],
    )
    stale = _read(workspace)
    assert stale["state"] == "stale"
    assert stale["fusionFormula"] == "OSSE"
    assert stale["currentFormula"] == "R-OSSE"

    _write_status(workspace, links=[_link(parameterDriftCount=1)])
    assert _read(workspace)["state"] == "stale"


def test_fusion_body_drift_is_new_only_until_that_fingerprint_is_returned(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    fingerprint = {
        "bbox_mm": [0, 0, 0, 10, 10, 10],
        "is_solid": True,
        "volume_mm3": 1000.0,
    }
    encoded = json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
    body_hash = "sha256:" + hashlib.sha256(encoded).hexdigest()
    _write_status(
        workspace,
        links=[_link(localBodyState="modified", bodyFingerprintHash=body_hash)],
    )
    assert _read(workspace)["fusionChangesAvailable"] is True

    returned = workspace / "speaker.wgreturn"
    returned.mkdir()
    (returned / "wgreturn.json").write_text(json.dumps({
        "instances": [{
            "design_id": "wgd_tritonia",
            "body_evidence": {"observed_fingerprint": fingerprint},
        }],
    }))
    returned_status = _read(workspace, returned_bundle=returned)
    assert returned_status["fusionChangesAvailable"] is False
    assert returned_status["wgChangesAvailable"] is False

    _write_status(workspace, links=[_link(localBodyState="modified")])
    assert _read(workspace)["state"] == "stale"


def test_whole_document_signature_detects_added_bodies_and_source_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    returned = workspace / "speaker.wgreturn"
    returned.mkdir(parents=True)
    sources = [{
        "id": "source-hf",
        "role": "HF",
        "instance_id": "instance-a",
        "expected_connected_components": 1,
        "observed": {"face_count": 1, "total_area_mm2": 500.0},
    }]
    source_hash = "sha256:" + hashlib.sha256(
        json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (returned / "wgreturn.json").write_text(json.dumps({
        "assembly": {
            "n_bodies_expected": 1,
            "signature_hash": "sha256:return-a",
        },
        "instances": [{"design_id": "wgd_tritonia", "body_evidence": {}}],
        "sources": sources,
    }))
    _write_status(workspace, links=[_link(
        documentSignatureHash="sha256:return-a",
        documentBodyCount=1,
        sourceStateHash=source_hash,
    )])
    assert _read(workspace, returned_bundle=returned)["fusionChangesAvailable"] is False

    _write_status(workspace, links=[_link(
        documentSignatureHash="sha256:return-with-mids",
        documentBodyCount=3,
        sourceStateHash="sha256:source-with-mf",
    )])
    changed = _read(workspace, returned_bundle=returned)
    assert changed["state"] == "stale"
    assert changed["fusionChangesAvailable"] is True


def test_an_unsaved_design_can_match_a_fusion_link_by_exact_hash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link()])
    assert _read(workspace, design_id=None)["state"] == "current"


def test_status_endpoint_hashes_the_design_in_wg_and_requires_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("server.cadlink.api.fusion_process_running", lambda: False)
    app = create_app(data_dir=tmp_path / "data")
    payload = FusionStatusRequest.model_validate(
        {"design": {"formula": "OSSE", "L": 120, "a": 45}}
    )
    with pytest.raises(Exception) as missing:
        asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert missing.value.status_code == 409

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app.state.workspace.select(workspace)
    response = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert response == {
        "cadApplication": "fusion360",
        "state": "closed",
        "running": False,
        "processRunning": False,
        "updatedAt": None,
        "documentName": None,
        "documentId": None,
        "currentFormula": "OSSE",
        "fusionFormula": None,
        "link": None,
        "wgChangesAvailable": False,
        "fusionChangesAvailable": False,
    }
