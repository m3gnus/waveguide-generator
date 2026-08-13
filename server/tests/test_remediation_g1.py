"""Regression coverage for the V1 inputs audit server remediation (G1)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from server.charts import api as charts_api
from server.design.schema import DesignConfig, Expr
from server.design.textcfg import parse, serialize
from server.engines.dryrun import DryRunEngine
from server.engines.registry import EngineInfo, EngineRegistry, resolve_auto_engine
from server.jobs.models import PolarConfig, SolveOptions, SolveRequest
from server.jobs.runtime import JobRuntime
from server.jobs.store import JobStore
from server.mesh import builder as mesh_builder
from server.preview.translate import design_to_mesher_config
from server.solver.context import SolverContext
from server.solver.result_mapping import _renormalize_directivity, observation_config
from server.workspace import api as workspace_api


def _design(**extra: Any) -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "r0": 12.7,
            "a0": 10,
            **extra,
        }
    )


def test_auto_is_the_contract_default_and_resolves_by_capability(monkeypatch) -> None:
    assert SolveOptions().engine == "auto"

    capabilities = [
        EngineInfo("metal", False, "absent", None),
        EngineInfo("bempp", True, "ready", "1"),
        EngineInfo("circsym", True, "ready", "1"),
        EngineInfo("dryrun", True, "enabled", "builtin"),
    ]
    monkeypatch.setattr("server.engines.registry.detect_engines", lambda **_kwargs: capabilities)
    assert resolve_auto_engine(solver_mode="full_3d") == "bempp"
    assert resolve_auto_engine(solver_mode="circsym") == "bempp"


def test_runtime_persists_auto_resolution_and_verbose_log(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WG2_ENABLE_DRYRUN", "1")
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "simulation": {"f1": 100, "f2": 200, "num_frequencies": 2},
            },
            "options": {"verbose": True, "stage_delay_ms": 0},
        }
    )

    async def scenario() -> None:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=EngineRegistry(
                detector=lambda: [EngineInfo("dryrun", True, "test", "builtin")],
                factory=lambda _name: DryRunEngine(),
            ),
        )
        job_id = await runtime.submit(request)
        await runtime.wait_idle()
        status = await runtime.get_job(job_id)
        assert status["config_summary"]["engine"] == "dryrun"
        assert any("Verbose solve options" in line for line in status["log_tail"])
        await runtime.shutdown()

    asyncio.run(scenario())


def test_polar_angle_step_conversion_and_at_least_one_axis() -> None:
    polar = PolarConfig.model_validate(
        {
            "angle_range": [-30, 90],
            "angle_step": 10,
            "enabled_axes": ["vertical", "vertical"],
        }
    )
    assert polar.angle_range == (-30.0, 90.0, 13)
    assert polar.enabled_axes == ["vertical"]
    non_divisible = PolarConfig.model_validate(
        {"angle_range": [0, 180, 26], "angle_step": 7}
    )
    assert non_divisible.resolved_grid() == {
        "start": 0.0,
        "end": 180.0,
        "count": 26,
        "requested_step": 7.0,
        "resolved_step": 7.2,
    }
    with pytest.raises(ValidationError, match="at least 1"):
        PolarConfig.model_validate({"enabled_axes": []})


def test_all_solve_controls_flow_into_solver_context() -> None:
    request = SolveRequest.model_validate(
        {
            "design": {
                "formula": "OSSE",
                "simulation": {"f1": 100, "f2": 1000, "num_frequencies": 5},
            },
            "options": {
                "frequency_spacing": "linear",
                "mesh_validation_mode": "off",
                "verbose": True,
                "polar_config": {
                    "angle_range": [10, 70, 7],
                    "distance": 3.5,
                    "norm_angle": 15,
                    "inclination": 32,
                    "enabled_axes": ["diagonal"],
                    "observation_origin": "throat",
                    "spherical_sampling": True,
                    "spherical_theta_count": 9,
                    "spherical_phi_count": 16,
                },
            },
        }
    )
    context = SolverContext.from_request(request, solver_mode="full_3d")
    assert context.frequency_spacing == "linear"
    assert context.mesh_validation_mode == "off"
    assert context.verbose is True
    assert context.polar_config == request.options.polar_config.model_dump(mode="json")

    class FutureObservationConfig:
        def __init__(
            self,
            *,
            planes: list[str],
            distance_m: float,
            angle_min_deg: float,
            angle_max_deg: float,
            angle_count: int,
            origin: str,
            sphere_grid: tuple[int, int],
            inclination_deg: float,
            normalization_angle_deg: float,
        ) -> None:
            self.values = locals()

    native = observation_config(
        context, FutureObservationConfig, RuntimeError, "test-adapter"
    )
    assert native.values["planes"] == ["diagonal"]
    assert native.values["distance_m"] == 3.5
    assert native.values["angle_count"] == 7
    assert native.values["origin"] == "throat"
    assert native.values["sphere_grid"] == (9, 16)
    assert native.values["inclination_deg"] == 32
    assert native.values["normalization_angle_deg"] == 15


def test_current_native_contract_gets_custom_diagonal_points_and_normalization() -> None:
    context = SolverContext(
        design=_design(),
        frequency_range=(100, 200),
        num_frequencies=2,
        polar_config=PolarConfig.model_validate(
            {
                "angle_range": [0, 90, 2],
                "distance": 2,
                "norm_angle": 10,
                "inclination": 30,
                "enabled_axes": ["diagonal"],
            }
        ).model_dump(mode="json"),
    )
    msh = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
6
1 0 0 0
2 1 0 0
3 0 1 0
4 0 0 1
5 1 0 1
6 0 1 1
$EndNodes
$Elements
2
1 2 2 2 2 1 2 3
2 2 2 1 1 4 5 6
$EndElements
"""

    class CurrentObservationConfig:
        def __init__(
            self,
            *,
            planes: list[str],
            distance_m: float,
            angle_min_deg: float,
            angle_max_deg: float,
            angle_count: int,
            origin: str,
            custom_points: dict[str, Any] | None = None,
        ) -> None:
            self.custom_points = custom_points

    native = observation_config(
        context,
        CurrentObservationConfig,
        RuntimeError,
        "test-adapter",
        msh_text=msh,
    )
    assert native.custom_points is not None
    assert native.custom_points["diagonal"][1].tolist() == pytest.approx(
        [1.0 / 3.0 + 3**0.5, 4.0 / 3.0, 1.0]
    )

    patterns = {"diagonal": [[[0.0, 0.0], [10.0, -3.0], [20.0, -9.0]]]}
    _renormalize_directivity(patterns, 10.0)
    assert patterns["diagonal"][0] == [[0.0, 3.0], [10.0, 0.0], [20.0, -6.0]]


def test_dryrun_honors_polar_spherical_spacing_validation_and_verbose() -> None:
    engine = DryRunEngine()
    result = engine.solve(
        {"formula": "OSSE", "simulation": {"sim_type": "freestanding"}},
        frequency_start_hz=100,
        frequency_end_hz=400,
        num_frequencies=4,
        frequency_spacing="linear",
        mesh_validation_mode="off",
        verbose=True,
        polar_config={
            "angle_range": [-30, 30, 5],
            "distance": 3,
            "norm_angle": 10,
            "inclination": 30,
            "enabled_axes": ["diagonal"],
            "observation_origin": "throat",
            "spherical_sampling": True,
            "spherical_theta_count": 5,
            "spherical_phi_count": 8,
        },
    )
    assert result["frequencies"] == [100.0, 200.0, 300.0, 400.0]
    assert set(result["directivity"]) == {"diagonal"}
    assert [row[0] for row in result["directivity"]["diagonal"][0]] == [
        -30.0,
        -15.0,
        0.0,
        15.0,
        30.0,
    ]
    assert result["metadata"]["mesh_validation"]["mode"] == "off"
    assert result["metadata"]["verbose"] is True
    assert result["metadata"]["directivity"]["observation_origin"] == "throat"
    assert result["metadata"]["balloon_sampling"]["status"] == "available"
    assert len(result["balloon"]["theta_deg"]) == 5
    assert len(result["balloon"]["phi_deg"]) == 8


def test_enclosure_resolution_tuples_max_edge_and_text_round_trip() -> None:
    design = _design(
        mesh={"max_edge": 22},
        enclosure={
            "depth": 80,
            "front_resolution": "10+1,12,13,14",
            "back_resolution": [20, 21, 22, 23],
        },
    )
    enclosure = design.root.enclosure
    assert enclosure is not None
    assert isinstance(enclosure.front_resolution, tuple)
    assert enclosure.front_resolution[0].value == 11
    assert design.root.mesh.max_edge == Expr(value=22)
    text = serialize(design)
    assert "FrontResolution = 10+1,12,13,14" in text
    assert "BackResolution = 20,21,22,23" in text
    assert "Mesh.MaxEdge = 22" in text
    reparsed = parse(text).design
    reparsed_enclosure = reparsed.root.enclosure
    assert reparsed_enclosure is not None
    assert [item.value for item in reparsed_enclosure.front_resolution] == [11, 12, 13, 14]
    assert [item.value for item in reparsed_enclosure.back_resolution] == [20, 21, 22, 23]
    assert reparsed.root.mesh.max_edge is not None
    assert reparsed.root.mesh.max_edge.value == 22
    translated = design_to_mesher_config(design)
    assert translated["mesh"]["encFrontResolution"] == 11
    assert translated["mesh"]["maxEdge"] == 22


def test_infinite_baffle_allows_inactive_enclosure_preconfiguration() -> None:
    design = _design(
        simulation={"sim_type": "infinite-baffle"},
        enclosure={
            "depth": 100,
            "space_l": 30,
            "front_resolution": [20, 21, 22, 23],
        },
    )
    translated = design_to_mesher_config(design)
    assert translated["mode"] == "infinite-baffle"
    assert "enclosure" not in translated
    assert mesh_builder._solver_mesher_config(design)["mode"] == "infinite-baffle"


def test_mesh_validation_policy_changes_runtime_outcome(monkeypatch) -> None:
    result = {
        "msh_text": "mesh",
        "stats": {
            "warnings": ["Solver mesh contains invalid, degenerate, or non-manifold triangles."]
        },
        "integrity": {"valid": False},
        "metadata": {},
        "canonical_mesh": {},
    }

    async def fake_worker(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            **result,
            "stats": {"warnings": list(result["stats"]["warnings"])},
        }

    monkeypatch.setattr(mesh_builder, "run_on_gmsh_worker", fake_worker)

    async def scenario() -> None:
        warned = await mesh_builder.build_solver_mesh(
            _design(), {"mesh_validation_mode": "warn"}
        )
        assert warned["stats"]["warnings"]
        disabled = await mesh_builder.build_solver_mesh(
            _design(), {"mesh_validation_mode": "off"}
        )
        assert disabled["stats"]["warnings"] == []
        with pytest.raises(RuntimeError, match="strict topology"):
            await mesh_builder.build_solver_mesh(
                _design(), {"mesh_validation_mode": "strict"}
            )

    asyncio.run(scenario())


@pytest.mark.skipif(
    importlib.util.find_spec("hornlab_mesher") is None,
    reason="hornlab-waveguide-mesher is not installed",
)
def test_realized_max_edge_guard_rejects_oversized_mesh() -> None:
    design = _design(
        mesh={
            "angular_segments": 12,
            "length_segments": 4,
            "throat_resolution": 8,
            "mouth_resolution": 15,
            "quadrants": 1,
            "wall_thickness": 2,
            "max_triangles": 50_000,
            "max_edge": 1,
        },
        source={"shape": 2, "radius": -1, "curvature": 0},
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="exceeded mesh.max_edge guard"):
            await mesh_builder.build_solver_mesh(
                design, {"mesh_validation_mode": "warn"}
            )

    asyncio.run(scenario())


def test_workspace_routes_use_v2_data_layout(tmp_path: Path, monkeypatch) -> None:
    state = workspace_api.WorkspaceState(tmp_path)
    router = workspace_api.create_workspace_router(state)
    endpoints = {route.path: route.endpoint for route in router.routes}

    async def scenario() -> None:
        initial = await endpoints["/api/workspace/path"]()
        assert initial["path"] == str((tmp_path / "workspace").resolve())
        selected = tmp_path / "selected"
        selected.mkdir()
        monkeypatch.setattr(workspace_api, "_select_workspace_folder", lambda: str(selected))
        response = await endpoints["/api/workspace/select"]()
        assert response == {"selected": True, "path": str(selected.resolve())}
        calls: list[tuple[list[str], dict[str, object]]] = []
        monkeypatch.setattr(
            workspace_api.subprocess,
            "Popen",
            lambda command, **kwargs: calls.append((command, kwargs)),
        )
        opened = await endpoints["/api/workspace/open"]()
        assert opened["path"] == str(selected.resolve())
        assert calls

    asyncio.run(scenario())
    persisted = workspace_api.WorkspaceState(tmp_path)
    assert persisted.path() == (tmp_path / "selected").resolve()


def test_chart_routes_expose_every_theme_and_use_hornlab_plots(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []

    fake = SimpleNamespace(
        render_all_charts_b64=lambda payload, theme: calls.append(("charts", theme))
        or {"frequency_response": "YQ=="},
        directivity_heatmap_from_legacy_dict=lambda frequencies, directivity, **kwargs: calls.append(
            ("directivity", kwargs["theme"])
        )
        or "Yg==",
    )
    monkeypatch.setattr(charts_api, "_plots", lambda: fake)
    monkeypatch.setattr(
        charts_api,
        "_preview",
        lambda theme: calls.append(("preview", theme)) or "Yw==",
    )
    charts_api._cache.clear()

    async def scenario() -> None:
        listed = await charts_api.themes()
        assert len(listed["themes"]) == 12
        # The default is the interface's own dark theme; the frontend resolves
        # its "Match interface" setting to console or vellum before the request
        # reaches here, so an export lands on the window's own surfaces.
        assert listed["default"] == "console"
        assert charts_api.ChartsRenderRequest().theme == "console"
        preview = await charts_api.theme_preview("console")
        assert preview == {
            "theme": "console",
            "image": "data:image/png;base64,Yw==",
        }
        charts = await charts_api.render_charts(
            charts_api.ChartsRenderRequest(
                frequencies=[100], spl=[90], theme="blueprint"
            )
        )
        assert charts == {
            "charts": {"frequency_response": "data:image/png;base64,YQ=="}
        }
        directivity = await charts_api.render_directivity(
            charts_api.DirectivityRenderRequest(
                frequencies=[100],
                directivity={"horizontal": [[[0, 0]]]},
                theme="sepia",
            )
        )
        assert directivity == {"image": "data:image/png;base64,Yg=="}

    asyncio.run(scenario())
    assert calls == [
        ("preview", "console"),
        ("charts", "blueprint"),
        ("directivity", "sepia"),
    ]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_chart_requests_reject_nonfinite_typed_and_legacy_values(value: float) -> None:
    with pytest.raises(ValidationError, match="finite"):
        charts_api.ChartsRenderRequest(frequencies=[value])
    with pytest.raises(ValidationError, match="finite"):
        charts_api.DirectivityRenderRequest(
            frequencies=[100.0],
            directivity={"horizontal": [[[0.0, value]]]},
        )
