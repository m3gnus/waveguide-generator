"""Exports size themselves from a deviation tolerance, not from the solve mesh.

Two defects are pinned here as regressions, both measured on the seed R-OSSE
against the pinned mesher before this module existed:

* an STL export densified the solve mesh ~7.5x and then handed the mesher the
  design's own ``mesh.max_triangles`` -- a solve-time *warning* threshold -- as
  a hard limit, so a design that solved fine at 8,171 triangles could not be
  exported at all ("52,468 exceeding the effective limit 50,000");
* the solid STEP sized its CAD point grid from the solver's millimetre
  resolutions, so refining an acoustic mesh made every STEP ~20x slower and
  ~10x larger (1.1 MB at res 15/6, 9.8 MB at res 3/1.5) while the export's own
  segment controls moved the grid by a single row.
"""

from __future__ import annotations

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.exports.core import (
    _geometry_params,
    _inner_grid,
    _prepared_design,
    _stl_grid_plan,
    _stl_mesher_config,
    _surface_grid_plan,
    _write_step,
)
from server.exports.sizing import (
    STL_CHORD_TOLERANCE_MM,
    STL_TRIANGLE_CEILING,
    GridPlan,
    SurfaceDeviation,
    _ceiling_trimmed,
    estimated_triangles,
    measure_deviation,
    plan_grid,
)

SEED_ROSSE = {
    "formula": "R-OSSE",
    "R": 140, "r0": 12.7, "a0": 15.5, "a": 25, "k": 2,
    "m": 0.85, "b": 0.2, "r": 0.4, "q": 3.4, "tmax": 1,
    "mesh": {
        "angular_segments": 40, "corner_segments": 4, "length_segments": 20,
        "throat_resolution": 6, "mouth_resolution": 15,
        "quadrants": 1234, "wall_thickness": 5,
        "rear_resolution": 40, "aperture_resolution_scale": 1.5,
        "max_triangles": 50_000, "allow_large_mesh": 0,
    },
}


def _seed(**mesh: float) -> DesignConfig:
    payload = {**SEED_ROSSE, "mesh": {**SEED_ROSSE["mesh"], **mesh}}
    return DesignConfig.model_validate(payload)


def _params(design: DesignConfig) -> dict:
    return _geometry_params(_stl_mesher_config(_prepared_design(design)))


# --- the measurement itself ----------------------------------------------


def test_deviation_is_measured_against_the_analytic_surface_and_falls_with_size() -> None:
    params = _params(_seed())
    coarse = measure_deviation(params, 48, 28)
    fine = measure_deviation(params, 192, 112)
    assert coarse is not None and fine is not None
    assert fine[0].angular_linear < coarse[0].angular_linear
    assert fine[0].axial_linear < coarse[0].axial_linear
    # Chord error falls as n^-2, so quadrupling the grid should gain about 16x
    # and certainly more than 4x. A constant here would mean the reference
    # sampling, not the geometry, is being measured.
    assert coarse[0].angular_linear / fine[0].angular_linear > 4.0


def test_a_cubic_fit_is_reported_as_closer_than_the_chord_it_replaces() -> None:
    measured = measure_deviation(_params(_seed()), 64, 40)
    assert measured is not None
    deviation = measured[0]
    assert deviation.angular_cubic < deviation.angular_linear
    assert deviation.axial_cubic < deviation.axial_linear


def test_the_cubic_estimate_declares_itself_invalid_on_a_coarse_grid() -> None:
    """It models a degree-3 fit locally, so it must not be trusted everywhere."""

    params = _params(_seed())
    fine = measure_deviation(params, 96, 56)
    coarse = measure_deviation(params, 32, 12)
    assert fine is not None and coarse is not None
    assert fine[0].cubic_estimate_is_valid
    assert not coarse[0].cubic_estimate_is_valid


def test_measurement_reports_the_grid_the_builder_actually_resolved() -> None:
    """Requests are snapped, and every decision must use the snapped counts."""

    measured = measure_deviation(_params(_seed()), 43, 23)
    assert measured is not None
    _, angular, length = measured
    assert angular % 4 == 0
    assert angular >= 43


def test_a_detuned_probe_catches_geometry_hidden_from_the_nested_lattice() -> None:
    """A p-expression must not alias to a false sub-tolerance reading."""

    payload = {
        **SEED_ROSSE,
        "formula": "OSSE",
        "L": 120,
        "a": "45 + 0.1*(1-cos(192*p))",
    }
    for key in ("R", "m", "b", "r", "tmax"):
        payload.pop(key, None)
    design = DesignConfig.model_validate(payload)
    params = _params(design)
    from server.exports.sizing import _distance_to_surface, _point_grid

    aliased, angular, length = _point_grid(params, 96, 56)
    dense, _, _ = _point_grid(params, 4 * angular, 4 * length)
    dense_error = float(_distance_to_surface(dense, aliased).max())
    assert dense_error > 0.5

    plan, _ = _stl_grid_plan(design)

    # The old nested-only check accepted 96x56 at 0.0625 mm because both the
    # candidate and its 2x reference sampled cos(192*p) at the same phase.
    assert (plan.angular, plan.length) != (96, 56)
    assert plan.warning is not None
    assert plan.triangles <= STL_TRIANGLE_CEILING


def test_detuned_cubic_error_can_reject_while_the_chord_is_still_trustworthy() -> None:
    """CAD acceptance must not fall back to an aliased nested cubic reading."""

    payload = {
        **SEED_ROSSE,
        "formula": "OSSE",
        "L": 120,
        "a": "45 + 0.003*(1-cos(192*p))",
    }
    for key in ("R", "m", "b", "r", "tmax"):
        payload.pop(key, None)
    measured = measure_deviation(_params(DesignConfig.model_validate(payload)), 96, 56)

    assert measured is not None
    deviation = measured[0]
    assert deviation.angular_linear < 0.35
    assert deviation.angular_cubic > 0.02


def test_for_fit_rejects_a_pairing_no_export_writes() -> None:
    deviation = SurfaceDeviation(1.0, 0.5, 2.0, 0.25)
    assert deviation.for_fit("linear", "linear") == 2.0
    assert deviation.for_fit("cubic", "cubic") == 0.5
    with pytest.raises(ValueError, match="unsupported export fit"):
        deviation.for_fit("quintic", "linear")


# --- planning -------------------------------------------------------------


def test_a_tighter_tolerance_buys_a_finer_grid() -> None:
    params = _params(_seed())
    loose = plan_grid(params, angular=("linear", 0.3), axial=("linear", 0.3))
    tight = plan_grid(params, angular=("linear", 0.03), axial=("linear", 0.03))
    assert tight.triangles > loose.triangles
    assert tight.deviation_mm is not None and tight.deviation_mm <= 0.03


def test_the_planned_stl_grid_meets_its_print_tolerance() -> None:
    plan, _ = _stl_grid_plan(_seed())
    assert plan.deviation_mm is not None
    assert plan.deviation_mm <= STL_CHORD_TOLERANCE_MM
    assert plan.warning is None


def test_triangle_estimate_matches_two_per_grid_cell() -> None:
    assert estimated_triangles(104, 60) == 12_480


# --- the two measured defects, as regressions -----------------------------


def test_stl_sizing_ignores_the_solver_mesh_resolutions() -> None:
    """Defect 1. Refining a solve used to make the export ~4x denser."""

    default = _stl_grid_plan(_seed())[0]
    refined = _stl_grid_plan(_seed(mouth_resolution=3, throat_resolution=1.5))[0]
    coarse = _stl_grid_plan(_seed(mouth_resolution=40, throat_resolution=20))[0]
    assert default == refined == coarse


def test_stl_sizing_ignores_mesh_max_triangles() -> None:
    """Defect 1. That budget is the solver's advisory warning threshold.

    Reading it turned a warning into a refusal on a mesh the export itself had
    densified, which is the failure the Windows 0.3.1 report described.
    """

    default = _stl_grid_plan(_seed())[0]
    assert _stl_grid_plan(_seed(max_triangles=5_000))[0] == default
    assert _stl_grid_plan(_seed(max_triangles=5_000_000))[0] == default


def test_stl_sizing_ignores_the_designs_own_segment_controls() -> None:
    default = _stl_grid_plan(_seed())[0]
    assert _stl_grid_plan(_seed(angular_segments=200, length_segments=100))[0] == default
    assert _stl_grid_plan(_seed(angular_segments=8, length_segments=4))[0] == default


def test_surface_step_sizing_ignores_the_solver_mesh_too() -> None:
    default = _surface_grid_plan(_seed())
    assert _surface_grid_plan(_seed(mouth_resolution=3, throat_resolution=1.5)) == default


@pytest.mark.parametrize(
    "payload",
    [
        SEED_ROSSE,
        {
            "formula": "OSSE",
            "L": 120,
            "a": 55,
            "a0": 10,
            "r0": 12.7,
            "morph": {
                "target_shape": 3,
                "target_exponent": 4,
                "target_width": 120,
                "target_height": 80,
                "rate": 3,
            },
        },
        {
            "formula": "OSSE",
            "L": 120,
            "r0": 12.7,
            "a": 60,
            "a0": 15.5,
            "guiding_curve": {
                "curve_type": 1,
                "width": 140,
                "aspect_ratio": 1.5,
                "distance": 0.5,
                "rotation": 25,
            },
        },
    ],
    ids=["R-OSSE-mouth-rollback", "superellipse-morph", "rotated-guiding-curve"],
)
def test_written_surface_step_meets_its_chord_target_after_occ_round_trip(
    tmp_path, payload: dict,
) -> None:
    """Probe the file, including loft stations, interiors, and periodic seam."""

    import gmsh

    design = DesignConfig.model_validate(payload)
    plan = _surface_grid_plan(design)
    source = _inner_grid(design, grid=(plan.angular, plan.length))
    analytic = _inner_grid(
        design,
        grid=(2 * source.shape[0], 2 * (source.shape[1] - 1)),
    )
    step_path = tmp_path / "surface.step"
    step_path.write_text(_write_step(source), encoding="utf-8")

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("written-surface-fidelity")
        gmsh.model.occ.importShapes(str(step_path), highestDimOnly=True)
        gmsh.model.occ.synchronize()
        # STEP preserves the ruled section order; sorting geometrically would
        # scramble the R-OSSE mouth rollback, whose last stations turn back.
        surfaces = [tag for _dim, tag in gmsh.model.getEntities(2)]
        assert len(surfaces) == source.shape[1] - 1

        cell_count = len(surfaces)
        cells = set(np.linspace(0, cell_count - 1, min(24, cell_count), dtype=int))
        cells.update(range(max(0, cell_count - 6), cell_count))
        maximum = 0.0
        for cell in sorted(cells):
            # Even columns are analytic loft stations; odd columns are newly
            # evaluated analytic points halfway between adjacent stations.
            for column in (2 * cell, 2 * cell + 1, 2 * cell + 2):
                points = analytic[:, column]
                closest, _ = gmsh.model.getClosestPoint(
                    2, surfaces[cell], points.reshape(-1).tolist()
                )
                maximum = max(
                    maximum,
                    float(
                        np.linalg.norm(
                            points - np.asarray(closest).reshape(-1, 3), axis=1
                        ).max()
                    ),
                )

            low, high = gmsh.model.getParametrizationBounds(2, surfaces[cell])
            midpoint = 0.5 * (low[1] + high[1])
            parameters = [low[0], midpoint, high[0], midpoint]
            seam = np.asarray(
                gmsh.model.getValue(2, surfaces[cell], parameters)
            ).reshape(2, 3)
            normals = np.asarray(
                gmsh.model.getNormal(surfaces[cell], parameters)
            ).reshape(2, 3)
            assert np.linalg.norm(seam[0] - seam[1]) < 1e-7
            assert np.dot(normals[0], normals[1]) > 1.0 - 1e-10

        assert maximum <= STL_CHORD_TOLERANCE_MM
    finally:
        gmsh.finalize()


# --- the backstop warns and trims; it never refuses ------------------------


def test_the_ceiling_trims_and_warns_instead_of_refusing() -> None:
    plan = GridPlan(400, 300, 0.01, estimated_triangles(400, 300))
    trimmed = _ceiling_trimmed(plan, 20_000, 0.05)
    assert trimmed.triangles <= 20_000
    assert trimmed.angular < plan.angular and trimmed.length < plan.length
    assert trimmed.warning is not None
    assert "coarsened" in trimmed.warning


def test_a_plan_inside_the_ceiling_is_left_alone_and_says_nothing() -> None:
    plan = GridPlan(104, 60, 0.09, estimated_triangles(104, 60))
    assert _ceiling_trimmed(plan, STL_TRIANGLE_CEILING, 0.1) is plan


def test_planning_under_a_tiny_ceiling_returns_a_plan_rather_than_raising() -> None:
    plan = plan_grid(
        _params(_seed()),
        angular=("linear", 0.001),
        axial=("linear", 0.001),
        triangle_ceiling=4_000,
    )
    assert plan.triangles <= 4_000
    assert plan.warning is not None


def test_the_backstop_ceiling_leaves_the_useful_fidelity_range_intact() -> None:
    """It must sit clear of real designs, not cut through the middle of them.

    The 50,000-triangle solve threshold that used to gate exports lands inside
    the range a waveguide actually uses (a 240x160 grid is ~78,000 triangles at
    0.017 mm), which is why refusals were reachable from ordinary settings.
    """

    assert STL_TRIANGLE_CEILING > estimated_triangles(240, 160)


def test_measure_deviation_declines_rather_than_guessing_on_a_bad_lattice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misaligned reference must return None, not a number nobody can trust."""

    import server.exports.sizing as sizing

    real = sizing._point_grid
    calls = {"n": 0}

    def shifted(params, angular, length):
        points, n_phi, n_length = real(params, angular, length)
        calls["n"] += 1
        if calls["n"] == 2:
            points = points + 1.0
        return points, n_phi, n_length

    monkeypatch.setattr(sizing, "_point_grid", shifted)
    assert measure_deviation(_params(_seed()), 64, 40) is None


def test_element_size_is_the_grids_own_longest_cell_edge() -> None:
    """Below it gmsh subdivides faces already inside tolerance, for nothing."""

    from server.exports.sizing import _point_grid, facet_element_size_mm

    params = _params(_seed())
    coarse = facet_element_size_mm(params, 48, 28)
    fine = facet_element_size_mm(params, 192, 112)
    assert coarse > fine > 0.0
    points, _, _ = _point_grid(params, 48, 28)
    longest = max(
        float(np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=2).max()),
        float(np.linalg.norm(points[:, 1:] - points[:, :-1], axis=2).max()),
    )
    assert coarse == pytest.approx(longest)


# --- end to end, through the builders that were broken --------------------


def test_the_reported_stl_refusal_now_exports() -> None:
    """The exact Windows 0.3.1 repro: solve fine, export impossible.

    Seed R-OSSE at quadrants=1, mouth 3 mm, throat 1.5 mm solved at 8,171
    triangles and refused to export with "52,468 exceeding the effective limit
    50,000" -- a limit the export reached only because it had densified the
    solve mesh ~7.5x first.
    """

    from server.exports.core import _build_stl_mesh_sync

    design = _seed(quadrants=1, mouth_resolution=3, throat_resolution=1.5)
    built = _build_stl_mesh_sync(design.model_dump(mode="json"))
    triangles = len(built["indices"]) // 3
    assert triangles > 0
    assert not built["warnings"]
    tags = np.asarray(built["surfaceTags"], dtype=int)
    assert int((tags == 1).sum()) < STL_TRIANGLE_CEILING


def test_stl_output_does_not_follow_the_solver_mesh() -> None:
    """Those extra triangles were free: identical geometry, four times the file."""

    from server.exports.core import _build_stl_mesh_sync

    default = _build_stl_mesh_sync(_seed().model_dump(mode="json"))
    refined = _build_stl_mesh_sync(
        _seed(mouth_resolution=3, throat_resolution=1.5).model_dump(mode="json")
    )
    assert len(refined["indices"]) == len(default["indices"])
    assert np.allclose(
        np.asarray(refined["vertices"]), np.asarray(default["vertices"])
    )


def test_solid_step_cost_does_not_follow_the_solver_mesh() -> None:
    """Defect 2. Refining a solve moved this from 1.1 MB to 9.8 MB."""

    from server.exports.core import _build_step_solid_sync

    default = _build_step_solid_sync(_seed().model_dump(mode="json")).step_text
    refined = _build_step_solid_sync(
        _seed(mouth_resolution=3, throat_resolution=1.5).model_dump(mode="json")
    ).step_text
    coarse = _build_step_solid_sync(
        _seed(mouth_resolution=40, throat_resolution=20).model_dump(mode="json")
    ).step_text
    # Byte-identical would also assert OCC's tag numbering; size is the claim.
    assert len(refined) == pytest.approx(len(default), rel=0.02)
    assert len(coarse) == pytest.approx(len(default), rel=0.02)


def test_solid_step_keeps_the_iso_header_order_catia_needs() -> None:
    """The header fix predates this change and must survive it."""

    from server.exports.core import _build_step_solid_sync

    text = _build_step_solid_sync(_seed().model_dump(mode="json")).step_text
    header = text.partition("HEADER;")[2].partition("ENDSEC;")[0]
    positions = [
        header.find(keyword)
        for keyword in ("FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA")
    ]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)
