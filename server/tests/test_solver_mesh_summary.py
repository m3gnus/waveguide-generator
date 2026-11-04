"""Solve admission uses the viewport's artifact and checks without its mesh payload."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.engines.registry import EngineRegistry
from server.jobs.mesh_preview import mount_solver_mesh
from server.mesh import builder
from test_solver_mesh_api import STUB_MSH, _rosse, _stub_worker


@pytest.fixture
def mesh_client(monkeypatch):
    calls = []

    async def artifact(*args, **kwargs):
        result = await _stub_worker(calls)(*args, **kwargs)
        result["metadata"] = {
            "surface_deviation_mm": 0.25,
            "surface_deviation": {"peak_is_upper_bound": True, "peak_mm": 0.25, "p95_mm": 0.2, "rms_mm": 0.125,
                                  "triangle_count": 4, "sampled_triangle_count": 4},
            "quality": {"element_shape": {"p1_radius_ratio": 0.15, "p1_angle_deg": 20.5,
                                         "min_angle_deg": 16.75, "sliver_fraction": 0.0}},
        }
        return result

    async def checked(*_args, **_kwargs):
        return True, None

    monkeypatch.setattr(builder, "run_mesh_build", artifact)
    monkeypatch.setattr(builder, "_run_wall_check", checked)
    builder.clear_solver_mesh_cache()
    app = FastAPI()
    mount_solver_mesh(app, EngineRegistry(detector=lambda: []))
    try:
        with TestClient(app) as client:
            yield client, calls
    finally:
        builder.clear_solver_mesh_cache()


@pytest.mark.parametrize("options", [{}, {"engine": "bempp", "symmetry": "half_yz"}])
def test_summary_omits_only_mesh_text_and_reuses_identical_artifact(mesh_client, options):
    client, calls = mesh_client
    body = {"design": _rosse().model_dump(mode="json"), "options": options}
    first = client.post("/api/solver-mesh", json=body)
    full = client.post("/api/solver-mesh", json=body)
    summary = client.post("/api/solver-mesh?representation=summary", json=body)
    assert first.status_code == full.status_code == summary.status_code == 200
    assert full.json()["msh_text"].encode() == first.json()["msh_text"].encode() == STUB_MSH.encode()
    expected = full.json()
    del expected["msh_text"]
    assert summary.json() == expected
    assert summary.json()["stats"]["mesh_cache_key"] == first.json()["stats"]["mesh_cache_key"]
    assert summary.json()["stats"]["mesh_cache_hit"] is True
    assert calls == [1]
    # Exact bytes for all retained numerical/plan fields, independent of JSON key order.
    assert json.dumps(summary.json(), sort_keys=True).encode() == json.dumps(expected, sort_keys=True).encode()


def test_summary_keeps_mesh_limit_refusal_override_and_artifact_binding(mesh_client, monkeypatch):
    client, calls = mesh_client

    async def large(*args, **kwargs):
        artifact = await _stub_worker(calls)(*args, **kwargs)
        artifact["stats"]["triangle_count"] = builder.MAX_SOLVER_MESH_ARTIFACT_TRIANGLES + 1
        return artifact

    monkeypatch.setattr(builder, "run_mesh_build", large)
    body = {"design": _rosse().model_dump(mode="json")}
    full = client.post("/api/solver-mesh", json=body)
    summary = client.post("/api/solver-mesh?representation=summary", json=body)
    assert full.status_code == summary.status_code == 409
    assert summary.content == full.content
    key = full.json()["detail"]["mesh_cache_key"]
    body["options"] = {"mesh_limit_override": True, "mesh_limit_override_key": key}
    accepted_full = client.post("/api/solver-mesh", json=body)
    accepted_summary = client.post("/api/solver-mesh?representation=summary", json=body)
    assert accepted_full.status_code == accepted_summary.status_code == 200
    assert accepted_summary.json() == {k: v for k, v in accepted_full.json().items() if k != "msh_text"}
    assert accepted_full.json()["msh_text"].encode() == STUB_MSH.encode()
    assert calls == [1]
    body["symmetry"] = "full"
    full = client.post("/api/solver-mesh", json=body)
    summary = client.post("/api/solver-mesh?representation=summary", json=body)
    assert full.status_code == summary.status_code == 409
    assert full.content == summary.content


@pytest.mark.parametrize("mode", ["warn", "strict"])
def test_summary_runs_the_same_cached_validation(mesh_client, monkeypatch, mode):
    client, calls = mesh_client

    async def crossing(*args, **kwargs):
        artifact = await _stub_worker(calls)(*args, **kwargs)
        artifact["integrity"].update(valid=False, self_intersection={
            "checked": True, "proper_crossing_count": 2, "coplanar_overlap_count": 1,
        })
        return artifact

    monkeypatch.setattr(builder, "run_mesh_build", crossing)
    body = {"design": _rosse().model_dump(mode="json"), "options": {"mesh_validation_mode": "off"}}
    assert client.post("/api/solver-mesh", json=body).status_code == 200
    body["options"]["mesh_validation_mode"] = mode
    full = client.post("/api/solver-mesh", json=body)
    summary = client.post("/api/solver-mesh?representation=summary", json=body)
    assert full.status_code == summary.status_code == (422 if mode == "strict" else 200)
    if mode == "strict":
        assert full.content == summary.content
    else:
        assert summary.json() == {k: v for k, v in full.json().items() if k != "msh_text"}
    assert calls == [1]
