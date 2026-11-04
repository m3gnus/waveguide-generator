"""Every saved-design load path imports a retired LOOKUP table as a Custom profile.

Schema validation refuses an unconverted table (``lookup_import_required``); these
paths read saved designs through ``server.jobs.design_reader`` instead.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.jobs.design_reader import read_design

TABLE = [[0, 12.7], [100, 80]]
WIRE = {"formula": "LOOKUP", "lookup_profile": TABLE,
        "mesh": {"length_segments": 2, "z_map_points": "0,0.2,1"}}


def _lookup_body() -> dict:
    return {
        "design": {**deepcopy(WIRE), "simulation": {"f1": 300, "f2": 3000, "num_frequencies": 4}},
        "options": {"engine": "dryrun", "stage_delay_ms": 0},
    }


# P1-1: stored-run retry is covered by test_jobs_runtime.py::test_replay_imports_lookup_only_for_the_new_request.

def test_discarded_lookup_mesh_regenerates_through_the_import(tmp_path, monkeypatch):
    import server.mesh.builder as mesh_builder
    from server.app import create_app
    from server.tests.test_jobs_api import _complete_job, _request_with_headers

    built: list[str] = []

    async def fake_build_solver_mesh(design, options, *, force_rebuild=False, cancel_cb=None, progress_cb=None):
        built.append(design.formula)
        return {"msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n"}

    monkeypatch.setattr(mesh_builder, "build_solver_mesh", fake_build_solver_mesh)

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        store = app.state.jobs_runtime.store
        store.initialize()
        row = _complete_job("lookup")
        # A run stored before the LOOKUP retirement: its historical design.
        row["config_json"] = {}
        row["script_snapshot"] = {"version": 1, "design": deepcopy(WIRE)}
        store.create_job(row)
        status, _raw, headers = await _request_with_headers(app, "GET", "/api/mesh-artifact/lookup")
        assert status == 200
        assert headers["x-wg2-mesh-regenerated"] == "1"
        assert built == ["FREEFORM"]
        assert store.get_job_row("lookup")["script_snapshot"] == {"version": 1, "design": WIRE}
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


# P1-2: CLI --request JSON and the solve API bodies.

def test_cli_request_document_imports_a_saved_lookup_design(tmp_path):
    from server.cli.request import load_request_document

    path = tmp_path / "request.json"
    path.write_text(json.dumps(_lookup_body()), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    built = load_request_document(path, engine="dryrun")
    assert built.request.design.formula == "FREEFORM"
    assert built.request.design.root.length.constant_value() == 100
    assert path.read_text(encoding="utf-8") == before


def test_solve_api_bodies_import_a_saved_lookup_design(tmp_path, monkeypatch):
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    from server.app import create_app
    from server.tests.test_jobs_api import _request

    async def scenario() -> None:
        app = create_app(data_dir=tmp_path)
        status, raw = await _request(app, "POST", "/api/solve/plan", body=_lookup_body())
        assert status == 200, raw
        status, raw = await _request(app, "POST", "/api/solve", body=_lookup_body())
        assert status == 200, raw
        job_id = json.loads(raw)["job_id"]
        stored = app.state.jobs_runtime.store.get_job_row(job_id)["config_json"]
        assert stored["geometry"]["design"]["formula"] == "FREEFORM"

        refused = _lookup_body()
        refused["design"]["scale"] = 0
        status, raw = await _request(app, "POST", "/api/solve/plan", body=refused)
        assert status == 422
        error = json.loads(raw)["error"]
        assert error["code"] == "invalid_request"
        assert b"lookup_import_required" not in raw
        assert "Transverse / uniform scale" in error["message"]
        await app.state.jobs_runtime.wait_idle()
        await app.state.jobs_runtime.shutdown()

    asyncio.run(scenario())


# P1-3: CAD Link text loads of a stored LOOKUP snapshot.

def test_imported_job_keeps_a_lookup_anchor_snapshot_reopenable():
    from server.jobs.models import ImportedGeometrySource, SolveRequest
    from server.jobs.runtime import JobRuntime
    from server.jobs.runtime.core import _ImportedValidation

    class _Store:
        def get_design(self, design_id):
            assert design_id == "anchor"
            return {"lineage_id": None, "snapshot_text": json.dumps(WIRE)}

    request = SolveRequest.model_construct(geometry=ImportedGeometrySource.model_construct(type="imported"))
    record = {"anchor": {"design_id": "anchor"}}
    validated = _ImportedValidation(request, record, "", {"resolved_quadrants": 1}, None)
    runtime = SimpleNamespace(cadlink_store=_Store())
    submission = asyncio.run(JobRuntime._prepare_imported_submission(runtime, request, validated=validated))
    assert submission.anchor_design_id == "anchor"
    assert submission.anchor_snapshot is not None
    assert submission.anchor_snapshot["design"]["formula"] == "FREEFORM"


def test_cadlink_freshness_reads_a_stored_lookup_snapshot():
    from server.cadlink.intake.ingest import evaluate_instance_freshness
    from server.preview.geometry_identity import geometry_hash_for_design
    from server.tests.test_cadlink_ingest import _Store, _instance

    geometry_hash = geometry_hash_for_design(read_design(WIRE))
    head = {"design_hash": "dh", "snapshot_text": json.dumps(WIRE)}
    result = evaluate_instance_freshness(_instance(geometry_hash=geometry_hash), _Store(head))
    assert result["verdict"] == "current", result


# P1-4: design save/serialize map an import refusal to the keyed 422 detail.

@pytest.mark.parametrize("operation", ["save", "serialize"])
def test_design_io_maps_an_import_refusal_to_a_keyed_422(operation):
    from server.design_io import api

    function = api._save_design if operation == "save" else api._serialize_design
    with pytest.raises(HTTPException) as raised:
        function({"design": {**deepcopy(WIRE), "scale": 0}})
    assert raised.value.status_code == 422
    assert raised.value.detail["field"] == "Transverse / uniform scale"
    assert raised.value.detail["next_step"]
    from server.jobs.design_reader import parse

    saved = function({"design": deepcopy(WIRE)})
    assert parse(saved["text"]).design.formula == "FREEFORM"
