"""Freshness, consistency, persistence, and endpoint transport contracts."""

from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import math
import os
from pathlib import Path
from types import SimpleNamespace
import asyncio

import pytest
import numpy as np
from fastapi import HTTPException
from pydantic import ValidationError

from server.app import create_app
from server.cadlink.api import (
    CadReturnIngestRequest,
    get_ingest,
    get_ingest_viewport_mesh,
    list_returns,
    post_ingest,
)
from server.cadlink.ingest import IngestRefusal, _canonical, compute_freshness, evaluate_instance_freshness, ingest_bundle, validate_registry_echoes
from server.mesh.imported import ImportedMeshDependencyError, polar_grid_from_symmetry
from server.mesh.artifact import mesh_text_sha256
from server.cadlink.wgreturn import WgReturnBundle
from server.cadlink.store import CadLinkStore
from server.engines.registry import EngineInfo, EngineRegistry
from server.jobs.models import SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.solver import metal


def _fingerprint(volume: float = 1.0):
    return {"is_solid": True, "volume_mm3": volume, "bbox_mm": [0, 0, 0, 1, 1, 1]}


def _instance(**updates):
    value = {"instance_id": "i", "design_id": "d", "export_id": "e", "export_sequence": 1, "design_hash": "dh", "geometry_hash": "gh", "origin_bundle_id": "b", "body_evidence": {"local_body_state": "unmodified", "baseline_fingerprint": _fingerprint(), "observed_fingerprint": _fingerprint()}}
    value.update(updates)
    return value


class _Store:
    def __init__(self, design=None, export=None):
        self.design = design
        self.export = export
    def get_design(self, _design_id):
        return self.design
    def get_export(self, _export_id):
        return self.export


@pytest.mark.parametrize(
    ("instance", "design", "recomputed", "verdict"),
    [
        (_instance(body_evidence={"local_body_state": "modified", "baseline_fingerprint": _fingerprint(), "observed_fingerprint": _fingerprint(2)}), {"design_hash": "dh"}, "gh", "body_modified"),
        (_instance(), None, "gh", "missing_design"),
        (_instance(), {"design_hash": "other"}, "gh", "design_changed"),
        (_instance(), {"design_hash": "dh"}, "other", "generator_changed"),
        (_instance(), {"design_hash": "dh"}, "gh", "current"),
    ],
)
def test_freshness_precedence_rows(instance, design, recomputed, verdict) -> None:
    result = evaluate_instance_freshness(instance, _Store(design), recompute=lambda _head: recomputed)
    assert result["verdict"] == verdict
    assert (result["finding_id"] is None) == (verdict == "current")


def test_freshness_recompute_failure_is_unknown_and_empty_instances_are_unlinked() -> None:
    result = evaluate_instance_freshness(_instance(), _Store({"design_hash": "dh"}), recompute=lambda _head: (_ for _ in ()).throw(RuntimeError("boom")))
    assert result["verdict"] == "unknown"
    assert "boom" in result["error"]
    assert compute_freshness({"instances": []}, _Store())["verdict"] == "unlinked"


def test_freshness_hash_rows_precede_unreadable_body_evidence() -> None:
    unreadable = _instance(
        body_evidence={
            "local_body_state": "unmodified",
            "baseline_fingerprint": None,
            "observed_fingerprint": _fingerprint(),
        }
    )
    changed = evaluate_instance_freshness(
        unreadable, _Store({"design_hash": "dh"}), recompute=lambda _head: "other"
    )
    assert changed["verdict"] == "generator_changed"

    unknown = evaluate_instance_freshness(
        unreadable, _Store({"design_hash": "dh"}), recompute=lambda _head: "gh"
    )
    assert unknown["verdict"] == "unknown"


def test_freshness_row_one_and_row_three_keep_precedence_over_other_errors() -> None:
    contradictory = _instance(
        body_evidence={
            "local_body_state": "unmodified",
            "baseline_fingerprint": _fingerprint(),
            "observed_fingerprint": _fingerprint(2),
        }
    )
    row_one = evaluate_instance_freshness(
        contradictory,
        _Store({"design_hash": "other"}),
        recompute=lambda _head: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert row_one["verdict"] == "body_modified"

    row_three = evaluate_instance_freshness(
        _instance(),
        _Store({"design_hash": "other"}),
        recompute=lambda _head: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert row_three["verdict"] == "design_changed"


def test_freshness_null_design_hash_is_unknown_when_no_later_row_matches() -> None:
    result = evaluate_instance_freshness(
        _instance(design_hash=None),
        _Store({"design_hash": "dh"}),
        recompute=lambda _head: "gh",
    )
    assert result["verdict"] == "unknown"


def test_consistency_contradiction_is_corruption() -> None:
    row = {"design_id": "other", "sequence": 1, "design_hash": "dh", "geometry_hash": "gh", "bundle_id": "b"}
    with pytest.raises(IngestRefusal) as caught:
        validate_registry_echoes({"instances": [_instance()]}, _Store(export=row))
    assert caught.value.corruption is True


def test_ingest_store_round_trip(tmp_path: Path) -> None:
    store = CadLinkStore(tmp_path / "cadlink.db")
    row = store.allocate_ingest(manifest_sha256="sha256:m", artifact_sha256="sha256:a", record_builder=lambda ingest_id, created: json.dumps({"ingest_id": ingest_id, "created_at": created}))
    assert row["ingest_id"].startswith("wgi_")
    assert store.get_ingest(row["ingest_id"]) == row


def test_endpoint_validates_workspace_and_returns_pipeline_record(monkeypatch, tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data")
    workspace = tmp_path / "workspace"
    bundle = workspace / "wgreturn" / "speaker.wgreturn"
    bundle.mkdir(parents=True)
    app.state.cad_workspace.select(workspace)
    expected = {"ingest_id": "wgi_01J5A8QK3M9T2XVBH0RD7NWE6C", "report_sha256": "sha256:test", "findings": []}
    monkeypatch.setattr("server.cadlink.api.ingest_bundle", lambda *_args, **_kwargs: expected)
    payload = CadReturnIngestRequest.model_validate({"bundlePath": "wgreturn/speaker.wgreturn", "mesh": {"rigidSizeMm": 20, "transitionMm": 40, "sourceSizeMm": {"source-hf": 4}}, "skippedSourceIds": [], "areaDriftOverrides": ["source-hf"]})
    response = asyncio.run(post_ingest(payload, SimpleNamespace(app=app)))
    assert response == expected
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CadReturnIngestRequest.model_validate({"bundlePath": "wgreturn/speaker.wgreturn", "mesh": {"rigidSizeMm": 20, "transitionMm": 40, "sourceSizeMm": {}}, "unexpected": True})


def test_return_listing_reads_cheap_inventory_and_marks_bad_manifests(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data")
    workspace = tmp_path / "workspace"
    good = workspace / "wgreturn" / "speaker.wgreturn"
    bad = workspace / "wgreturn" / "broken.wgreturn"
    good.mkdir(parents=True)
    bad.mkdir()
    (good / "wgreturn.json").write_text(
        json.dumps(
            {
                "document": {"name": "Speaker v4", "request_id": "request-a"},
                "instances": [{"instance_id": "instance-a"}],
                "sources": [
                    {
                        "id": "source-hf",
                        "role": "HF",
                        "required": True,
                        "suggested_resolution_mm": 3.5,
                        "default_drive_channel_id": "drive-hf",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (bad / "wgreturn.json").write_text("{not json", encoding="utf-8")
    os.utime(good, (200, 200))
    os.utime(bad, (100, 100))
    app.state.cad_workspace.select(workspace)

    result = asyncio.run(list_returns(SimpleNamespace(app=app)))

    assert [item["name"] for item in result["items"]] == [
        "speaker.wgreturn",
        "broken.wgreturn",
    ]
    assert result["items"][1]["readable"] is False
    assert result["items"][1]["reason"]
    assert result["items"][0] == {
        "name": "speaker.wgreturn",
        "bundlePath": "wgreturn/speaker.wgreturn",
        "modifiedAt": result["items"][0]["modifiedAt"],
        "readable": True,
        "documentName": "Speaker v4",
        "requestId": "request-a",
        "sourceCount": 1,
        "instanceCount": 1,
        "sources": [
            {
                "id": "source-hf",
                "role": "HF",
                "required": True,
                "suggestedResolutionMm": 3.5,
                "defaultDriveChannelId": "drive-hf",
            }
        ],
    }


def test_return_listing_rejects_escaping_symlinks_and_plain_files_and_explains_bad_sizes(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path / "data")
    workspace = tmp_path / "workspace"
    returns = workspace / "wgreturn"
    returns.mkdir(parents=True)
    outside = tmp_path / "outside.wgreturn"
    outside.mkdir()
    (returns / "escape.wgreturn").symlink_to(outside, target_is_directory=True)
    (returns / "plain.wgreturn").write_text("not a directory", encoding="utf-8")
    invalid = returns / "invalid-size.wgreturn"
    invalid.mkdir()
    (invalid / "wgreturn.json").write_text(
        json.dumps(
            {
                "document": {"name": "Invalid"},
                "instances": [],
                "sources": [
                    {
                        "id": "source-hf",
                        "role": "HF",
                        "required": True,
                        "suggested_resolution_mm": 0,
                        "default_drive_channel_id": "drive-hf",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app.state.cad_workspace.select(workspace)

    result = asyncio.run(list_returns(SimpleNamespace(app=app)))

    assert [item["name"] for item in result["items"]] == ["invalid-size.wgreturn"]
    assert result["items"][0]["readable"] is False
    assert "positive" in result["items"][0]["reason"]


def test_return_listing_reports_an_unconfigured_wglink_folder(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data")
    result = asyncio.run(list_returns(SimpleNamespace(app=app)))
    assert result == {"items": [], "cadFolderConfigured": False}


def test_endpoint_error_mapping_and_workspace_guards(monkeypatch, tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data")
    payload = CadReturnIngestRequest.model_validate(
        {
            "bundlePath": "wgreturn/speaker.wgreturn",
            "mesh": {
                "rigidSizeMm": 20,
                "transitionMm": 40,
                "sourceSizeMm": {"source-hf": 4},
            },
        }
    )
    with pytest.raises(Exception) as no_workspace:
        asyncio.run(post_ingest(payload, SimpleNamespace(app=app)))
    assert no_workspace.value.status_code == 409

    workspace = tmp_path / "workspace"
    (workspace / "wgreturn" / "speaker.wgreturn").mkdir(parents=True)
    app.state.cad_workspace.select(workspace)
    escaped = payload.model_copy(update={"bundle_path": "wgreturn/../outside.wgreturn"})
    with pytest.raises(Exception) as traversal:
        asyncio.run(post_ingest(escaped, SimpleNamespace(app=app)))
    assert traversal.value.status_code == 422

    monkeypatch.setattr(
        "server.cadlink.api.ingest_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IngestRefusal("stage 3 consistency gate", "corrupt", corruption=True)
        ),
    )
    with pytest.raises(Exception) as conflict:
        asyncio.run(post_ingest(payload, SimpleNamespace(app=app)))
    assert conflict.value.status_code == 409

    monkeypatch.setattr(
        "server.cadlink.api.ingest_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IngestRefusal(
                "stage 5 role resolution",
                "source 'source-hf' area drift exceeds 1%",
                area_drift_sources=["source-hf"],
            )
        ),
    )
    with pytest.raises(Exception) as drift:
        asyncio.run(post_ingest(payload, SimpleNamespace(app=app)))
    assert drift.value.status_code == 422
    assert drift.value.detail == {
        "message": "stage 5 role resolution: source 'source-hf' area drift exceeds 1%",
        "area_drift_sources": ["source-hf"],
    }

    monkeypatch.setattr(
        "server.cadlink.api.ingest_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ImportedMeshDependencyError("missing gmsh")
        ),
    )
    with pytest.raises(Exception) as unavailable:
        asyncio.run(post_ingest(payload, SimpleNamespace(app=app)))
    assert unavailable.value.status_code == 503


def test_get_ingest_reads_store_off_event_loop(monkeypatch, tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data")
    ingest_id = "wgi_01J5A8QK3M9T2XVBH0RD7NWE6C"
    expected = {"ingest_id": ingest_id}
    calls = []

    async def fake_to_thread(function, *args):
        calls.append((function, args))
        return expected

    monkeypatch.setattr("server.cadlink.api.asyncio.to_thread", fake_to_thread)
    assert asyncio.run(get_ingest(ingest_id, SimpleNamespace(app=app))) == expected
    assert calls


def test_viewport_mesh_endpoint_distinguishes_absence_and_corruption(
    monkeypatch, tmp_path: Path
) -> None:
    ingest_id = "wgi_01J5A8QK3M9T2XVBH0RD7NWE6C"
    app = SimpleNamespace(state=SimpleNamespace(cadlink_store=object()))
    current_record: dict[str, object] = {"ingest_id": ingest_id}

    async def fake_to_thread(function, *args):
        if function.__name__ == "get_ingestion_record":
            return current_record
        return function(*args)

    monkeypatch.setattr("server.cadlink.api.asyncio.to_thread", fake_to_thread)
    with pytest.raises(HTTPException) as absent:
        asyncio.run(
            get_ingest_viewport_mesh(ingest_id, SimpleNamespace(app=app))
        )
    assert absent.value.status_code == 404

    viewport_path = tmp_path / "viewport.msh"
    viewport_path.write_text("visual", encoding="utf-8")
    current_record = {
        "ingest_id": ingest_id,
        "viewport_mesh": {
            "available": True,
            "store_path": str(viewport_path),
            "content_sha256": mesh_text_sha256("different"),
        },
    }
    with pytest.raises(HTTPException) as corrupt:
        asyncio.run(
            get_ingest_viewport_mesh(ingest_id, SimpleNamespace(app=app))
        )
    assert corrupt.value.status_code == 409

    current_record["viewport_mesh"]["content_sha256"] = mesh_text_sha256("visual")  # type: ignore[index]
    response = asyncio.run(
        get_ingest_viewport_mesh(ingest_id, SimpleNamespace(app=app))
    )
    assert response.body == b"visual"


def test_canonical_json_coerces_numpy_values() -> None:
    assert json.loads(_canonical({"scalar": np.int64(3), "array": np.array([1.5])})) == {
        "array": [1.5],
        "scalar": 3,
    }


def test_ingestion_record_publishes_role_findings_and_numpy_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "fixture.wgreturn"
    bundle_path.mkdir()
    manifest_path = bundle_path / "wgreturn.json"
    assembly_path = bundle_path / "assembly.step"
    manifest_path.write_text("{}", encoding="utf-8")
    assembly_path.write_text("STEP", encoding="utf-8")
    manifest = {
        "return": {"id": "wgr_01J5A8QK3M9T2XVBH0RD7NWE6C"},
        "coordinate_system": {"solver_anchor_instance_id": None},
        "assembly": {"n_bodies_expected": 1},
        "scope": {
            "included": [{}],
            "skipped": [],
            "status": "clean",
            "fem_air_volumes": [],
        },
        "instances": [],
        "sources": [{"id": "source-hf", "role": "HF", "instance_id": None}],
    }
    bundle = WgReturnBundle(
        path=bundle_path,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256="sha256:" + "1" * 64,
        assembly_path=assembly_path,
        artifact_sha256="sha256:" + "2" * 64,
        members={"assembly.step": assembly_path},
    )
    built = {
        "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
        "viewport_msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Comments\nviewport\n$EndComments\n",
        "viewport_mesh": {
            "available": True,
            "stats": {"triangle_count": np.int64(4), "domain": "full"},
            "metadata": {"purpose": "cad-viewport"},
        },
        "transformed_geometry_hash": "sha256:" + "3" * 64,
        "normalisation": {"matrix": np.eye(4)},
        "role_resolution": {"source-hf": {"surfaces": [1]}},
        "role_findings": [
            {"kind": "source-paint-missing", "source_id": "source-hf"}
        ],
        "symmetry": {
            "cut_planes": [],
            "planes": {
                "x0": {"accepted": False},
                "y0": {"accepted": False},
                "z0": {"accepted": False},
            },
        },
        "healing": {"performed": False, "mode": "none"},
        "polar_grid_derivation": polar_grid_from_symmetry(
            {
                "cut_planes": [],
                "planes": {
                    "x0": {"accepted": False},
                    "y0": {"accepted": False},
                    "z0": {"accepted": False},
                },
            }
        ),
        "sizing_estimate": {"triangles": np.int64(1)},
        "tag_allocation": {
            "tag_namespace": "wg-import-v1",
            "tag_map": {
                "1": {"source_id": None, "instance_id": None, "role": "rigid"},
                "101": {
                    "source_id": "source-hf",
                    "instance_id": None,
                    "role": "HF",
                },
            },
            "source_tags": {"source-hf": 101},
        },
        "stats": {"triangle_count": np.int64(1)},
        "metadata": {"numpy_array": np.array([1, 2])},
        "integrity": {"valid": np.bool_(True)},
    }
    monkeypatch.setattr("server.cadlink.ingest.read_wgreturn", lambda _path: bundle)
    monkeypatch.setattr(
        "server.cadlink.ingest.build_imported_mesh", lambda *_args, **_kwargs: built
    )
    record = ingest_bundle(
        bundle_path,
        {
            "rigid_size_mm": 20,
            "transition_mm": 30,
            "source_size_mm": {"source-hf": 8},
        },
        [],
        CadLinkStore(tmp_path / "cadlink.db"),
        tmp_path / "data",
    )
    assert record["role_findings"] == [
        {"kind": "source-paint-missing", "source_id": "source-hf"}
    ]
    assert any(item["kind"] == "source-paint-missing" for item in record["findings"])
    assert record["mesh"]["metadata"]["numpy_array"] == [1, 2]
    assert record["viewport_mesh"]["available"] is True
    assert record["viewport_mesh"]["stats"]["triangle_count"] == 4
    assert Path(record["viewport_mesh"]["store_path"]).read_text(encoding="utf-8").endswith(
        "$EndComments\n"
    )


def test_visual_mesh_failure_is_advisory_and_does_not_create_healing_finding(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "visual-failure.wgreturn"
    bundle_path.mkdir()
    manifest_path = bundle_path / "wgreturn.json"
    assembly_path = bundle_path / "assembly.step"
    manifest_path.write_text("{}", encoding="utf-8")
    assembly_path.write_text("STEP", encoding="utf-8")
    manifest = {
        "return": {"id": "wgr_01J5A8QK3M9T2XVBH0RD7NWE6C"},
        "coordinate_system": {"solver_anchor_instance_id": None},
        "assembly": {"n_bodies_expected": 1},
        "scope": {
            "included": [{}], "skipped": [], "status": "clean", "fem_air_volumes": [],
        },
        "instances": [],
        "sources": [],
    }
    bundle = WgReturnBundle(
        path=bundle_path,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256="sha256:" + "1" * 64,
        assembly_path=assembly_path,
        artifact_sha256="sha256:" + "2" * 64,
        members={"assembly.step": assembly_path},
    )
    symmetry = {
        "cut_planes": [],
        "planes": {axis: {"accepted": False} for axis in ("x0", "y0", "z0")},
    }
    built = {
        "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
        "transformed_geometry_hash": "sha256:" + "3" * 64,
        "normalisation": {"matrix": np.eye(4), "anchor_instance_id": None},
        "role_resolution": {},
        "role_findings": [],
        "symmetry": symmetry,
        "healing": {"performed": False, "mode": "none", "options": []},
        "polar_grid_derivation": polar_grid_from_symmetry(symmetry),
        "sizing_estimate": {"triangles": 1},
        "tag_allocation": {
            "tag_namespace": "wg-import-v1",
            "tag_map": {"1": {"source_id": None, "instance_id": None, "role": "rigid"}},
            "source_tags": {},
        },
        "stats": {"triangle_count": 1},
        "metadata": {},
        "integrity": {"valid": True},
        "viewport_mesh": {"available": False, "reason": "visual tessellation failed"},
    }
    monkeypatch.setattr("server.cadlink.ingest.read_wgreturn", lambda _path: bundle)
    monkeypatch.setattr(
        "server.cadlink.ingest.build_imported_mesh", lambda *_args, **_kwargs: built
    )
    record = ingest_bundle(
        bundle_path,
        {"rigid_size_mm": 20, "transition_mm": 30, "source_size_mm": {}},
        [],
        CadLinkStore(tmp_path / "cadlink.db"),
        tmp_path / "data",
    )
    assert record["viewport_mesh"] == {
        "available": False,
        "reason": "visual tessellation failed",
    }
    assert record["healing"]["performed"] is False
    assert all(item["kind"] != "healing-performed" for item in record["findings"])


def test_occ_ingest_end_to_end_writes_tag_names_reuses_cache_and_solves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmsh = pytest.importorskip("gmsh")
    from hornlab_mesher.cad import write_step
    from hornlab_mesher.geometry import PointGridHornGeometry

    n_phi = 16
    n_length = 6
    inner = np.empty((n_phi, n_length + 1, 3), dtype=float)
    for phi_index in range(n_phi):
        phi = math.tau * phi_index / n_phi
        for length_index in range(n_length + 1):
            fraction = length_index / n_length
            radius = 10.0 + 20.0 * fraction
            inner[phi_index, length_index] = (
                radius * math.cos(phi),
                radius * math.sin(phi),
                60.0 * fraction,
            )
    outer = inner.copy()
    radial = np.linalg.norm(outer[:, :, :2], axis=2)
    scale = (radial + 4.0) / radial
    outer[:, :, 0] *= scale
    outer[:, :, 1] *= scale
    geometry = PointGridHornGeometry(
        inner_points=inner,
        outer_points=outer,
        wall_thickness_mm=4.0,
    )

    bundle = tmp_path / "workspace" / "wgreturn" / "tiny.wgreturn"
    bundle.mkdir(parents=True)
    step_path, info = write_step(geometry, bundle / "assembly.step", open_throat=False)
    step = step_path.read_bytes()
    from hornlab_mesher.step_import import advanced_face_order, gmsh_surface_tags

    initialized_here = not gmsh.isInitialized()
    if initialized_here:
        gmsh.initialize(interruptible=False)
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()
    gmsh.model.occ.importShapes(str(step_path), highestDimOnly=True)
    gmsh.model.occ.synchronize()
    surface_tags = gmsh_surface_tags()
    face_ids = advanced_face_order(step_path)
    source_surface = next(
        surface
        for surface in surface_tags
        if str(gmsh.model.getType(2, surface)).casefold() == "plane"
    )
    source_face_id = face_ids[surface_tags.index(source_surface)]
    area = float(gmsh.model.occ.getMass(2, source_surface))
    second_surface = next(surface for surface in surface_tags if surface != source_surface)
    second_face_id = face_ids[surface_tags.index(second_surface)]
    second_area = float(gmsh.model.occ.getMass(2, second_surface))
    second_center = np.asarray(gmsh.model.occ.getCenterOfMass(2, second_surface))
    mirrored_surface = next(
        surface
        for surface in surface_tags
        if surface not in {source_surface, second_surface}
        and np.allclose(
            gmsh.model.occ.getCenterOfMass(2, surface),
            [-second_center[0], second_center[1], second_center[2]],
            atol=1.0e-6,
        )
        and float(gmsh.model.occ.getMass(2, surface))
        == pytest.approx(second_area, rel=1.0e-9)
    )
    mirrored_face_id = face_ids[surface_tags.index(mirrored_surface)]
    gmsh.clear()
    manifest = {
        "wgreturn_version": "1.0",
        "required_features": [
            "checksummed-files-v1",
            "assembly-frame-v1",
            "instance-records-v1",
        ],
        "return": {
            "id": "wgr_01J5A8QK3M9T2XVBH0RD7NWE6C",
            "created_at": "2026-08-12T09:14:03Z",
        },
        "generator": {
            "adapter": "test",
            "adapter_version": "1",
            "cad_app": "test",
            "cad_version": "1",
        },
        "document": {"name": "tiny", "native_id": None},
        "coordinate_system": {
            "length_unit": "mm",
            "handedness": "right",
            "matrix_convention": "row-major-local-to-parent",
            "solver_anchor_instance_id": None,
        },
        "assembly": {
            "file": "assembly.step",
            "n_bodies_expected": 1,
            "bbox_mm": [list(info.bounding_box_mm[0]), list(info.bounding_box_mm[1])],
        },
        "files": {
            "assembly.step": {
                "sha256": "sha256:" + hashlib.sha256(step).hexdigest(),
                "size_bytes": len(step),
                "media_type": "model/step",
                "purpose": "exterior-assembly",
            }
        },
        "scope": {
            "selection": "root",
            "included": [
                {
                    "object_id": "tiny",
                    "name": "tiny",
                    "body_kind": "solid",
                    "visible": True,
                    "external_reference": "local",
                    "wglink_instance_id": None,
                }
            ],
            "skipped": [],
            "fem_air_volumes": [],
            "status": "clean",
        },
        "instances": [],
        "sources": [
            {
                "id": "source-mf",
                "role": "MF",
                "instance_id": None,
                "required": True,
                "default_drive_channel_id": "drive-mf",
                "patch_policy": "single-connected",
                "expected_connected_components": 1,
                "selectors": {"advanced_face_indices": [source_face_id]},
                "observed": {
                    "face_count": 1,
                    "total_area_mm2": area,
                    "per_face_area_mm2": [area],
                    "bodies": ["tiny"],
                },
                "suggested_resolution_mm": 8,
            },
            {
                "id": "source-hf-left",
                "role": "HF",
                "instance_id": None,
                "required": True,
                "default_drive_channel_id": "drive-hf-left",
                "patch_policy": "single-connected",
                "expected_connected_components": 1,
                "selectors": {"advanced_face_indices": [second_face_id]},
                "observed": {
                    "face_count": 1,
                    "total_area_mm2": second_area,
                    "per_face_area_mm2": [second_area],
                    "bodies": ["tiny"],
                },
                "suggested_resolution_mm": 8,
            },
            {
                "id": "source-hf-right",
                "role": "HF",
                "instance_id": None,
                "required": True,
                "default_drive_channel_id": "drive-hf-right",
                "patch_policy": "single-connected",
                "expected_connected_components": 1,
                "selectors": {"advanced_face_indices": [mirrored_face_id]},
                "observed": {
                    "face_count": 1,
                    "total_area_mm2": second_area,
                    "per_face_area_mm2": [second_area],
                    "bodies": ["tiny"],
                },
                "suggested_resolution_mm": 8,
            },
        ],
        "acoustics": None,
    }
    (bundle / "wgreturn.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    sizes = {
        "rigid_size_mm": 20,
        "transition_mm": 30,
        "source_size_mm": {
            "source-hf-left": 8,
            "source-hf-right": 8,
            "source-mf": 8,
        },
    }
    data_dir = tmp_path / "data"
    store = CadLinkStore(data_dir / "cadlink.db")
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        first = ingest_bundle(bundle, sizes, [], store, data_dir)
        second = ingest_bundle(bundle, sizes, [], store, data_dir)
        changed_sizes = deepcopy(sizes)
        changed_sizes["rigid_size_mm"] = 18
        third = ingest_bundle(bundle, changed_sizes, [], store, data_dir)
    finally:
        if initialized_here and gmsh.isInitialized():
            gmsh.finalize()

    assert first["mesh_cache_hit"] is False
    assert second["mesh_cache_hit"] is True
    assert third["mesh_cache_hit"] is False
    assert first["mesh_content_sha256"] == second["mesh_content_sha256"]
    assert first["viewport_mesh"]["available"] is True
    assert first["viewport_mesh"]["stats"]["domain"] == "full"
    assert second["viewport_mesh"]["cache_hit"] is True
    assert third["viewport_mesh"]["cache_hit"] is True
    assert first["viewport_mesh"]["content_sha256"] == third["viewport_mesh"]["content_sha256"]
    assert first["viewport_mesh"]["store_path"] != first["mesh_store_path"]
    assert first["normalisation"]["assembly_frame_is_solver_frame"] is True
    assert set(first["tag_map"]) == {"1", "101", "102", "103"}
    assert first["symmetry"]["planes"]["x0"]["accepted"] is False
    assert first["symmetry"]["planes"]["x0"]["role_mismatches"] > 0
    assert int(first["mesh"]["stats"]["tag_counts"]["102"]) > 0
    assert int(first["mesh"]["stats"]["tag_counts"]["103"]) > 0
    mesh_text = Path(first["mesh_store_path"]).read_text(encoding="utf-8")
    assert '"wg-import-v1|rigid"' in mesh_text
    assert '"wg-import-v1|tag=101|source_id=source-mf|instance_id=null|role=MF"' in mesh_text
    assert '"wg-import-v1|tag=102|source_id=source-hf-left|instance_id=null|role=HF"' in mesh_text
    assert '"wg-import-v1|tag=103|source_id=source-hf-right|instance_id=null|role=HF"' in mesh_text

    bad_bundle = tmp_path / "workspace" / "wgreturn" / "bad-placement.wgreturn"
    bad_bundle.mkdir()
    (bad_bundle / "assembly.step").write_bytes(step)
    bad_manifest = deepcopy(manifest)
    bad_manifest["return"]["id"] = "wgr_01J5A8QK3M9T2XVBH0RD7NWE6D"
    bad_manifest["coordinate_system"]["solver_anchor_instance_id"] = "anchor"
    bad_manifest["scope"]["included"][0]["wglink_instance_id"] = "anchor"
    bad_manifest["instances"] = [
        {
            "instance_id": "anchor",
            "design_id": "wgd_01J4Y2WZQK8Z3TFD3E7V9XKQ4M",
            "lineage_id": None,
            "edit_version": 1,
            "design_hash": "sha256:" + "1" * 64,
            "export_id": "wge_01J4Y2ZD000000000000000000",
            "export_sequence": 1,
            "geometry_hash": "sha256:" + "2" * 64,
            "origin_bundle_id": "wgb_01J4Y2ZF000000000000000000",
            "build_mode": "freestanding",
            "parameter_prefix": "wg_bad_",
            "occurrence_path": "bad",
            # A centimetre value accidentally treated as millimetres: 1 -> 10.
            "assembly_from_link": [
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 10],
                [0, 0, 0, 1],
            ],
            "chirality": "original",
            "body_evidence": {
                "local_body_state": "unmodified",
                "baseline_fingerprint": _fingerprint(),
                "observed_fingerprint": _fingerprint(),
                "observed_at": "2026-08-12T09:14:03Z",
            },
            "source_contract": {
                "role": "HF",
                "throat_z_mm": -4,
                "throat_plane_link": {
                    "origin_mm": [0, 0, -4],
                    "normal": [0, 0, 1],
                },
                "axis_link": {
                    "origin_mm": [0, 0, -4],
                    "direction": [0, 0, 1],
                },
                "throat_diameter_mm": math.sqrt(4.0 * area / math.pi),
                "expected_disc_area_mm2": area,
            },
        }
    ]
    bad_manifest["sources"] = [
        {
            "id": "source-hf",
            "role": "HF",
            "instance_id": "anchor",
            "required": True,
            "default_drive_channel_id": "drive-hf",
            "patch_policy": "single-connected",
            "expected_connected_components": 1,
            "selectors": {"linked_throat": {"instance_id": "anchor"}},
            "observed": {
                "face_count": 1,
                "total_area_mm2": area,
                "per_face_area_mm2": [area],
                "bodies": ["tiny"],
            },
            "suggested_resolution_mm": 8,
        }
    ]
    (bad_bundle / "wgreturn.json").write_text(
        json.dumps(bad_manifest, sort_keys=True), encoding="utf-8"
    )
    bad_initialized_here = not gmsh.isInitialized()
    if bad_initialized_here:
        gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        with pytest.raises(IngestRefusal, match=r"anchor throat face.*residual 10"):
            ingest_bundle(
                bad_bundle,
                {
                    "rigid_size_mm": 20,
                    "transition_mm": 30,
                    "source_size_mm": {"source-hf": 8},
                },
                [],
                store,
                data_dir,
            )
    finally:
        if bad_initialized_here and gmsh.isInitialized():
            gmsh.finalize()

    monkeypatch.setattr(
        metal, "metal_status", lambda: {"available": True, "reason": "ok"}
    )
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(
        metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs)
    )

    def native_result() -> SimpleNamespace:
        return SimpleNamespace(
            frequencies_hz=np.asarray([100.0, 200.0]),
            observation_angles_deg=np.asarray([-180.0, 0.0, 180.0]),
            observation_planes=["horizontal", "vertical", "diagonal"],
            pressure_complex=np.ones((2, 3, 3), dtype=np.complex128) * 20.0e-6,
            directivity_db=np.zeros((2, 3, 3)),
            impedance=np.ones(2, dtype=np.complex128),
            solver_log=[],
            timings={},
            native_diagnostics=[],
        )

    monkeypatch.setattr(
        metal,
        "native_solve_multi_source",
        lambda _path, sources, _config, frequencies_hz=None: [
            native_result() for _source in sources
        ],
    )
    registry = EngineRegistry(
        detector=lambda: [
            EngineInfo(
                name="metal",
                available=True,
                reason="test conformance engine",
                version="test",
            )
        ],
        factory=lambda name: metal.MetalEngine() if name == "metal" else None,
    )
    request = SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": first["ingest_id"],
                "manifest_sha256": first["manifest_sha256"],
                "artifact_sha256": first["artifact_sha256"],
                "drive_channels": [
                    {"id": "drive-mf", "source_ids": ["source-mf"]},
                    {"id": "drive-hf-left", "source_ids": ["source-hf-left"]},
                    {"id": "drive-hf-right", "source_ids": ["source-hf-right"]},
                ],
                "mesh": first["mesh_sizes"],
                "acknowledged_findings": [
                    f'{first["report_sha256"]}:{finding["id"]}'
                    for finding in first["findings"]
                    if finding.get("blocking")
                ],
            },
            "options": {
                "engine": "metal",
                "frequencies_hz": [100.0, 200.0],
                "polar_config": {"angle_range": [-180.0, 180.0, 5]},
            },
        }
    )

    async def solve_conformance_record() -> dict[str, object]:
        runtime = JobRuntime(
            JobStore(data_dir / "jobs-conformance.db"),
            engine_registry=registry,
            cadlink_store=store,
        )
        try:
            job_id = await runtime.submit(request)
            for _ in range(300):
                row = runtime.store.get_job_row(job_id)
                if row["status"] in {"complete", "error"}:
                    break
                await asyncio.sleep(0.01)
            assert row["status"] == "complete", row.get("error_message")
            return await runtime.get_results(job_id)
        finally:
            await runtime.shutdown()

    results = asyncio.run(solve_conformance_record())
    assert results["channel_order"] == [
        "drive-mf",
        "drive-hf-left",
        "drive-hf-right",
    ]
    assert set(results["channels"]) == {
        "drive-mf",
        "drive-hf-left",
        "drive-hf-right",
    }
    assert results["metadata"]["observation_origin_effective"] == "throat"
