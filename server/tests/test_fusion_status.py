"""Fusion presence and linked-design freshness contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app import create_app
from server.cadlink.api import FusionStatusRequest, fusion_status
from server.cadlink.fusion_status import (
    FUSION_STATUS_FILENAME,
    FUSION_STATUS_TTL,
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
                    {"name": "Tritonia V", "links": links or []}
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

    _write_status(workspace, links=[_link(localBodyState="modified")])
    assert _read(workspace)["state"] == "stale"


def test_an_unsaved_design_can_match_a_fusion_link_by_exact_hash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link()])
    assert _read(workspace, design_id=None)["state"] == "current"


def test_status_endpoint_hashes_the_design_in_wg_and_requires_a_workspace(
    tmp_path: Path,
) -> None:
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
        "updatedAt": None,
        "documentName": None,
        "currentFormula": "OSSE",
        "fusionFormula": None,
        "link": None,
    }
