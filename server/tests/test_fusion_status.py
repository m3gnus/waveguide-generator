"""Fusion presence and linked-design freshness contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
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
                "workspaceRoot": str(workspace_root) if workspace_root is not None else None,
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
        "staleDetectionExplanation": None,
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
