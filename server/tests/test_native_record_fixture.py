"""Saved 0.3.6/0.3.7 native source-contour records still admit and dispatch.

WG no longer ingests native source contours, but records it published then
must keep solving. The fixtures under fixtures/native-records were written by
the retired ingestion and are frozen (see their README). The native solve is
intercepted: these tests check admission, the stored excitation and the Metal
dispatch contract, not acoustics.
"""

import asyncio
from copy import deepcopy
from dataclasses import replace
import gzip
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import meshio
import numpy as np
import pytest

from server.cadlink.store import CadLinkStore
from server.contracts.source_contour import validate_contour_request
from server.jobs.models import SolveRequest
from server.jobs.runtime import ImportedSolveRefusal, JobRuntime
from server.jobs.store import JobStore
from server.solver import metal
from server.solver.imported import imported_anchor_frame, read_verified_import_mesh
from server.tests.test_imported_jobs import _native_result, _PausedRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "native-records"


def saved_record(name, root):
    """Place one frozen record and its retained mesh in a fresh registry under ``root``."""

    record = json.loads((FIXTURES / name / "record.json").read_text())
    bundle = root / "imports" / "native-source" / record["ingest_id"]
    bundle.mkdir(parents=True)
    (bundle / "solve.msh").write_bytes(gzip.decompress((FIXTURES / name / "solve.msh.gz").read_bytes()))
    record["bundle_store_path"] = str(bundle)
    record["mesh_store_path"] = str(bundle / "solve.msh")
    store = CadLinkStore(root / "registry.sqlite")
    store.initialize()
    store.close()
    with sqlite3.connect(root / "registry.sqlite") as conn:
        conn.execute(
            "INSERT INTO ingests (ingest_id, manifest_sha256, artifact_sha256, record_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (record["ingest_id"], record["manifest_sha256"], record["artifact_sha256"],
             json.dumps(record), record["created_at"]),
        )
    return record


def request(record):
    native = record["native_source"]
    channels = native["channels"] if "channels" in native else [native["channel"]]
    return SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": record["ingest_id"],
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "required_features": native["required_features"],
                "mesh": record["mesh_sizes"],
                "drive_channels": channels,
            },
            "options": {
                "engine": "metal",
                "frequencies_hz": [100, 200],
                "polar_config": {"field_plane": False, "angle_range": [-180, 180, 37]},
            },
        }
    )


def runtime_scenario(root, body):
    async def scenario():
        store = CadLinkStore(root / "registry.sqlite")
        runtime = JobRuntime(
            JobStore(root / "jobs.sqlite"), engine_registry=_PausedRegistry(), cadlink_store=store,
        )
        try:
            await body(runtime)
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_single_channel_record_submits_and_refuses_a_contradicting_weight(tmp_path):
    record = saved_record("single-channel", tmp_path)
    assert record["producer"] == "native"

    async def body(runtime):
        bad = request(record)
        bad.geometry.drive_channels[0].patch_weights["piston"] = 0.4
        with pytest.raises(ImportedSolveRefusal, match="contradicts"):
            await runtime.submit(bad)
        job_id = await runtime.submit(request(record))
        row = runtime.store.get_job_row(job_id)
        assert row["config_json"]["geometry"]["drive_channels"][0]["patch_weights"] == {"piston": 1}

    runtime_scenario(tmp_path, body)


def test_shared_record_admission_and_common_frame_refusal(tmp_path):
    record = saved_record("shared-assembly", tmp_path)

    async def body(runtime):
        for change in ("feature", "weight", "skip"):
            bad = request(record)
            if change == "feature":
                bad.geometry.required_features = ["native-source-contour-v1"]
            elif change == "weight":
                bad.geometry.drive_channels[1].patch_weights["woofer/piston"] = 0
            else:
                bad.geometry.skipped_source_ids = ["woofer/piston"]
            with pytest.raises(ImportedSolveRefusal):
                await runtime.submit(bad)
        job = await runtime.submit(request(record))
        assert len(runtime.store.get_job_row(job)["config_json"]["geometry"]["drive_channels"]) == 2

    runtime_scenario(tmp_path, body)
    bad = deepcopy(record)
    bad["normalisation"]["source_frame"]["origin_m"][0] = 0.1
    with pytest.raises(ValueError, match="frame"):
        validate_contour_request(request(record).geometry, bad)


def test_shared_record_dispatch_frame_and_boundary_condition(tmp_path, monkeypatch):
    from hornlab_metal_bem.bie import _build_source_face_scale

    record = saved_record("shared-assembly", tmp_path)
    req = request(record)
    validate_contour_request(req.geometry, record)
    frame = imported_anchor_frame(record)
    np.testing.assert_array_equal(frame["origin"], [0, 0, 0.007])
    mesh = meshio.read(record["mesh_store_path"])
    tri, tags = mesh.get_cells_type("triangle"), mesh.get_cell_data("gmsh:physical", "triangle")
    xyz = mesh.points[tri]
    normals = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    captured = {}

    def dispatch(path, sources, config, frequencies_hz=None):
        captured.update(sources=sources, config=config, mesh_text=Path(path).read_text())
        return [_native_result(), _native_result()]

    monkeypatch.setattr(
        metal, "metal_status", lambda: {"available": True, "reason": "CPU dispatch inspection"}
    )
    monkeypatch.setattr(metal, "native_solve_multi_source", dispatch)
    response = metal.solve_imported_metal_from_msh_text(read_verified_import_mesh(record), req, record)
    assert len(captured["sources"]) == 2
    assert captured["mesh_text"] == read_verified_import_mesh(record)
    np.testing.assert_array_equal(captured["config"].frame_override.origin, frame["origin"])
    np.testing.assert_array_equal(captured["config"].frame_override.axis, [0, 0, 1])
    for channel, spec in zip(req.geometry.drive_channels, captured["sources"]):
        assert spec == {
            record["source_tags"][key]: complex(weight) for key, weight in channel.patch_weights.items()
        }
        config = replace(captured["config"], velocity_sources=spec)
        scale = _build_source_face_scale(
            SimpleNamespace(vertices=mesh.points.T, elements=tri.T), tags, config,
            frame["axis"], frame["origin"],
        )
        actual, expected = np.zeros(len(tri), complex), np.zeros(len(tri), complex)
        for key, weight in channel.patch_weights.items():
            mask = tags == record["source_tags"][key]
            actual[mask] = weight * (1 if scale is None else scale[mask])
            expected[mask] = weight * (1 if channel.motion == "normal" else normals[mask, 2])
        np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0)
        inactive = set(record["source_tags"]) - set(channel.source_ids)
        assert not actual[np.isin(tags, [record["source_tags"][s] for s in inactive])].any()
        assert not actual[tags == 1].any()
        metadata = response["channels"][channel.id]["metadata"]
        assert metadata["physical_source_id"] == channel.physical_source_id
        assert metadata["source_frame"] == record["native_source"]["source_frames"][channel.physical_source_id]
        assert metadata["observation_frame"] == record["native_source"]["observation_frame"]
