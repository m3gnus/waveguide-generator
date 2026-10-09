"""Real passive-body ingestion, job admission and CPU prescribed-velocity checks."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import meshio
import numpy as np
import pytest

from hornlab_mesher.assembly_artifact import export_assembly
from hornlab_mesher.passage_mesh import certify_passage_mesh
from hornlab_mesher.phase_plug import PhasePlug
from server.cadlink.native_source import ingest_native_source, read_native_source
from server.cadlink.store import CadLinkStore
from server.contracts.source_contour import validate_contour_request
from server.jobs.runtime import ImportedSolveRefusal, JobRuntime
from server.jobs.store import JobStore
from server.solver import metal
from server.solver.imported import read_verified_import_mesh
from server.tests.test_imported_jobs import _native_result, _PausedRegistry
from server.tests.test_shared_source_assembly import case, request


@pytest.fixture(scope="module")
def passages(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-passages")
    result = []
    for narrow in (False, True):
        stage = root / str(narrow)
        stage.mkdir()
        model, drives = case(narrow)
        bodies = (PhasePlug("vane/A", 2, 6, 2, 2.5, 2.4, 2.9),)
        if narrow:
            bodies = (
                PhasePlug("core", 2, 6, 0, 1, 0, 1.4),
                PhasePlug("vane/A", 2, 6, 1.6, 2.1, 2, 2.5),
            )
        model = replace(model, phase_plugs=bodies)
        size = 8 if narrow else 4
        manifest = export_assembly(model, drives, stage / "native", mesh_size_mm=size)
        store = CadLinkStore(stage / "registry.sqlite")
        store.initialize()
        try:
            record = ingest_native_source(
                stage / "native",
                store=store,
                data_dir=stage,
                sizes={
                    "rigid_size_mm": size,
                    "transition_mm": 3,
                    "source_size_mm": {
                        p["id"]: 2 for p in manifest["patches"] if p["role"] == "moving"
                    },
                },
            )
        finally:
            store.close()
        result.append((model, stage, record))
    return result


@pytest.mark.parametrize("index", [0, 1])
def test_actual_passage_dispatch_and_inactive_velocity(index, passages, monkeypatch):
    from hornlab_metal_bem.bie import _build_source_face_scale

    model, _, record = passages[index]
    req = request(record)
    validate_contour_request(req.geometry, record)
    mesh = meshio.read(record["mesh_store_path"])
    tri, tags = mesh.get_cells_type("triangle"), mesh.get_cell_data("gmsh:physical", "triangle")
    assert len(tri) <= 22000
    quality = record["mesh"]["geometric_quality"]["passages"]
    assert quality["certified_clearance_mm"] >= 0.8 * quality["minimum_clearance_mm"]
    assert len(quality["components"]) == 1 + len(model.phase_plugs)
    captured = {}

    def dispatch(path, sources, config, frequencies_hz=None):
        captured.update(sources=sources, config=config, mesh=Path(path).read_text())
        return [_native_result(), _native_result()]

    monkeypatch.setattr(
        metal, "metal_status", lambda: {"available": True, "reason": "CPU BC inspection"}
    )
    monkeypatch.setattr(metal, "native_solve_multi_source", dispatch)
    metal.solve_imported_metal_from_msh_text(read_verified_import_mesh(record), req, record)
    assert captured["mesh"] == read_verified_import_mesh(record)
    xyz = mesh.points[tri]
    normals = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    for channel, spec in zip(req.geometry.drive_channels, captured["sources"]):
        assert spec == {
            record["source_tags"][key]: complex(weight)
            for key, weight in channel.patch_weights.items()
        }
        config = replace(captured["config"], velocity_sources=spec)
        scale = _build_source_face_scale(
            SimpleNamespace(vertices=mesh.points.T, elements=tri.T),
            tags,
            config,
            [0, 0, 1],
            [0, 0, 0.007],
        )
        actual, expected = np.zeros(len(tri), complex), np.zeros(len(tri), complex)
        for key, weight in channel.patch_weights.items():
            selected = tags == record["source_tags"][key]
            actual[selected] = weight * (1 if scale is None else scale[selected])
            expected[selected] = weight * (
                1 if channel.motion == "normal" else normals[selected, 2]
            )
        np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0)
        assert not actual[tags == 1].any()
        inactive = set(record["source_tags"]) - set(channel.source_ids)
        assert not actual[np.isin(tags, [record["source_tags"][key] for key in inactive])].any()


def test_real_job_admission_and_missing_passage_negotiation(passages, tmp_path):
    _, stage, record = passages[1]

    async def scenario():
        cad = CadLinkStore(stage / "registry.sqlite")
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.sqlite"), engine_registry=_PausedRegistry(), cadlink_store=cad
        )
        try:
            bad = request(record)
            bad.geometry.required_features.remove("native-phase-plug-passages-v1")
            with pytest.raises(ImportedSolveRefusal):
                await runtime.submit(bad)
            job = await runtime.submit(request(record))
            assert (
                runtime.store.get_job_row(job)["config_json"]["geometry"]["required_features"][-1]
                == "native-phase-plug-passages-v1"
            )
        finally:
            await runtime.shutdown()
            cad.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["feature", "body", "gap", "refinement", "selector"])
def test_tampered_passage_manifest_refused(change, passages, tmp_path):
    _, stage, _ = passages[1]
    target = tmp_path / "native"
    shutil.copytree(stage / "native", target)
    manifest = json.loads((target / "source.json").read_text())
    if change == "feature":
        manifest["required_features"].pop()
    elif change == "body":
        manifest["passage_contract"]["bodies"][0]["genus"] = 1
    elif change == "gap":
        manifest["passage_contract"]["clearances_mm"]["source"] += 1
    elif change == "refinement":
        manifest["density"]["passage_refinement"] = True
    else:
        manifest["rigid_faces"][-1]["advanced_face_indices"] = manifest["patches"][0][
            "advanced_face_indices"
        ]
    (target / "source.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, KeyError)):
        read_native_source(target)


@pytest.mark.parametrize("change", ["inverted", "missing"])
def test_per_body_topology_cannot_hide_in_global_volume(change, passages):
    model, _, record = passages[1]
    mesh = meshio.read(record["mesh_store_path"])
    tri = mesh.get_cells_type("triangle").copy()
    tags = mesh.get_cell_data("gmsh:physical", "triangle")
    q = mesh.points[tri] * 1000 - model.parts[0][1]
    core = np.linalg.norm(q[..., :2], axis=2).max(axis=1) <= 1.4 + 1e-6
    core &= (q[..., 2].min(axis=1) >= 2 - 1e-6) & (q[..., 2].max(axis=1) <= 6 + 1e-6)
    assert core.any()
    if change == "inverted":
        tri[core] = tri[core][:, [0, 2, 1]]
    else:
        tri, tags = tri[~core], tags[~core]
    with pytest.raises(ValueError, match="orientation|inventory"):
        certify_passage_mesh(model, mesh.points * 1000, tri, tags != 1)


def test_tampered_request_passage_contract(passages):
    _, _, record = passages[1]
    changed = deepcopy(record)
    changed["native_source"]["passage_contract"]["open_passage_count"] += 1
    with pytest.raises(ValueError, match="topology"):
        validate_contour_request(request(record).geometry, changed)
