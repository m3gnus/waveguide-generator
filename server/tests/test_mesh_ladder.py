"""The per-band mesh ladder: planning, sizing gates, merging and provenance."""

from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace

import numpy as np
import pytest

from server.design.schema import DesignConfig, Expr
from server.jobs.models import SolveOptions
from server.solver import metal
from server.solver.context import SolverContext
from server.solver.mesh_ladder import (
    LADDER_ELEMENTS_PER_WAVELENGTH,
    LadderBand,
    LadderBandMesh,
    MeshLadderPlan,
    band_edges,
    build_ladder_band_meshes,
    merge_band_results,
    plan_mesh_ladder,
    scalable_resolution_fields,
    scaled_design,
    valid_frequency_hz,
)


def _design(**mesh: object) -> DesignConfig:
    payload: dict[str, object] = {"formula": "OSSE", "mesh": {"quadrants": 14, **mesh}}
    return DesignConfig.model_validate(payload)


def _log_sweep(low: float, high: float, count: int) -> list[float]:
    return np.geomspace(low, high, count).tolist()


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def test_band_edges_descend_by_the_band_ratio_and_cover_the_sweep() -> None:
    edges = band_edges(_log_sweep(400.0, 16000.0, 40), band_ratio=2.0)

    assert edges[0] == 16000.0
    assert edges == pytest.approx([16000.0, 8000.0, 4000.0, 2000.0, 1000.0, 500.0])
    # The last edge's own band reaches down to zero, so 400 Hz is covered.
    assert edges[-1] / 2.0 <= 400.0


def test_the_top_band_is_never_coarsened() -> None:
    """The design's own mesh is what the top of the sweep runs on, always.

    Coarsening the top band would silently re-mesh the frequencies most
    sensitive to resolution, which is exactly what ``TODO.md`` 2.4a says not to
    do to an archived design.
    """

    # A mesh far finer than its sweep needs: without the pin the top band would
    # be coarsened to the elements-per-wavelength target and the design's own
    # mesh would never be solved on at all.
    plan = plan_mesh_ladder(
        _design(throat_resolution=4.0, mouth_resolution=8.0),
        _log_sweep(400.0, 16000.0, 40),
        reference_max_edge_mm=1.0,
    )

    assert plan is not None
    assert plan.bands[0].resolution_scale == 1.0
    assert plan.bands[0].upper_hz == 16000.0
    assert plan.bands[1].resolution_scale > 1.0
    assert all(band.resolution_scale >= 1.0 for band in plan.bands)


def test_every_swept_frequency_lands_in_exactly_one_band() -> None:
    frequencies = _log_sweep(100.0, 20000.0, 40)
    plan = plan_mesh_ladder(
        _design(mouth_resolution=8.0), frequencies, reference_max_edge_mm=10.0
    )

    assert plan is not None
    positions = [index for band in plan.bands for index in band.positions]
    assert sorted(positions) == list(range(len(frequencies)))
    assert len(positions) == len(set(positions))


def test_band_scale_keeps_the_band_amplitude_valid_at_its_own_top() -> None:
    plan = plan_mesh_ladder(
        _design(mouth_resolution=8.0),
        _log_sweep(400.0, 16000.0, 40),
        reference_max_edge_mm=10.0,
    )

    assert plan is not None
    for band in plan.bands[1:]:
        predicted_edge_mm = 10.0 * band.resolution_scale
        assert valid_frequency_hz(
            predicted_edge_mm, LADDER_ELEMENTS_PER_WAVELENGTH
        ) >= band.upper_hz - 1e-6


def test_there_is_no_ladder_when_nothing_can_be_coarsened() -> None:
    """A mesh already too coarse for its sweep has nothing to give back."""

    plan = plan_mesh_ladder(
        _design(mouth_resolution=26.0),
        _log_sweep(8000.0, 16000.0, 8),
        reference_max_edge_mm=200.0,
    )

    assert plan is None


def test_there_is_no_ladder_when_the_design_sets_no_mm_resolution() -> None:
    assert scalable_resolution_fields(_design()) == ()
    assert (
        plan_mesh_ladder(
            _design(), _log_sweep(400.0, 16000.0, 40), reference_max_edge_mm=10.0
        )
        is None
    )


def test_bands_that_would_build_the_same_mesh_are_merged() -> None:
    """Two rungs at one scale must not build, solve and report one mesh twice."""

    # A reference edge far coarser than the sweep needs keeps the top octaves
    # all pinned at scale 1.0, so they have to fold into one band.
    plan = plan_mesh_ladder(
        _design(mouth_resolution=8.0),
        _log_sweep(400.0, 16000.0, 40),
        reference_max_edge_mm=20.0,
    )

    assert plan is not None
    scales = [band.resolution_scale for band in plan.bands]
    assert len(scales) == len(set(scales))
    assert plan.bands[0].upper_hz == 16000.0
    assert plan.bands[0].lower_hz < 8000.0
    assert [band.index for band in plan.bands] == list(range(len(plan.bands)))


# --------------------------------------------------------------------------
# Design scaling
# --------------------------------------------------------------------------


def test_scaling_multiplies_every_scalar_mm_resolution() -> None:
    design = _design(throat_resolution=4.0, mouth_resolution=8.0, rear_resolution=20.0)

    scaled = scaled_design(design, 2.5)

    assert scaled.root.mesh.throat_resolution.constant_value() == pytest.approx(10.0)
    assert scaled.root.mesh.mouth_resolution.constant_value() == pytest.approx(20.0)
    assert scaled.root.mesh.rear_resolution.constant_value() == pytest.approx(50.0)
    # The original must be untouched: the baseline band solves on it.
    assert design.root.mesh.mouth_resolution.constant_value() == pytest.approx(8.0)


def test_scaling_by_one_returns_the_very_same_design() -> None:
    design = _design(mouth_resolution=8.0)

    assert scaled_design(design, 1.0) is design


def test_a_formula_valued_resolution_is_left_alone() -> None:
    """Flattening a per-slice formula to a number is a different mesh change."""

    design = _design(mouth_resolution=8.0)
    design.root.mesh.rear_resolution = Expr.model_validate("10+5*p")

    scaled = scaled_design(design, 3.0)

    assert scaled.root.mesh.rear_resolution.raw == "10+5*p"
    assert scaled.root.mesh.mouth_resolution.constant_value() == pytest.approx(24.0)


@pytest.mark.parametrize("scale", [0.0, -1.0, math.inf, math.nan])
def test_an_unusable_scale_is_refused(scale: float) -> None:
    with pytest.raises(ValueError):
        scaled_design(_design(mouth_resolution=8.0), scale)


# --------------------------------------------------------------------------
# Building band meshes: the realized-validity gate and its one retry
# --------------------------------------------------------------------------


class _Options:
    mesh_validation_mode = "warn"


def _plan(*rungs: tuple[float, float]) -> MeshLadderPlan:
    """Build a plan from explicit ``(resolution_scale, upper_hz)`` rungs."""

    bands = tuple(
        LadderBand(
            index=index,
            lower_hz=upper_hz / 2.0,
            upper_hz=upper_hz,
            frequencies=(upper_hz,),
            positions=(index,),
            resolution_scale=scale,
        )
        for index, (scale, upper_hz) in enumerate(rungs)
    )
    return MeshLadderPlan(
        bands=bands,
        band_ratio=2.0,
        elements_per_wavelength=LADDER_ELEMENTS_PER_WAVELENGTH,
        reference_max_edge_mm=10.0,
    )


def _mesh(max_edge_mm: float, triangles: int, *, key: str = "k") -> dict[str, object]:
    return {
        "msh_text": f"mesh-{key}-{max_edge_mm}",
        "stats": {
            "max_edge_mm": max_edge_mm,
            "triangle_count": triangles,
            "vertex_count": triangles // 2,
            "mesh_cache_key": key,
            "mesh_cache_hit": False,
        },
    }


def _run_band_meshes(monkeypatch, plan, baseline, builds):
    calls: list[float] = []

    async def fake_build(design, options, cancel_cb=None, progress_cb=None):
        scale = design.root.mesh.mouth_resolution.constant_value() / 8.0
        calls.append(scale)
        outcome = builds.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    import server.mesh.builder as builder

    monkeypatch.setattr(builder, "build_solver_mesh", fake_build)
    meshes = asyncio.run(
        build_ladder_band_meshes(
            _design(mouth_resolution=8.0), plan, baseline, _Options()
        )
    )
    return meshes, calls


def test_a_band_mesh_too_coarse_to_be_valid_is_resized_once_and_kept(
    monkeypatch,
) -> None:
    """The realized edge is not the requested one, so one secant step is allowed."""

    baseline = _mesh(10.0, 4000, key="baseline")
    plan = _plan((1.0, 4000.0), (2.0, 2000.0))
    meshes, calls = _run_band_meshes(
        monkeypatch,
        plan,
        baseline,
        [_mesh(30.0, 1000, key="too-coarse"), _mesh(18.0, 1500, key="resized")],
    )

    assert len(calls) == 2
    assert calls[1] < calls[0]
    assert meshes[1].fallback_reason is None
    assert meshes[1].stats["mesh_cache_key"] == "resized"


def test_a_band_that_stays_too_coarse_falls_back_to_the_baseline_mesh(
    monkeypatch,
) -> None:
    baseline = _mesh(10.0, 4000, key="baseline")
    plan = _plan((1.0, 4000.0), (2.0, 2000.0))
    meshes, calls = _run_band_meshes(
        monkeypatch,
        plan,
        baseline,
        [_mesh(40.0, 800, key="a"), _mesh(35.0, 850, key="b")],
    )

    assert len(calls) == 2
    assert meshes[1].msh_text == baseline["msh_text"]
    assert "valid only to" in meshes[1].fallback_reason


def test_a_mesher_refusal_falls_back_instead_of_failing_the_solve(monkeypatch) -> None:
    """Coarsening chords thin shells, and the mesher says so. That is not fatal."""

    baseline = _mesh(10.0, 4000, key="baseline")
    meshes, _ = _run_band_meshes(
        monkeypatch,
        _plan((1.0, 4000.0), (2.0, 2000.0)),
        baseline,
        [RuntimeError("Solver mesh self-intersects: 8 triangle pairs cross")],
    )

    assert meshes[1].msh_text == baseline["msh_text"]
    assert "self-intersects" in meshes[1].fallback_reason


def test_a_band_mesh_that_is_not_smaller_than_the_baseline_is_refused(
    monkeypatch,
) -> None:
    baseline = _mesh(10.0, 4000, key="baseline")
    meshes, _ = _run_band_meshes(
        monkeypatch,
        _plan((1.0, 4000.0), (2.0, 2000.0)),
        baseline,
        [_mesh(18.0, 4200, key="bigger")],
    )

    assert meshes[1].msh_text == baseline["msh_text"]
    assert "not smaller than the baseline" in meshes[1].fallback_reason


def test_band_metadata_names_the_mesh_each_band_actually_ran_on() -> None:
    band = LadderBand(
        index=2,
        lower_hz=500.0,
        upper_hz=1000.0,
        frequencies=(600.0, 900.0),
        positions=(3, 4),
        resolution_scale=2.5,
    )
    entry = LadderBandMesh(
        band=band,
        msh_text="mesh",
        stats=_mesh(21.0, 900, key="cache-key")["stats"],
        max_valid_hz=2722.0,
        fallback_reason=None,
    ).as_metadata()

    assert entry["index"] == 2
    assert entry["frequency_count"] == 2
    assert entry["resolution_scale"] == pytest.approx(2.5)
    assert entry["triangle_count"] == 900
    assert entry["mesh_cache_key"] == "cache-key"
    assert entry["max_valid_frequency_hz"] == pytest.approx(2722.0)


# --------------------------------------------------------------------------
# Merging native results
# --------------------------------------------------------------------------


def _band_result(frequencies: tuple[float, ...], *, dofs: int) -> SimpleNamespace:
    count = len(frequencies)
    return SimpleNamespace(
        frequencies_hz=np.asarray(frequencies, dtype=np.float64),
        pressure_complex=np.asarray(
            [[complex(value, 0.0)] * 3 for value in frequencies], dtype=np.complex128
        ),
        impedance=np.asarray([complex(value, 1.0) for value in frequencies]),
        surface_pressure_complex=np.ones((count, dofs), dtype=np.complex128),
        surface_pressure_avg={"1": np.asarray(frequencies, dtype=np.float64)},
        observation_angles_deg=np.asarray([0.0, 90.0, 180.0]),
        timings={"solve": 1.0, "backend": "metal"},
        solver_log=[{"frequency_hz": value} for value in frequencies],
        native_diagnostics=[{"frequency_hz": value} for value in frequencies],
    )


def test_merging_puts_every_band_on_one_ascending_frequency_axis() -> None:
    merged = merge_band_results(
        [
            _band_result((4000.0, 8000.0), dofs=50),
            _band_result((1000.0, 2000.0), dofs=20),
        ]
    )

    assert merged.result.frequencies_hz.tolist() == [1000.0, 2000.0, 4000.0, 8000.0]
    assert merged.result.pressure_complex[:, 0].real.tolist() == [
        1000.0,
        2000.0,
        4000.0,
        8000.0,
    ]
    assert merged.result.impedance.real.tolist() == [1000.0, 2000.0, 4000.0, 8000.0]
    assert merged.result.surface_pressure_avg["1"].tolist() == [
        1000.0,
        2000.0,
        4000.0,
        8000.0,
    ]
    assert [row["frequency_hz"] for row in merged.result.solver_log] == [
        1000.0,
        2000.0,
        4000.0,
        8000.0,
    ]
    assert [row["frequency_hz"] for row in merged.result.native_diagnostics] == [
        1000.0,
        2000.0,
        4000.0,
        8000.0,
    ]
    assert merged.result.timings["solve"] == pytest.approx(2.0)


@pytest.mark.parametrize("lower_dofs", [20, 50])
def test_mesh_shaped_surface_arrays_are_dropped_and_named(lower_dofs: int) -> None:
    """There is no single mesh they could belong to, so keeping one is a lie.

    ``lower_dofs=50`` is the case a shape check alone would miss: a band that
    fell back to the baseline mesh has the *same* dof count as band 0, so the
    two surface solutions concatenate cleanly and silently describe two
    different meshes.
    """

    merged = merge_band_results(
        [
            _band_result((4000.0, 8000.0), dofs=50),
            _band_result((1000.0, 2000.0), dofs=lower_dofs),
        ]
    )

    assert merged.result.surface_pressure_complex is None
    assert "surface_pressure_complex" in merged.dropped_fields


def test_a_single_band_merges_to_itself_untouched() -> None:
    only = _band_result((1000.0, 2000.0), dofs=20)

    merged = merge_band_results([only])

    assert merged.result is only
    assert merged.dropped_fields == ()


# --------------------------------------------------------------------------
# The seam probe
# --------------------------------------------------------------------------


def _pattern(levels_db: list[float]) -> np.ndarray:
    return np.asarray([10.0 ** (value / 20.0) for value in levels_db], dtype=np.complex128)


def test_pattern_deviation_scores_only_the_main_lobe() -> None:
    """A difference outside -6 dB is not what this project scores a mesh on."""

    reference = _pattern([0.0, -3.0, -30.0])
    candidate = _pattern([0.0, -3.0, -10.0])

    assert metal._pattern_deviation_db(reference, candidate) == pytest.approx(0.0)


def test_pattern_deviation_reports_main_lobe_rms_in_db() -> None:
    reference = _pattern([0.0, -2.0, -30.0])
    candidate = _pattern([0.0, -2.4, -30.0])

    deviation = metal._pattern_deviation_db(reference, candidate)

    assert deviation == pytest.approx(0.4 / math.sqrt(2.0), rel=1e-6)


def test_the_seam_probe_reads_both_ends_of_the_band(monkeypatch) -> None:
    solved: list[tuple[str, list[float]]] = []

    def fake_solve(msh_text, frequencies, config, *, sort_after):
        solved.append((msh_text, list(frequencies)))
        return SimpleNamespace(
            frequencies_hz=np.asarray(frequencies),
            pressure_complex=np.asarray([_pattern([0.0, -2.0, -30.0])]),
        )

    monkeypatch.setattr(metal, "_native_solve_mesh", fake_solve)
    reference = SimpleNamespace(
        frequencies_hz=np.asarray([2000.0, 4000.0]),
        pressure_complex=np.asarray([_pattern([0.0, -2.0, -30.0])] * 2),
    )
    band = LadderBandMesh(
        band=LadderBand(
            index=1,
            lower_hz=500.0,
            upper_hz=1000.0,
            frequencies=(600.0, 900.0),
            positions=(0, 1),
            resolution_scale=2.0,
        ),
        msh_text="coarse",
        stats={},
        max_valid_hz=2000.0,
        fallback_reason=None,
    )

    check = metal._seam_overlap_deviation_db(reference, band, "fine", object())

    assert check["seam_frequency_hz"] == 2000.0
    assert check["interior_frequency_hz"] == 600.0
    assert check["main_lobe_rms_db"] == pytest.approx(0.0)
    assert solved == [
        ("coarse", [2000.0]),
        ("coarse", [600.0]),
        ("fine", [600.0]),
    ]


def test_a_failing_seam_probe_skips_the_interior_probe(monkeypatch) -> None:
    """A band already rejected must not pay for a second diagnostic."""

    solved: list[str] = []

    def fake_solve(msh_text, frequencies, config, *, sort_after):
        solved.append(msh_text)
        return SimpleNamespace(
            frequencies_hz=np.asarray(frequencies),
            pressure_complex=np.asarray([_pattern([0.0, -4.0, -30.0])]),
        )

    monkeypatch.setattr(metal, "_native_solve_mesh", fake_solve)
    reference = SimpleNamespace(
        frequencies_hz=np.asarray([2000.0]),
        pressure_complex=np.asarray([_pattern([0.0, -2.0, -30.0])]),
    )
    band = LadderBandMesh(
        band=LadderBand(
            index=1,
            lower_hz=500.0,
            upper_hz=1000.0,
            frequencies=(600.0,),
            positions=(0,),
            resolution_scale=2.0,
        ),
        msh_text="coarse",
        stats={},
        max_valid_hz=2000.0,
        fallback_reason=None,
    )

    check = metal._seam_overlap_deviation_db(reference, band, "fine", object())

    assert check["main_lobe_rms_db"] > metal.LADDER_SEAM_TOLERANCE_DB
    assert check["interior_main_lobe_rms_db"] is None
    assert solved == ["coarse"]


# --------------------------------------------------------------------------
# The solve option
# --------------------------------------------------------------------------


def test_the_mesh_ladder_option_is_off_by_default_and_normalizes() -> None:
    assert SolveOptions().mesh_ladder == "off"
    assert SolveOptions(mesh_ladder=" AUTO ").mesh_ladder == "auto"
    with pytest.raises(ValueError):
        SolveOptions(mesh_ladder="octave")


# --------------------------------------------------------------------------
# Through the adapter
# --------------------------------------------------------------------------


_LADDER_MSH = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
6
1 0 0 0
2 0.02 0 0
3 0 0.02 0
4 0 0 0.05
5 0.12 0 0.05
6 0 0.12 0.05
$EndNodes
$Elements
2
1 2 2 2 2 1 2 3
2 2 2 1 1 4 5 6
$EndElements
"""


def _adapter_band(
    index: int, frequencies: tuple[float, ...], msh_text: str, scale: float
) -> LadderBandMesh:
    return LadderBandMesh(
        band=LadderBand(
            index=index,
            lower_hz=min(frequencies) / 2.0,
            upper_hz=max(frequencies),
            frequencies=frequencies,
            positions=tuple(range(len(frequencies))),
            resolution_scale=scale,
        ),
        msh_text=msh_text,
        stats={"triangle_count": 100 * (index + 1), "max_edge_mm": 10.0 * (index + 1)},
        max_valid_hz=20000.0,
        fallback_reason=None,
    )


def _install_native_stubs(monkeypatch, solve_frequencies) -> None:
    monkeypatch.setattr(metal, "native_config", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(metal, "native_solve", lambda path, config: pytest.fail("grid path"))
    monkeypatch.setattr(metal, "native_solve_frequencies", solve_frequencies)
    monkeypatch.setattr(metal, "metal_status", lambda: {"available": True, "reason": "mock"})
    monkeypatch.setattr(
        metal, "ObservationConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )


def _adapter_context(frequencies: tuple[float, ...]) -> SolverContext:
    context = SolverContext(
        design=_design(mouth_resolution=8.0),
        frequency_range=(frequencies[0], frequencies[-1]),
        num_frequencies=len(frequencies),
        frequencies_hz=frequencies,
        quadrants=14,
    )
    context.polar_config["field_plane"] = True
    return context


def _adapter_result(frequencies: list[float]) -> SimpleNamespace:
    count = len(frequencies)
    return SimpleNamespace(
        frequencies_hz=np.asarray(frequencies, dtype=np.float64),
        observation_angles_deg=np.asarray([0.0, 90.0, 180.0]),
        observation_planes=["horizontal", "vertical"],
        pressure_complex=np.ones((count, 2, 3), dtype=np.complex128) * 20e-6,
        directivity_db=np.asarray([[[0.0, -6.0, -20.0], [0.0, -6.0, -20.0]]] * count),
        impedance=np.asarray([1j] * count),
        timings={"solve": 0.5},
        solver_log=[],
        native_diagnostics=[],
    )


def test_a_laddered_solve_returns_one_sorted_sweep_and_says_it_laddered(
    monkeypatch,
) -> None:
    meshes: list[str] = []

    def solve_frequencies(path, frequencies, config):
        meshes.append(open(path, encoding="utf-8").read()[:40])
        return _adapter_result(list(frequencies))

    _install_native_stubs(monkeypatch, solve_frequencies)
    bands = [
        _adapter_band(0, (4000.0, 8000.0), _LADDER_MSH, 1.0),
        _adapter_band(1, (1000.0, 2000.0), _LADDER_MSH + "\n", 2.0),
    ]

    response = metal.solve_metal_from_msh_text(
        _LADDER_MSH,
        _adapter_context((1000.0, 2000.0, 4000.0, 8000.0)),
        ladder_bands=bands,
    )

    assert response["frequencies"] == [1000.0, 2000.0, 4000.0, 8000.0]
    ladder = response["metadata"]["mesh_ladder"]
    assert ladder["applied"] is True
    assert ladder["mesh_artifact_band_index"] == 0
    assert [band["index"] for band in ladder["bands"]] == [0, 1]
    assert ladder["bands"][1]["resolution_scale"] == pytest.approx(2.0)
    # Two band solves plus the seam probe on the coarsened band.
    assert len(meshes) >= 3


def test_a_laddered_solve_retains_no_field_traces_and_names_why(monkeypatch) -> None:
    _install_native_stubs(
        monkeypatch, lambda path, frequencies, config: _adapter_result(list(frequencies))
    )

    response = metal.solve_metal_from_msh_text(
        _LADDER_MSH,
        _adapter_context((1000.0, 2000.0)),
        ladder_bands=[_adapter_band(0, (1000.0, 2000.0), _LADDER_MSH, 1.0)],
    )

    assert response["_field_traces"] is None
    assert response["_field_trace_unavailable_reason"] == "unsupported_per_band_mesh_ladder"


def test_the_baseline_band_must_carry_the_baseline_artifact(monkeypatch) -> None:
    """Observation geometry comes from the baseline mesh; band 0 must be it."""

    _install_native_stubs(
        monkeypatch, lambda path, frequencies, config: _adapter_result(list(frequencies))
    )

    with pytest.raises(ValueError, match="band 0 must carry the baseline"):
        metal.solve_metal_from_msh_text(
            _LADDER_MSH,
            _adapter_context((1000.0, 2000.0)),
            ladder_bands=[_adapter_band(0, (1000.0, 2000.0), "other", 1.0)],
        )


def test_a_band_the_native_helper_refuses_is_re_solved_on_the_baseline(
    monkeypatch,
) -> None:
    coarse = _LADDER_MSH + "\n"
    seen: list[str] = []

    def solve_frequencies(path, frequencies, config):
        text = open(path, encoding="utf-8").read()
        seen.append("coarse" if text == coarse else "baseline")
        if text == coarse:
            raise RuntimeError(
                "native_symmetry_plane='yz' requires a positive-x reduced-domain mesh"
            )
        return _adapter_result(list(frequencies))

    _install_native_stubs(monkeypatch, solve_frequencies)
    bands = [
        _adapter_band(0, (4000.0, 8000.0), _LADDER_MSH, 1.0),
        _adapter_band(1, (1000.0, 2000.0), coarse, 2.0),
    ]

    response = metal.solve_metal_from_msh_text(
        _LADDER_MSH,
        _adapter_context((1000.0, 2000.0, 4000.0, 8000.0)),
        ladder_bands=bands,
    )

    assert response["frequencies"] == [1000.0, 2000.0, 4000.0, 8000.0]
    band = response["metadata"]["mesh_ladder"]["bands"][1]
    assert band["solved_on_baseline_mesh"] is True
    assert "native solve refused" in band["fallback_reason"]
    assert seen[-1] == "baseline"


def test_a_band_that_fails_the_seam_tolerance_is_re_solved_on_the_baseline(
    monkeypatch,
) -> None:
    coarse = _LADDER_MSH + "\n"

    def solve_frequencies(path, frequencies, config):
        text = open(path, encoding="utf-8").read()
        result = _adapter_result(list(frequencies))
        if text == coarse:
            # A main lobe 3 dB down where the baseline is flat: far past the
            # 0.10 dB seam tolerance.
            result.pressure_complex = result.pressure_complex * 0.7
            result.pressure_complex[:, :, 0] = 20e-6
        return result

    _install_native_stubs(monkeypatch, solve_frequencies)
    bands = [
        _adapter_band(0, (4000.0, 8000.0), _LADDER_MSH, 1.0),
        _adapter_band(1, (1000.0, 2000.0), coarse, 2.0),
    ]

    response = metal.solve_metal_from_msh_text(
        _LADDER_MSH,
        _adapter_context((1000.0, 2000.0, 4000.0, 8000.0)),
        ladder_bands=bands,
    )

    band = response["metadata"]["mesh_ladder"]["bands"][1]
    assert band["solved_on_baseline_mesh"] is True
    assert "main-lobe rms" in band["fallback_reason"]
    assert band["seam_check"]["main_lobe_rms_db"] > metal.LADDER_SEAM_TOLERANCE_DB


def test_the_projected_work_ratio_reports_what_the_ladder_expected_to_save(
    monkeypatch,
) -> None:
    """Half the dofs is a quarter of the dense work; the metadata must say so."""

    _install_native_stubs(
        monkeypatch, lambda path, frequencies, config: _adapter_result(list(frequencies))
    )
    coarse = _adapter_band(1, (1000.0, 2000.0), _LADDER_MSH + "\n", 2.0)
    coarse.stats["vertex_count"] = 500
    baseline = _adapter_band(0, (4000.0, 8000.0), _LADDER_MSH, 1.0)
    baseline.stats["vertex_count"] = 1000

    response = metal.solve_metal_from_msh_text(
        _LADDER_MSH,
        _adapter_context((1000.0, 2000.0, 4000.0, 8000.0)),
        ladder_bands=[baseline, coarse],
    )

    ladder = response["metadata"]["mesh_ladder"]
    # Two frequencies at 1.0 and two at 0.25 of the baseline's work.
    assert ladder["projected_solve_work_ratio"] == pytest.approx(4.0 / 2.5)
    assert all(
        band["solve_wall_time_seconds"] >= 0.0 for band in ladder["bands"]
    )
