"""Contract-compatible STEP, binary STL, and profile CSV builders."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import logging
import math
from pathlib import Path
import re
import struct
import tempfile
from typing import TYPE_CHECKING, Any, Mapping

import numpy as np

from server.design.schema import DesignConfig, Expr
from server.exports.sizing import (
    EXPORT_CORNER_SEGMENTS,
    STEP_SURFACE_TOLERANCE_MM,
    STL_CHORD_TOLERANCE_MM,
    STL_TRIANGLE_CEILING,
    GridPlan,
    SurfaceDeviation,
    axial_band_of_column,
    facet_element_size_mm,
    measure_deviation,
    plan_cad_resolution,
    plan_grid,
    quad_distance,
)
from server.exports.sizing import _point_grid
from server.mesh.builder import _solver_mesher_config, _triangles_and_tags
from server.mesh.gmsh_worker import _preserve_native_windows_path, run_on_gmsh_worker
from server.preview.translate import design_to_mesher_config

if TYPE_CHECKING:
    from hornlab_mesher.cad import CadInfo


logger = logging.getLogger(__name__)

MAX_EXPORT_SEGMENT_INPUT = 1_000_000.0


@dataclass(frozen=True)
class StepSolidResult:
    """A solid STEP document and the mesher's description of its CAD body."""

    step_text: str
    cad_info: CadInfo


@dataclass(frozen=True)
class StlResult:
    """Binary STL bytes, plus anything the export had to say about sizing."""

    data: bytes
    warning: str | None = None


def _number(value: Expr | None, fallback: float) -> float:
    if value is not None:
        number = value.constant_value()
        if number is not None and math.isfinite(number):
            return float(number)
        raise ValueError("export mesh controls must be finite scalar expressions")
    return fallback


def _segment_number(value: Expr | None, fallback: float, field: str) -> float:
    number = _number(value, fallback)
    if abs(number) > MAX_EXPORT_SEGMENT_INPUT:
        raise ValueError(
            f"{field} is outside the supported export range of "
            f"±{int(MAX_EXPORT_SEGMENT_INPUT)}"
        )
    return number


def validate_export_segments(design: DesignConfig) -> None:
    """Reject segment controls an export cannot make sense of.

    The geometry exports no longer *read* these fields -- they size themselves
    from a deviation tolerance (``server/exports/sizing.py``). They are still
    validated here because the design carries them for the solver and for the
    profile CSVs, and a non-finite or absurd value should be a 422 rather than
    something the export silently ignores.
    """

    mesh = design.root.mesh
    _segment_number(mesh.length_segments, 0, "mesh.length_segments")
    _segment_number(mesh.angular_segments, 0, "mesh.angular_segments")
    _segment_number(mesh.corner_segments, 0, "mesh.corner_segments")


def _profile_angular(value: float) -> int:
    count = max(4, int(math.floor(value + 0.5)))
    if count % 4 == 0:
        return count
    return max(8, int(math.ceil(count / 8.0) * 8))


def _prepared_design(
    design: DesignConfig,
    *,
    grid: tuple[int, int] | None = None,
    profile_sampling: bool = False,
) -> DesignConfig:
    """Reopen the full domain and strip everything a part does not have.

    ``grid`` pins the export's own sampling, chosen by the planners in
    ``server/exports/sizing.py``. Without it this is the probe form: the
    design's own counts stand, which is what a planner measures from before it
    decides. Profile CSVs are the exception -- their rows *are* the artifact,
    so they keep the design's counts verbatim (v1 compatibility).
    """

    validate_export_segments(design)
    payload = copy.deepcopy(design.model_dump(mode="json"))
    root = payload
    if profile_sampling:
        original_length = _segment_number(
            design.root.mesh.length_segments, 40, "mesh.length_segments"
        )
        root["mesh"]["length_segments"] = max(1, int(math.floor(original_length + 0.5)))
        root["mesh"]["angular_segments"] = _profile_angular(
            _number(design.root.mesh.angular_segments, 40)
        )
    else:
        if grid is not None:
            root["mesh"]["angular_segments"] = int(grid[0])
            root["mesh"]["length_segments"] = int(grid[1])
        # Corner arcs get the finest count the export ever used rather than a
        # multiple of the solve setting; only morph and guiding-curve designs
        # have corner arcs at all.
        root["mesh"]["corner_segments"] = EXPORT_CORNER_SEGMENTS
    root["mesh"]["quadrants"] = 1234
    root["mesh"]["wall_thickness"] = 0
    root["mesh"]["vertical_offset"] = 0
    if root.get("enclosure") is not None:
        root["enclosure"]["depth"] = 0
    root.setdefault("simulation", {})["sim_type"] = "freestanding"
    return DesignConfig.model_validate(root)


def _bare_grid_config(
    design: DesignConfig,
    *,
    grid: tuple[int, int] | None = None,
    profile_sampling: bool = False,
) -> dict[str, Any]:
    prepared = _prepared_design(
        design,
        grid=grid,
        profile_sampling=profile_sampling,
    )
    config = design_to_mesher_config(prepared)
    config["mode"] = "bare"
    config.pop("enclosure", None)
    mesh = config.setdefault("mesh", {})
    mesh.update(
        {
            "quadrants": 1234,
            "wallThickness": 0.0,
            "verticalOffset": 0.0,
            "scaleToMetres": False,
        }
    )
    return config


def _inner_grid(
    design: DesignConfig,
    *,
    grid: tuple[int, int] | None = None,
    profile_sampling: bool = False,
) -> np.ndarray:
    try:
        from hornlab_mesher.viewport import build_viewport_geometry_from_config
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-waveguide-mesher viewport geometry is unavailable; install the pinned server requirements"
        ) from exc
    geometry = build_viewport_geometry_from_config(
        _bare_grid_config(
            design,
            grid=grid,
            profile_sampling=profile_sampling,
        )
    )
    grid = geometry.get("grid") if isinstance(geometry, Mapping) else None
    if not isinstance(grid, Mapping):
        raise RuntimeError("HornLab mesher returned no inner point grid")
    n_phi = int(grid.get("grid_n_phi") or 0)
    n_length = int(grid.get("grid_n_length") or 0)
    points = np.asarray(grid.get("inner_points"), dtype=float)
    expected = n_phi * (n_length + 1) * 3
    if n_phi < 4 or n_length < 1 or points.size != expected:
        raise RuntimeError(
            f"HornLab inner point grid has {points.size} values; expected {expected}"
        )
    points = points.reshape(n_phi, n_length + 1, 3)
    if not np.isfinite(points).all():
        raise RuntimeError("HornLab inner point grid contains non-finite coordinates")
    return points


# ISO 10303-21 wants file_description before file_name; OpenCASCADE 7.7 and
# later write them the other way round, so every gmsh from 4.12 on emits a
# header that strict readers (CATIA) reject -- they take the product structure
# and drop the shapes. hornlab_mesher.cad.normalise_step_header does the same
# job for the solid export; this narrower swap is kept local because the mesher
# is version-pinned and the header OCC hands us here is always these three
# statements. Fold the two together at the next mesher pin bump.
_HEADER_SWAP = re.compile(
    r"(?P<name>FILE_NAME\s*\(.*?\);)(?P<gap>\s*)(?P<description>FILE_DESCRIPTION\s*\(.*?\);)",
    re.DOTALL,
)


def _normalise_step_header(step_text: str) -> str:
    header, separator, rest = step_text.partition("ENDSEC;")
    if not separator:
        return step_text
    fixed, swapped = _HEADER_SWAP.subn(
        lambda match: match["description"] + match["gap"] + match["name"], header, count=1
    )
    return fixed + separator + rest if swapped else step_text


def _assert_step(step_text: str) -> None:
    required = (
        "ISO-10303-21",
        "END-ISO-10303-21",
        "ADVANCED_FACE",
        "B_SPLINE_SURFACE",
    )
    if not step_text.strip() or any(token not in step_text for token in required):
        raise RuntimeError("STEP export did not contain valid surface geometry")
    header = step_text.partition("HEADER;")[2].partition("ENDSEC;")[0]
    positions = [
        header.find(keyword)
        for keyword in ("FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA")
    ]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise RuntimeError(
            "STEP header is not in ISO 10303-21 order (file_description, "
            "file_name, file_schema); strict CAD readers reject it"
        )


def _write_step(inner_points: np.ndarray) -> str:
    import gmsh

    initialized_here = False
    step_path: Path | None = None
    try:
        if not gmsh.isInitialized():
            with _preserve_native_windows_path():
                gmsh.initialize()
            initialized_here = True
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-8)
        gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-8)
        gmsh.clear()
        gmsh.model.add("WaveguideInnerSurface")
        wire_tags: list[int] = []
        construction_curves: list[int] = []
        n_phi, n_columns, _ = inner_points.shape
        for column in range(n_columns):
            point_tags = [
                int(gmsh.model.occ.addPoint(*(float(value) for value in inner_points[index, column])))
                for index in range(n_phi)
            ]
            point_tags.append(point_tags[0])
            # The grid points sample the analytic profile, so the profile curve
            # must interpolate them.  ``addBSpline`` treats the same points as
            # control poles and pulls the curve inside the intended ring;
            # ``addSpline`` constructs OCC's interpolating B-spline instead.
            # Repeating the first point gives OCC a periodic, tangent-continuous
            # seam (also after STEP round-trip), rather than a separate closing
            # chord.
            curve = int(gmsh.model.occ.addSpline(point_tags))
            construction_curves.append(curve)
            wire_tags.append(int(gmsh.model.occ.addWire([curve], checkClosed=True)))
        gmsh.model.occ.addThruSections(
            wire_tags, makeSolid=False, makeRuled=True, maxDegree=1
        )
        gmsh.model.occ.remove([(1, tag) for tag in construction_curves], recursive=True)
        gmsh.model.occ.synchronize()
        with tempfile.NamedTemporaryFile(prefix="waveguide-inner-", suffix=".step", delete=False) as handle:
            step_path = Path(handle.name)
        gmsh.write(str(step_path))
        text = _normalise_step_header(
            step_path.read_text(encoding="utf-8", errors="replace")
        )
        _assert_step(text)
        return text
    finally:
        if step_path is not None:
            step_path.unlink(missing_ok=True)
        if initialized_here and gmsh.isInitialized():
            with _preserve_native_windows_path():
                gmsh.finalize()


def _geometry_params(config: Mapping[str, Any]) -> dict[str, Any]:
    """The analytic geometry a mesher config describes, for the planners."""

    from hornlab_mesher.config_builder import build_geometry_params

    params, _, _ = build_geometry_params(dict(config))
    return params


#: Polyline samples per grid segment when the written loft is measured. The
#: samples lie on the real surface, while the triangles between them approximate
#: its angular curvature. Measured on the rounded-rectangle morph, grid points
#: that lie exactly on the loft read 0.0080 mm at 8 samples, 0.0032 at 16 and
#: 0.0016 at 24. Sixteen keeps that approximation error to about a thirtieth of
#: the target at a third of the cost of 24; the independent convergence sweep
#: and the acceptance margin below cover the remaining sampling error.
_LOFT_SAMPLES_PER_SEGMENT = 16
#: Grid segments either side of the located one that the fine search covers.
#: The loft's own parameterisation is within about 1.5x of uniform across a
#: segment, so two is roughly a factor of three of headroom.
_LOFT_SEARCH_WINDOW = 2
#: Angular density of the reference the written loft is measured against, as a
#: multiple of the requested count, and a detuning offset so the reference is
#: not phase-locked to the candidate.
#:
#: Two is not enough here, and this is the one place where that is not obvious.
#: The overshoot is a narrow spike where a straight side meets its corner arc --
#: the builder always samples that arc at the same four angles whatever the
#: count, so the sample spacing steps by a factor of about 2.7 across a single
#: knot. On the 156x50 grid the converged reading is about 0.10146 mm; a 2x
#: reference reads about 0.09934 mm and a 3x reference about 0.1000 mm, so a
#: search believing the 2x reading accepts a grid that misses the target.
#: Sampled at 8x with 16 strip samples the reading is about 0.10090 mm.
_WRITTEN_REFERENCE_MULTIPLE = 8
_WRITTEN_REFERENCE_OFFSET = 9
#: What the dense reference still cannot see. A sampled maximum is a lower
#: bound on the real one, and the spike above is narrow enough that lower-density
#: phases read below the roughly 0.10146 mm converged value. Accepting requires
#: the reading inside the tolerance with 5% room, several times the largest
#: shortfall observed across the 8x--128x convergence sweep. It
#: tightens acceptance and never loosens it, and it applies only to this
#: reading -- the chord it is taken alongside is converged at 2x.
_WRITTEN_SAMPLING_MARGIN = 0.05


def _loft_strips(inner_points: np.ndarray, samples: int) -> np.ndarray:
    """Sample the ruled loft ``_write_step`` builds, band by band, in process.

    Returns ``(bands, samples * n_phi + 1, 2, 3)``: for each axial band, a
    closed run of points on the section curve at each end of it. The loft is
    ``makeRuled=True, maxDegree=1``, so the line joining corresponding boundary
    samples is an exact ruling. Triangulating between neighbouring rulings is a
    controlled approximation of the angular spline, checked by the convergence
    sweep above. Sampling the two section splines separately would be invalid:
    OCC gives each
    curve its own chord-length parameterisation, and pairing them by normalised
    parameter skews the strip badly enough to read 4.7 mm on a surface that is
    0.15 mm out.
    """

    import gmsh

    initialized_here = False
    try:
        if not gmsh.isInitialized():
            with _preserve_native_windows_path():
                gmsh.initialize()
            initialized_here = True
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-8)
        gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-8)
        gmsh.clear()
        gmsh.model.add("WaveguideInnerSurfaceProbe")
        n_phi, n_columns, _ = inner_points.shape
        wire_tags = []
        for column in range(n_columns):
            point_tags = [
                int(gmsh.model.occ.addPoint(*(float(v) for v in inner_points[index, column])))
                for index in range(n_phi)
            ]
            point_tags.append(point_tags[0])
            curve = int(gmsh.model.occ.addSpline(point_tags))
            wire_tags.append(int(gmsh.model.occ.addWire([curve], checkClosed=True)))
        gmsh.model.occ.addThruSections(
            wire_tags, makeSolid=False, makeRuled=True, maxDegree=1
        )
        gmsh.model.occ.synchronize()
        faces = [tag for _dim, tag in gmsh.model.getEntities(2)]
        if len(faces) != n_columns - 1:
            raise RuntimeError(
                f"the probe loft has {len(faces)} bands for {n_columns} sections"
            )
        count = n_phi * samples
        strips = np.empty((n_columns - 1, count + 1, 2, 3), dtype=float)
        for band, face in enumerate(faces):
            low, high = gmsh.model.getParametrizationBounds(2, face)
            u = np.linspace(low[0], high[0], count + 1)
            for side, v in enumerate((low[1], high[1])):
                parameters = np.column_stack((u, np.full_like(u, v)))
                strips[band, :, side] = np.asarray(
                    gmsh.model.getValue(2, face, parameters.reshape(-1).tolist())
                ).reshape(-1, 3)
        if not np.isfinite(strips).all():
            raise RuntimeError("the probe loft evaluated to non-finite coordinates")
        return strips
    finally:
        if initialized_here and gmsh.isInitialized():
            with _preserve_native_windows_path():
                gmsh.finalize()


def _nearest_ruling(
    points: np.ndarray, strip: np.ndarray, candidates: np.ndarray
) -> np.ndarray:
    """Index into ``candidates`` of the closest ruling, for each point.

    A ruling is the straight segment across one band at one position around the
    ring. ``candidates`` is either every ruling (shape ``(k,)``, shared by all
    points) or one shortlist per point (shape ``(len(points), k)``).
    """

    start = strip[candidates, 0]
    end = strip[candidates, 1]
    if start.ndim == 2:
        start, end = start[None], end[None]
    edge = end - start
    offset = points[:, None, :] - start
    length = (edge * edge).sum(-1)
    position = np.where(
        length > 0.0, (offset * edge).sum(-1) / np.where(length > 0.0, length, 1.0), 0.0
    )
    closest = start + np.clip(position, 0.0, 1.0)[..., None] * edge
    delta = points[:, None, :] - closest
    return (delta * delta).sum(-1).argmin(axis=1)


def _distance_to_loft(
    reference: np.ndarray,
    strips: np.ndarray,
    base_band: np.ndarray,
    samples: int,
    *,
    band_halo: int = 1,
    window: int = _LOFT_SEARCH_WINDOW,
) -> np.ndarray:
    """Distance from analytic samples to the loft, cell searched, not indexed.

    The band each reference column falls in is exact -- the builder's axial map
    nests -- and the position around the ring is found rather than assumed:
    nearest of the per-segment *rulings*, then nearest of the fine rulings
    around it, then the exact distance to the three quads spanning it. Every
    shortlist is widened around the nearest ruling before exact point-to-triangle
    distances are taken. Missing a nearer cell can only raise the distance to
    this sampled approximation; its separate angular approximation error is
    controlled by strip density, the dense reference and the margin above.

    Locating on rulings rather than on grid points is what makes the last step
    safe. A band's cells are long across the ring and thin along it near the
    mouth, and the reverse near the throat, so the nearest *corner* can be
    several cells away from the nearest cell: measuring three cells around it
    read 0.79 mm where the surface is 0.155 mm out. A ruling spans the band, so
    the nearest one is the cell -- and because ``makeRuled`` is degree one
    across a band, the ruling is exactly on the surface rather than a model of
    it.
    """

    rows, columns, _ = reference.shape
    bands, fine, _, _ = strips.shape
    segments = fine - 1
    span = np.arange(-window * samples, window * samples + 1)
    neighbours = np.array([-1, 0, 1])
    index = np.arange(rows)
    # The reference rows are the same positions around the ring in every
    # column, so the coarse search is a property of the band, not of the
    # column. Repeating it per column was most of the cost of the measurement.
    seeds: dict[int, np.ndarray] = {}
    out = np.empty((rows, columns))
    for column in range(columns):
        base = int(base_band[column])
        candidates = np.unique(
            np.clip(np.arange(-band_halo, band_halo + 1) + base, 0, bands - 1)
        )
        points = reference[:, column]
        best = np.full(rows, np.inf)
        for band in candidates:
            strip = strips[band]
            seed = seeds.get(band)
            if seed is None:
                rulings = strip[::samples]
                seed = (
                    _nearest_ruling(points, rulings, np.arange(len(rulings)))
                    * samples
                )
                seeds[band] = seed
            near = (seed[:, None] + span[None, :]) % segments
            fine_index = near[index, _nearest_ruling(points, strip, near)]
            quads = (fine_index[:, None] + neighbours[None, :]) % segments
            # One band's strip is itself a phi-periodic grid two columns wide,
            # so the shared cell measurement applies to it unchanged.
            best = np.minimum(
                best,
                quad_distance(
                    points, strip, quads, np.zeros_like(quads)
                ).min(axis=1),
            )
        out[:, column] = best
    return out


def _written_surface_measure(
    params: Mapping[str, Any], angular: int, length: int
) -> tuple[SurfaceDeviation, int, int] | None:
    """Measure a candidate grid against the surface the STEP will contain.

    The analytic chord is measured first and is kept: it is exact for the axial
    direction, which the loft rules linearly, and it carries the nested and
    detuned sampling checks. It is *not* enough on its own. ``_write_step``
    interpolates each ring with OCC's C2 spline, and a rounded-rectangle morph
    samples that ring unevenly -- corner arcs land about 1.3 degrees apart
    between sides spaced 3.6 degrees -- so the spline overshoots at the
    curvature step where a straight side meets its corner. On the 120x80 r12
    morph, each grid against its own 2x reference, the surface sits about twice
    as far from the analytic geometry as the chord through the same points, and
    the gap widens as the grid refines:

        108x56 chord 0.0805 -> surface 0.1545    172x96  0.0288 -> 0.0811
        132x72 chord 0.0501 -> surface 0.1175    216x112 0.0214 -> 0.0608

    A chord target therefore cannot certify this export, in either direction.
    So the loft itself is built and sampled, and the reading is the larger of
    the two. Attributing the loft's reading to the angular direction is what
    keeps it from over-refining the length: the axial fit is exactly linear and
    already measured exactly, so the excess is the ring spline's.

    The tolerance the two short-circuits below compare against is this export's
    own rather than an argument: a ``plan_grid`` measurement hook is not handed
    one, and this hook exists for ``_surface_grid_plan``. They only decide
    whether a more expensive reading is worth taking on a grid already rejected.
    """

    analytic = measure_deviation(params, angular, length)
    if analytic is None:
        return None
    deviation, n_phi, n_length = analytic

    def reading(written: float) -> tuple[SurfaceDeviation, int, int]:
        angular_linear = max(deviation.angular_linear, written)
        return (
            SurfaceDeviation(
                angular_linear=angular_linear,
                angular_cubic=angular_linear,
                axial_linear=deviation.axial_linear,
                axial_cubic=deviation.axial_linear,
                cubic_modelled=False,
            ),
            n_phi,
            n_length,
        )

    if max(deviation.angular_linear, deviation.axial_linear) > STL_CHORD_TOLERANCE_MM:
        # Already too coarse on the cheap reading. Building the loft could only
        # confirm it, so spend the probe on a finer grid instead.
        return reading(0.0)

    coarse, _, _ = _point_grid(params, angular, length)
    strips = _loft_strips(coarse, _LOFT_SAMPLES_PER_SEGMENT)
    band = axial_band_of_column(2 * n_length + 1, n_length)

    def against(multiple: int, offset: int) -> float | None:
        reference, _, reference_length = _point_grid(
            params, multiple * angular + offset, 2 * n_length
        )
        if reference_length != 2 * n_length:
            return None
        return float(
            _distance_to_loft(
                reference, strips, band, _LOFT_SAMPLES_PER_SEGMENT
            ).max()
        ) * (1.0 + _WRITTEN_SAMPLING_MARGIN)

    # Cheap reference first. A grid this already rejects does not need the dense
    # one, and on the way up from the probe grid most of them do not.
    written = against(2, 5)
    if written is None:
        return None
    if written <= STL_CHORD_TOLERANCE_MM:
        dense = against(_WRITTEN_REFERENCE_MULTIPLE, _WRITTEN_REFERENCE_OFFSET)
        if dense is None:
            return None
        written = max(written, dense)
    return reading(written)


def _surface_grid_plan(design: DesignConfig) -> GridPlan:
    """Size the ruled inner-surface STEP against the surface it writes.

    This export is not the manufacturable part -- that is the solid -- but an
    open reference bore the user lofts or thickens in CAD, and ``_write_step``
    builds it as a *ruled* loft (``makeRuled=True, maxDegree=1``) through
    interpolating profile splines. The planner therefore measures that loft
    rather than the chord through its samples; export tests separately check the
    written STEP after an OCC round trip, including samples on and between loft
    stations.
    """

    return plan_grid(
        _geometry_params(_bare_grid_config(design)),
        angular=("linear", STL_CHORD_TOLERANCE_MM),
        axial=("linear", STL_CHORD_TOLERANCE_MM),
        measure=_written_surface_measure,
    )


def _build_step_sync(design_dump: dict[str, Any]) -> str:
    design = DesignConfig.model_validate(design_dump)
    plan = _surface_grid_plan(design)
    return _write_step(_inner_grid(design, grid=(plan.angular, plan.length)))


async def build_step(design: DesignConfig) -> str:
    """Build the open, ruled, full-domain HornLab inner acoustic surface."""

    return await run_on_gmsh_worker(_build_step_sync, design.model_dump(mode="json"))


def _build_step_solid_sync(design_dump: dict[str, Any]) -> StepSolidResult:
    design = DesignConfig.model_validate(design_dump)
    try:
        from hornlab_mesher.cad import write_step_from_config
    except ImportError as exc:
        raise RuntimeError(
            "the installed hornlab-waveguide-mesher has no CAD export; "
            "install the pinned server requirements"
        ) from exc

    # The solver's own config, so the exported part is the geometry that was
    # solved rather than a second derivation of it. write_step_from_config
    # reopens a reduced domain to all four quadrants: a solve may run on a
    # quarter model, but a part cannot be a quarter of itself. It does not
    # restore ``mesh.vertical_offset`` the same way (``hornlab_mesher/cad.py``),
    # so the placement has to survive in the config the server hands it: this is
    # a CAD boundary, and the solid STEP carries the placement in every domain.
    # (The wglink bundle of the same design always did; keeping it here is what
    # stops an ATH-imported design declaring Mesh.Quadrants = 1 or 12 from
    # exporting an unplaced solid alongside a placed bundle.)
    config = _solver_mesher_config(design, keep_placement=True)
    # Corner arcs first, so the sampling search below measures the geometry
    # that will actually be written. Choosing the resolution against the
    # design's own corner count and then building with a different one would
    # leave a morph design's measured deviation describing a grid nobody gets.
    config["mesh"] = {
        **(config.get("mesh") or {}),
        "cornerSegments": EXPORT_CORNER_SEGMENTS,
    }
    # The solver's mm resolutions used to size the CAD point grid as well, so
    # refining an acoustic mesh made every STEP roughly 20x slower and 10x
    # larger while the export's own controls moved the grid by one row. The
    # part is a CAD artifact: it is sampled to a CAD tolerance instead, and the
    # result is the same file whatever the solve was set to.
    plan = plan_cad_resolution(config, tolerance_mm=STEP_SURFACE_TOLERANCE_MM)
    config["mesh"] = {
        **config["mesh"],
        "throatResolution": plan.resolution_mm,
        "mouthResolution": plan.resolution_mm,
        "rearResolution": plan.resolution_mm,
    }
    logger.info(
        "STEP solid sampled at %.3g mm (measured deviation %s mm, tolerance %g mm)",
        plan.resolution_mm,
        "unmeasured" if plan.deviation_mm is None else f"{plan.deviation_mm:.5f}",
        STEP_SURFACE_TOLERANCE_MM,
    )
    with tempfile.NamedTemporaryFile(
        prefix="waveguide-solid-", suffix=".step", delete=False
    ) as handle:
        step_path = Path(handle.name)
    try:
        # Public callers run this on the Gmsh worker. Keep the synchronous
        # helper safe as well: contract tests and maintenance tools call it
        # directly, allowing the pinned mesher to own a native session.
        with _preserve_native_windows_path():
            _, cad_info = write_step_from_config(config, step_path)
        # Normalised here as well as in the mesher: the mesher is version-pinned,
        # and a pin that predates its own header fix would otherwise hand CATIA
        # a file it cannot read.
        text = _normalise_step_header(
            step_path.read_text(encoding="utf-8", errors="replace")
        )
    finally:
        step_path.unlink(missing_ok=True)
    _assert_step(text)
    return StepSolidResult(step_text=text, cad_info=cad_info)


async def build_step_solid(design: DesignConfig) -> StepSolidResult:
    """Build the manufacturable solid: wall thickness, enclosure, open throat.

    Unlike the inner-surface export this needs no Thicken or cap step in CAD --
    it imports into Fusion 360 or Onshape as a closed B-rep solid in
    millimetres. Designs with no wall thickness have no material to enclose, so
    the mesher falls back to a surface body for those.
    """

    return await run_on_gmsh_worker(
        _build_step_solid_sync, design.model_dump(mode="json")
    )


def binary_stl(
    vertices_m: list[float] | np.ndarray,
    indices: list[int] | np.ndarray,
    surface_tags: list[int] | np.ndarray,
    model_name: str = "MWG Horn",
) -> bytes:
    """Serialize tag-1 horn triangles as v1-compatible transformed millimetres."""

    points = np.asarray(vertices_m, dtype=float).reshape(-1, 3)
    triangles = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    tags = np.asarray(surface_tags, dtype=np.int64).reshape(-1)
    if not np.isfinite(points).all():
        raise ValueError("STL vertex coordinates must all be finite")
    if len(tags) != len(triangles):
        raise ValueError("surface tag count does not match triangle count")
    triangles = triangles[tags == 1]
    if not len(triangles):
        raise RuntimeError("solver mesh contains no horn inner-surface triangles (tag 1)")
    if np.any(triangles < 0) or np.any(triangles >= len(points)):
        raise ValueError("triangle index is outside the vertex buffer")

    # HornLab's solver tuple is (x, vertical, axial), whereas v1's local
    # geometry tuple is (x, axial, vertical). Applying the binding v1
    # (x, y, z) -> (x, -z, y) mapping therefore yields this solver-boundary
    # transform: (x, vertical, axial) -> (x, -vertical, axial). This is a
    # reflection (determinant -1), so reverse every triangle after applying it
    # to preserve the authoritative mesher's normal side.
    transformed = np.column_stack(
        (points[:, 0] * 1000.0, -points[:, 1] * 1000.0, points[:, 2] * 1000.0)
    )
    float32_limit = np.finfo(np.float32).max
    if not np.isfinite(transformed).all() or np.any(np.abs(transformed) > float32_limit):
        raise ValueError("STL vertex coordinates exceed the finite binary STL range")
    output = bytearray(84 + len(triangles) * 50)
    header = model_name.encode("utf-8")[:79]
    output[: len(header)] = header
    struct.pack_into("<I", output, 80, len(triangles))
    if len(triangles):
        # Built as one array rather than a struct.pack_into per triangle with an
        # np.cross and an np.linalg.norm on 3-vectors inside the loop. A real
        # export is tens of thousands of triangles, and the per-call NumPy
        # dispatch dominated. The winding swap is preserved exactly: the loop
        # unpacked `v0, v2, v1`, so column 1 of the triangle is the *third*
        # vertex written and column 2 is the second.
        corners = transformed[triangles]
        v0 = corners[:, 0]
        v1 = corners[:, 2]
        v2 = corners[:, 1]
        normals = np.cross(v1 - v0, v2 - v0)
        magnitudes = np.sqrt(np.einsum("ij,ij->i", normals, normals))
        # Exactly the loop's rule: normalise only where the magnitude is
        # positive, and leave a zero-area facet's normal as the raw zero vector.
        np.divide(normals, magnitudes[:, None], out=normals, where=magnitudes[:, None] > 0)
        facets = np.empty(
            len(triangles),
            dtype=np.dtype(
                [("n", "<f4", 3), ("v0", "<f4", 3), ("v1", "<f4", 3), ("v2", "<f4", 3), ("attr", "<u2")]
            ),
        )
        facets["n"] = normals
        facets["v0"] = v0
        facets["v1"] = v1
        facets["v2"] = v2
        facets["attr"] = 0
        output[84:] = facets.tobytes()
    return bytes(output)


def _stl_mesher_config(design: DesignConfig) -> dict[str, Any]:
    """The pinned-grid config an STL is meshed from.

    ``preserveGrid`` makes the CAD faces *be* the sampled grid cells, so the
    grid alone sets the exported surface's deviation and the millimetre
    resolutions only decide how finely gmsh subdivides facets that are already
    inside tolerance. Both are the export's to choose.
    """

    config = _solver_mesher_config(design)
    config.setdefault("mesh", {}).update(
        {"topology": "legacy", "preserveGrid": True, "scaleToMetres": True}
    )
    return config


def _stl_grid_plan(design: DesignConfig) -> tuple[GridPlan, dict[str, Any]]:
    """Size the STL to a print-resolution chord deviation.

    Nothing here reads ``mesh.max_triangles``: that is the solver's advisory
    warning threshold, and passing it through turned a warning into a refusal
    on a mesh the export itself had densified. The export carries its own
    generous ceiling instead, and trims to it rather than refusing.
    """

    params = _geometry_params(_stl_mesher_config(_prepared_design(design)))
    plan = plan_grid(
        params,
        angular=("linear", STL_CHORD_TOLERANCE_MM),
        axial=("linear", STL_CHORD_TOLERANCE_MM),
        triangle_ceiling=STL_TRIANGLE_CEILING,
    )
    return plan, params


def _build_stl_mesh_sync(design_dump: dict[str, Any]) -> dict[str, Any]:
    """Mesh the export's own pinned grid, sized to a print-resolution chord."""

    try:
        from hornlab_mesher.config_builder import build_from_config
    except ImportError as exc:
        raise RuntimeError(
            "hornlab-waveguide-mesher is unavailable; install the pinned server requirements"
        ) from exc
    # meshio is imported here rather than at module scope: it pulls its own CLI
    # entry point and rich with it, which was 145 ms on every server start for
    # a parser that only runs when somebody exports geometry.
    import meshio

    design = DesignConfig.model_validate(design_dump)
    plan, params = _stl_grid_plan(design)
    prepared = _prepared_design(design, grid=(plan.angular, plan.length))
    config = _stl_mesher_config(prepared)
    element_mm = facet_element_size_mm(params, plan.angular, plan.length)
    config["mesh"].update(
        {
            # One element per grid cell: below the longest cell edge gmsh
            # subdivides faces that are already inside tolerance, which is how
            # a refined solve mesh used to quadruple an STL for no fidelity.
            "throatResolution": element_mm,
            "mouthResolution": element_mm,
            "rearResolution": element_mm,
            # The export's own headroom, not the design's advisory budget. The
            # plan is already trimmed to STL_TRIANGLE_CEILING, so this exists
            # only so a surprise warns below instead of refusing here.
            "maxTriangles": STL_TRIANGLE_CEILING * 4,
            "allowLargeMesh": True,
        }
    )
    warnings = [plan.warning] if plan.warning else []
    logger.info(
        "STL grid %dx%d (~%d triangles, measured deviation %s mm, tolerance %g mm)",
        plan.angular,
        plan.length,
        plan.triangles,
        "unmeasured" if plan.deviation_mm is None else f"{plan.deviation_mm:.5f}",
        STL_CHORD_TOLERANCE_MM,
    )
    with tempfile.TemporaryDirectory(prefix="wg2-stl-mesh-") as temp_dir:
        mesh_path = Path(temp_dir) / "waveguide.msh"
        # Normally Gmsh is already open on the owner thread. Preserve the
        # native environment when this synchronous seam is exercised directly
        # and the pinned mesher opens and closes its own session instead.
        with _preserve_native_windows_path():
            build_from_config(config, mesh_path, allow_large_mesh=True)
        parsed = meshio.read(mesh_path)
        triangles, tags = _triangles_and_tags(parsed)
        vertices = np.asarray(parsed.points, dtype=float)
        if vertices.ndim != 2 or vertices.shape[1:] != (3,) or not np.isfinite(vertices).all():
            raise ValueError("parsed STL mesh contains invalid or non-finite coordinates")
    horn_triangles = int((tags == 1).sum())
    if horn_triangles > STL_TRIANGLE_CEILING:
        warnings.append(
            f"This STL has {horn_triangles:,} triangles, above the "
            f"{STL_TRIANGLE_CEILING:,} this export aims to stay under. It is "
            "written in full; slicers may be slow to load it."
        )
    return {
        "vertices": vertices.reshape(-1).tolist(),
        "indices": triangles.reshape(-1).astype(int).tolist(),
        "surfaceTags": tags.astype(int).tolist(),
        "warnings": warnings,
    }


async def build_stl(design: DesignConfig, model_name: str = "MWG Horn") -> StlResult:
    """Build the horn surface at print resolution, sized by the export itself."""

    canonical = await run_on_gmsh_worker(
        _build_stl_mesh_sync,
        design.model_dump(mode="json"),
    )
    warnings = [str(item) for item in canonical.get("warnings") or []]
    return StlResult(
        data=binary_stl(
            canonical.get("vertices", []),
            canonical.get("indices", []),
            canonical.get("surfaceTags", []),
            model_name,
        ),
        warning=" ".join(warnings) or None,
    )


def profile_csv(inner_points_mm: np.ndarray, kind: str) -> str:
    """Format one v1 Fusion CSV artifact from a phi-major inner point grid."""

    points = np.asarray(inner_points_mm, dtype=float)
    if points.ndim != 3 or points.shape[2] != 3:
        raise ValueError("profile points must have shape (angular, axial, 3)")
    angular, axial_count, _ = points.shape
    if kind == "profiles":
        rows = ["# x_cm;y_cm;z_cm"]
        for phi in range(angular):
            for length in range(axial_count):
                # Mesher points are (x, vertical, axial-position), already the v1 CSV
                # order (x, local-z, local-y) after the local/solver axis swap.
                x, vertical, axial_position = points[phi, length] * 0.1
                rows.append(f"{x:.6f};{vertical:.6f};{axial_position:.6f}")
            rows.append("")
    elif kind == "slices":
        rows = ["# slices: x_cm;y_cm;z_cm"]
        for length in range(axial_count):
            for phi in range(angular + 1):
                x, vertical, axial_position = points[phi % angular, length] * 0.1
                rows.append(f"{x:.6f};{vertical:.6f};{axial_position:.6f}")
            rows.append("")
    else:
        raise ValueError("profile CSV kind must be 'profiles' or 'slices'")
    return "\r\n".join(rows) + "\r\n"


def build_profiles(design: DesignConfig, kind: str) -> str:
    """Rebuild the bare, uniform-ring inner surface without vertical offset."""

    return profile_csv(
        _inner_grid(design, profile_sampling=True),
        kind,
    )


__all__ = [
    "StepSolidResult",
    "StlResult",
    "binary_stl",
    "build_profiles",
    "build_step",
    "build_step_solid",
    "build_stl",
    "profile_csv",
    "validate_export_segments",
]
