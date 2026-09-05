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
    _LOFT_SAMPLES_PER_SEGMENT,
    _WRITTEN_CORNER_ARC_SUBDIVISION,
    _bare_grid_config,
    _distance_to_loft,
    _geometry_params,
    _inner_grid,
    _prepared_design,
    _stl_grid_plan,
    _stl_mesher_config,
    _surface_grid_plan,
    _write_step,
    _written_surface_measure,
)
from server.exports.sizing import (
    STL_CHORD_TOLERANCE_MM,
    STL_TRIANGLE_CEILING,
    GridPlan,
    SurfaceDeviation,
    _ceiling_trimmed,
    axial_band_of_column,
    estimated_triangles,
    measure_deviation,
    plan_cad_resolution,
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


#: A rounded-rectangle morph: the geometry whose corner arcs make the angular
#: lattice non-nested, and whose ring samples are uneven enough that the written
#: spline leaves the chord through them behind. Both defects pinned below are on
#: this design.
ROUNDED_RECTANGLE_MORPH = {
    "formula": "OSSE",
    "L": 120,
    "a": 55,
    "a0": 10,
    "r0": 12.7,
    "morph": {
        "target_shape": 1,
        "target_width": 120,
        "target_height": 80,
        "corner_radius": 12,
        "rate": 3,
    },
}


def _seed(**mesh: float) -> DesignConfig:
    payload = {**SEED_ROSSE, "mesh": {**SEED_ROSSE["mesh"], **mesh}}
    return DesignConfig.model_validate(payload)


def _rounded(**mesh: float) -> DesignConfig:
    payload = dict(ROUNDED_RECTANGLE_MORPH)
    if mesh:
        payload["mesh"] = dict(mesh)
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


# --- corner arcs: the lattice does not nest, and the spline is not the chord --


def test_corner_arcs_make_the_doubled_angular_lattice_non_nested() -> None:
    """The premise of both defects, stated as a fact about the builder.

    No angular request reproduces a corner-arc ring at twice its density, so a
    search for one has no answer: 204 does resolve to exactly twice 108 rows,
    and its every-other row is still 17 mm from the coarse one.
    """

    from server.exports.sizing import _point_grid

    params = _params(_rounded())
    coarse, n_phi, n_length = _point_grid(params, 96, 56)
    assert n_phi == 108

    _, doubled_phi, _ = _point_grid(params, 2 * n_phi, 2 * n_length)
    assert doubled_phi != 2 * n_phi

    same_count, count, _ = _point_grid(params, 204, 56)
    assert count == 2 * n_phi
    assert np.abs(same_count[::2] - coarse).max() > 1.0

    # The axial map, by contrast, does nest -- which is what makes the axial
    # direction exactly measurable and the axial band of a sample exactly known.
    refined, refined_phi, refined_length = _point_grid(params, 96, 2 * n_length)
    assert (refined_phi, refined_length) == (n_phi, 2 * n_length)
    assert np.array_equal(refined[:, ::2], coarse)


def test_a_non_nested_lattice_is_measured_instead_of_declined() -> None:
    """Defect 1. The old code returned None here and shipped the probe grid."""

    measured = measure_deviation(_params(_rounded()), 96, 56)
    assert measured is not None
    deviation, angular, length = measured
    assert (angular, length) == (108, 56)
    assert deviation.angular_linear > 0.0 and deviation.axial_linear > 0.0
    # A chord reading, and nothing more: the cubic estimate is a *uniform*
    # cubic, and a ring with corner arcs in it is not uniformly sampled.
    assert not deviation.cubic_modelled
    assert not deviation.cubic_estimate_is_valid


def test_the_searched_cell_measurement_agrees_with_an_exhaustive_one() -> None:
    """The correspondence-free reading must be the real distance, not a proxy.

    The previous attempt at this located cells by proportional index, which on
    a corner-arc ring names a cell some way from the nearest one. Every sample
    is checked here against a brute-force search over every cell of the grid.
    """

    from server.exports.sizing import (
        _point_grid,
        _triangle_distance,
        axial_band_of_column,
        distance_to_facets,
    )

    params = _params(_rounded())
    coarse, n_phi, n_length = _point_grid(params, 96, 56)
    reference, _, reference_length = _point_grid(params, 197, 2 * n_length)
    assert reference_length == 2 * n_length

    band = axial_band_of_column(reference.shape[1], n_length)
    searched = distance_to_facets(reference, coarse, band)

    following = np.roll(coarse, -1, axis=0)
    corner_00 = coarse[:, :-1].reshape(-1, 3)
    corner_10 = following[:, :-1].reshape(-1, 3)
    corner_11 = following[:, 1:].reshape(-1, 3)
    corner_01 = coarse[:, 1:].reshape(-1, 3)
    rng = np.random.default_rng(20260905)
    rows = rng.integers(0, reference.shape[0], 60)
    columns = rng.integers(0, reference.shape[1], 60)
    for row, column in zip(rows, columns):
        point = reference[row, column][None, :]
        exhaustive = np.minimum(
            _triangle_distance(point, corner_00, corner_10, corner_11),
            _triangle_distance(point, corner_00, corner_11, corner_01),
        ).min()
        assert searched[row, column] == pytest.approx(exhaustive, abs=1e-9)


def test_the_written_spline_is_measured_because_the_chord_does_not_bound_it() -> None:
    """Defect 2. A chord target cannot certify this export.

    ``_write_step`` interpolates each ring with OCC's C2 spline. Corner arcs
    land about 1.3 degrees apart between sides spaced 3.6 degrees, and the
    spline overshoots at that step, so the written surface sits further from the
    analytic geometry than the chord through the same points does. On the grid
    this design used to ship, the chord is inside the 0.1 mm target and the
    written surface is half again outside it.
    """

    from server.exports.core import _written_surface_measure

    params = _params(_rounded())
    chord, _, _ = measure_deviation(params, 96, 56)
    written, angular, length = _written_surface_measure(params, 96, 56)

    assert (angular, length) == (108, 56)
    assert chord.angular_linear < STL_CHORD_TOLERANCE_MM
    assert written.angular_linear > 1.5 * chord.angular_linear
    assert written.angular_linear > STL_CHORD_TOLERANCE_MM
    # The axial direction is ruled exactly linearly, so it is the chord and is
    # not inflated by the reading that catches the ring spline.
    assert written.axial_linear == pytest.approx(chord.axial_linear)


def test_the_surface_plan_for_a_corner_design_is_measured_and_inside_target() -> None:
    """Defect 1 and 2 together, at the planner.

    The old planner had no reading for this design at all: ``measure_deviation``
    declined, and ``plan_grid`` shipped its unmeasured 96x56 probe grid, whose
    written file is 0.155 mm out.
    """

    plan = _surface_grid_plan(_rounded())
    assert (plan.angular, plan.length) != (96, 56)
    assert plan.deviation_mm is not None
    assert plan.deviation_mm <= STL_CHORD_TOLERANCE_MM
    assert plan.warning is None
    # Bounded: the target is met by refining, not by exhausting the range.
    assert plan.triangles < STL_TRIANGLE_CEILING


def test_a_plan_names_the_grid_the_builder_resolves_it_to() -> None:
    """A plan's counts are requests, so a reading describes what is written.

    Reporting the resolved count instead made a corner-arc design ask for the
    108 rows it had just measured and get 120, so no reading described the file.
    """

    from server.exports.sizing import _point_grid

    design = _rounded()
    plan = _surface_grid_plan(design)
    written = _inner_grid(design, grid=(plan.angular, plan.length))
    measured, resolved_angular, resolved_length = measure_deviation(
        _params(design), plan.angular, plan.length
    )
    assert written.shape[:2] == (resolved_angular, resolved_length + 1)
    assert plan.triangles == estimated_triangles(resolved_angular, resolved_length)
    del measured

    same, _, _ = _point_grid(_params(design), plan.angular, plan.length)
    assert np.array_equal(same, written)


def test_the_search_steps_past_requests_that_name_the_same_grid() -> None:
    """Angular requests snap, so a one-segment step can measure nothing new.

    131, 133, 134 and 136 all name the same 148-row grid here. A search that
    could not see that spent four of its six probes on it and shipped 0.102 mm
    against a 0.10 mm target.
    """

    from server.exports.sizing import _next_distinct_request, _point_grid

    params = _params(_rounded())
    _, resolved_angular, resolved_length = _point_grid(params, 131, 49)
    assert resolved_angular == 148

    for stuck in (133, 134, 136):
        _, repeated, _ = _point_grid(params, stuck, 49)
        assert repeated == resolved_angular

    angular, length = _next_distinct_request(
        params, (134, 49), (131, 49), (resolved_angular, resolved_length)
    )
    _, moved_angular, _ = _point_grid(params, angular, length)
    assert moved_angular > resolved_angular


def test_the_solid_step_still_falls_back_rather_than_read_a_chord_as_a_fit() -> None:
    """The 0.02 mm solid contract must not be certified by a chord reading.

    ``plan_cad_resolution`` searches a degree-3 fit. The correspondence-free
    reading carries no cubic model, so a corner-arc design has to take the
    fixed conservative sampling -- which is what it took before, when the
    reading was absent entirely.
    """

    from server.exports.sizing import (
        EXPORT_CORNER_SEGMENTS,
        _FALLBACK_CAD_RESOLUTION_MM,
    )
    from server.mesh.builder import _solver_mesher_config

    config = _solver_mesher_config(_rounded(), keep_placement=True)
    config["mesh"] = {
        **(config.get("mesh") or {}),
        "cornerSegments": EXPORT_CORNER_SEGMENTS,
    }
    plan = plan_cad_resolution(config, tolerance_mm=0.02)
    assert plan.deviation_mm is None
    assert plan.resolution_mm <= _FALLBACK_CAD_RESOLUTION_MM


def test_corner_design_exports_ignore_the_solver_mesh_too() -> None:
    """Independence has to survive the new reading, on the new design."""

    default_surface = _surface_grid_plan(_rounded())
    assert (
        _surface_grid_plan(_rounded(mouth_resolution=3, throat_resolution=1.5))
        == default_surface
    )
    default_stl = _stl_grid_plan(_rounded())[0]
    assert (
        _stl_grid_plan(_rounded(angular_segments=200, length_segments=100))[0]
        == default_stl
    )


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


def _mouth_azimuths(grid) -> np.ndarray:
    points = np.asarray(grid["inner_points"]).reshape(
        grid["grid_n_phi"], grid["grid_n_length"] + 1, 3
    )
    return np.degrees(np.arctan2(points[:, -1, 1], points[:, -1, 0])) % 360.0


def test_the_written_surface_reference_samples_inside_the_fixed_corner_arc() -> None:
    """Refining the count cannot reach the arc; the builder's key can.

    The surface planner's reference leans on a private builder control, so this
    pins both halves of why. A rounded-rectangle morph samples its corner arc
    with three intervals whatever the angular count, and a builder that stopped
    honouring the control would silently hand the planner a reference that
    never looks between them -- the blind spot that certified a 0.114 mm grid
    at 0.098 mm. It must also refine only the sampling: the points the two
    grids share have to be the same points.
    """

    from hornlab_mesher.config_builder import build_point_grid
    from hornlab_mesher.profile_sampling import ACOUSTIC_CORNER_ARC_SUBDIVISION_KEY

    params = _geometry_params(_bare_grid_config(_rounded()))

    def grid(angular: int, subdivision: int):
        return build_point_grid({
            **params,
            "angularSegments": angular,
            "lengthSegments": 56,
            ACOUSTIC_CORNER_ARC_SUBDIVISION_KEY: subdivision,
        })

    coarse = grid(147, 1)
    azimuths = np.sort(_mouth_azimuths(coarse))
    above = azimuths[azimuths > 45.0].min()
    below = azimuths[azimuths < 45.0].max()
    # The arc straddling the diagonal is a single interval, and four times the
    # angular budget does not divide it.
    refined = np.sort(_mouth_azimuths(grid(588, 1)))
    assert not ((refined > below + 1e-9) & (refined < above - 1e-9)).any()

    subdivided = grid(147, _WRITTEN_CORNER_ARC_SUBDIVISION)
    inside = _mouth_azimuths(subdivided)
    assert ((inside > below + 1e-9) & (inside < above - 1e-9)).sum() >= 8

    # Same surface, more samples of it: every shared azimuth is the same point,
    # in every column, or the reference would be measuring another geometry.
    coarse_points = np.asarray(coarse["inner_points"]).reshape(
        coarse["grid_n_phi"], coarse["grid_n_length"] + 1, 3
    )
    fine_points = np.asarray(subdivided["inner_points"]).reshape(
        subdivided["grid_n_phi"], subdivided["grid_n_length"] + 1, 3
    )
    fine_azimuths = _mouth_azimuths(subdivided)
    shared = 0
    for row, azimuth in enumerate(_mouth_azimuths(coarse)):
        gap = np.abs(((fine_azimuths - azimuth + 180.0) % 360.0) - 180.0)
        match = int(gap.argmin())
        if gap[match] < 1e-9:
            shared += 1
            assert np.abs(coarse_points[row] - fine_points[match]).max() == 0.0
    assert shared == coarse["grid_n_phi"]


def test_surface_step_rejects_the_grid_that_missed_between_corner_arc_samples() -> None:
    """The old 147x56 plan claimed 0.098353 mm for a 0.1138 mm mouth deviation.

    Raising AngularSegments does not subdivide the fixed rounded corner arc.
    This candidate must be rejected rather than certified from reference
    points that never visit its largest error. Reading it against references
    that subdivide the arc one, four, sixteen and thirty-two times gives
    0.098353, 0.110423, 0.119540 and 0.119540 mm, so the correction is
    converged well inside the subdivision this export uses.
    """

    design = DesignConfig.model_validate(ROUNDED_RECTANGLE_MORPH)
    measured = _written_surface_measure(_geometry_params(_bare_grid_config(design)), 147, 56)
    assert measured is not None
    assert measured[0].angular_linear > STL_CHORD_TOLERANCE_MM


def _strips_of_written_faces(surfaces, n_phi: int, samples: int) -> np.ndarray:
    """Points on the *written file's* surface: both ends of every ruling.

    ``(bands, n_phi * samples + 1, 2, 3)``, the shape ``_distance_to_loft``
    reads. The loft is ruled with ``maxDegree=1``, so the segment joining the
    two boundary samples taken at one parameter lies exactly on the face; both
    ends come from that same face parameterisation, because evaluating the two
    section curves separately would pair them by their own chord lengths and
    skew the strip. Nothing here is rebuilt from the design: the geometry is
    whatever OCC read back out of the STEP.
    """

    import gmsh

    count = n_phi * samples
    strips = np.empty((len(surfaces), count + 1, 2, 3))
    for band, face in enumerate(surfaces):
        low, high = gmsh.model.getParametrizationBounds(2, face)
        u = np.linspace(low[0], high[0], count + 1)
        for side, v in enumerate((low[1], high[1])):
            parameters = np.column_stack((u, np.full_like(u, v))).reshape(-1)
            strips[band, :, side] = np.asarray(
                gmsh.model.getValue(2, face, parameters.tolist())
            ).reshape(-1, 3)
    return strips


# --- an independent reading of the written face, sharing no distance code ----
#
# The production measurement replaces the face between consecutive strip
# samples with a chord and takes a point-to-quad distance. These helpers do
# something different: they use the *ruled* premise directly. ``makeRuled`` with
# ``maxDegree=1`` makes the face degree one in v, so at a fixed surface
# parameter u it traces exactly the straight segment between its two boundary
# points, and the face is the union of those segments. The exact distance from a
# point to the face is therefore
#
#     min over u of  dist(point, segment(S(u, v_lo), S(u, v_hi)))
#
# a one-dimensional minimisation whose inner term is closed form. Nothing below
# calls ``_distance_to_loft``, ``quad_distance`` or ``distance_to_facets``, so a
# defect in the production kernel cannot hide in the number it is checked
# against. The premise itself is verified rather than assumed, by
# ``_off_ruling_residual``.


def _ruling_ends(face, u_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Both ends of the ruling at each surface parameter, from the face itself."""

    import gmsh

    low, high = gmsh.model.getParametrizationBounds(2, face)
    ends = []
    for v in (low[1], high[1]):
        parameters = np.column_stack((u_values, np.full_like(u_values, v))).reshape(-1)
        ends.append(
            np.asarray(gmsh.model.getValue(2, face, parameters.tolist())).reshape(-1, 3)
        )
    return ends[0], ends[1]


def _point_to_segments(point: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """Closed-form distance from one point to many segments."""

    edge = ends - starts
    length = (edge * edge).sum(-1)
    offset = point[None, :] - starts
    position = np.clip(
        np.where(length > 0.0, (offset * edge).sum(-1) / np.where(length > 0.0, length, 1.0), 0.0),
        0.0, 1.0,
    )
    delta = offset - position[:, None] * edge
    return np.sqrt((delta * delta).sum(-1))


def _off_ruling_residual(face, samples: int = 129) -> float:
    """How far an interior isoparametric point sits off its own straight ruling.

    The premise every reading below rests on. If the face were not degree one
    in v this is where it would show, and the minimisation would be measuring a
    surface the file does not contain.
    """

    import gmsh

    low, high = gmsh.model.getParametrizationBounds(2, face)
    u = np.linspace(low[0], high[0], samples)
    start, end = _ruling_ends(face, u)
    worst = 0.0
    for fraction in (0.25, 0.5, 0.75):
        v = low[1] + fraction * (high[1] - low[1])
        parameters = np.column_stack((u, np.full_like(u, v))).reshape(-1)
        interior = np.asarray(
            gmsh.model.getValue(2, face, parameters.tolist())
        ).reshape(-1, 3)
        edge = end - start
        length = (edge * edge).sum(-1)
        offset = interior - start
        position = np.clip(
            np.where(length > 0.0, (offset * edge).sum(-1) / np.where(length > 0.0, length, 1.0), 0.0),
            0.0, 1.0,
        )
        delta = offset - position[:, None] * edge
        worst = max(worst, float(np.sqrt((delta * delta).sum(-1)).max()))
    return worst


def _golden_minimum(face, point: np.ndarray, lo: float, hi: float, iterations: int = 80):
    """Golden-section on one u bracket. Returns (distance, u, iterate_gap)."""

    ratio = (np.sqrt(5.0) - 1.0) / 2.0
    left, right = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    values = []

    def at(u: float) -> float:
        start, end = _ruling_ends(face, np.array([u]))
        return float(_point_to_segments(point, start, end)[0])

    f_left, f_right = at(left), at(right)
    for _ in range(iterations):
        if f_left <= f_right:
            hi, right, f_right = right, left, f_left
            left = hi - ratio * (hi - lo)
            f_left = at(left)
        else:
            lo, left, f_left = left, right, f_right
            right = lo + ratio * (hi - lo)
            f_right = at(right)
        values.append(min(f_left, f_right))
    best = min(f_left, f_right)
    tail = values[-8:]
    return best, (left if f_left <= f_right else right), float(max(tail) - min(tail))


def _independent_distance_to_written_face(faces, bands, point: np.ndarray, scan: int = 4097):
    """Exact distance from one point to the written faces, by ruling search.

    Seeded by a scan dense enough that the bracket around its discrete minimum
    contains the continuous one -- the distance along the ring is 1-Lipschitz in
    arc length, so a scan step bounds how far the true minimum can hide -- and
    then refined. Two different brackets are refined independently and must
    agree, so the answer is convergent rather than a lucky seed.
    """

    import gmsh

    best = (np.inf, None, None, None)
    for band in bands:
        face = faces[band]
        low, high = gmsh.model.getParametrizationBounds(2, face)
        u = np.linspace(low[0], high[0], scan)
        start, end = _ruling_ends(face, u)
        coarse = _point_to_segments(point, start, end)
        index = int(coarse.argmin())
        if coarse[index] < best[0]:
            best = (float(coarse[index]), band, u, index)
    _coarse, band, u, index = best
    face = faces[band]
    narrow, u_narrow, gap = _golden_minimum(
        face, point, u[max(index - 1, 0)], u[min(index + 1, len(u) - 1)]
    )
    wide, _u_wide, wide_gap = _golden_minimum(
        face, point, u[max(index - 4, 0)], u[min(index + 4, len(u) - 1)]
    )
    return {
        "distance": min(narrow, wide),
        "band": band,
        "u": u_narrow,
        # The face's whole parameter span, so a caller can size a strip
        # interval without knowing how densely the seed scan was taken.
        "u_range": float(u[-1] - u[0]),
        "iterate_gap": max(gap, wide_gap),
        "seed_gap": abs(narrow - wide),
    }


def _chord_sagitta(face, u_star: float, step: float) -> float:
    """Largest chord-to-face deviation over the strip intervals around ``u*``.

    This is the bound the production reading's own approximation licenses: it
    replaces the face between consecutive strip samples with a chord, so the
    distance it reports cannot differ from the true one by more than how far
    that chord departs from the face. Measured here from the file rather than
    assumed, which is what makes the agreement check below a real constraint
    instead of a restatement of the expected number.
    """

    edges = u_star + step * np.arange(-2, 3, dtype=float)
    start, end = _ruling_ends(face, edges)
    middles = 0.5 * (edges[:-1] + edges[1:])
    mid_start, mid_end = _ruling_ends(face, middles)
    chord_start = 0.5 * (start[:-1] + start[1:])
    chord_end = 0.5 * (end[:-1] + end[1:])
    return max(
        float(np.linalg.norm(mid_start - chord_start, axis=1).max()),
        float(np.linalg.norm(mid_end - chord_end, axis=1).max()),
    )


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
        ROUNDED_RECTANGLE_MORPH,
    ],
    ids=[
        "R-OSSE-mouth-rollback",
        "superellipse-morph",
        "rotated-guiding-curve",
        "rounded-rectangle-morph",
    ],
)
def test_written_surface_step_meets_its_chord_target_after_occ_round_trip(
    tmp_path, payload: dict,
) -> None:
    """Probe the file, including loft stations, interiors, and periodic seam.

    The deviation is measured, not projected. ``gmsh.model.getClosestPoint``
    used to take this reading, and it over-reports on a trimmed ruled band at
    its boundary: on the 248x56 rounded-rectangle grid it returned 0.169936 mm
    for a mouth-ring sample 0.092936 mm from that same written face, and on the
    grid before it 0.199449 mm for a point 0.1120 mm out. It converges to a
    local minimum, and asking the two neighbouring bands as well -- which is
    what an earlier fix here did, after one band alone read 0.513322 mm for a
    point 0.152655 mm from the surface -- narrows the failure without removing
    it. So the faces are sampled instead: every ruling is taken from the file's
    own geometry, and the reading is a point-to-quad distance between
    neighbouring rulings.

    That reading is exact against the quad strip, not against the face, and it
    is worth being precise about which parts of it are approximations and which
    way each one leans. There are four, not one:

    1. *The ruling in v is exact.* ``makeRuled`` with ``maxDegree=1`` makes the
       face degree one in v, so the segment between the two boundary samples at
       one parameter lies on the face. Measured, not assumed --
       ``_off_ruling_residual`` puts interior isoparametric points on their own
       ruling to about 1e-13 mm.
    2. *The reference is a finite set of points, so the maximum it finds is a
       lower bound on the true maximum over the surface.* One-sided, and it
       under-reads. ``_WRITTEN_SAMPLING_MARGIN`` in the planner exists for this.
    3. *The quad between two rulings stands in for the spline through them, and
       its error is two-sided.* The quad's corners are on the face but its
       interior is not, so whether the chord falls nearer to or further from a
       reference point depends on which side of the local curvature that point
       sits. Measured against the independent minimisation below on the
       rounded-rectangle morph: at 16 samples per segment the readings ran from
       0.00012 mm below the true distance to 0.00067 mm above it over a spread
       of sampled points, and at the point that sets the maximum it reads high.
    4. *The cell and band search is bounded, so it can only over-read* -- see
       ``_distance_to_loft``. It costs nothing measurable here, but it is a
       second approximation and it leans the other way from (3).

    (3) converges. Holding the reference fixed and doubling only the strip
    density, the rounded-rectangle maximum moved 0.094490 mm -> 0.094005 mm,
    which is second order: the directly measured error at 16 samples was
    0.00067 mm, and 4/3 of the 0.00048 mm difference predicts 0.00065 mm. Note
    that the difference between two densities is not itself a bound on either --
    the bound asserted below is measured from the file, as the largest departure
    of the strip chord from the face near the point being checked.

    None of this is a proof over every design; it is a measurement with stated
    limits, and acceptance keeps ``_WRITTEN_SAMPLING_MARGIN`` of the reading in
    hand on top of it.

    Every column of the reference is measured, not a sample of the bands: this
    reading costs no OCC projection, so there is no longer a budget to spend.
    """

    import gmsh

    design = DesignConfig.model_validate(payload)
    plan = _surface_grid_plan(design)
    source = _inner_grid(design, grid=(plan.angular, plan.length))
    # The morph builder keeps three arc intervals regardless of the requested
    # angular count. A merely 2x/8x angular grid therefore never probes between
    # those arc samples. Refine them separately in this independent reference.
    from hornlab_mesher.config_builder import build_point_grid
    from hornlab_mesher.profile_sampling import ACOUSTIC_CORNER_ARC_SUBDIVISION_KEY

    reference = build_point_grid({
        **_geometry_params(_bare_grid_config(design)),
        "angularSegments": 2 * source.shape[0],
        "lengthSegments": 2 * (source.shape[1] - 1),
        ACOUSTIC_CORNER_ARC_SUBDIVISION_KEY: 16,
    })
    analytic = np.asarray(reference["inner_points"]).reshape(
        reference["grid_n_phi"], reference["grid_n_length"] + 1, 3,
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
        # Even columns of the reference are analytic loft stations; odd columns
        # are newly evaluated analytic points halfway between adjacent ones. A
        # station lies on the shared edge of two bands, so the search covers the
        # band each column falls in and its neighbours.
        bands = axial_band_of_column(analytic.shape[1], source.shape[1] - 1)
        readings = _distance_to_loft(
            analytic,
            _strips_of_written_faces(
                surfaces, source.shape[0], _LOFT_SAMPLES_PER_SEGMENT
            ),
            bands,
            _LOFT_SAMPLES_PER_SEGMENT,
        )
        maximum = float(readings.max())

        # The premise the independent reading rests on, checked on the file
        # rather than taken from how the loft was built.
        for probe in (0, cell_count // 2, cell_count - 1):
            assert _off_ruling_residual(surfaces[probe]) < 1e-9

        # Check the reading that decides this test against a minimisation that
        # shares no code with it. One point keeps the cost to a fraction of a
        # second on top of a round trip that already dominates the runtime.
        row, column = np.unravel_index(int(readings.argmax()), readings.shape)
        worst = analytic[row, column]
        base = int(bands[column])
        neighbourhood = sorted({
            max(base - 1, 0), base, min(base + 1, cell_count - 1),
        })
        independent = _independent_distance_to_written_face(
            surfaces, neighbourhood, worst
        )
        # Converged, and not on one lucky bracket: two seeds, and the last
        # iterates of each no longer move.
        assert independent["iterate_gap"] < 1e-9
        assert independent["seed_gap"] < 1e-9
        # The tolerance itself, decided without the production distance kernel.
        assert independent["distance"] <= STL_CHORD_TOLERANCE_MM
        # And the production reading has to agree with it to within what its own
        # chord approximation licenses, measured from the file at this point.
        # Twice the sagitta leaves room for an asymmetric interval and for the
        # nearest point falling in the neighbouring one; the floor keeps a flat
        # patch, where the sagitta is zero, from asserting exact equality.
        sagitta = _chord_sagitta(
            surfaces[independent["band"]],
            independent["u"],
            independent["u_range"] / (source.shape[0] * _LOFT_SAMPLES_PER_SEGMENT),
        )
        assert abs(float(readings[row, column]) - independent["distance"]) <= (
            2.0 * sagitta + 1e-6
        )
        for cell in range(cell_count):
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
    """A misaligned reference must return None, not a number nobody can trust.

    Every reference after the candidate is shifted, so neither comparison is
    available: the nested 2x lattice does not line up, and the axially refined
    grid the correspondence-free path needs does not either.
    """

    import server.exports.sizing as sizing

    real = sizing._point_grid
    calls = {"n": 0}

    def shifted(params, angular, length):
        points, n_phi, n_length = real(params, angular, length)
        calls["n"] += 1
        if calls["n"] > 1:
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
