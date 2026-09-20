"""Fusion presence and linked-design freshness contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from types import SimpleNamespace
import hashlib
import threading
import time

import pytest

from server.app import create_app
from server.cadlink.api import (
    FusionStatusRequest,
    _realized_dimensions_payload,
    fusion_status,
)
from server.cadlink.identity import design_hash
from server.cadlink.fusion_status import (
    FUSION_STATUS_FILENAME,
    FUSION_STATUS_TTL,
    fusion_process_running,
    read_fusion_status,
)
from server.cadlink.store import CadLinkStore
from server.design.schema import DesignConfig


NOW = datetime(2026, 8, 12, 15, 30, tzinfo=timezone.utc)
TARGET_DESIGN_ID = "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M"
TARGET_LINEAGE_ID = "wgl_01J4Y2WZQK8Z3TFD3E7V9XKQ4M"


def _write_status(
    workspace: Path,
    *,
    links: list[dict[str, object]] | None = None,
    document: bool = True,
    updated_at: datetime = NOW,
    adapter_version: str | None = None,
    workspace_root: Path | None = None,
    delivery_version: int | None = 3,
    applying_operation: dict[str, object] | None = None,
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
                "adapterVersion": adapter_version,
                **({"deliveryVersion": delivery_version} if delivery_version is not None else {}),
                "workspaceRoot": str(workspace_root) if workspace_root is not None else None,
                "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
                "document": (
                    {
                        "name": "Tritonia V",
                        "id": "fusion:doc-a",
                        "links": links or [],
                        **(
                            {"applyingOperation": applying_operation}
                            if applying_operation is not None
                            else {}
                        ),
                    }
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


def _registered_export(
    store: CadLinkStore, design: DesignConfig, manifest: object, *, key: str
) -> tuple[object, dict[str, object]]:
    saved = store.save(
        requested=None,
        design_hash=design_hash(design),
        filename="tritonia.cfg",
        snapshot_builder=lambda _identity: "snapshot",
    )
    identity = saved["identity"]
    exported = store.allocate_export(
        design_id=identity.design_id,
        geometry_hash="sha256:geometry-" + key,
        artifact_sha256="sha256:artifact-" + key,
        manifest_json=json.dumps(manifest),
        idempotency_key=key,
    )
    return identity, exported


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
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr("server.cadlink.fusion_status.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("server.cadlink.fusion_status.subprocess.run", fake_run)
    assert fusion_process_running(system="Darwin") is True
    assert calls[0][0] == ["/usr/bin/pgrep", "-x", "Autodesk Fusion"]
    assert "creationflags" not in calls[0][1]

    monkeypatch.setattr(
        "server.platform.process.subprocess.CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    assert fusion_process_running(system="Windows") is False
    assert calls[1][0] == ["/usr/bin/tasklist", "/FI", "IMAGENAME eq Fusion360.exe", "/NH"]
    assert calls[1][1]["creationflags"] == 0x08000000


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
    legacy = _read(workspace)
    assert legacy["state"] == "stale"
    assert legacy["link"]["parameterDriftCount"] == 1
    assert "driftedParameters" not in legacy["link"]


def test_repeated_design_requires_an_exact_instance_and_never_uses_first_match(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _write_status(
        workspace,
        links=[
            _link(instanceId="instance-b", designName="Right"),
            _link(instanceId="instance-a", designName="Left"),
        ],
    )

    ambiguous = _read(workspace)

    assert ambiguous["state"] == "instance_selection_required"
    assert ambiguous["link"] is None
    assert ambiguous["selectedInstanceId"] is None
    assert [link["instanceId"] for link in ambiguous["matchingLinks"]] == [
        "instance-a",
        "instance-b",
    ]

    selected = _read(workspace, instance_id="instance-b")
    assert selected["state"] == "current"
    assert selected["selectedInstanceId"] == "instance-b"
    assert selected["link"]["designName"] == "Right"

    stale_choice = _read(workspace, instance_id="instance-was-detached")
    assert stale_choice["state"] == "instance_selection_required"
    assert stale_choice["link"] is None


def test_returned_body_evidence_is_joined_by_instance_not_shared_design(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    fingerprints = {
        "instance-a": {
            "bbox_mm": [0, 0, 0, 1, 1, 1],
            "is_solid": True,
            "volume_mm3": 1.0,
        },
        "instance-b": {
            "bbox_mm": [0, 0, 0, 2, 2, 2],
            "is_solid": True,
            "volume_mm3": 8.0,
        },
    }
    body_hash = "sha256:" + hashlib.sha256(
        json.dumps(
            fingerprints["instance-b"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    _write_status(
        workspace,
        links=[
            _link(instanceId="instance-a"),
            _link(
                instanceId="instance-b",
                localBodyState="modified",
                bodyFingerprintHash=body_hash,
            ),
        ],
    )
    returned = workspace / "speaker.wgreturn"
    returned.mkdir()
    (returned / "wgreturn.json").write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "instance_id": instance_id,
                        "design_id": "wgd_tritonia",
                        "body_evidence": {"observed_fingerprint": fingerprint},
                    }
                    for instance_id, fingerprint in fingerprints.items()
                ]
            }
        ),
        encoding="utf-8",
    )

    status = _read(
        workspace, instance_id="instance-b", returned_bundle=returned
    )

    assert status["fusionChangesAvailable"] is False
    assert status["link"]["instanceId"] == "instance-b"


def test_parameter_drift_names_are_validated_and_define_the_aggregate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link(
        parameterDriftCount=99,
        driftedParameters=[
            "wg_tritonia_v_mouth_w",
            "wg_tritonia_v_depth",
            "wg_tritonia_v_depth",
        ],
    )])

    status = _read(workspace)

    assert status["state"] == "stale"
    assert status["link"]["driftedParameters"] == [
        "wg_tritonia_v_depth",
        "wg_tritonia_v_mouth_w",
    ]
    assert status["link"]["parameterDriftCount"] == 2


@pytest.mark.parametrize("invalid", ["wg_depth", ["wg_depth", None], ["", "wg_depth"]])
def test_invalid_parameter_drift_names_fall_back_to_the_legacy_count(
    tmp_path: Path, invalid: object,
) -> None:
    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link(
        parameterDriftCount=1,
        driftedParameters=invalid,
    )])

    link = _read(workspace)["link"]

    assert link["parameterDriftCount"] == 1
    assert "driftedParameters" not in link


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
    unchanged = _read(workspace, returned_bundle=returned)
    assert unchanged["fusionChangesAvailable"] is False
    assert unchanged["documentChanged"] is False
    assert unchanged["documentChangeDetectable"] is True
    assert unchanged["staleDetectionExplanation"] is None

    _write_status(workspace, links=[_link(
        documentSignatureHash="sha256:return-with-mids",
        documentBodyCount=3,
        sourceStateHash="sha256:source-with-mf",
    )])
    changed = _read(workspace, returned_bundle=returned)
    assert changed["state"] == "stale"
    assert changed["fusionChangesAvailable"] is True
    assert changed["documentChanged"] is True
    assert changed["documentChangeDetectable"] is True


def test_hashless_return_reports_document_staleness_as_undetectable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    returned = workspace / "legacy.wgreturn"
    returned.mkdir(parents=True)
    (returned / "wgreturn.json").write_text(json.dumps({
        "wgreturn_version": "1.0",
        "assembly": {"n_bodies_expected": 1},
        "instances": [],
        "sources": [],
    }))
    _write_status(
        workspace,
        links=[_link(documentSignatureHash="sha256:current-document")],
    )

    status = _read(workspace, returned_bundle=returned)

    assert status["documentChanged"] is False
    assert status["documentChangeDetectable"] is False
    assert status["staleDetectionExplanation"] == (
        "stale detection unavailable: this returned bundle predates wgreturn 1.1 "
        "and carries no document signature"
    )


def test_an_unsaved_design_can_match_a_fusion_link_by_exact_hash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link()])
    assert _read(workspace, design_id=None)["state"] == "current"


def test_status_endpoint_hashes_the_design_and_reports_wglink_folder_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("server.cadlink.api.fusion_process_running", lambda: False)
    app = create_app(data_dir=tmp_path / "data")
    payload = FusionStatusRequest.model_validate(
        {"design": {"formula": "OSSE", "L": 120, "a": 45}}
    )
    missing = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert missing["cadFolderConfigured"] is False
    assert missing["cadFolderPath"] is None

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app.state.cad_workspace.select(workspace)
    response = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert response == {
        "cadApplication": "fusion360",
        "cadFolderConfigured": True,
        "cadFolderPath": str(workspace.resolve()),
        "cadConnectionIssue": None,
        "state": "closed",
        "running": False,
        "processRunning": False,
        "adapterVersion": None,
        "workspaceRoot": None,
        "updatedAt": None,
        "documentName": None,
        "documentId": None,
        "currentFormula": "OSSE",
        "fusionFormula": None,
        "link": None,
        "matchingLinks": [],
        "selectedInstanceId": None,
        "wgChangesAvailable": False,
        "fusionChangesAvailable": False,
        "documentChanged": False,
        "documentChangeDetectable": False,
        # No link is selected in a closed status, so there is no measured half
        # to place on a revision -- distinct from "unknown", which is what an
        # add-in that publishes neither revision token leaves behind.
        "observationFreshness": None,
        "staleDetectionExplanation": None,
        "addinDeliveryVersion": None,
        "recoveryRequired": None,
        "heartbeatTransport": None,
        "realizedDimensions": {
            "state": "link_unavailable",
            "instanceId": None,
            "exportId": None,
            "parameters": [],
        },
    }


def test_returns_and_status_share_one_cached_manifest_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink import api as cadlink_api

    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    returned = workspace / "wgreturn" / "speaker.wgreturn"
    returned.mkdir(parents=True)
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": 45})
    current_hash = design_hash(design)
    (returned / "wgreturn.json").write_text(
        json.dumps(
            {
                "document": {"name": "Speaker"},
                "assembly": {"signature_hash": "sha256:document", "n_bodies_expected": 1},
                "scope": {"selection": "root"},
                "instances": [
                    {
                        "instance_id": "instance-a",
                        "design_id": TARGET_DESIGN_ID,
                        "body_evidence": {"observed_fingerprint": {"volume": 1.0}},
                    }
                ],
                "sources": [],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(data_dir=data_dir)
    app.state.cad_workspace.select(workspace)
    _write_status(
        data_dir,
        links=[
            _link(
                designId=TARGET_DESIGN_ID,
                designHash=current_hash,
                bodyFingerprintHash="sha256:old",
                documentSignatureHash="sha256:document",
                documentBodyCount=1,
            )
        ],
        updated_at=datetime.now(timezone.utc),
        adapter_version="1.2.3",
        workspace_root=workspace,
    )
    original_parse = cadlink_api._parse_return_manifest
    parses = 0

    def counted_parse(path: Path):
        nonlocal parses
        parses += 1
        return original_parse(path)

    monkeypatch.setattr(cadlink_api, "_parse_return_manifest", counted_parse)
    monkeypatch.setattr(cadlink_api, "fusion_process_running", lambda: False)
    payload = FusionStatusRequest.model_validate(
        {
            "design": design.model_dump(mode="json"),
            "identity": {
                "designId": TARGET_DESIGN_ID,
                "lineageId": TARGET_LINEAGE_ID,
                "baseEditVersion": 1,
            },
            "instanceId": "instance-a",
            "returnBundlePath": "wgreturn/speaker.wgreturn",
        }
    )

    inventory = asyncio.run(cadlink_api.list_returns(SimpleNamespace(app=app)))
    status = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    unchanged = asyncio.run(cadlink_api.list_returns(SimpleNamespace(app=app)))

    assert inventory == unchanged
    assert status["selectedInstanceId"] == "instance-a"
    assert parses == 1


def test_status_resolution_with_500_returns_keeps_event_loop_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.cadlink import api as cadlink_api

    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    for index in range(500):
        returned = workspace / "wgreturn" / f"speaker-{index:03d}.wgreturn"
        returned.mkdir(parents=True)
        (returned / "wgreturn.json").write_text(
            json.dumps(
                {
                    "document": {"name": f"Speaker {index}"},
                    "instances": [
                        {
                            "instance_id": f"instance-{index}",
                            "design_id": TARGET_DESIGN_ID if index == 499 else f"wgd_{index}",
                        }
                    ],
                    "sources": [],
                }
            ),
            encoding="utf-8",
        )
    app = create_app(data_dir=data_dir)
    app.state.cad_workspace.select(workspace)
    original_parse = cadlink_api._parse_return_manifest

    parse_threads: set[str] = set()
    parse_calls: list[Path] = []

    def measurable_parse(path: Path):
        parse_threads.add(threading.current_thread().name)
        parse_calls.append(path)
        time.sleep(0.0002)
        return original_parse(path)

    monkeypatch.setattr(cadlink_api, "_parse_return_manifest", measurable_parse)
    monkeypatch.setattr(cadlink_api, "fusion_process_running", lambda: False)
    payload = FusionStatusRequest.model_validate(
        {
            "design": {"formula": "OSSE", "L": 120, "a": 45},
            "identity": {
                "designId": TARGET_DESIGN_ID,
                "lineageId": TARGET_LINEAGE_ID,
                "baseEditVersion": 1,
            },
            "instanceId": "instance-499",
        }
    )

    async def exercise() -> tuple[dict[str, object], float]:
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            previous = asyncio.get_running_loop().time()
            while not stop.is_set():
                await asyncio.sleep(0.005)
                current = asyncio.get_running_loop().time()
                gaps.append(current - previous)
                previous = current

        ticker_task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        try:
            response = await fusion_status(payload, SimpleNamespace(app=app))
        finally:
            stop.set()
            await ticker_task
        return response, max(gaps)

    status, largest_gap = asyncio.run(exercise())

    assert status["cadFolderConfigured"] is True
    # The regression this guards is 33 ms of scanning and reparsing on the loop
    # at 500 bundles. That is smaller than the scheduling jitter of a shared CI
    # runner -- this ticker has been measured at 94 ms there with the loop never
    # blocked -- so the wall clock cannot be the assertion. Assert instead that
    # resolution ran off the loop thread and that the selected manifest was
    # parsed once rather than five times, which is what the fix actually did.
    assert parse_threads and "MainThread" not in parse_threads
    selected = [path for path in parse_calls if path.parent.name.endswith("-499")]
    assert len(selected) <= 1, f"selected manifest parsed {len(selected)} times"
    assert largest_gap < 1.0


def test_status_endpoint_reports_old_addins_and_folder_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("server.cadlink.api.fusion_process_running", lambda: False)
    data_dir = tmp_path / "data"
    selected = tmp_path / "selected"
    selected.mkdir()
    app = create_app(data_dir=data_dir)
    app.state.cad_workspace.select(selected)
    payload = FusionStatusRequest.model_validate(
        {"design": {"formula": "OSSE", "L": 120, "a": 45}}
    )
    updated_at = datetime.now(timezone.utc)

    _write_status(data_dir, document=False, updated_at=updated_at)
    old_addin = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert old_addin["cadConnectionIssue"] == "addin_upgrade_required"

    _write_status(
        data_dir,
        document=False,
        updated_at=updated_at,
        adapter_version="1.2.3",
    )
    unavailable = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert unavailable["cadConnectionIssue"] == "folder_unreadable"

    other = tmp_path / "other"
    other.mkdir()
    _write_status(
        data_dir,
        document=False,
        updated_at=updated_at,
        adapter_version="1.2.3",
        workspace_root=other,
    )
    mismatch = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert mismatch["cadConnectionIssue"] == "folder_mismatch"

    _write_status(
        data_dir,
        document=False,
        updated_at=updated_at,
        adapter_version="1.2.3",
        workspace_root=selected,
    )
    connected = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))
    assert connected["cadConnectionIssue"] is None


def test_status_reads_role_preserving_parameters_from_the_linked_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app.state.cad_workspace.select(workspace)
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": 45})
    identity, exported = _registered_export(
        app.state.cadlink_store,
        design,
        {
            "parameters": [
                {
                    "name": "wg_tritonia_v_throat_dia",
                    "value": 25.4,
                    "unit": "mm",
                    "role": "interface",
                },
                {
                    "name": "wg_tritonia_v_coverage_h",
                    "value": 90,
                    "role": "informational",
                },
            ]
        },
        key="captured",
    )
    link = _link(
        designId=identity.design_id,
        exportId=exported["export_id"],
        designHash=design_hash(design),
    )
    monkeypatch.setattr("server.cadlink.api.fusion_process_running", lambda: False)
    monkeypatch.setattr(
        "server.cadlink.api.read_fusion_status",
        lambda *_args, **_kwargs: {
            "cadApplication": "fusion360",
            "state": "current",
            "running": True,
            "processRunning": True,
            "updatedAt": "2026-08-13T12:00:00Z",
            "documentName": "Tritonia V",
            "documentId": "fusion:doc-a",
            "currentFormula": "OSSE",
            "fusionFormula": "OSSE",
            "link": link,
            "wgChangesAvailable": False,
            "fusionChangesAvailable": False,
        },
    )
    payload = FusionStatusRequest.model_validate(
        {
            "design": design.model_dump(mode="json"),
            "identity": {
                "designId": identity.design_id,
                "lineageId": identity.lineage_id,
                "baseEditVersion": identity.edit_version,
            },
        }
    )

    response = asyncio.run(fusion_status(payload, SimpleNamespace(app=app)))

    assert response["realizedDimensions"] == {
        "state": "current",
        "instanceId": "instance-a",
        "exportId": exported["export_id"],
        "parameters": [
            {
                "instanceId": "instance-a",
                "name": "wg_tritonia_v_throat_dia",
                "value": 25.4,
                "unit": "mm",
                "role": "interface",
            },
            {
                "instanceId": "instance-a",
                "name": "wg_tritonia_v_coverage_h",
                "value": 90.0,
                "unit": None,
                "role": "informational",
            },
        ],
    }


def test_realized_parameter_absent_and_stale_states_are_explicit(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    design = DesignConfig.model_validate({"formula": "OSSE", "L": 120, "a": 45})
    _identity, exported = _registered_export(store, design, {}, key="old-bundle")
    base_status = {"link": _link(exportId=exported["export_id"])}

    not_captured = _realized_dimensions_payload(
        base_status, store, current_design_hash=design_hash(design)
    )
    missing = _realized_dimensions_payload(
        {"link": _link(exportId="wge_registry_row_was_removed")},
        store,
        current_design_hash=design_hash(design),
    )
    captured = store.allocate_export(
        design_id=exported["design_id"],
        geometry_hash="sha256:geometry-new",
        artifact_sha256="sha256:artifact-new",
        manifest_json=json.dumps(
            {
                "parameters": [
                    {
                        "name": "wg_tritonia_v_depth",
                        "value": 190,
                        "unit": "mm",
                        "role": "interface",
                    }
                ]
            }
        ),
        idempotency_key="captured-old-design",
    )
    stale = _realized_dimensions_payload(
        {"link": _link(exportId=captured["export_id"])},
        store,
        current_design_hash="sha256:newer-design-on-screen",
    )

    assert not_captured["state"] == "not_captured"
    assert missing["state"] == "export_missing"
    assert stale["state"] == "stale"
    assert stale["parameters"][0]["value"] == 190.0
    assert _realized_dimensions_payload(
        {"state": "not_linked", "link": None},
        store,
        current_design_hash=design_hash(design),
    )["state"] == "no_link"


@pytest.mark.parametrize("selected", ["instance-b", "missing"])
def test_return_request_resolves_the_selected_same_design_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str,
) -> None:
    from fastapi import HTTPException
    from server.cadlink.api import FusionReturnRequest, request_fusion_return
    from server.cadlink.fusion_return import RETURN_REQUESTS_DIRECTORY

    _write_status(tmp_path, updated_at=datetime.now(timezone.utc), links=[
        _link(instanceId="instance-a"), _link(instanceId="instance-b"),
    ])
    monkeypatch.setattr("server.cadlink.api.fusion_process_running", lambda: True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(data_dir=tmp_path)))
    payload = FusionReturnRequest(
        designId="wgd_tritonia", documentId="fusion:doc-a", instanceId=selected,
        expectedReturnStateHash="sha256:state",
    )
    folder = tmp_path / "ipc" / "wglink" / RETURN_REQUESTS_DIRECTORY
    if selected == "missing":
        with pytest.raises(HTTPException) as error:
            asyncio.run(request_fusion_return(payload, request))
        assert error.value.status_code == 409
        assert not folder.exists() or list(folder.iterdir()) == []
    else:
        result = asyncio.run(request_fusion_return(payload, request))
        published = json.loads((folder / f"{result['requestId']}.json").read_text())
        assert published["instanceId"] == "instance-b"
        assert published["requestId"] == result["requestId"]
        assert published["expectedReturnStateHash"] == "sha256:state"


@pytest.mark.parametrize("version", [None, 2, True], ids=["unreported", "version-2", "bool"])
def test_an_addin_below_wgs_delivery_version_is_outdated_and_read_no_further(
    tmp_path: Path, version: object
) -> None:
    """There is no route to an older add-in (CAD-OPERATIONS.md, "Delivery version").

    Nothing it reports about the document is used: the state is only the
    prompt to load the add-in WG installed.
    """

    _write_status(tmp_path, links=[], delivery_version=version)  # type: ignore[arg-type]

    status = read_fusion_status(
        tmp_path, current_design_hash="", current_formula="", design_id=TARGET_DESIGN_ID,
        now=NOW,
    )

    assert status["state"] == "addin_outdated"
    assert status["running"] is True
    assert status["documentId"] is None and status["link"] is None
    assert status["addinDeliveryVersion"] == (2 if version == 2 else None)


def test_an_interrupted_operation_in_the_heartbeat_is_reported_as_recovery_required(
    tmp_path: Path,
) -> None:
    _write_status(
        tmp_path,
        links=[],
        applying_operation={
            "operationId": "req-9", "kind": "update",
            "instanceId": "instance-a", "exportId": "wge_4", "phase": "applied",
        },
    )

    status = read_fusion_status(
        tmp_path, current_design_hash="", current_formula="", design_id=TARGET_DESIGN_ID,
        now=NOW,
    )

    assert status["recoveryRequired"] == {
        "operationId": "req-9", "kind": "update",
        "instanceId": "instance-a", "exportId": "wge_4", "phase": "applied",
    }
    _write_status(tmp_path, links=[])
    assert read_fusion_status(
        tmp_path, current_design_hash="", current_formula="", design_id=TARGET_DESIGN_ID,
        now=NOW,
    )["recoveryRequired"] is None


@pytest.mark.parametrize("case", ["outdated-addin", "no-baseline"])
def test_a_return_request_is_refused_before_anything_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    """An older add-in gets nothing; nor does a request with no baseline to prove."""

    from fastapi import HTTPException
    from server.cadlink.api import FusionReturnRequest, request_fusion_return
    from server.cadlink.fusion_status import ADDIN_OUTDATED_MESSAGE

    _write_status(
        tmp_path,
        updated_at=datetime.now(timezone.utc),
        links=[_link(instanceId="instance-a")],
        delivery_version=2 if case == "outdated-addin" else 3,
    )
    monkeypatch.setattr("server.cadlink.api.fusion_process_running", lambda: True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(data_dir=tmp_path)))
    payload = FusionReturnRequest(
        designId="wgd_tritonia", documentId="fusion:doc-a", instanceId="instance-a",
        expectedReturnStateHash=None if case == "no-baseline" else "sha256:state",
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(request_fusion_return(payload, request))

    assert error.value.status_code == 409
    if case == "outdated-addin":
        assert error.value.detail == ADDIN_OUTDATED_MESSAGE
    else:
        assert "has not reported the model's state" in error.value.detail
    assert not (tmp_path / "ipc" / "wglink" / ".fusion-return-requests").exists()


# --- CL13: which revision the heartbeat's measured half describes -----------
#
# WGLink's periodic heartbeat inspects no geometry: it publishes identity from
# stored attributes and, for the measured half, whatever a previous measurement
# left in its cache (fusion-addins/WGLink/README.md, "The heartbeat reads cached
# state only"). ``geometryRevisionToken`` is the revision the document is at now
# and ``measuredRevisionToken`` the revision that cache was filled at, so the two
# together are the only thing that says whether the measured half is current.
# Without them WG compared a cached hash against a returned one and read
# "equal" as "unchanged", which reports a moved document as up to date.


def _returned_root(folder: Path, *, signature: str, bodies: int = 1) -> Path:
    """A minimal root-scope return carrying a document signature to compare."""

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "wgreturn.json").write_text(json.dumps({
        "wgreturn_version": "1.1",
        "scope": {"selection": "root"},
        "assembly": {"n_bodies_expected": bodies, "signature_hash": signature},
        "instances": [],
        "sources": [],
    }))
    return folder


def _measured_link(**updates: object) -> dict[str, object]:
    """A link whose measured half was taken at the revision it is published at."""

    link = _link(
        localBodyState="unmodified",
        documentSignatureHash="sha256:document-a",
        documentBodyCount=1,
        geometryRevisionToken="rev-1",
        measuredRevisionToken="rev-1",
    )
    link.update(updates)
    return link


def test_a_document_that_moved_since_its_measurement_is_not_reported_current(
    tmp_path: Path,
) -> None:
    """The cached hash equals the returned one, and that proves nothing.

    Every comparable field is byte-for-byte what a current document would
    publish. The only difference is that the document is at ``rev-2`` and the
    measurement was taken at ``rev-1``, so WG holds an observation of a revision
    Fusion has already left. Reporting ``current`` here is a claim about a
    measurement that did not occur.
    """

    workspace = tmp_path / "workspace"
    returned = _returned_root(workspace / "speaker.wgreturn", signature="sha256:document-a")

    _write_status(workspace, links=[_measured_link()])
    current = _read(workspace, returned_bundle=returned)
    assert current["state"] == "current"
    assert current["documentChangeDetectable"] is True

    _write_status(workspace, links=[_measured_link(geometryRevisionToken="rev-2")])
    moved = _read(workspace, returned_bundle=returned)

    # The defect, first: today this reads "current".
    assert moved["state"] != "current"
    # Not "compared and equal": nothing current was compared at all.
    assert moved["documentChangeDetectable"] is False
    assert moved["staleDetectionExplanation"] is not None
    assert "measured" in moved["staleDetectionExplanation"]
    assert current["observationFreshness"] == "current"
    assert moved["observationFreshness"] == "stale"


def test_an_uncomputable_revision_token_is_an_earlier_observation_too(
    tmp_path: Path,
) -> None:
    """A null ``geometryRevisionToken`` means the revision could not be keyed.

    WGLink publishes it as null when ``_geometry_change_key`` raised, so there is
    nothing to compare the measurement's own token against. That is the same
    answer as an unequal pair: the measured half may describe an earlier
    revision, and WG may not say otherwise.
    """

    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_measured_link(geometryRevisionToken=None)])

    status = _read(workspace)

    assert status["state"] != "current"
    assert status["documentChangeDetectable"] is False
    assert status["observationFreshness"] == "stale"


def test_no_measurement_at_all_is_distinct_from_an_earlier_one(tmp_path: Path) -> None:
    """Three states, not two: none, earlier, current.

    An empty ``documentSignatureHash`` with ``localBodyState: "unknown"`` is what
    a restart or a switch to an unmeasured document publishes. It is never
    another document's measurement and never a claim of a fresh one, so it reads
    from the hashes rather than the tokens -- a null ``measuredRevisionToken``
    accompanies it, and must not be mistaken for a stale observation of
    something.
    """

    workspace = tmp_path / "workspace"
    returned = _returned_root(workspace / "speaker.wgreturn", signature="sha256:document-a")
    _write_status(workspace, links=[_link(
        localBodyState="unknown",
        documentSignatureHash=None,
        documentBodyCount=0,
        sourceStateHash=None,
        geometryRevisionToken="rev-7",
        measuredRevisionToken=None,
    )])

    status = _read(workspace, returned_bundle=returned)

    assert status["state"] != "current"
    assert status["documentChangeDetectable"] is False
    # The existing honesty valve now says why, instead of reporting an
    # undetectable comparison with no explanation at all.
    assert status["staleDetectionExplanation"] is not None
    assert "not measured" in status["staleDetectionExplanation"]
    assert status["observationFreshness"] == "none"


def test_both_revision_tokens_reach_the_link_payload(tmp_path: Path) -> None:
    """``_link_payload`` is an allow-list, and it dropped both names."""

    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_measured_link(
        geometryRevisionToken="rev-9", measuredRevisionToken="rev-8",
    )])

    link = _read(workspace)["link"]

    assert link["geometryRevisionToken"] == "rev-9"
    assert link["measuredRevisionToken"] == "rev-8"


def test_an_addin_that_sends_neither_token_behaves_exactly_as_today(
    tmp_path: Path,
) -> None:
    """Both fields are additive under heartbeat schema 1.

    An older WGLink never publishes either name. Omitting the member rather than
    defaulting it keeps "never sent" distinguishable from "sent as null", the way
    ``driftedParameters`` already is -- and every other member of the answer has
    to read exactly as it did before this change.
    """

    workspace = tmp_path / "workspace"
    returned = _returned_root(workspace / "speaker.wgreturn", signature="sha256:document-a")

    legacy_link = _link(
        localBodyState="unmodified",
        documentSignatureHash="sha256:document-a",
        documentBodyCount=1,
    )
    assert "geometryRevisionToken" not in legacy_link
    assert "measuredRevisionToken" not in legacy_link
    _write_status(workspace, links=[legacy_link])
    legacy = _read(workspace, returned_bundle=returned)

    _write_status(workspace, links=[_measured_link()])
    tokened = _read(workspace, returned_bundle=returned)

    # Nothing is invented for an add-in that said nothing.
    assert "geometryRevisionToken" not in legacy["link"]
    assert "measuredRevisionToken" not in legacy["link"]
    assert legacy["observationFreshness"] == "unknown"
    assert legacy["state"] == "current"
    assert legacy["documentChangeDetectable"] is True
    assert legacy["staleDetectionExplanation"] is None
    # And the whole answer matches the one a current measurement produces,
    # member for member, apart from the two new names themselves.
    assert {
        name: value for name, value in legacy.items()
        if name not in {"link", "matchingLinks", "observationFreshness"}
    } == {
        name: value for name, value in tokened.items()
        if name not in {"link", "matchingLinks", "observationFreshness"}
    }


def test_evidence_of_a_change_survives_an_earlier_observation(tmp_path: Path) -> None:
    """Only the absence of evidence is withdrawn, never the evidence.

    ``documentChanged`` and ``fusionChangesAvailable`` stay true across a move
    of the revision. Not because a difference cannot stop being one -- undo the
    edit back to the returned state and this observation still reports a
    difference the document no longer has -- but because that is the
    conservative direction, and WGLink re-measures inline before any guarded
    mutation. What may not stand is the narrower claim that the comparison was
    made against the document as it is now.
    """

    workspace = tmp_path / "workspace"
    returned = _returned_root(workspace / "speaker.wgreturn", signature="sha256:returned")
    _write_status(workspace, links=[_measured_link(
        documentSignatureHash="sha256:moved-on", geometryRevisionToken="rev-2",
    )])

    status = _read(workspace, returned_bundle=returned)

    assert status["documentChanged"] is True
    assert status["fusionChangesAvailable"] is True
    assert status["observationFreshness"] == "stale"
    assert status["documentChangeDetectable"] is False


def test_no_selected_link_reports_no_observation_freshness(tmp_path: Path) -> None:
    """The field describes the selected link's measured half, and there is none."""

    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[])

    assert _read(workspace)["observationFreshness"] is None


def test_observation_freshness_names_agree_with_the_frontend() -> None:
    """One state machine, spelled once on each side.

    The server decides the freshness and the frontend decides what to show for
    it, so a name added on one side and not the other is a silent fall-through
    to whatever the other side's default happens to be. This is the mechanical
    check that the two lists are the same list; the behavioural agreement is in
    the tests above and in ``CadLinkPanel.test.tsx``.
    """

    from server.cadlink import fusion_status as module

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "api" / "cadlink.ts"
    ).read_text(encoding="utf-8")
    declaration = re.search(
        r"export type CadObservationFreshness\s*=\s*([^;]+);", source
    )
    assert declaration is not None, "the frontend no longer declares the union"
    declared = set(re.findall(r"'([a-z_-]+)'", declaration.group(1)))

    assert declared == {
        module.OBSERVATION_CURRENT,
        module.OBSERVATION_STALE,
        module.OBSERVATION_NONE,
        module.OBSERVATION_UNKNOWN,
    }


def test_an_unreadable_signature_with_a_measured_body_is_an_earlier_observation(
    tmp_path: Path,
) -> None:
    """No measurement at all is both hashes, not the signature alone.

    WGLink builds a link's body state and its document signature separately: the
    signature comes from one root-scope read that may raise, and the per-body
    state from the records beside it. So an empty ``documentSignatureHash`` next
    to a measured ``localBodyState`` is a real heartbeat -- a measurement was
    taken, and only its document half is missing. Reading the empty signature on
    its own would report "WGLink has not measured this document yet" over an
    observation that exists and is out of date, which sends the user looking for
    a first measurement instead of a fresh one.
    """

    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link(
        localBodyState="modified",
        documentSignatureHash=None,
        geometryRevisionToken="rev-2",
        measuredRevisionToken="rev-1",
    )])

    status = _read(workspace)

    assert status["observationFreshness"] == "stale"
    assert status["state"] != "current"
    assert status["documentChangeDetectable"] is False
    assert status["staleDetectionExplanation"] is not None
    assert "has moved since" in status["staleDetectionExplanation"]


def test_non_string_revision_tokens_are_never_compared_as_equal(tmp_path: Path) -> None:
    """``_link_payload`` is the trust boundary, and it coerces both tokens.

    The heartbeat is a JSON file on disk and an HTTP post: nothing guarantees a
    token arrives as a string. Two equal numbers compared directly would say
    "measured at the revision we are at now" and license ``current`` -- a
    measurement claim built on a type the add-in never promised. Coerced, a
    non-string token is no token, which is the same answer as a revision that
    could not be keyed.
    """

    workspace = tmp_path / "workspace"
    _write_status(workspace, links=[_link(
        localBodyState="unmodified",
        documentSignatureHash="sha256:document-a",
        documentBodyCount=1,
        geometryRevisionToken=5,
        measuredRevisionToken=5,
    )])

    status = _read(workspace)

    assert status["observationFreshness"] == "stale"
    assert status["state"] != "current"
    assert status["documentChangeDetectable"] is False
    assert status["link"]["geometryRevisionToken"] is None
    assert status["link"]["measuredRevisionToken"] is None
