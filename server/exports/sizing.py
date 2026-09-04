"""Export-owned geometry sizing, decoupled from the solver mesh.

An export is an artifact for CAD or a 3D printer, so how densely it samples the
analytic surface is a *fidelity* question. It used to be a solver question:
``mesh.throat_resolution`` / ``mesh.mouth_resolution`` sized the CAD point grid
and ``mesh.max_triangles`` -- a solve-time warning threshold -- became a hard
export refusal. Refining an acoustic mesh therefore made every STEP roughly
20x slower and 10x larger, and could make an STL impossible to export at all,
while the export's own segment controls moved the result by one grid row.

The sizing here reads none of those fields. Each planner measures how far the
analytic surface actually strays from what the artifact will contain and
refines until that is inside a fixed tolerance:

* ``measure_deviation`` samples the analytic surface at twice a candidate grid
  and measures each sample's *distance to the surface the artifact will
  contain* -- not to the interpolation evaluated at a matching index, which is
  a different place on the surface whenever sampling is non-uniform. It reports
  per direction and per fit, because the exports interpolate differently: STL
  facets are linear, and the solid STEP is a degree-3 fit.
* ``plan_grid`` chooses angular and axial segment counts directly, for the two
  exports that pin their own grid.
* ``plan_cad_resolution`` chooses the millimetre sampling target the solid
  STEP's builder refines against, because ``write_step_from_config`` reaches
  its grid only through that knob.

Measured on the seed R-OSSE against the pinned mesher (35a4426), the cubic
estimate tracks the deviation of the written STEP -- read back through OCC and
probed with gmsh's own closest-point -- to within a factor of 1.5, on the
conservative side.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np

# --- the tolerances, and the one ceiling ---------------------------------

#: Deviation the exported CAD surface may have from the analytic formula.
#: 20 um is an order of magnitude finer than the positional accuracy of any
#: process that consumes the file (CNC finishing ~50 um, SLA XY pixel ~35 um,
#: FDM ~100 um and up). The CAD master is deliberately tighter than the print
#: tolerance below: downstream operations refine against it, so it must not be
#: the accuracy floor. Not a UI knob -- an export has no grid controls.
STEP_SURFACE_TOLERANCE_MM = 0.020

#: Chord deviation the exported triangles may have from the analytic surface.
#: 0.1 mm is at or below every common layer height (FDM 0.1-0.3 mm, SLA
#: 0.025-0.1 mm) and well inside a 0.4 mm nozzle, so it is print resolution
#: rather than screen resolution. Densifying past it buys nothing a printer can
#: reproduce, which is the whole complaint this replaces.
STL_CHORD_TOLERANCE_MM = 0.10

#: Generous backstop on the exported triangle count. It is not a refusal: a
#: plan that would exceed it is trimmed back to it and the export says so.
#: The useful fidelity range for a waveguide tops out near 78,000 triangles,
#: so this sits well clear of it rather than cutting through the middle the way
#: the 50,000-triangle solve threshold did.
STL_TRIANGLE_CEILING = 150_000

#: Corner arcs are sampled at the finest count the export ever used, rather
#: than at a multiple of the user's solve setting. Only rounded-rectangle morph
#: and guiding-curve designs have corner arcs at all.
EXPORT_CORNER_SEGMENTS = 12

_MIN_ANGULAR, _MAX_ANGULAR = 32, 720
_MIN_LENGTH, _MAX_LENGTH = 16, 480
_PROBE_ANGULAR, _PROBE_LENGTH = 96, 56

_MIN_CAD_RESOLUTION_MM, _MAX_CAD_RESOLUTION_MM = 1.0, 64.0
_PROBE_CAD_RESOLUTION_MM = 24.0
#: What the solid STEP samples at when the measurement is unavailable. Finer
#: than any tolerance search would choose, so a fallback is never a fidelity
#: regression -- only a missed saving.
_FALLBACK_CAD_RESOLUTION_MM = 12.0

_GRID_ATTEMPTS = 6
_CAD_ATTEMPTS = 6

# Chord (linear) deviation falls as n^-2; a cubic fit falls far faster. The
# exponents only set the step size of the search, which re-measures after every
# move, so an imperfect one costs an extra probe rather than a wrong answer.
_LINEAR_EXPONENT = 2.0
_CUBIC_EXPONENT = 4.0

#: The cubic estimate models what a degree-3 fit does *locally*, so it stops
#: being conservative once the grid is too coarse for that fit to follow the
#: geometry globally. Measured against the written STEP on the seed R-OSSE
#: (pinned mesher 35a4426), read back through OCC and probed with gmsh's own
#: closest-point, the estimate over-reports true deviation while the chord is
#: at or under about 0.36 mm and under-reports past it:
#:
#:     chord 0.182 -> estimate/true 1.22   chord 0.528 -> 0.59
#:     chord 0.245 -> 1.39                 chord 0.691 -> 0.37
#:     chord 0.355 -> 1.13                 chord 0.901 -> 0.23
#:
#: A cubic acceptance therefore also requires the chord inside this bound. It
#: is a validity floor for the measurement, not a second fidelity target --
#: which is why it is stated in chord terms rather than as a tolerance anyone
#: would read as a promise. On this design it is what binds, and the export
#: lands three orders of magnitude inside its stated tolerance as a result.
_CUBIC_VALIDITY_CHORD_MM = 0.35

# Midpoint of a segment from four consecutive samples. The interior weights are
# the uniform cubic (Catmull-Rom) midpoint; the one-sided sets are the same
# cubic Lagrange polynomial evaluated half a step in from an end.
_CUBIC_INTERIOR = np.array([-1.0, 9.0, 9.0, -1.0]) / 16.0
_CUBIC_FIRST = np.array([0.3125, 0.9375, -0.3125, 0.0625])
_CUBIC_LAST = _CUBIC_FIRST[::-1]


@dataclass(frozen=True)
class SurfaceDeviation:
    """How far the analytic surface strays from each interpolation, in mm."""

    angular_linear: float
    angular_cubic: float
    axial_linear: float
    axial_cubic: float

    def of(self, direction: str, fit: str) -> float:
        """Deviation in one direction, under one interpolation."""

        try:
            return {
                ("angular", "linear"): self.angular_linear,
                ("angular", "cubic"): self.angular_cubic,
                ("axial", "linear"): self.axial_linear,
                ("axial", "cubic"): self.axial_cubic,
            }[(direction, fit)]
        except KeyError:
            raise ValueError(f"unsupported export fit: {direction}/{fit}") from None

    def for_fit(self, angular: str, axial: str) -> float:
        """Deviation for one artifact's own pair of interpolations."""

        return max(self.of("angular", angular), self.of("axial", axial))

    @property
    def cubic_estimate_is_valid(self) -> bool:
        """Whether the grid is fine enough for the cubic estimate to be safe."""

        return max(self.angular_linear, self.axial_linear) <= _CUBIC_VALIDITY_CHORD_MM


@dataclass(frozen=True)
class GridPlan:
    """Segment counts an export pins, and what they were measured to buy."""

    angular: int
    length: int
    deviation_mm: float | None
    triangles: int
    warning: str | None = None


@dataclass(frozen=True)
class CadPlan:
    """Millimetre sampling target for the solid STEP's own grid refinement."""

    resolution_mm: float
    deviation_mm: float | None


def estimated_triangles(angular: int, length: int) -> int:
    """Triangles a pinned grid yields: two per cell, plus a rim allowance.

    Measured against the pinned mesher on the seed R-OSSE, this is within 2%
    of the tag-1 count for every grid in the useful range.
    """

    return 2 * int(angular) * int(length)


def _point_grid(
    params: Mapping[str, Any], angular: int, length: int
) -> tuple[np.ndarray, int, int]:
    """Sample the analytic inner surface at a requested grid size.

    The builder snaps the request (angular counts go to a multiple of four,
    among other rules), so the *resolved* counts come back with the points and
    every caller works from those rather than from what it asked for.
    """

    from hornlab_mesher.config_builder import build_point_grid

    grid = build_point_grid(
        {**params, "angularSegments": int(angular), "lengthSegments": int(length)}
    )
    n_phi = int(grid["grid_n_phi"])
    n_length = int(grid["grid_n_length"])
    points = np.asarray(grid["inner_points"], dtype=float)
    if points.size != n_phi * (n_length + 1) * 3:
        raise RuntimeError("HornLab inner point grid has an inconsistent size")
    return points.reshape(n_phi, n_length + 1, 3), n_phi, n_length


def _cubic_axial_midpoints(points: np.ndarray) -> np.ndarray:
    """Cubic estimate of every axial cell midpoint, one-sided at the ends.

    The ends are not clamped to the chord: an R-OSSE's curvature peaks at the
    mouth roll-back, so a linear end swamps the whole measurement -- it read
    0.090 mm where the written STEP was 0.0004 mm out.
    """

    columns = points.shape[1]
    if columns < 4:
        return 0.5 * (points[:, :-1] + points[:, 1:])
    out = np.empty((points.shape[0], columns - 1, 3), dtype=float)
    w = _CUBIC_INTERIOR
    out[:, 1:-1] = (
        w[0] * points[:, :-3]
        + w[1] * points[:, 1:-2]
        + w[2] * points[:, 2:-1]
        + w[3] * points[:, 3:]
    )
    out[:, 0] = sum(weight * points[:, i] for weight, i in zip(_CUBIC_FIRST, range(4)))
    out[:, -1] = sum(weight * points[:, i] for weight, i in zip(_CUBIC_LAST, range(-4, 0)))
    return out


def _angular_midpoints(grid: np.ndarray, fit: str) -> np.ndarray:
    """Where the written surface sits between two neighbouring profiles."""

    if fit == "linear":
        return 0.5 * (grid + np.roll(grid, -1, axis=0))
    if fit == "cubic":
        ring = [np.roll(grid, shift, axis=0) for shift in (1, 0, -1, -2)]
        return sum(w * r for w, r in zip(_CUBIC_INTERIOR, ring))
    raise ValueError(f"unsupported export fit: {fit}")


def _axial_midpoints(grid: np.ndarray, fit: str) -> np.ndarray:
    """Where the written surface sits between two neighbouring rings."""

    if fit == "linear":
        return 0.5 * (grid[:, :-1] + grid[:, 1:])
    if fit == "cubic":
        return _cubic_axial_midpoints(grid)
    raise ValueError(f"unsupported export fit: {fit}")


def _refined(coarse: np.ndarray, fit: str) -> np.ndarray:
    """Densify a grid to 2x the way the artifact interpolates between samples.

    The result is what the written surface looks like halfway between every
    pair of samples: the chord for ``linear``, a uniform cubic for ``cubic``.
    A cell's middle is the tensor product of the two -- the operator applied
    across the ring midpoints it already has. Averaging the four edge midpoints
    instead drops the curvature correction in one direction, which put the
    cell centres 250x further out than the edges around them and made a cubic
    fit look no better than the chord it replaces.
    """

    n_phi, columns, _ = coarse.shape
    angular_mid = _angular_midpoints(coarse, fit)
    axial_mid = _axial_midpoints(coarse, fit)
    out = np.empty((2 * n_phi, 2 * columns - 1, 3), dtype=float)
    out[::2, ::2] = coarse
    out[1::2, ::2] = angular_mid
    out[::2, 1::2] = axial_mid
    out[1::2, 1::2] = _angular_midpoints(axial_mid, fit)
    return out


def _triangle_distance(
    p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray
) -> np.ndarray:
    """Exact distance from each point to its own triangle, vectorised."""

    ab, ac, ap = b - a, c - a, p - a
    d1 = np.einsum("...i,...i->...", ab, ap)
    d2 = np.einsum("...i,...i->...", ac, ap)
    bp = p - b
    d3 = np.einsum("...i,...i->...", ab, bp)
    d4 = np.einsum("...i,...i->...", ac, bp)
    cp = p - c
    d5 = np.einsum("...i,...i->...", ab, cp)
    d6 = np.einsum("...i,...i->...", ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    denominator = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        v = np.where(denominator != 0.0, vb / denominator, 0.0)
        w = np.where(denominator != 0.0, vc / denominator, 0.0)
        t_ab = np.where((d1 - d3) != 0.0, d1 / (d1 - d3), 0.0)
        t_ac = np.where((d2 - d6) != 0.0, d2 / (d2 - d6), 0.0)
        bc_denominator = (d4 - d3) + (d5 - d6)
        t_bc = np.where(bc_denominator != 0.0, (d4 - d3) / bc_denominator, 0.0)
    closest = a + v[..., None] * ab + w[..., None] * ac
    for mask, candidate in (
        (
            (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0),
            b + np.clip(t_bc, 0.0, 1.0)[..., None] * (c - b),
        ),
        ((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + np.clip(t_ac, 0.0, 1.0)[..., None] * ac),
        ((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + np.clip(t_ab, 0.0, 1.0)[..., None] * ab),
        ((d6 >= 0) & (d5 <= d6), c),
        ((d3 >= 0) & (d4 <= d3), b),
        ((d1 <= 0) & (d2 <= 0), a),
    ):
        closest = np.where(mask[..., None], candidate, closest)
    return np.linalg.norm(p - closest, axis=-1)


def _distance_to_cells(
    reference: np.ndarray,
    surface: np.ndarray,
    row_index: np.ndarray,
    col_index: np.ndarray,
    offsets: tuple[tuple[int, int], ...],
) -> np.ndarray:
    """Distance from each sample to the nearest of a few nearby surface cells.

    Measuring distance to the *surface* rather than to the interpolation
    evaluated at a matching index is what makes this independent of how the
    builder distributes its samples. The index form over-reported a sharp OSSE
    sevenfold -- 0.356 mm against a written 0.052 mm -- because a non-uniform
    axial map puts the same index at a different place on the surface, while it
    tracked the seed R-OSSE to 3% and so looked sound on one design.
    """

    n_phi, columns, _ = surface.shape
    best: np.ndarray | None = None
    for row_offset, col_offset in offsets:
        rows = (row_index + row_offset) % n_phi
        cols = np.clip(col_index + col_offset, 0, columns - 2)
        next_rows = (rows + 1) % n_phi
        corner_00 = surface[rows[:, None], cols[None, :]]
        corner_10 = surface[next_rows[:, None], cols[None, :]]
        corner_11 = surface[next_rows[:, None], cols[None, :] + 1]
        corner_01 = surface[rows[:, None], cols[None, :] + 1]
        distance = np.minimum(
            _triangle_distance(reference, corner_00, corner_10, corner_11),
            _triangle_distance(reference, corner_00, corner_11, corner_01),
        )
        best = distance if best is None else np.minimum(best, distance)
    assert best is not None
    return best


_NEIGHBOURING_CELLS = ((-1, -1), (-1, 0), (0, -1), (0, 0))


def _distance_to_surface(reference: np.ndarray, surface: np.ndarray) -> np.ndarray:
    """Measure a separately sampled reference against a candidate surface.

    The proportional index is only a cell locator.  The distance itself is to
    the nearby triangles, so this remains valid when the builder's axial map is
    non-uniform and corresponding indices are not corresponding points.
    """

    surface_rows, surface_columns, _ = surface.shape
    reference_rows, reference_columns, _ = reference.shape
    rows = np.floor(
        np.arange(reference_rows) * surface_rows / reference_rows
    ).astype(int)
    columns = np.floor(
        np.arange(reference_columns)
        * (surface_columns - 1)
        / max(1, reference_columns - 1)
    ).astype(int)
    columns = np.minimum(columns, surface_columns - 2)
    return _distance_to_cells(
        reference, surface, rows, columns, _NEIGHBOURING_CELLS
    )


def _cubic_weights(position: float, nodes: tuple[int, int, int, int]) -> np.ndarray:
    """Lagrange weights at one parameter position for four sample indices."""

    weights = np.ones(4, dtype=float)
    for index, node in enumerate(nodes):
        for other in nodes:
            if other != node:
                weights[index] *= (position - other) / (node - other)
    return weights


def _cubic_resample_axis(
    points: np.ndarray, target_count: int, *, axis: int, periodic: bool
) -> np.ndarray:
    """Evaluate the planner's cubic model at an independently sampled lattice."""

    source_count = points.shape[axis]
    positions = (
        np.arange(target_count, dtype=float) * source_count / target_count
        if periodic
        else np.linspace(0.0, source_count - 1, target_count)
    )
    samples = []
    for position in positions:
        base = int(math.floor(position))
        if periodic:
            nodes = (base - 1, base, base + 1, base + 2)
            indices = [node % source_count for node in nodes]
        elif base < 1:
            nodes = (0, 1, 2, 3)
            indices = list(nodes)
        elif base >= source_count - 2:
            nodes = tuple(range(source_count - 4, source_count))
            indices = list(nodes)
        else:
            nodes = (base - 1, base, base + 1, base + 2)
            indices = list(nodes)
        selected = np.take(points, indices, axis=axis)
        weights = _cubic_weights(position, nodes)
        shape = [1] * selected.ndim
        shape[axis] = 4
        samples.append(np.sum(selected * weights.reshape(shape), axis=axis))
    return np.stack(samples, axis=axis)


def _cubic_resample(coarse: np.ndarray, rows: int, columns: int) -> np.ndarray:
    angular = _cubic_resample_axis(coarse, rows, axis=0, periodic=True)
    return _cubic_resample_axis(angular, columns, axis=1, periodic=False)


def measure_deviation(
    params: Mapping[str, Any], angular: int, length: int
) -> tuple[SurfaceDeviation, int, int] | None:
    """Measure how far the analytic surface strays from what will be written.

    The reference is the same builder sampled at exactly twice the resolved
    grid, so every odd sample is a point of the analytic surface lying inside a
    cell of the grid the artifact pins. Each is then measured against the
    surface that cell will actually contain -- the chord for a facet, a uniform
    cubic for a fitted patch -- which makes this a direct reading of deviation
    rather than a curvature heuristic, and it needs no extra dependency.

    Returns ``None`` when the doubled grid is not a clean 2x lattice -- a
    sampling mode this comparison cannot be made on -- so callers fall back to a
    fixed conservative size instead of trusting a bad number.
    """

    coarse, n_phi, n_length = _point_grid(params, angular, length)
    fine, fine_phi, fine_length = _point_grid(params, 2 * n_phi, 2 * n_length)
    if fine_phi != 2 * n_phi or fine_length != 2 * n_length:
        return None
    if not np.allclose(fine[::2, ::2], coarse, rtol=0.0, atol=1e-9):
        return None

    # A facet export contains the coarse cells themselves, so each analytic
    # sample is measured against the cell it falls inside. A fitted export
    # contains a smooth surface through the same points, approximated here by
    # its own 2x subdivision -- at that density every sample sits on a vertex,
    # so the four cells meeting there are the ones to measure against.
    rows = np.arange(fine.shape[0])
    cols = np.arange(fine.shape[1])
    linear = _distance_to_cells(
        fine, coarse, rows // 2, np.minimum(cols // 2, n_length - 1), ((0, 0),)
    )
    cubic = _distance_to_cells(
        fine, _refined(coarse, "cubic"), rows, cols, _NEIGHBOURING_CELLS
    )

    # A nested 2x lattice alone can alias an expression-capable surface.  For
    # example, cos(192*p) has exactly the same value at every point of a 96-ring
    # candidate and its 192-ring reference, despite changing between them.  A
    # slightly detuned lattice probes different phases.  This is a bounded
    # numerical check rather than a proof over arbitrary user expressions, but
    # accepting requires both independent samplings to agree.
    detuned, detuned_phi, detuned_length = _point_grid(
        params, 2 * n_phi + 4, 2 * n_length + 1
    )
    if detuned_phi == fine_phi and detuned_length == fine_length:
        return None
    detuned_linear = float(_distance_to_surface(detuned, coarse).max())
    detuned_cubic = float(
        np.linalg.norm(
            detuned
            - _cubic_resample(coarse, detuned_phi, detuned_length + 1),
            axis=2,
        ).max()
    )
    # Samples on an angular edge speak for the ring direction and those on an
    # axial edge for the profile direction; a cell's own middle belongs to both,
    # and is where a two-triangle facet is furthest from the surface.
    face_linear = float(linear[1::2, 1::2].max())
    face_cubic = float(cubic[1::2, 1::2].max())
    deviation = SurfaceDeviation(
        angular_linear=max(float(linear[1::2, ::2].max()), face_linear, detuned_linear),
        angular_cubic=max(float(cubic[1::2, ::2].max()), face_cubic, detuned_cubic),
        axial_linear=max(float(linear[::2, 1::2].max()), face_linear, detuned_linear),
        axial_cubic=max(float(cubic[::2, 1::2].max()), face_cubic, detuned_cubic),
    )
    return deviation, n_phi, n_length


def _scaled(count: int, factor: float, low: int, high: int) -> int:
    return int(min(high, max(low, round(count * factor))))


def _refined_count(count: int, ratio: float, exponent: float, low: int, high: int) -> int:
    """Next count for one direction, from that direction's own error ratio.

    A direction that needs refining always moves by at least one segment. The
    two used to share a single rounded factor, which let a search stall: a grid
    sitting 1% outside its tolerance scaled the axial count by 1.006, rounded
    back to itself, and then spent every remaining attempt refining the
    direction that was already inside.
    """

    factor = min(2.0, max(0.5, ratio ** (1.0 / exponent)))
    scaled = _scaled(count, factor, low, high)
    if ratio > 1.0 and scaled <= count:
        scaled = min(high, count + 1)
    elif ratio < 1.0 and scaled >= count:
        scaled = max(low, count - 1)
    return scaled


def plan_grid(
    params: Mapping[str, Any],
    *,
    angular: tuple[str, float],
    axial: tuple[str, float],
    triangle_ceiling: int | None = None,
) -> GridPlan:
    """Choose the coarsest grid that meets each direction's own tolerance.

    ``angular`` and ``axial`` are each ``(fit, tolerance_mm)``: the fit names
    how the artifact interpolates between samples in that direction, so the
    measurement matches what will actually be written. Refinement is a measured
    loop, not a formula -- every probe reads the real deviation and scales each
    direction from its own share of it, so a design whose curvature a formula
    would misjudge costs an extra probe rather than fidelity.
    """

    angular_fit, angular_tolerance = angular
    axial_fit, axial_tolerance = axial
    wants_cubic = "cubic" in (angular_fit, axial_fit)
    exponent = _CUBIC_EXPONENT if wants_cubic else _LINEAR_EXPONENT
    angular_count, length_count = _PROBE_ANGULAR, _PROBE_LENGTH
    tolerance_for_report = min(angular_tolerance, axial_tolerance)
    best: GridPlan | None = None
    finest: GridPlan | None = None
    for _ in range(_GRID_ATTEMPTS):
        measured = measure_deviation(params, angular_count, length_count)
        if measured is None:
            plan = GridPlan(
                angular_count,
                length_count,
                None,
                estimated_triangles(angular_count, length_count),
            )
            return _ceiling_trimmed(
                best or plan, triangle_ceiling, tolerance_for_report
            )
        deviation, angular_count, length_count = measured
        angular_ratio = deviation.of("angular", angular_fit) / angular_tolerance
        axial_ratio = deviation.of("axial", axial_fit) / axial_tolerance
        plan = GridPlan(
            angular_count,
            length_count,
            deviation.for_fit(angular_fit, axial_fit),
            estimated_triangles(angular_count, length_count),
        )
        if finest is None or plan.triangles > finest.triangles:
            finest = plan
        trustworthy = deviation.cubic_estimate_is_valid or not wants_cubic
        if max(angular_ratio, axial_ratio) <= 1.0 and trustworthy:
            if best is None or plan.triangles < best.triangles:
                best = plan
            # Comfortably inside: one trim toward the requirement, keeping this
            # fit if the smaller grid turns out to miss it.
            if max(angular_ratio, axial_ratio) > 0.5:
                break
        elif best is not None:
            break
        if not trustworthy:
            # Too coarse to trust a cubic reading at all. Refine on the chord,
            # which is measured directly and is valid at every size.
            chord = _CUBIC_VALIDITY_CHORD_MM
            angular_ratio = max(angular_ratio, deviation.angular_linear / chord)
            axial_ratio = max(axial_ratio, deviation.axial_linear / chord)
            step_exponent = _LINEAR_EXPONENT
        else:
            step_exponent = exponent
        next_angular = _refined_count(
            angular_count, angular_ratio, step_exponent, _MIN_ANGULAR, _MAX_ANGULAR
        )
        next_length = _refined_count(
            length_count, axial_ratio, step_exponent, _MIN_LENGTH, _MAX_LENGTH
        )
        if (next_angular, next_length) == (angular_count, length_count):
            break
        angular_count, length_count = next_angular, next_length
    # Nothing met the tolerance inside the attempt budget: ship the finest grid
    # tried, with the deviation it was measured at, rather than an unmeasured
    # guess. The ceiling below still applies.
    return _ceiling_trimmed(
        best or finest or GridPlan(
            angular_count,
            length_count,
            None,
            estimated_triangles(angular_count, length_count),
        ),
        triangle_ceiling,
        tolerance_for_report,
    )


def _ceiling_trimmed(
    plan: GridPlan, ceiling: int | None, tolerance_mm: float
) -> GridPlan:
    """Trim a plan back to the backstop ceiling, and say so. Never refuse."""

    if ceiling is None or plan.triangles <= ceiling:
        return plan
    angular, length = plan.angular, plan.length
    # Rounding a single scale factor can land just over the ceiling, and a
    # backstop that overshoots its own number is not a backstop. Step down
    # until it does not, or until the floors stop it.
    for _ in range(_GRID_ATTEMPTS):
        factor = math.sqrt(ceiling / estimated_triangles(angular, length))
        angular = _scaled(angular, min(factor, 0.99), _MIN_ANGULAR, _MAX_ANGULAR)
        length = _scaled(length, min(factor, 0.99), _MIN_LENGTH, _MAX_LENGTH)
        if estimated_triangles(angular, length) <= ceiling:
            break
        if (angular, length) == (_MIN_ANGULAR, _MIN_LENGTH):
            break
    triangles = estimated_triangles(angular, length)
    return GridPlan(
        angular,
        length,
        None,
        triangles,
        warning=(
            f"This geometry needs about {plan.triangles:,} triangles to hold "
            f"{tolerance_mm:g} mm; the export was coarsened to roughly "
            f"{triangles:,} to stay inside its {ceiling:,}-triangle ceiling, "
            "so fine detail is smoother than the target."
        ),
    )


def plan_cad_resolution(
    config: Mapping[str, Any], *, tolerance_mm: float = STEP_SURFACE_TOLERANCE_MM
) -> CadPlan:
    """Choose the mm sampling target the solid STEP's grid refinement uses.

    ``write_step_from_config`` owns its own curvature-driven refinement and is
    reachable only through the millimetre resolutions, so this searches those
    rather than segment counts. Nothing here reads the design's solver
    resolutions: that coupling is the defect.
    """

    from hornlab_mesher.config_builder import build_geometry_params, resolve_geometry

    resolution = _PROBE_CAD_RESOLUTION_MM
    best: CadPlan | None = None
    for _ in range(_CAD_ATTEMPTS):
        probe = _with_resolution(config, resolution)
        try:
            resolved = resolve_geometry(probe)
            params, _, _ = build_geometry_params(probe)
        except Exception:  # noqa: BLE001 - a probe must never fail the export
            return CadPlan(_FALLBACK_CAD_RESOLUTION_MM, None)
        metadata = resolved.sampling_metadata
        angular = int(metadata.get("geometrySampleAngularSegments") or 0)
        length = int(metadata.get("geometrySampleLengthSegments") or 0)
        if angular < 4 or length < 2:
            return CadPlan(_FALLBACK_CAD_RESOLUTION_MM, None)
        measured = measure_deviation(params, angular, length)
        if measured is None:
            return CadPlan(_FALLBACK_CAD_RESOLUTION_MM, None)
        deviation, _, _ = measured
        value = deviation.for_fit("cubic", "cubic")
        chord = max(deviation.angular_linear, deviation.axial_linear)
        if value <= tolerance_mm and deviation.cubic_estimate_is_valid:
            if best is None or resolution > best.resolution_mm:
                best = CadPlan(resolution, value)
            if value > 0.25 * tolerance_mm:
                break
        elif best is not None:
            break
        if value <= 0.0 and chord <= 0.0:
            break
        # Move on whichever binds. The two terms answer to this knob very
        # differently -- measured on the seed R-OSSE the chord is close to
        # linear in it while the cubic deviation goes as roughly its fourth
        # power -- so each gets its own exponent. Sharing one made the search
        # crawl and time out into the fallback.
        factors = []
        if value > 0.0:
            factors.append((tolerance_mm / value) ** (1.0 / _CUBIC_EXPONENT))
        if chord > 0.0:
            factors.append(_CUBIC_VALIDITY_CHORD_MM / chord)
        if not factors:
            break
        factor = min(1.6, max(0.4, min(factors)))
        next_resolution = min(
            _MAX_CAD_RESOLUTION_MM, max(_MIN_CAD_RESOLUTION_MM, resolution * factor)
        )
        if math.isclose(next_resolution, resolution, rel_tol=1e-3):
            break
        resolution = next_resolution
    if best is not None:
        return best
    # Nothing passed inside the attempt budget. Falling back must never be
    # *coarser* than where the search had already got to, or a design that was
    # being refined toward its tolerance would be handed a worse grid for
    # running out of probes.
    return CadPlan(min(_FALLBACK_CAD_RESOLUTION_MM, resolution), None)


def _with_resolution(config: Mapping[str, Any], resolution: float) -> dict[str, Any]:
    working = dict(config)
    mesh = dict(working.get("mesh") or {})
    mesh["throatResolution"] = float(resolution)
    mesh["mouthResolution"] = float(resolution)
    mesh["rearResolution"] = float(resolution)
    working["mesh"] = mesh
    return working


def facet_element_size_mm(
    params: Mapping[str, Any], angular: int, length: int
) -> float:
    """Element size at which a pinned grid meshes to two triangles per cell.

    With ``preserveGrid`` the CAD faces *are* the grid's cells, so any element
    size below the longest cell edge subdivides faces that already sit inside
    tolerance -- triangles that cost file size and buy no accuracy. Measured on
    the seed R-OSSE against the pinned mesher, the tag-1 count reaches its
    2*A*L floor exactly here and does not fall further above it.
    """

    grid, _, _ = _point_grid(params, angular, length)
    angular_chord = np.linalg.norm(np.roll(grid, -1, axis=0) - grid, axis=2).max()
    axial_chord = np.linalg.norm(grid[:, 1:] - grid[:, :-1], axis=2).max()
    return float(max(angular_chord, axial_chord))
