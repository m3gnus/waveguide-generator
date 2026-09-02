"""Per-band mesh ladder: solve each octave on a mesh sized for that octave.

BEM cost is set by the mesh, not by the frequency -- the per-frequency cost on
a fixed mesh is essentially flat.  A sweep therefore pays the *top* octave's
element count at 100 Hz as well as at 20 kHz, and buys nothing for it.  The
ladder splits the sweep into descending octaves and coarsens the mesh for every
octave below the top one, so each band pays its own price.

Three rules keep it from changing the answer:

1. **The top band always runs the user's own mesh, unscaled.**  The ladder only
   ever coarsens *below* the top octave.  Whatever the user chose for the top of
   their sweep is what the top of the sweep still runs on, bit for bit, so the
   frequencies most sensitive to resolution are untouched and
   ``TODO.md`` §2.4a's "do not silently re-mesh archived designs" concern does
   not apply to them.
2. **A band is coarsened only as far as it stays amplitude-valid at its own top
   frequency**, on the ``elements_per_wavelength`` rule the solver's own
   diagnostic uses.  The reference length is *measured*, not assumed: it is the
   realized ``max_edge_mm`` of the top band's mesh, which is exactly the length
   ``mesh_max_valid_frequency_hz`` is derived from.
3. **The realized band mesh is re-checked after it is built**, and a band whose
   built mesh would be less valid than the baseline mesh would have been at
   those frequencies falls back to the baseline mesh.  So the ladder can never
   make a band worse than the single-mesh sweep it replaces; at worst it wastes
   one sub-second mesh build.

Sizing is a scale factor applied to the design's own mm resolution fields, so
the user's relative grading (fine throat, coarse mouth) is preserved rather than
replaced by a flat target.  Fields the design leaves unset keep the mesher's
default and are not scaled: under-coarsening is safe, and materialising another
package's defaults into a design is not.
"""

from __future__ import annotations

import math
from copy import copy as shallow_copy
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from server.design.schema import DesignConfig, Expr

#: The elements-per-wavelength rule the ladder sizes against.  It is the same
#: 6.0 ``hornlab_metal_bem.config.SolverConfig.mesh_elements_per_wavelength_min``
#: uses to compute ``mesh_max_valid_frequency_hz``, so "valid at the band's top
#: frequency" here means exactly what the solver's own diagnostic means by it.
LADDER_ELEMENTS_PER_WAVELENGTH = 6.0

#: Band width as a frequency ratio.  Octaves, measured -- see
#: ``scripts/measure_mesh_ladder.py``.  Half-octave bands double the mesh builds
#: for a saving the solve cannot repay, and two-octave bands leave most of the
#: win behind.
DEFAULT_BAND_RATIO = 2.0

#: How many meshes one band may build before giving up and taking the baseline.
#: Two: the first sizing attempt and one corrective step from what it realized.
_MAX_SIZING_ATTEMPTS = 2

#: A band whose scale factor is this close to another's shares that band's mesh
#: rather than building a second, near-identical one.
_SCALE_MERGE_TOLERANCE = 1.05

#: Design fields the ladder scales.  ``mesh.max_edge`` is deliberately absent:
#: it is a user guard on the realized mesh, not a density control, and scaling
#: it would disarm the guard exactly when the mesh gets coarser.
_MESH_RESOLUTION_FIELDS = (
    "throat_resolution",
    "mouth_resolution",
    "rear_resolution",
)
_ENCLOSURE_RESOLUTION_FIELDS = (
    "front_resolution",
    "back_resolution",
)


@dataclass(frozen=True, slots=True)
class LadderBand:
    """One octave of the sweep and the mesh scale it will be solved on."""

    index: int
    lower_hz: float
    upper_hz: float
    frequencies: tuple[float, ...]
    positions: tuple[int, ...]
    resolution_scale: float

    @property
    def is_baseline(self) -> bool:
        return self.resolution_scale == 1.0


@dataclass(frozen=True, slots=True)
class MeshLadderPlan:
    bands: tuple[LadderBand, ...]
    band_ratio: float
    elements_per_wavelength: float
    reference_max_edge_mm: float

    def as_metadata(self) -> dict[str, Any]:
        return {
            "band_ratio": self.band_ratio,
            "elements_per_wavelength": self.elements_per_wavelength,
            "reference_max_edge_mm": self.reference_max_edge_mm,
            "band_count": len(self.bands),
        }


def scalable_resolution_fields(design: DesignConfig) -> tuple[str, ...]:
    """Name the mm resolution fields this design actually sets."""

    names: list[str] = []
    mesh = design.root.mesh
    for field in _MESH_RESOLUTION_FIELDS:
        expr = getattr(mesh, field, None)
        if expr is not None and expr.constant_value() is not None:
            names.append(f"mesh.{field}")
    enclosure = design.root.enclosure
    if enclosure is not None:
        for field in _ENCLOSURE_RESOLUTION_FIELDS:
            expr = getattr(enclosure, field, None)
            if expr is not None and expr.constant_value() is not None:
                names.append(f"enclosure.{field}")
    return tuple(names)


def scaled_design(design: DesignConfig, scale: float) -> DesignConfig:
    """Return ``design`` with every scalar mm resolution multiplied by ``scale``.

    A non-scalar (formula) resolution is left alone rather than being flattened
    to a number: the mesher evaluates those per slice, and replacing one with a
    constant would change the mesh in a way that has nothing to do with the
    ladder.
    """

    if scale == 1.0:
        return design
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("mesh ladder resolution scale must be finite and positive")
    variant = design.model_copy(deep=True)
    for holder, fields in (
        (variant.root.mesh, _MESH_RESOLUTION_FIELDS),
        (variant.root.enclosure, _ENCLOSURE_RESOLUTION_FIELDS),
    ):
        if holder is None:
            continue
        for field in fields:
            expr = getattr(holder, field, None)
            if expr is None:
                continue
            value = expr.constant_value()
            if value is None:
                continue
            setattr(holder, field, Expr(value=float(value) * scale))
    return variant


def band_edges(frequencies: Sequence[float], *, band_ratio: float) -> list[float]:
    """Return descending band upper bounds covering ``frequencies``."""

    if band_ratio <= 1.0:
        raise ValueError("mesh ladder band ratio must be greater than 1")
    top = float(frequencies[-1])
    bottom = float(frequencies[0])
    edges = [top]
    while edges[-1] / band_ratio > bottom:
        edges.append(edges[-1] / band_ratio)
    return edges


def _target_edge_mm(frequency_hz: float, elements_per_wavelength: float) -> float:
    from .acoustics import reference_sound_speed_m_per_s

    return (
        1000.0
        * reference_sound_speed_m_per_s()
        / (elements_per_wavelength * frequency_hz)
    )


def valid_frequency_hz(max_edge_mm: float, elements_per_wavelength: float) -> float:
    """The amplitude-validity ceiling a mesh with this coarsest edge supports."""

    from .acoustics import reference_sound_speed_m_per_s

    if max_edge_mm <= 0.0 or not math.isfinite(max_edge_mm):
        return math.inf
    return (
        1000.0
        * reference_sound_speed_m_per_s()
        / (elements_per_wavelength * max_edge_mm)
    )


def plan_mesh_ladder(
    design: DesignConfig,
    frequencies: Sequence[float],
    *,
    reference_max_edge_mm: float,
    band_ratio: float | None = None,
    elements_per_wavelength: float = LADDER_ELEMENTS_PER_WAVELENGTH,
) -> MeshLadderPlan | None:
    """Partition ``frequencies`` into descending bands with a mesh scale each.

    ``reference_max_edge_mm`` is the *realized* coarsest edge of the baseline
    mesh, so the scale a band gets is the ratio between the edge its top
    frequency can afford and the edge the user's mesh actually has.  Returns
    ``None`` when the sweep collapses to a single band or nothing can be
    coarsened -- there is then no ladder, only the ordinary single-mesh solve.
    """

    # Read at call time rather than bound at import: band width is the ladder's
    # one open design parameter, and the measurement harness rebinds it.
    band_ratio = DEFAULT_BAND_RATIO if band_ratio is None else band_ratio
    values = np.asarray(list(frequencies), dtype=np.float64).reshape(-1)
    if values.size < 2 or not np.all(np.isfinite(values)) or np.any(values <= 0.0):
        return None
    if not np.all(np.diff(values) > 0.0):
        raise ValueError("mesh ladder frequencies must be strictly ascending")
    if not scalable_resolution_fields(design):
        return None
    if not math.isfinite(reference_max_edge_mm) or reference_max_edge_mm <= 0.0:
        return None

    edges = band_edges(values.tolist(), band_ratio=band_ratio)
    bands: list[LadderBand] = []
    for index, upper in enumerate(edges):
        lower = upper / band_ratio if index + 1 < len(edges) else 0.0
        mask = (values <= upper * (1.0 + 1e-12)) & (values > lower * (1.0 + 1e-12))
        positions = np.flatnonzero(mask)
        if positions.size == 0:
            continue
        if index == 0:
            scale = 1.0
        else:
            scale = _target_edge_mm(upper, elements_per_wavelength) / (
                reference_max_edge_mm
            )
            scale = max(1.0, scale)
        bands.append(
            LadderBand(
                index=len(bands),
                lower_hz=float(lower),
                upper_hz=float(upper),
                frequencies=tuple(float(v) for v in values[positions]),
                positions=tuple(int(p) for p in positions),
                resolution_scale=float(scale),
            )
        )

    merged = _merge_equal_scale_bands(bands)
    if len(merged) < 2:
        return None
    if all(band.resolution_scale == 1.0 for band in merged):
        return None
    return MeshLadderPlan(
        bands=tuple(merged),
        band_ratio=float(band_ratio),
        elements_per_wavelength=float(elements_per_wavelength),
        reference_max_edge_mm=float(reference_max_edge_mm),
    )


def _merge_equal_scale_bands(bands: Sequence[LadderBand]) -> list[LadderBand]:
    """Fold adjacent bands whose scales are within the merge tolerance.

    Two bands that would build the same mesh must not build it twice: even on a
    cache hit that is a second solver invocation, a second progress phase and a
    second row of ladder metadata describing one mesh.
    """

    merged: list[LadderBand] = []
    for band in bands:
        previous = merged[-1] if merged else None
        if previous is not None:
            ratio = max(previous.resolution_scale, band.resolution_scale) / min(
                previous.resolution_scale, band.resolution_scale
            )
            if ratio <= _SCALE_MERGE_TOLERANCE:
                merged[-1] = replace(
                    previous,
                    # Bands arrive top-down, so the newcomer extends the run
                    # downwards and contributes the lower bound.
                    lower_hz=band.lower_hz,
                    frequencies=tuple(sorted(previous.frequencies + band.frequencies)),
                    positions=tuple(sorted(previous.positions + band.positions)),
                    resolution_scale=min(
                        previous.resolution_scale, band.resolution_scale
                    ),
                )
                continue
        merged.append(band)
    return [replace(band, index=index) for index, band in enumerate(merged)]


@dataclass(frozen=True, slots=True)
class LadderBandMesh:
    """One band paired with the mesh it will actually be solved on."""

    band: LadderBand
    msh_text: str
    stats: Mapping[str, Any]
    max_valid_hz: float
    fallback_reason: str | None

    @property
    def frequencies(self) -> tuple[float, ...]:
        return self.band.frequencies

    def as_metadata(self) -> dict[str, Any]:
        stats = self.stats or {}
        return {
            "index": self.band.index,
            "lower_hz": self.band.lower_hz,
            "upper_hz": self.band.upper_hz,
            "frequency_count": len(self.band.frequencies),
            "first_frequency_hz": self.band.frequencies[0],
            "last_frequency_hz": self.band.frequencies[-1],
            "resolution_scale": self.band.resolution_scale,
            "triangle_count": int(stats.get("triangle_count", 0)),
            "vertex_count": int(stats.get("vertex_count", 0)),
            "max_edge_mm": float(stats.get("max_edge_mm", float("nan"))),
            "max_valid_frequency_hz": self.max_valid_hz,
            "mesh_cache_key": str(stats.get("mesh_cache_key", "")),
            "mesh_cache_hit": bool(stats.get("mesh_cache_hit", False)),
            "fallback_reason": self.fallback_reason,
        }


async def build_ladder_band_meshes(
    design: DesignConfig,
    plan: MeshLadderPlan,
    baseline_mesh: Mapping[str, Any],
    options: Any,
    *,
    cancel_cb: Any = None,
    progress_cb: Any = None,
) -> list[LadderBandMesh]:
    """Build one mesh per band, verifying each against the baseline it replaces.

    A coarsened band mesh is only kept when its *realized* coarsest edge still
    supports the band's own top frequency, or the baseline mesh's own ceiling
    where that is lower.  Anything else -- a mesh that came out coarser than
    asked, a coarsening that chorded a thin shell into self-intersection, a
    mesher refusal -- falls back to the baseline mesh, which is the behaviour
    the ladder replaced.  The cost of a fallback is one sub-second mesh build.
    """

    from server.mesh.builder import build_solver_mesh

    baseline_stats = dict(baseline_mesh["stats"])
    baseline_edge_mm = float(baseline_stats.get("max_edge_mm", 0.0))
    baseline_valid_hz = valid_frequency_hz(
        baseline_edge_mm, plan.elements_per_wavelength
    )
    baseline_triangles = int(baseline_stats.get("triangle_count", 0))

    def baseline_band(band: LadderBand, reason: str | None) -> LadderBandMesh:
        return LadderBandMesh(
            band=band,
            msh_text=baseline_mesh["msh_text"],
            stats=baseline_stats,
            max_valid_hz=baseline_valid_hz,
            fallback_reason=reason,
        )

    meshes: list[LadderBandMesh] = []
    for band in plan.bands:
        if cancel_cb is not None:
            cancel_cb()
        if band.resolution_scale == 1.0:
            meshes.append(baseline_band(band, None))
            continue
        required_hz = min(band.upper_hz, baseline_valid_hz)
        scale = band.resolution_scale
        refusal: str | None = None
        # A requested mm resolution does not become an element of that length:
        # measured on ATH reference geometry the realized coarsest edge lands
        # anywhere from 0.77x to 1.16x of what was asked, so one corrective
        # step is worth an extra sub-second build rather than losing the whole
        # band to the baseline mesh.
        for attempt in range(_MAX_SIZING_ATTEMPTS):
            try:
                built = await build_solver_mesh(
                    scaled_design(design, scale), options, cancel_cb, progress_cb
                )
            except (RuntimeError, ValueError) as exc:
                refusal = f"band mesh build refused: {exc}"
                break
            stats = dict(built["stats"])
            realized_hz = valid_frequency_hz(
                float(stats.get("max_edge_mm", 0.0)), plan.elements_per_wavelength
            )
            triangles = int(stats.get("triangle_count", 0))
            if baseline_triangles and triangles >= baseline_triangles:
                refusal = (
                    f"band mesh is not smaller than the baseline ({triangles:,} "
                    f"vs {baseline_triangles:,} triangles)"
                )
                break
            if realized_hz >= required_hz:
                refusal = None
                meshes.append(
                    LadderBandMesh(
                        band=replace(band, resolution_scale=scale),
                        msh_text=built["msh_text"],
                        stats=stats,
                        max_valid_hz=realized_hz,
                        fallback_reason=None,
                    )
                )
                break
            refusal = (
                f"band mesh is valid only to {realized_hz:.0f} Hz, below the "
                f"{required_hz:.0f} Hz this band needs"
            )
            if attempt + 1 >= _MAX_SIZING_ATTEMPTS:
                break
            # Coarsest edge scales as 1/validity, so this is the secant step
            # that lands the next attempt on the frequency the band needs.
            scale *= realized_hz / required_hz
            if scale <= 1.0:
                refusal = (
                    "the band cannot be coarsened and stay valid to "
                    f"{required_hz:.0f} Hz"
                )
                break
        if refusal is not None:
            meshes.append(baseline_band(band, refusal))
    return meshes


#: Native fields whose trailing axis is the *mesh*, not the observation grid.
#: They cannot be concatenated across bands because each band has a different
#: mesh, and silently keeping one band's copy would attach that band's surface
#: solution to every frequency in the sweep.
MESH_SHAPED_RESULT_FIELDS = frozenset(
    {"surface_pressure_complex", "surface_neumann_complex"}
)


@dataclass(frozen=True, slots=True)
class MergedBandResults:
    result: Any
    dropped_fields: tuple[str, ...]


def merge_band_results(results: Sequence[Any]) -> MergedBandResults:
    """Concatenate per-band native results onto one ascending frequency axis.

    Everything whose leading axis is frequency and whose trailing shape agrees
    across bands is concatenated and re-sorted; mesh-shaped surface arrays are
    dropped and named, because there is no single mesh they could belong to.
    Non-frequency fields (observation grid, sphere grid, config) come from the
    first band, which is the top band and therefore the user's own mesh.
    """

    if not results:
        raise ValueError("merging band results requires at least one result")
    if len(results) == 1:
        return MergedBandResults(result=results[0], dropped_fields=())

    counts = [len(np.asarray(item.frequencies_hz).reshape(-1)) for item in results]
    frequencies = np.concatenate(
        [np.asarray(item.frequencies_hz, dtype=np.float64).reshape(-1) for item in results]
    )
    order = np.argsort(frequencies, kind="stable")

    merged = shallow_copy(results[0])
    dropped: set[str] = set()
    for name, value in vars(results[0]).items():
        if isinstance(value, np.ndarray) and value.ndim and value.shape[0] == counts[0]:
            if name in MESH_SHAPED_RESULT_FIELDS:
                setattr(merged, name, None)
                dropped.add(name)
                continue
            parts = [getattr(item, name, None) for item in results]
            stacked = _concatenate_frequency_axis(parts, counts)
            if stacked is None:
                setattr(merged, name, None)
                dropped.add(name)
                continue
            setattr(merged, name, stacked[order])
        elif isinstance(value, Mapping):
            mapped: dict[Any, Any] = {}
            changed = False
            for key, item in value.items():
                if not (
                    isinstance(item, np.ndarray)
                    and item.ndim
                    and item.shape[0] == counts[0]
                ):
                    mapped[key] = item
                    continue
                parts = [
                    (getattr(other, name, None) or {}).get(key) for other in results
                ]
                stacked = _concatenate_frequency_axis(parts, counts)
                if stacked is None:
                    dropped.add(f"{name}[{key}]")
                    changed = True
                    continue
                mapped[key] = stacked[order]
                changed = True
            if changed:
                setattr(merged, name, mapped)

    merged.frequencies_hz = frequencies[order]
    merged.solver_log = _reorder_entries(
        [list(getattr(item, "solver_log", []) or []) for item in results], counts, order
    )
    merged.native_diagnostics = _reorder_entries(
        [list(getattr(item, "native_diagnostics", []) or []) for item in results],
        counts,
        order,
    )
    merged.timings = _sum_timings(getattr(item, "timings", None) for item in results)
    return MergedBandResults(result=merged, dropped_fields=tuple(sorted(dropped)))


def _concatenate_frequency_axis(
    parts: Sequence[Any], counts: Sequence[int]
) -> np.ndarray | None:
    arrays: list[np.ndarray] = []
    trailing: tuple[int, ...] | None = None
    for part, count in zip(parts, counts):
        if part is None:
            return None
        array = np.asarray(part)
        if not array.ndim or array.shape[0] != count:
            return None
        if trailing is None:
            trailing = array.shape[1:]
        elif array.shape[1:] != trailing:
            return None
        arrays.append(array)
    if not arrays:
        return None
    return np.concatenate(arrays, axis=0)


def _reorder_entries(
    per_band: Sequence[list[Any]], counts: Sequence[int], order: np.ndarray
) -> list[Any]:
    """Concatenate per-frequency log rows and put them in frequency order.

    Rows that are not one-per-frequency (a backend that logs something else)
    are concatenated in band order and left alone rather than mis-indexed.
    """

    if any(len(rows) != count for rows, count in zip(per_band, counts)):
        return [row for rows in per_band for row in rows]
    flat = [row for rows in per_band for row in rows]
    return [flat[int(index)] for index in order]


def _sum_timings(timings: Iterable[Any]) -> dict[str, Any]:
    total: dict[str, Any] = {}
    for entry in timings:
        if not isinstance(entry, Mapping):
            continue
        for key, value in entry.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total[key] = float(total.get(key, 0.0)) + float(value)
            else:
                total.setdefault(key, value)
    return total


__all__ = [
    "DEFAULT_BAND_RATIO",
    "LADDER_ELEMENTS_PER_WAVELENGTH",
    "LadderBand",
    "LadderBandMesh",
    "build_ladder_band_meshes",
    "MESH_SHAPED_RESULT_FIELDS",
    "MergedBandResults",
    "MeshLadderPlan",
    "band_edges",
    "merge_band_results",
    "plan_mesh_ladder",
    "scalable_resolution_fields",
    "scaled_design",
    "valid_frequency_hz",
]
