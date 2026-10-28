"""Enclosure admission must agree across preview, planning and mesh builds."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.app import create_app
from server.engines.registry import EngineInfo, EngineRegistry
from server.mesh import builder as mesh_builder
from server.preview.core import PreviewComputeService, PreviewProtocol
from server.protocol.frame import decode
from server.tests.test_jobs_api import _request as http_request
from server.tests.test_preview_ws_protocol import FakeTransport, _request, _wait_until


@pytest.fixture
def enclosure_design():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "enclosure-baffle-design.json").read_text()
    )


def _assert_roundover_message(message):
    assert "OSSE-ROUNDOVER does not support enclosure.depth" in message
    assert "Could not create line" not in message


def _with_roundover(enclosure_design):
    from server.tests.test_native_formulas import design_payload

    design = design_payload("OSSE-ROUNDOVER")
    design["enclosure"] = enclosure_design["enclosure"]
    return design


@pytest.mark.parametrize("lod", ["coarse", "fine"])
@pytest.mark.parametrize(("margin", "refused"), [(0, False), (0.05, False), (0.1, False), (0, True)])
def test_viewport_shares_flush_admission(enclosure_design, lod, margin, refused):
    for side in ("space_l", "space_r", "space_t", "space_b"):
        enclosure_design["enclosure"][side] = margin
    if refused:
        enclosure_design = _with_roundover(enclosure_design)

    async def scenario():
        transport = FakeTransport()
        service = PreviewComputeService(max_workers=1)
        protocol = PreviewProtocol(epoch=71, preview_service=service)
        task = asyncio.create_task(protocol.run(transport))
        try:
            await transport.incoming.put(_request(71, 1, lod=lod, design=enclosure_design))
            await _wait_until(
                lambda: bool(transport.binary) or any(m["kind"] == "error" for m in transport.json),
                timeout=15,
            )
            errors = [m for m in transport.json if m["kind"] == "error"]
            if refused:
                assert not transport.binary
                assert len(errors) == 1
                assert errors[0]["code"] == "validation"
                _assert_roundover_message(json.dumps(errors[0]))
            else:
                assert not errors
                header, buffers = decode(transport.binary[0])
                assert header["kind"] == "preview"
                assert buffers
        finally:
            await transport.incoming.put(None)
            await task
            await service.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("symmetry", ["auto", "full", "half_xz", "half_yz", "quarter"])
@pytest.mark.parametrize(("margin", "refused"), [(0, False), (0.05, False), (0.1, False), (0, True)])
def test_plan_submit_and_solver_mesh_share_enclosure_admission(
    tmp_path, monkeypatch, enclosure_design, symmetry, margin, refused,
):
    for name in ("space_l", "space_r", "space_t", "space_b"):
        enclosure_design["enclosure"][name] = margin

    async def no_build(*args, **kwargs):
        pytest.fail("a refused enclosure must not start a mesh build")

    if refused:
        enclosure_design = _with_roundover(enclosure_design)
        monkeypatch.setattr(mesh_builder, "run_mesh_build", no_build)

    async def scenario():
        app = create_app(data_dir=tmp_path, solver_warmup=False)
        app.state.jobs_runtime.store.initialize()
        registry = EngineRegistry(
            detector=lambda: [EngineInfo("metal", True, "test", "test")],
            factory=lambda name: object(),
        )
        # The router and mesh viewport use the same registry instance.
        monkeypatch.setattr(app.state.jobs_runtime.engine_registry, "capabilities", registry.capabilities)
        monkeypatch.setattr(app.state.jobs_runtime.engine_registry, "get_engine", registry.get_engine)
        body = {"design": enclosure_design, "options": {"engine": "metal", "symmetry": symmetry}}
        paths = ["/api/solve/plan", "/api/solver-mesh"]
        if refused:
            paths.append("/api/solve")
        for path in paths:
            status, raw = await http_request(app, "POST", path, body=body)
            payload = json.loads(raw)
            assert status == (422 if refused else 200), payload
            if refused:
                _assert_roundover_message(json.dumps(payload))
            elif path == "/api/solve/plan":
                assert not payload.get("refusal")
            else:
                assert payload["stats"]["triangle_count"] > 0
                assert payload["msh_text"].startswith("$MeshFormat")
        assert app.state.jobs_runtime.store.list_jobs() == ([], 0)

    try:
        asyncio.run(scenario())
    finally:
        mesh_builder.clear_solver_mesh_cache()


def test_plan_validates_the_resolved_mesh_not_the_design_quadrants(enclosure_design, monkeypatch):
    from hornlab_mesher import config_builder
    from server.jobs.models import SolveRequest
    from server.jobs.runtime.core import resolve_submission

    # Bottom is a symmetry cut in quarter/half_xz, but a real wall in half_yz.
    enclosure_design["enclosure"].update(space_l=0.1, space_r=0.1, space_t=0.1)
    registry = EngineRegistry(
        detector=lambda: [EngineInfo("metal", True, "test", "test")],
        factory=lambda name: object(),
    )
    request = SolveRequest.model_validate({
        "design": enclosure_design, "options": {"engine": "metal", "symmetry": "half_yz"},
    })
    # The quarter-domain enclosure has a baffle on both physical sides. Its
    # bottom becomes a real, flush wall only in the resolved half-yz domain.
    real_bounds = config_builder.resolve_enclosure_bounds
    original = real_bounds(mesh_builder._solver_mesher_config(request.design))
    assert not original.get("flush_mount", False)
    validated = []

    def record_bounds(config, **kwargs):
        bounds = real_bounds(config, **kwargs)
        validated.append((str(config["mesh"]["quadrants"]), bounds.get("flush_mount", False)))
        return bounds

    monkeypatch.setattr(config_builder, "resolve_enclosure_bounds", record_bounds)
    asyncio.run(resolve_submission(request, registry, preview=True))
    assert validated == [("14", True)]


@pytest.mark.parametrize("enclosure", [None, {}, {"depth": 0}])
def test_source_disk_inactive_enclosure_plans_submits_and_meshes(
    tmp_path, monkeypatch, enclosure,
):
    from hornlab_mesher import config_builder
    from server.tests.test_native_formulas import design_payload

    def no_bounds(*args, **kwargs):
        pytest.fail("standalone source geometry must not enter enclosure validation")

    monkeypatch.setattr(config_builder, "resolve_enclosure_bounds", no_bounds)

    async def scenario():
        app = create_app(data_dir=tmp_path, solver_warmup=False)
        runtime = app.state.jobs_runtime
        runtime.store.initialize()
        registry = EngineRegistry(
            detector=lambda: [EngineInfo("metal", True, "test", "test")],
            factory=lambda name: object(),
        )
        monkeypatch.setattr(runtime.engine_registry, "capabilities", registry.capabilities)
        monkeypatch.setattr(runtime.engine_registry, "get_engine", registry.get_engine)
        # Exercise real admission and persistence, leaving the solve queued.
        monkeypatch.setattr(runtime, "_ensure_scheduler", lambda: None)
        design = design_payload("SOURCE-DISK")
        design["enclosure"] = enclosure
        body = {"design": design, "options": {"engine": "metal"}}
        try:
            status, raw = await http_request(app, "POST", "/api/solve/plan", body=body)
            plan = json.loads(raw)
            assert status == 200, plan
            assert not plan.get("refusal")

            status, raw = await http_request(app, "POST", "/api/solver-mesh", body=body)
            mesh = json.loads(raw)
            assert status == 200, mesh
            assert mesh["stats"]["triangle_count"] > 0
            assert mesh["msh_text"].startswith("$MeshFormat")

            status, raw = await http_request(app, "POST", "/api/solve", body=body)
            accepted = json.loads(raw)
            assert status == 200, accepted
            job = runtime.store.get_job_row(accepted["job_id"])
            assert job["status"] == "queued"
        finally:
            await runtime.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        mesh_builder.clear_solver_mesh_cache()


def test_source_disk_active_enclosure_remains_unsupported(tmp_path, monkeypatch):
    from server.tests.test_native_formulas import design_payload

    async def no_build(*args, **kwargs):
        pytest.fail("an unsupported source enclosure must not start a mesh build")

    monkeypatch.setattr(mesh_builder, "run_mesh_build", no_build)

    async def scenario():
        app = create_app(data_dir=tmp_path, solver_warmup=False)
        runtime = app.state.jobs_runtime
        runtime.store.initialize()
        registry = EngineRegistry(
            detector=lambda: [EngineInfo("metal", True, "test", "test")],
            factory=lambda name: object(),
        )
        monkeypatch.setattr(runtime.engine_registry, "capabilities", registry.capabilities)
        monkeypatch.setattr(runtime.engine_registry, "get_engine", registry.get_engine)
        design = design_payload("SOURCE-DISK")
        design["enclosure"] = {"depth": 20}
        body = {"design": design, "options": {"engine": "metal"}}
        for path in ("/api/solve/plan", "/api/solve", "/api/solver-mesh"):
            status, raw = await http_request(app, "POST", path, body=body)
            payload = json.loads(raw)
            assert status == 422, payload
            assert "SOURCE-DISK does not support enclosure.depth" in raw.decode()
        assert runtime.store.list_jobs() == ([], 0)

    asyncio.run(scenario())
