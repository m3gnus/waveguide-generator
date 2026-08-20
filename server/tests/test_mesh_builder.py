"""Gmsh owner-thread, topology, cancellation, and real OCC mesh tests."""

from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig, Expr
from server.mesh import builder as mesh_builder
from server.mesh.builder import (
    DENSE_SOLVER_MEMORY_LIMIT_BYTES,
    MAX_SOLVER_MESH_ARTIFACT_TRIANGLES,
    _dense_solver_memory_requirements,
    _enforce_artifact_triangle_ceiling,
    _enforce_dense_solver_memory_ceiling,
    _large_mesh_warnings,
    _mesh_policy,
    _require_closed_acoustic_topology,
    _solver_mesher_config,
    build_solver_mesh,
    clear_solver_mesh_cache,
    solver_mesh_cache_info,
)
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.mesh.integrity import mesh_integrity_report


def _tiny_design() -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 60,
            "a": 30,
            "a0": 10,
            "r0": 10,
            "k": 1,
            "n": 4,
            "q": 0.99,
            "s": 0.8,
            "mesh": {
                "angular_segments": 12,
                "length_segments": 4,
                "throat_resolution": 8,
                "mouth_resolution": 15,
                "quadrants": 1,
                "wall_thickness": 2,
                "max_triangles": 50000,
            },
            "source": {"shape": 2, "radius": -1, "curvature": 0},
            "simulation": {"f1": 500, "f2": 1000, "num_frequencies": 2},
        }
    )


def _placed_design(quadrants: int) -> DesignConfig:
    """A design whose 30 mm placement the global Scale doubles to 60 mm."""

    design = _tiny_design().model_copy(deep=True)
    design.root.scale = Expr(value=2.0)
    design.root.mesh.quadrants = Expr(value=float(quadrants))
    design.root.mesh.vertical_offset = Expr(value=30.0)
    return design


@pytest.mark.parametrize("quadrants", [1, 12, 14, 1234])
def test_solve_mesh_is_recentred_in_every_domain(quadrants: int) -> None:
    """The solve frame must not depend on which domain the resolver picked.

    The solver's symmetry images only mirror through the origin, so a y-cut
    reduced mesh can never carry the placement; letting the full domains keep
    it split the frame the field plane and the mesh artifact live in.
    """

    config = _solver_mesher_config(_placed_design(quadrants))
    assert config["mesh"]["quadrants"] == quadrants
    assert config["mesh"]["verticalOffset"] == 0.0


@pytest.mark.parametrize("quadrants", [1, 12, 14, 1234])
def test_keep_placement_hands_the_cad_boundary_the_scaled_offset(quadrants: int) -> None:
    config = _solver_mesher_config(_placed_design(quadrants), keep_placement=True)
    assert config["mesh"]["quadrants"] == quadrants
    assert config["mesh"]["verticalOffset"] == 60.0


def test_a_non_scalar_placement_is_refused_whatever_the_domain() -> None:
    design = _placed_design(1234)
    design.root.mesh.vertical_offset = Expr(value=30.0, raw="30 * p")
    with pytest.raises(ValueError, match="mesh.vertical_offset"):
        _solver_mesher_config(design)


def test_user_triangle_budget_is_advisory_but_dense_ceiling_is_not() -> None:
    design = _tiny_design().model_copy(deep=True)
    design.root.mesh.max_triangles = Expr(value=4_500.0)
    design.root.mesh.allow_large_mesh = Expr(value=0.0)
    guarded, warning_limit, multiplier = _mesh_policy(
        design, _solver_mesher_config(design)
    )

    assert warning_limit == 4_500
    assert multiplier == 4
    # The mesher converts its full-domain limit back to this actual-domain cap
    # for both its estimate and generated-count checks.
    assert guarded["mesh"]["maxTriangles"] == (
        MAX_SOLVER_MESH_ARTIFACT_TRIANGLES * 4
    )
    assert guarded["mesh"]["allowLargeMesh"] is False
    # Neither the old approval flag nor a ten-million user threshold can lift
    # the independent process-safety limit.
    design.root.mesh.max_triangles = Expr(value=10_000_000.0)
    design.root.mesh.allow_large_mesh = Expr(value=1.0)
    guarded, warning_limit, _ = _mesh_policy(design, _solver_mesher_config(design))
    assert warning_limit == 10_000_000
    assert guarded["mesh"]["maxTriangles"] == (
        MAX_SOLVER_MESH_ARTIFACT_TRIANGLES * 4
    )
    assert guarded["mesh"]["allowLargeMesh"] is False


def test_7173_triangle_mesh_continues_with_advisory_warning() -> None:
    warnings = _large_mesh_warnings(
        triangle_count=7_173,
        full_domain_count=7_173,
        budget_full_domain=4_500,
        domain_multiplier=1.0,
        metadata={"meshTriangleDominantRegion": "rear", "meshTriangleDominantTargetMm": 8},
    )
    assert len(warnings) == 1
    assert "7,173" in warnings[0]
    assert "warning threshold of 4,500" in warnings[0]
    assert "solve will continue" in warnings[0]
    assert _large_mesh_warnings(
        triangle_count=4_500,
        full_domain_count=4_500,
        budget_full_domain=4_500,
        domain_multiplier=1.0,
        metadata={},
    ) == []


def test_build_solver_mesh_continues_past_user_budget_and_preserves_warning(
    monkeypatch, tmp_path
) -> None:
    """Exercise the public build path with the former 7,173-vs-4,500 failure."""

    triangle_count = 7_173
    triangles = np.tile(np.asarray([[0, 1, 2]], dtype=np.int64), (triangle_count, 1))
    tags = np.full(triangle_count, 2, dtype=np.int32)
    parsed = SimpleNamespace(
        points=np.asarray([[0, 0, 0], [0.001, 0, 0], [0, 0.001, 0]], dtype=float),
        cells=[SimpleNamespace(type="triangle", data=triangles)],
        cell_data={"gmsh:physical": [tags]},
    )

    def fake_build_from_config(config, mesh_path, *, allow_large_mesh):
        assert config["mesh"]["maxTriangles"] == MAX_SOLVER_MESH_ARTIFACT_TRIANGLES
        assert config["mesh"]["allowLargeMesh"] is False
        assert allow_large_mesh is False
        mesh_path.write_text("$MeshFormat\n", encoding="utf-8")
        return SimpleNamespace(
            # Deliberately spoofed low: quadrants, not metadata, must control
            # full-domain warning and memory accounting.
            metadata={
                "meshDomainMultiplier": 0.001,
                "meshTriangleDominantRegion": "rear",
                "meshTriangleDominantTargetMm": 8,
            },
            units="m",
        )

    fake_package = ModuleType("hornlab_mesher")
    fake_package.__path__ = [str(tmp_path)]  # type: ignore[attr-defined]
    fake_config_builder = ModuleType("hornlab_mesher.config_builder")
    fake_config_builder.build_from_config = fake_build_from_config  # type: ignore[attr-defined]
    fake_meshio = ModuleType("meshio")
    fake_meshio.read = lambda _path: parsed  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hornlab_mesher", fake_package)
    monkeypatch.setitem(
        sys.modules, "hornlab_mesher.config_builder", fake_config_builder
    )
    monkeypatch.setitem(sys.modules, "meshio", fake_meshio)

    design = _tiny_design().model_copy(deep=True)
    design.root.mesh.wall_thickness = Expr(value=0.0)
    design.root.mesh.quadrants = Expr(value=1234.0)
    design.root.mesh.max_triangles = Expr(value=4_500.0)
    clear_solver_mesh_cache()
    try:
        result = asyncio.run(
            build_solver_mesh(
                design,
                {"mesh_validation_mode": "off"},
                force_rebuild=True,
            )
        )
    finally:
        clear_solver_mesh_cache()

    assert result["stats"]["triangle_count"] == 7_173
    assert result["stats"]["domain_multiplier"] == 1.0
    assert result["stats"]["full_domain_triangle_count"] == 7_173
    assert result["stats"]["dense_solver_used_vertex_count"] == 3
    assert result["stats"]["dense_solver_domain_multiplier"] == 1
    assert result["stats"]["dense_solver_metal_bytes_per_dof_squared"] == 88
    assert result["stats"]["dense_solver_bempp_bytes_per_vertex_squared"] == 24
    assert result["stats"]["dense_solver_memory_estimate_bytes"] == max(
        result["stats"]["dense_solver_metal_estimate_bytes"],
        result["stats"]["dense_solver_bempp_estimate_bytes"],
    )
    assert len(result["stats"]["warnings"]) == 1
    warning = result["stats"]["warnings"][0]
    assert "7,173" in warning
    assert "warning threshold of 4,500" in warning
    assert "solve will continue" in warning


def test_actual_domain_artifact_ceiling_is_enforced_post_build() -> None:
    _enforce_artifact_triangle_ceiling(MAX_SOLVER_MESH_ARTIFACT_TRIANGLES)
    with pytest.raises(RuntimeError, match="solver-artifact sanity ceiling"):
        _enforce_artifact_triangle_ceiling(MAX_SOLVER_MESH_ARTIFACT_TRIANGLES + 1)


def _triangles_using(vertex_count: int) -> np.ndarray:
    padded = vertex_count + (-vertex_count % 3)
    return (np.arange(padded, dtype=np.int64) % vertex_count).reshape((-1, 3))


@pytest.mark.parametrize(
    ("quadrants", "allowed_vertices", "rejected_vertices"),
    [(1234, 9_879, 9_880), (12, 9_879, 9_880), (1, 9_459, 9_460)],
)
def test_dense_solver_memory_ceiling_boundaries_follow_p1_dofs_and_symmetry(
    quadrants: int, allowed_vertices: int, rejected_vertices: int
) -> None:
    allowed = _enforce_dense_solver_memory_ceiling(
        _triangles_using(allowed_vertices), quadrants
    )
    assert allowed["used_vertex_count"] == allowed_vertices
    assert allowed["estimated_bytes"] <= DENSE_SOLVER_MEMORY_LIMIT_BYTES
    with pytest.raises(RuntimeError, match="8 GiB safety ceiling"):
        _enforce_dense_solver_memory_ceiling(
            _triangles_using(rejected_vertices), quadrants
        )


def test_dense_solver_guard_counts_only_vertices_referenced_by_triangles() -> None:
    # A parsed artifact may retain unused point records. They are not P1 DOFs
    # and therefore must not inflate the dense-system estimate.
    triangles = np.asarray([[0, 4_999, 9_999]], dtype=np.int64)
    requirements = _dense_solver_memory_requirements(triangles, 1234)
    assert requirements["used_vertex_count"] == 3
    assert requirements["metal_dof_count"] == 3


def test_infinite_baffle_aperture_p0_unknowns_raise_the_memory_estimate() -> None:
    triangles = _triangles_using(8_000)
    tags = np.full(len(triangles), 12, dtype=np.int32)
    ordinary = _dense_solver_memory_requirements(triangles, 1234)
    coupled = _dense_solver_memory_requirements(
        triangles,
        1234,
        mode="infinite-baffle",
        tags=tags,
    )
    assert coupled["aperture_triangle_count"] == len(triangles)
    assert coupled["metal_dof_count"] == 8_000 + len(triangles)
    assert coupled["estimated_bytes"] > ordinary["estimated_bytes"]
    with pytest.raises(RuntimeError, match="aperture P0 DOFs"):
        _enforce_dense_solver_memory_ceiling(
            triangles,
            1234,
            mode="infinite-baffle",
            tags=tags,
        )


@pytest.mark.parametrize("mode", ["enclosure", "freestanding", "infinite-baffle"])
@pytest.mark.parametrize(
    "integrity",
    [
        {"valid": True, "off_plane_open_edge_count": 3},
        {"valid": False, "off_plane_open_edge_count": 0},
    ],
)
def test_closed_acoustic_models_fail_closed_for_holes_or_invalid_topology(
    mode: str, integrity: dict[str, object]
) -> None:
    with pytest.raises(RuntimeError, match=f"Closed {mode} acoustic model rejected"):
        _require_closed_acoustic_topology({"mode": mode}, integrity)


def test_bare_horn_and_reduced_cut_plane_exceptions_remain() -> None:
    # A bare horn owns an open mouth rim. A reduced closed model owns only cut
    # plane edges, which the integrity report has already excluded here.
    _require_closed_acoustic_topology(
        {"mode": "bare"}, {"valid": False, "off_plane_open_edge_count": 7}
    )
    _require_closed_acoustic_topology(
        {"mode": "enclosure"}, {"valid": True, "off_plane_open_edge_count": 0}
    )


def test_integrity_report_detects_known_open_fixture() -> None:
    report = mesh_integrity_report(
        np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        np.asarray([[0, 1, 2]], dtype=int),
    )
    assert report["valid"] is True
    assert report["is_watertight"] is False
    assert report["open_edge_count"] == 3
    assert report["nonmanifold_edge_count"] == 0


def test_integrity_report_counts_each_defect_by_its_own_rule() -> None:
    """Pins the interaction between the rules, not just each one alone.

    The report is computed with array operations rather than a per-triangle
    Python loop (measured 787 ms to 53 ms on a 15,842-triangle sheet), and the
    rules compose in a specific order that a vectorised rewrite could easily
    get subtly wrong: an out-of-range face is counted and contributes nothing
    else, a repeated-index face is degenerate and also contributes nothing
    else, and only the surviving faces can be duplicates, zero-area, or
    contribute edges.
    """

    points = np.asarray(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]], dtype=float
    )
    report = mesh_integrity_report(
        points,
        np.asarray(
            [
                [0, 1, 2],  # the original
                [2, 1, 0],  # same triangle, rotated and reversed -> duplicate
                [0, 0, 1],  # repeated index -> degenerate, and nothing else
                [0, 1, 9],  # out of range -> invalid, and nothing else
                [1, 2, 3],
                [0, 4, 1],  # point 4 coincides with point 0 -> zero area
            ],
            dtype=int,
        ),
    )
    assert report["invalid_index_triangle_count"] == 1
    # One repeated-index face plus one zero-area face, counted once each.
    assert report["degenerate_triangle_count"] == 2
    assert report["duplicate_triangle_count"] == 1


def test_a_zero_area_face_is_degenerate_even_with_distinct_indices() -> None:
    """Three collinear points have distinct indices and no area."""

    report = mesh_integrity_report(
        np.asarray([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float),
        np.asarray([[0, 1, 2]], dtype=int),
    )
    assert report["degenerate_triangle_count"] == 1
    assert report["valid"] is False


def test_identical_faces_through_coincident_points_are_duplicates() -> None:
    """Distinct indices, same geometry: v1's mesher can emit these on a seam."""

    points = np.asarray(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float
    )
    report = mesh_integrity_report(points, np.asarray([[0, 1, 2], [3, 4, 5]], dtype=int))
    assert report["duplicate_triangle_count"] == 1


def test_non_finite_corners_are_degenerate_and_never_duplicates() -> None:
    """A NaN coordinate never compares equal, so two NaN faces are not a pair."""

    points = np.asarray([[0, 0, 0], [1, 0, 0], [float("nan"), 1, 0]], dtype=float)
    report = mesh_integrity_report(points, np.asarray([[0, 1, 2], [0, 1, 2]], dtype=int))
    assert report["degenerate_triangle_count"] == 2
    assert report["duplicate_triangle_count"] == 1  # by index, not by geometry


def test_integrity_report_separates_cut_plane_edges_from_holes() -> None:
    """A reduced domain is open along its cut plane and closed everywhere else.

    ``open_edge_count`` alone cannot tell a legitimate y=0 cut from an
    enclosure whose front baffle never met the mouth rim, so a leaking box
    reads exactly like a correctly halved one.
    """

    points = np.asarray(
        [[0, 0, 0], [1, 0, 0], [0.5, 0, 1], [0.5, 2, 0.5]], dtype=float
    )
    on_plane_only = mesh_integrity_report(
        points, np.asarray([[0, 1, 2]], dtype=int), symmetry_plane_axes=(1,)
    )
    assert on_plane_only["open_edge_count"] == 3
    assert on_plane_only["off_plane_open_edge_count"] == 0

    with_hole = mesh_integrity_report(
        points, np.asarray([[0, 1, 2], [0, 1, 3]], dtype=int), symmetry_plane_axes=(1,)
    )
    assert with_hole["open_edge_count"] == 4
    assert with_hole["off_plane_open_edge_count"] == 2

    unreduced = mesh_integrity_report(
        points, np.asarray([[0, 1, 2]], dtype=int), symmetry_plane_axes=()
    )
    assert unreduced["off_plane_open_edge_count"] == 3


def test_reduced_solve_mesh_is_open_only_on_its_own_cut_planes() -> None:
    """The real mesher's cut-plane snap must survive the conversion to metres.

    The synthetic fixture above proves the arithmetic; this proves the
    tolerance. A reduced enclosure mesh is open along its cuts by construction,
    and every one of those free edges has to land inside
    ``SYMMETRY_PLANE_TOLERANCE_M`` or the guard cries wolf on every solve.
    """

    design = _tiny_design().model_copy(deep=True)
    design.root.mesh.wall_thickness = Expr(value=4.0)
    for quadrants in (1, 12, 14):
        design.root.mesh.quadrants = Expr(value=float(quadrants))
        result = asyncio.run(build_solver_mesh(design, options={}, force_rebuild=True))
        integrity = result["stats"]["integrity"]
        assert integrity["open_edge_count"] > 0, quadrants
        assert integrity["off_plane_open_edge_count"] == 0, quadrants
        assert result["stats"]["warnings"] == [], quadrants


def test_bare_horn_mouth_rim_is_not_reported_as_a_leak() -> None:
    """A bare open shell has no closure to owe.

    Its mouth rim is free by construction -- Metal disables its own open-edge
    guard for exactly this mode -- so the leak warning must stay quiet even
    though the off-plane count is legitimately non-zero.
    """

    design = _tiny_design().model_copy(deep=True)
    design.root.mesh.wall_thickness = Expr(value=0.0)
    result = asyncio.run(build_solver_mesh(design, options={}, force_rebuild=True))
    integrity = result["stats"]["integrity"]
    assert integrity["off_plane_open_edge_count"] > 0
    assert result["stats"]["warnings"] == []


def test_enclosure_roundover_does_not_depend_on_the_solve_domain() -> None:
    """The resolver picks a domain; it must not pick a different box.

    V1 flattened the roundover to a sharp box on any reduced domain whose edge
    radius reached its smallest margin. The mesher clamps instead of
    discarding, so that rule made a quarter solve model a sharp box while the
    identical full-domain solve kept a 17 mm roundover.
    """

    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 80,
            "a": 40,
            "a0": 12,
            "r0": 12.7,
            "k": 1,
            "n": 4,
            "q": 0.99,
            "s": 0.8,
            "mesh": {
                "angular_segments": 24,
                "length_segments": 8,
                "throat_resolution": 6,
                "mouth_resolution": 10,
                "max_triangles": 300000,
            },
            # edge_radius == the margin: exactly the case v1 flattened.
            "enclosure": {
                "depth": 200,
                "edge_radius": 18,
                "edge_type": 1,
                "space_l": 18,
                "space_b": 18,
                "space_r": 18,
                "space_t": 18,
            },
            "source": {"shape": 2, "radius": -1, "curvature": 0},
            "simulation": {"f1": 500, "f2": 1000, "num_frequencies": 2},
        }
    )

    clamped: dict[int, float] = {}
    for quadrants in (1, 12, 14, 1234):
        design.root.mesh.quadrants = Expr(value=float(quadrants))
        result = asyncio.run(build_solver_mesh(design, options={}, force_rebuild=True))
        assert result["stats"]["integrity"]["off_plane_open_edge_count"] == 0, quadrants
        clamped[quadrants] = float(
            result["metadata"]["acousticEnclosureClampedEdgeMm"]
        )

    assert clamped[1234] > 0.0
    assert clamped == {q: clamped[1234] for q in (1, 12, 14, 1234)}


def test_gmsh_worker_serializes_every_call_on_one_persistent_thread() -> None:
    async def scenario() -> None:
        active = 0
        maximum = 0

        def observe():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            identity = threading.get_ident()
            active -= 1
            return identity

        identities = await asyncio.gather(
            *(run_on_gmsh_worker(observe) for _ in range(8))
        )
        assert len(set(identities)) == 1
        assert maximum == 1

    asyncio.run(scenario())


def test_worker_owned_gmsh_initializes_noninterruptible(monkeypatch) -> None:
    from server.mesh import gmsh_worker

    state = {"initialized": False, "arguments": [], "finalized": 0}
    fake = SimpleNamespace(
        isInitialized=lambda: state["initialized"],
        initialize=lambda **kwargs: (
            state["arguments"].append(kwargs),
            state.__setitem__("initialized", True),
        ),
        finalize=lambda: (
            state.__setitem__("initialized", False),
            state.__setitem__("finalized", state["finalized"] + 1),
        ),
    )
    monkeypatch.setitem(sys.modules, "gmsh", fake)
    assert gmsh_worker._run_in_gmsh_session(lambda: "ok") == "ok"
    assert state["arguments"] == [{"interruptible": False}]
    assert state["finalized"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native PATH regression")
def test_real_gmsh_session_preserves_native_windows_path(tmp_path: Path) -> None:
    """A Gmsh session must not make PATH-only child executables disappear."""

    from ctypes import wintypes

    from server.mesh import gmsh_worker

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetEnvironmentVariableW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    kernel32.GetEnvironmentVariableW.restype = wintypes.DWORD
    kernel32.SetEnvironmentVariableW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    kernel32.SetEnvironmentVariableW.restype = wintypes.BOOL

    size = kernel32.GetEnvironmentVariableW("PATH", None, 0)
    assert size > 0
    buffer = ctypes.create_unicode_buffer(size)
    assert kernel32.GetEnvironmentVariableW("PATH", buffer, size) < size
    native_path = buffer.value

    probe = tmp_path / "wg2-gmsh-path-probe.exe"
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "where.exe", probe)
    test_path = f"{native_path}{os.pathsep}{tmp_path}"

    def set_native_path(value: str) -> None:
        assert kernel32.SetEnvironmentVariableW("PATH", value)

    def run_probe() -> None:
        subprocess.run(
            [probe.name, "cmd.exe"],
            check=True,
            capture_output=True,
            text=True,
        )

    set_native_path(test_path)
    try:
        run_probe()
        assert gmsh_worker._run_in_gmsh_session(lambda: "ok") == "ok"
        run_probe()
    finally:
        set_native_path(native_path)


def test_mesh_build_cancellation_at_prebuild_checkpoint() -> None:
    calls = 0

    def cancel() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cancelled during mesh stage")

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="cancelled during mesh stage"):
            await build_solver_mesh(
                _tiny_design(), {"mesh_validation_mode": "warn"}, cancel
            )

    asyncio.run(scenario())


def test_solver_mesh_cache_reuses_artifact_and_supports_force_rebuild(
    monkeypatch,
) -> None:
    calls = 0

    async def fake_worker(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "msh_text": f"$MeshFormat\nartifact-{calls}",
            "stats": {"warnings": []},
            "integrity": {"valid": True},
            "metadata": {"build": calls},
        }

    monkeypatch.setattr(mesh_builder, "run_on_gmsh_worker", fake_worker)
    clear_solver_mesh_cache()

    async def scenario() -> None:
        first = await build_solver_mesh(
            _tiny_design(), {"mesh_validation_mode": "warn"}
        )
        second = await build_solver_mesh(
            _tiny_design(), {"mesh_validation_mode": "warn"}
        )
        forced = await build_solver_mesh(
            _tiny_design(),
            {"mesh_validation_mode": "warn"},
            force_rebuild=True,
        )

        assert first["stats"]["mesh_cache_hit"] is False
        assert second["stats"]["mesh_cache_hit"] is True
        assert second["msh_text"] == first["msh_text"]
        assert second["stats"]["mesh_cache_key"] == first["stats"]["mesh_cache_key"]
        assert forced["stats"]["mesh_cache_hit"] is False
        assert forced["msh_text"] != first["msh_text"]

        clear_solver_mesh_cache()
        after_clear = await build_solver_mesh(
            _tiny_design(), {"mesh_validation_mode": "warn"}
        )
        assert after_clear["stats"]["mesh_cache_hit"] is False
        assert after_clear["msh_text"] != forced["msh_text"]

    try:
        asyncio.run(scenario())
        assert calls == 3
        assert solver_mesh_cache_info().entries == 1
    finally:
        clear_solver_mesh_cache()


def test_solver_mesh_cache_key_ignores_solve_sampling_but_tracks_geometry(
    monkeypatch,
) -> None:
    calls = 0

    async def fake_worker(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "msh_text": f"$MeshFormat\nartifact-{calls}",
            "stats": {"warnings": []},
            "integrity": {"valid": True},
            "metadata": {},
        }

    monkeypatch.setattr(mesh_builder, "run_on_gmsh_worker", fake_worker)
    baseline = _tiny_design().model_dump(mode="json")
    sampling_only = _tiny_design().model_dump(mode="json")
    sampling_only["simulation"]["f1"] = 600
    changed_geometry = _tiny_design().model_dump(mode="json")
    changed_geometry["L"] = 61
    clear_solver_mesh_cache()

    async def scenario() -> None:
        first = await build_solver_mesh(baseline, {"mesh_validation_mode": "warn"})
        sampled = await build_solver_mesh(
            sampling_only, {"mesh_validation_mode": "strict"}
        )
        changed = await build_solver_mesh(
            changed_geometry, {"mesh_validation_mode": "warn"}
        )

        assert sampled["stats"]["mesh_cache_hit"] is True
        assert sampled["stats"]["mesh_cache_key"] == first["stats"]["mesh_cache_key"]
        assert changed["stats"]["mesh_cache_hit"] is False
        assert changed["stats"]["mesh_cache_key"] != first["stats"]["mesh_cache_key"]

    try:
        asyncio.run(scenario())
        assert calls == 2
    finally:
        clear_solver_mesh_cache()


@pytest.mark.skipif(
    importlib.util.find_spec("hornlab_mesher") is None,
    reason="hornlab-waveguide-mesher is not installed",
)
def test_small_real_occ_build_has_source_tag_stats_integrity_and_msh() -> None:
    async def scenario() -> None:
        stages = []
        result = await build_solver_mesh(
            _tiny_design(),
            {"mesh_validation_mode": "warn"},
            lambda: None,
            lambda *values: stages.append(values),
        )
        assert result["msh_text"].startswith("$MeshFormat")
        assert result["stats"]["vertex_count"] > 20
        assert result["stats"]["triangle_count"] > 20
        assert result["stats"]["tag_counts"]["2"] > 0
        assert result["stats"]["domain_multiplier"] == 4.0
        assert result["integrity"]["valid"] is True
        assert [stage for stage, _, _ in stages] == ["mesh_prepare", "mesh_validate"]

    asyncio.run(scenario())
