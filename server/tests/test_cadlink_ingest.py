"""Freshness, consistency, persistence, and endpoint transport contracts."""

from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import math
from pathlib import Path
from types import SimpleNamespace
import asyncio

import pytest
import numpy as np
from pydantic import ValidationError

from server.app import create_app
from server.cadlink.api import CadReturnIngestRequest, get_ingest, post_ingest
from server.cadlink.ingest import IngestRefusal, _canonical, compute_freshness, evaluate_instance_freshness, ingest_bundle, validate_registry_echoes
from server.mesh.imported import ImportedMeshDependencyError
from server.cadlink.wgreturn import WgReturnBundle
from server.cadlink.store import CadLinkStore


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
    app.state.workspace.select(workspace)
    expected = {"ingest_id": "wgi_01J5A8QK3M9T2XVBH0RD7NWE6C", "report_sha256": "sha256:test", "findings": []}
    monkeypatch.setattr("server.cadlink.api.ingest_bundle", lambda *_args, **_kwargs: expected)
    payload = CadReturnIngestRequest.model_validate({"bundlePath": "wgreturn/speaker.wgreturn", "mesh": {"rigidSizeMm": 20, "transitionMm": 40, "sourceSizeMm": {"source-hf": 4}}, "skippedSourceIds": [], "areaDriftOverrides": ["source-hf"]})
    response = asyncio.run(post_ingest(payload, SimpleNamespace(app=app)))
    assert response == expected
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CadReturnIngestRequest.model_validate({"bundlePath": "wgreturn/speaker.wgreturn", "mesh": {"rigidSizeMm": 20, "transitionMm": 40, "sourceSizeMm": {}}, "unexpected": True})


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
    app.state.workspace.select(workspace)
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
        "transformed_geometry_hash": "sha256:" + "3" * 64,
        "normalisation": {"matrix": np.eye(4)},
        "role_resolution": {"source-hf": {"surfaces": [1]}},
        "role_findings": [
            {"kind": "source-paint-missing", "source_id": "source-hf"}
        ],
        "symmetry": {"cut_planes": []},
        "healing": {"performed": False, "mode": "none"},
        "polar_grid_derivation": {"axes": {}},
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


def test_occ_ingest_end_to_end_writes_tag_names_and_reuses_cache(tmp_path: Path) -> None:
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
    finally:
        if initialized_here and gmsh.isInitialized():
            gmsh.finalize()

    assert first["mesh_cache_hit"] is False
    assert second["mesh_cache_hit"] is True
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
