"""Standalone file -> immutable mesh -> ordinary jobs, without mounting CAD routes."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import numpy as np
import pytest

from server.cadlink.store import CadLinkStore
from server.jobs.api import create_jobs_router
from server.jobs.models_api import MeshRegistration, mount_models_api, register_mesh
from server.jobs.models import SolveRequest
from server.jobs.runtime import ImportedSolveRefusal, JobRuntime
from server.jobs.store import JobStore
from server.mesh.artifact import read_verified_import_mesh
from server.mesh.file_import import mesh_text, parse_file_mesh, prepare_file_mesh
from server.mesh.records import get_prepared_mesh
from server.mesh.integrity import mesh_integrity_report
from server.solver.bempp_imported import imported_bempp_preflight
from server.solver.base import EngineRunResult
from test_imported_jobs import _AlwaysRegistry
from test_jobs_api import _request as asgi_request

MESH = (Path(__file__).parent / "fixtures/mesh_import/tetra.msh").read_text()


@asynccontextmanager
async def api_client(app):
    async def call(method, path, body=None):
        path, _, query = path.partition("?")
        status, raw = await asgi_request(app, method, path, body=body, query=query)
        return SimpleNamespace(status_code=status, text=raw.decode(), json=lambda: json.loads(raw))

    yield SimpleNamespace(
        get=lambda path: call("GET", path), post=lambda path, json: call("POST", path, json)
    )


def registration(**changes):
    return MeshRegistration.model_validate(
        {
            "filename": "tetra.msh",
            "msh_text": MESH,
            "units": "mm",
            "forward": "+z",
            "up": "+y",
            "source_tags": [2],
            **changes,
        }
    )


def request(record, **changes):
    return SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": record["ingest_id"],
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "drive_channels": [{"id": "group_2", "source_ids": ["group_2"]}],
                **changes,
            },
            "options": {
                "engine": "metal",
                "frequencies_hz": [100, 200],
                "polar_config": {"angle_range": [-180, 180, 73]},
            },
            "label": "mesh test",
            "client_request_id": "mesh-test-press",
        }
    )


@pytest.mark.parametrize("units,scale", [("mm", 0.001), ("cm", 0.01), ("m", 1), ("in", 0.0254)])
def test_units_transform_groups_and_precision(units, scale):
    parsed = parse_file_mesh(MESH.replace("100 0 0", "100.1234567890123 0 0"))
    mesh, spec = prepare_file_mesh(parsed, units, "+x", "+z")
    assert spec["rotation"] == [[0, 1, 0], [0, 0, 1], [1, 0, 0]]
    assert mesh.points[1, 2] == pytest.approx(100.1234567890123 * scale, rel=1e-15)
    reparsed = parse_file_mesh(mesh_text(mesh))
    np.testing.assert_array_equal(mesh.points, reparsed.points)
    assert reparsed.names == {1: "Rigid", 2: "HF"}


@pytest.mark.parametrize(
    "old,new",
    [
        ("2.2 0 8", "4.1 0 8"),
        ("2.2 0 8", "2.2 1 8"),
        ("1 2 2 2 2 1 3 2", "1 3 2 2 2 1 3 2 4"),
        ("1 2 2 2 2 1 3 2", "1 9 2 2 2 1 3 2 4 2 3"),
        ("1 2 2 2 2 1 3 2", "1 4 2 2 2 1 3 2 4"),
        ("1 2 2 2 2 1 3 2", "1 2 2 0 2 1 3 2"),
        ("1 2 2 2 2 1 3 2", "1 2 2 2 2 1 3 9"),
        ("2 100 0 0", "1 100 0 0"),
        ("2 100 0 0", "2 nan 0 0"),
        ("$Nodes\n4", "$Nodes\n999999999999999"),
        ("$EndElements", ""),
    ],
)
def test_unsupported_or_corrupt_mesh_is_refused(old, new):
    with pytest.raises(ValueError):
        parse_file_mesh(MESH.replace(old, new))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("1 3 2", "1 2 3"),
        lambda text: text.replace("$Elements\n4", "$Elements\n3").replace("4 2 2 1 1 2 3 4\n", ""),
        lambda text: text.replace("1 3 2", "1 1 2"),
    ],
)
def test_bad_topology_is_refused_without_repair(mutate):
    with pytest.raises(ValueError, match="closed|outward"):
        prepare_file_mesh(parse_file_mesh(mutate(MESH)), "mm", "+z", "+y")


def test_registration_needs_valid_axes_sources_and_keeps_exact_original(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        for changes in ({"up": "+z"}, {"source_tags": [7]}, {"source_tags": [2, 2]}):
            with pytest.raises(ValueError):
                register_mesh(store, tmp_path, registration(**changes))
        record = register_mesh(store, tmp_path, registration())
        retained = get_prepared_mesh(store, record["ingest_id"])
        assert retained == record
        assert (
            Path(record["mesh_store_path"]).parent / "original.msh"
        ).read_bytes() == MESH.encode()
        assert record["sources"][0]["observed"]["total_area_mm2"] == pytest.approx(5000)
        assert "identity" not in record and "return_id" not in record and "mesh_sizes" not in record
        # The same file prepared the same way is the same retained mesh.
        assert register_mesh(store, tmp_path, registration()) == record
        # Prepared another way, it is another mesh of the same model, filed
        # in the same folder.
        other = register_mesh(store, tmp_path, registration(units="cm"))
        assert other["ingest_id"] != record["ingest_id"]
        assert other["manifest_sha256"] != record["manifest_sha256"]
        assert other["archive_stem"] == record["archive_stem"]
        model_id = "mesh-file:" + record["artifact_sha256"]
        assert store.get_ingest(record["ingest_id"])["model_id"] == model_id
        assert store.get_model(model_id)["latest_ingest_id"] == other["ingest_id"]
    finally:
        store.close()


def test_registration_retains_verified_integrity_for_bempp_admission(tmp_path):
    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, registration())
        mesh = parse_file_mesh(read_verified_import_mesh(record))
        integrity = record["mesh"]["integrity"]
        assert integrity == mesh_integrity_report(mesh.points, mesh.triangles)
        assert integrity["is_watertight"] is True
        assert integrity["open_edge_count"] == integrity["off_plane_open_edge_count"] == 0
        assert imported_bempp_preflight(record, []) is None
        # The existing engine refusal must still reject a free rim.
        integrity["off_plane_open_edge_count"] = 1
        assert "1 open edge off" in imported_bempp_preflight(record, [])
    finally:
        store.close()


def test_inspection_preserves_untagged_meshes_but_registration_requires_groups(tmp_path):
    text = MESH.replace("1 2 2 2 2 1 3 2", "1 2 0 1 3 2")
    assert 0 in parse_file_mesh(text, require_groups=False).tags
    with pytest.raises(ValueError, match="physical group"):
        parse_file_mesh(text)


def test_touching_shells_cannot_hide_an_inward_component():
    from server.mesh.file_import import FileMesh

    points = np.asarray(
        [[0, 0, 0], [100, 0, 0], [0, 100, 0], [0, 0, 100], [-10, 0, 0], [0, -10, 0], [0, 0, -10]],
        dtype=float,
    )
    faces = np.asarray(
        [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3], [0, 5, 4], [0, 4, 6], [0, 6, 5], [4, 5, 6]]
    )
    with pytest.raises(ValueError, match="nonmanifold vertex|outward"):
        prepare_file_mesh(FileMesh(points, faces, np.ones(8, dtype=int), {}), "mm", "+z", "+y")


class RecordingEngine:
    name = "metal"

    def __init__(self):
        self.calls = []

    async def run(self, body, *, imported_record, **kwargs):
        self.calls.append((body.model_dump(mode="json"), imported_record["_execution_msh_text"]))
        return EngineRunResult(
            results={
                "frequencies": [100, 200],
                "channels": {"group_2": {"frequencies": [100, 200], "metadata": {}}},
                "channel_order": ["group_2"],
                "metadata": {},
            },
            msh_text=imported_record["_execution_msh_text"],
        )


def test_http_import_plan_solve_restart_and_faithful_retry_without_cad_routes(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        engine = RecordingEngine()
        runtime = JobRuntime(
            JobStore.for_data_dir(tmp_path),
            cadlink_store=store,
            engine_registry=_AlwaysRegistry(engine),
        )
        app = FastAPI()
        app.state.data_dir, app.state.cadlink_store, app.state.jobs_runtime = (
            tmp_path,
            store,
            runtime,
        )
        mount_models_api(app)
        app.include_router(create_jobs_router(runtime))
        try:
            async with api_client(app) as client:
                assert (await client.get("/api/cadlink/returns")).status_code == 404
                inspected = await client.post(
                    "/api/models/files/inspect", json={"filename": "tetra.msh", "msh_text": MESH}
                )
                assert inspected.status_code == 200, inspected.text
                response = await client.post("/api/models/files", json=registration().model_dump())
                assert response.status_code == 200, response.text
                record = response.json()["prepared"]
                model_id = response.json()["model"]["model_id"]
                assert record["model_id"] == model_id == "mesh-file:" + record["artifact_sha256"]
                body = request(record).model_dump(mode="json")
                plan = await client.post("/api/solve/imported-plan", json=body)
                assert plan.status_code == 200 and plan.json()["engine"] == "metal", plan.text
                accepted = await client.post("/api/solve", json=body)
                assert accepted.status_code == 200, accepted.text
                job_id = accepted.json()["job_id"]
                assert (await client.post("/api/solve", json=body)).json()["job_id"] == job_id
                await runtime.wait_idle()
                job = await runtime.get_job(job_id)
                assert job["status"] == "complete", job.get("error_message")
                assert job["config_summary"]["import_origin"] == "mesh-file"
                assert job["cad_source"]["identity"] is None
                assert job["cad_source"]["archive_stem"]
                assert "CAD" not in job["design_availability"]["reason"]
                assert engine.calls[0][1] == record["msh_text"]
                assert job["cad_source"]["model_id"] == model_id
                metadata = runtime.store.get_job_row(job_id)["task_metadata"]["imported_geometry"]
                assert metadata["model_id"] == model_id
                assert metadata["prepared_id"] == record["ingest_id"]
                # The exact request is the setup revision it was solved with (model_solve.py).
                revision = store.get_setup_revision(metadata["setup_revision_id"])
                assert json.loads(revision["setup_json"])["options"]["engine"] == "metal"
                assert job["cad_source"]["setup_revision_id"] == metadata["setup_revision_id"]
                path = f"/api/models/{model_id}?prepared_id={record['ingest_id']}"
                read = (await client.get(path)).json()
                assert read["prepared"] == record
                assert read["latest_prepared"]["ingest_id"] == record["ingest_id"]
                assert read["original_available"] is True
            await runtime.shutdown()
            store.close()
            # Reopen the real databases and retained files, without any in-memory state.
            store = CadLinkStore.for_data_dir(tmp_path)
            runtime = JobRuntime(
                JobStore.for_data_dir(tmp_path),
                cadlink_store=store,
                engine_registry=_AlwaysRegistry(engine),
            )
            retry_id = await runtime.retry(job_id)
            await runtime.wait_idle()
            retried = await runtime.get_job(retry_id)
            assert retried["status"] == "complete", retried.get("error_message")
            assert len(engine.calls) == 2
            first, second = engine.calls
            assert first[0]["geometry"] == second[0]["geometry"]
            assert first[0]["options"] == second[0]["options"]
            assert first[1] == second[1]
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())


def test_missing_or_changed_mesh_and_mismatched_hashes_are_refused(tmp_path):
    async def scenario():
        store = CadLinkStore.for_data_dir(tmp_path)
        runtime = JobRuntime(
            JobStore.for_data_dir(tmp_path),
            cadlink_store=store,
            engine_registry=_AlwaysRegistry(SimpleNamespace(name="metal")),
        )
        try:
            record = register_mesh(store, tmp_path, registration())
            body = request(record)
            body.geometry.artifact_sha256 = "sha256:" + "0" * 64
            with pytest.raises(ImportedSolveRefusal, match="ingest_sha_mismatch"):
                await runtime.submit(body)
            original = read_verified_import_mesh(record)
            Path(record["mesh_store_path"]).write_text(original + "changed")
            with pytest.raises(ImportedSolveRefusal, match="ingest_mesh_unavailable"):
                await runtime.submit(request(record))
            Path(record["mesh_store_path"]).unlink()
            with pytest.raises(ImportedSolveRefusal, match="ingest_mesh_unavailable"):
                await runtime.submit(request(record))
            assert runtime.store.list_jobs()[1] == 0
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
