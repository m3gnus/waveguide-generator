"""Explicit conversion, measured on the profile export's generated inner grid.

This module fits authored data; production sampling and wall comparison stay
with the mesher. Coordinates and tolerances are finished (scaled) millimetres.
"""
from __future__ import annotations

from collections.abc import Callable
import math
from typing import Literal

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.interpolate import PchipInterpolator

from server.design.schema import DesignCommon, DesignConfig, Expr, LookupConfig, StrictModel
from server.design.native import NATIVE_FORMULAS, has_native_roundover
from server.design.text_import import uses_current_ath_import
from server.exports.core import _bare_grid_config, _inner_grid
from server.preview.translate import _expr, design_to_mesher_config

ANGULAR = 144
AXIAL = 1024
AZIMUTHS = np.arange(ANGULAR) * 360.0 / ANGULAR
MATCH_MM = 0.01
REFUSE_MM = 0.5


class WallDeviation(StrictModel):
    max_deviation_mm: float
    azimuth_deg: float
    z_mm: float
    sample_count: int


class FeatureChange(StrictModel):
    name: str
    change: Literal['removed', 'changed', 'baked']
    message: str


class GeneratedComparisonMethod(StrictModel):
    angular_samples: int
    angular_samples_before: int
    angular_samples_after: int
    axial_segments_before: int
    axial_segments_after: int
    azimuths_deg: list[float]
    grid: str
    limits: list[str]


class GeneratedComparison(StrictModel):
    H: WallDeviation
    V: WallDeviation
    off_axis: list[WallDeviation]
    wall: WallDeviation
    mouth: WallDeviation
    axial_extent_deviation_mm: float
    source_cap_angle_before_deg: float
    source_cap_angle_after_deg: float
    features: list[FeatureChange]
    curve_deviation_mm: float | None = None
    method: GeneratedComparisonMethod


class ConvertResponse(StrictModel):
    content_key: str
    design: DesignConfig
    report: GeneratedComparison
    verdict: Literal['match', 'approximate']


def _refuse(message: str, remedy: str) -> None:
    from hornlab_mesher.errors import UserRefusal
    raise UserRefusal('Convert to Custom profile', message, remedy)


def _number(value: Expr | None, default: float = 0) -> float:
    return default if value is None else float(value.constant_value())


def _at(value: Expr | None, phi: float) -> float:
    from hornlab_mesher.profile_common import eval_param
    return float(eval_param(_expr(value), phi, 0))


def _active_morph(design: DesignConfig) -> bool:
    target = design.root.morph.target_shape
    return target is not None and target.constant_value() != 0


def legacy_lookup_source(payload: dict) -> DesignConfig:
    """Input-only shape. Never hand this wrapper to a runtime build endpoint."""
    return DesignConfig.model_construct(root=LookupConfig.model_validate(payload, context={"expression_dialect": "wg-v1"}))


def _legacy_config(source: DesignConfig) -> dict:
    from server.preview.translate import _common_mesher_config
    scale = _number(source.root.scale, 1)
    return _common_mesher_config(source.root, profile={
        'formula': 'LOOKUP', 'lookupProfile': [[z*scale, r*scale] for z, r in source.root.lookup_profile],
        'a0': _expr(source.root.a0) if source.root.a0 is not None else 0,
    })


def _legacy_grid(source: DesignConfig, *, authored: bool = False) -> tuple[np.ndarray, dict]:
    from hornlab_mesher.config_builder import build_geometry_params
    from hornlab_mesher.profile_sampling import build_point_grid_arrays
    config = _legacy_config(source)
    config['mode'] = 'bare'
    config.pop('enclosure', None)
    config['mesh'].update(quadrants=1234, wallThickness=0, verticalOffset=0, scaleToMetres=False,
                          angularSegments=ANGULAR)
    if not authored and not _active_morph(source):
        config['mesh']['lengthSegments'] = AXIAL
    else:
        config['mesh']['lengthSegments'] = max(1, int(math.floor(_number(source.root.mesh.length_segments, 40)+.5)))
    params, formula, _ = build_geometry_params(config)
    grid = build_point_grid_arrays(params)
    return np.asarray(grid['inner_grid']), grid


def _sample(design: DesignConfig) -> np.ndarray:
    if isinstance(design.root, LookupConfig):
        return _legacy_grid(design)[0]
    # Morph start snapping is a property of the authored axial table. Increasing
    # that count would silently change the very source we promise to convert.
    authored = (design.formula in NATIVE_FORMULAS or _active_morph(design) or design.root.mesh.ath_roundover is not None
                or bool(design.root.mesh.z_map_points and design.root.mesh.z_map_points.strip()))
    if authored:
        if design.formula in NATIVE_FORMULAS:
            return _inner_grid(design, profile_sampling=True)
        payload = design.model_dump(mode='json')
        payload['mesh']['angular_segments'] = ANGULAR
        return _inner_grid(DesignConfig.model_validate(payload), profile_sampling=True)
    return _inner_grid(design, grid=(ANGULAR, AXIAL))


def _meridians(points: np.ndarray) -> np.ndarray:
    """Reorder exported ring samples onto stated physical azimuths.

    Morph and guiding curves may redistribute angles. Interpolating actual
    polar coordinates avoids mistaking an array index for an azimuth.
    """
    result = np.empty((ANGULAR, points.shape[1], 2))
    for index, ring in enumerate(points.transpose(1, 0, 2)):
        angles = np.mod(np.degrees(np.arctan2(ring[:, 1], ring[:, 0])), 360)
        order = np.argsort(angles)
        angles = angles[order]
        radius = np.linalg.norm(ring[order, :2], axis=1)
        result[:, index, 0] = np.interp(AZIMUTHS, angles, ring[order, 2], period=360)
        result[:, index, 1] = np.interp(AZIMUTHS, angles, radius, period=360)
    return result


def _monotone(wall: np.ndarray, feature: str = '') -> None:
    dz = np.diff(wall[:, :, 0], axis=1)
    dr = np.diff(wall[:, :, 1], axis=1)
    if np.any((dz < -1e-10) | ((dz <= 0) & (abs(dr) > 1e-9))):
        _refuse(f'The generated profile{feature} rolls back or folds in z; a Custom profile cannot represent it.',
                'Keep this horn in its original family, or reduce its rollback/truncation until every meridian advances toward the mouth, then convert again.')


def _cap_angle(design: DesignConfig, points: np.ndarray) -> float:
    # Use the same cap construction as the viewport and the solved mesh,
    # including explicit source radii and the family's automatic-angle rule.
    from hornlab_mesher.config_builder import build_geometry_params
    from hornlab_mesher.preview.source_cap import _source_geometry
    from hornlab_mesher.builders.point_grid_sources import _source_cap_height, _source_cap_radius

    config = (_legacy_config(design) if isinstance(design.root, LookupConfig) else
              design_to_mesher_config(design) if design.formula in NATIVE_FORMULAS else _bare_grid_config(design))
    params, _, _ = build_geometry_params(config)
    geometry = _source_geometry(params, design.formula, points)
    throat = float(np.mean(np.linalg.norm(points[:, 0, :2], axis=1)))
    height = _source_cap_height(throat, geometry)
    radius = _source_cap_radius(throat, geometry)
    return math.degrees(math.asin(min(1, throat / max(radius, throat * 1.001)))) if height > 1e-12 else 0.0


def _radial_difference(left: np.ndarray, right: np.ndarray, *, align_disjoint: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Symmetric radial distance of all branches at each common z.

    Comparison can inspect a rollback even though conversion must refuse it.
    At a fold, matching only the first radius would hide the rolled lip.
    """
    lo = max(left[:, 0].min(), right[:, 0].min())
    hi = min(left[:, 0].max(), right[:, 0].max())
    if hi < lo and align_disjoint:
        # Import always opens the best representation, including translated
        # tables. Compare relative meridians and retain the physical movement
        # independently in the extent and 3D mouth report.
        right = right.copy()
        right[:, 0] += left[:, 0].min()-right[:, 0].min()
        lo = max(left[:, 0].min(), right[:, 0].min())
        hi = min(left[:, 0].max(), right[:, 0].max())
    if hi < lo:
        _refuse('The generated profiles have no common axial range.',
                'Keep the original design or move the throat and mouth into the same axial range before converting.')
    grid = np.unique(np.r_[left[:, 0], right[:, 0], np.linspace(lo, hi, 2049)])
    grid = grid[(grid >= lo) & (grid <= hi)]
    def branches(rows):
        # Ignore repeated identical rows, which ATH's rounded axial tables emit.
        keep = np.r_[True, np.any(np.diff(rows, axis=0) != 0, axis=1)]
        rows = rows[keep]
        signs = np.sign(np.diff(rows[:, 0]))
        turns = np.flatnonzero(signs[1:] != signs[:-1])+1
        result = []
        for start, end in zip(np.r_[0, turns], np.r_[turns, len(rows)-1]):
            span = rows[start:end+1]
            span = span[np.argsort(span[:, 0])]
            values = np.interp(grid, span[:, 0], span[:, 1])
            values[(grid < span[0, 0]) | (grid > span[-1, 0])] = np.nan
            result.append(values)
        return np.array(result)
    a, b = branches(left), branches(right)
    distances = abs(a[:, None, :]-b[None, :, :])
    distances = np.where(np.isnan(distances), np.inf, distances)
    forward = distances.min(axis=1)
    backward = distances.min(axis=0)
    forward[~np.isfinite(a)] = 0
    backward[~np.isfinite(b)] = 0
    return grid, np.maximum(forward.max(axis=0), backward.max(axis=0))


def compare_generated_designs(
    before: DesignConfig, after: DesignConfig, *, features: list[FeatureChange] | None = None,
    before_points: np.ndarray | None = None, after_points: np.ndarray | None = None,
    align_disjoint: bool = False,
) -> GeneratedComparison:
    """Compare any two buildable ring-grid families using the export owner.

    No extent is silently cropped: radial comparisons cover the overlap and
    the independent extent error participates in both verdict and refusal.
    """
    first = _sample(before) if before_points is None else before_points
    second = _sample(after) if after_points is None else after_points
    a, b = _meridians(first), _meridians(second)
    deviations = []
    extent = 0.0
    for phi, left, right in zip(AZIMUTHS, a, b):
        extent = max(extent, abs(left[:, 0].min()-right[:, 0].min()), abs(left[:, 0].max()-right[:, 0].max()))
        grid, delta = _radial_difference(left, right, align_disjoint=align_disjoint)
        peak = int(np.argmax(delta))
        deviations.append(WallDeviation(max_deviation_mm=float(delta[peak]), azimuth_deg=float(phi),
                                        z_mm=float(grid[peak]), sample_count=len(grid)))
    mouth_delta = np.linalg.norm(a[:, -1, :] - b[:, -1, :], axis=1)
    peak = int(np.argmax(mouth_delta))
    return GeneratedComparison(
        H=deviations[0], V=deviations[ANGULAR // 4],
        off_axis=[d for d in deviations if d.azimuth_deg not in (0, 90)],
        wall=max(deviations, key=lambda d: d.max_deviation_mm),
        mouth=WallDeviation(max_deviation_mm=float(mouth_delta[peak]), azimuth_deg=float(AZIMUTHS[peak]),
                            z_mm=float(a[peak, -1, 0]), sample_count=ANGULAR),
        axial_extent_deviation_mm=float(extent),
        source_cap_angle_before_deg=_cap_angle(before, first), source_cap_angle_after_deg=_cap_angle(after, second),
        features=_feature_changes(before, after) if features is None else features, method=GeneratedComparisonMethod(
            angular_samples=ANGULAR, angular_samples_before=first.shape[0], angular_samples_after=second.shape[0],
            axial_segments_before=first.shape[1] - 1, axial_segments_after=second.shape[1] - 1,
            azimuths_deg=AZIMUTHS.tolist(), grid='Full 360° domain at 2.5°; union of both generated z grids and 2049 uniform overlap samples per meridian; polar-linear interpolation of exported rings.',
            limits=['Sampled estimate, not a bound between angular or axial samples and not a mesh/STEP surface-distance certificate.',
                    'The bare inner grid excludes the outer wall, enclosure, baffle, rear body and source surface; source-cap opening angle is checked separately.',
                    'Active morphs, explicit axial maps and ATH lips retain the authored axial count to preserve snapping and construction; other profiles use 1024 axial segments.',
                    'Native constructions use their resolved inner-wall grid at the native owner’s density; the actual axial count is reported above.',
                    'Radial errors use the common z range and all fold branches; axial extent error and 3D mouth-ring displacement at a common azimuth are reported separately.',
                    *(['Disjoint imported axial ranges are aligned at the throat for radial comparison; the actual translation remains in the extent and mouth measurements.'] if align_disjoint else [])]),
    )


def _feature_changes(before: DesignConfig, after: DesignConfig) -> list[FeatureChange]:
    if before.formula != 'FREEFORM' and after.formula == 'FREEFORM':
        return _preserve_shared(before)[1]
    changes = []
    for path, name in [('throat_ext_length','Throat extension'), ('slot_length','Slot'),
                       ('mesh.ath_roundover','ATH Mesh.Roundover'), ('mesh.ath_rear_shape','ATH Mesh.RearShape'),
                       ('guiding_curve','Guiding curves'), ('morph','Mouth morph')]:
        def value(design):
            item = design.root
            for field in path.split('.'):
                item = getattr(item, field, None)
            return item
        old, new = value(before), value(after)
        if old is not None and old != new:
            changes.append(FeatureChange(name=name, change='removed' if new is None else 'changed',
                message=f'{name} changes as a construction control; the inner-wall comparison does not certify its other surfaces or future edits.'))
    return changes


def _preserve_shared(source: DesignConfig) -> tuple[dict, list[FeatureChange]]:
    """One preservation rule for conversion; unrelated authored values survive."""
    payload = source.root.model_dump(mode='json')
    common = {key: value for key, value in payload.items() if key in DesignCommon.model_fields}
    features: list[FeatureChange] = []
    def feature(name: str, message: str, change: str = 'baked') -> None:
        features.append(FeatureChange(name=name, change=change, message=message))
    for key in ('text_import_version', 'ath_axial_sampling_rule', 'ath_nac_present', 'ath_nac', 'ath_axial_scale'):
        common.pop(key, None)
    for key, label in [('throat_ext_length', 'Throat extension'), ('slot_length', 'Slot')]:
        value = getattr(source.root, key)
        if value is not None and value.constant_value() != 0:
            feature(label, f'{label} is baked into the fitted wall; its separate construction control is removed.')
    for key in ('throat_ext_angle', 'throat_ext_length', 'slot_length'):
        common[key] = None  # already present in the generated meridians
    common['cross_section'] = {'exponent': 2, 'aspect_ratio': 1}
    if _active_morph(source):
        feature('Mouth morph', 'The generated morph is baked into the profiles and cross-section stations; the original morph controls no longer drive it.', 'removed')
        m = source.root.morph
        if m.target_width is None or m.target_height is None:
            feature('Implicit mouth rounding', 'The generated implicit mouth dimensions are baked into the fit instead of being rounded again by the original family.')
    common['morph'] = {**common['morph'], 'target_shape': 0}
    guide = getattr(source.root, 'guiding_curve', None)
    if guide is not None and guide.curve_type is not None and guide.curve_type.constant_value() != 0:
        feature('Guiding curves', 'The generated guiding shape is baked into the fit; its guiding-curve controls are removed.', 'removed')
    mesh = common['mesh']
    for key, label in [('throat_adapter', 'Throat adapter'), ('axial_scale', 'Axial scale'),
                       ('terminating_arc', 'Terminating arc')]:
        if payload.get(key) is not None:
            feature(label, f'{label} is baked into the fitted wall; its separate construction control is removed.')
    if mesh.get('mouth_roundover_radius_mm') is not None:
        feature('Mouth roundover', 'The native mouth roundover is baked into the fit; its outer lip is outside the inner-wall comparison.', 'removed')
    for key in ('size_mm', 'mouth_roundover_radius_mm'):
        mesh.pop(key, None)
    for key, label in [('ath_roundover', 'ATH Mesh.Roundover'), ('ath_rear_shape', 'ATH Mesh.RearShape'),
                       ('ath_throat_ext_segments', 'ATH throat-extension sampling')]:
        if mesh.get(key) is not None:
            feature(label, f'{label} is removed as a construction control; the bare-wall comparison does not certify its outer lip or rear surfaces.', 'removed')
    for key in list(mesh):
        if key.startswith('ath_'):
            mesh.pop(key)
    scale = _number(source.root.scale, 1)
    native = source.formula in NATIVE_FORMULAS
    if native and source.root.source.shape is None:
        common['source']['shape'] = 2  # native families have a flat source
    if uses_current_ath_import(source.root.text_import_version) or native:
        # ATH's mesh and source dimensions are physical; native FREEFORM scales
        # them. Re-express the values so the finished settings stay the same.
        for key in ('wall_thickness', 'throat_resolution', 'mouth_resolution', 'rear_resolution', 'vertical_offset', 'max_edge'):
            value = mesh.get(key)
            if value is None and key == 'wall_thickness':
                value = (5 if has_native_roundover(source.root) else 0) if native else (0 if source.root.simulation.sim_type == 'infinite-baffle' else 5)
            if value is not None:
                mesh[key] = _divide_expr(value, scale)
        if common['source'].get('radius') is not None:
            radius = source.root.source.radius
            if radius.constant_value() is None or radius.constant_value() > 0:
                common['source']['radius'] = _divide_expr(common['source']['radius'], scale)
    return common, features


def _divide_expr(value: object, scale: float) -> object:
    expr = Expr.model_validate(value)
    if scale == 1:
        return value
    return {'value': expr.value / scale if expr.value is not None else None,
            'raw': f'({expr.raw})/{scale:.17g}' if expr.raw is not None else None}


def _axisymmetric(source: DesignConfig) -> bool:
    root = source.root
    if root.cross_section.exponent != 2 or root.cross_section.aspect_ratio != 1 or _active_morph(source):
        return False
    guide = getattr(root, 'guiding_curve', None)
    if guide and guide.curve_type is not None and guide.curve_type.constant_value() != 0:
        return False
    # An unresolved azimuth expression is never evidence of axial symmetry.
    for value in root.model_dump().values():
        if isinstance(value, dict) and 'raw' in value and Expr.model_validate(value).constant_value() is None:
            return False
    return True


def _end_angle(samples: np.ndarray, mouth: bool = False, linear: bool = False) -> float:
    if linear:
        left, right = samples[-2:] if mouth else samples[:2]
        return math.degrees(math.atan2(right[1]-left[1], right[0]-left[0]))
    rows = samples[-4:] if mouth else samples[:4]
    z0 = rows[-1 if mouth else 0, 0]
    # Estimate the end slope from nearby generated coordinates, rather than
    # the chord of a sparsely fitted endpoint span.
    slope = np.polynomial.polynomial.polyfit(rows[:, 0] - z0, rows[:, 1], min(3, len(rows)-1))[1]
    return float(np.clip(math.degrees(math.atan(slope)), -90, 90))


def _merge_straight_rows(rows: np.ndarray) -> np.ndarray:
    """Remove only collinear intermediate rows, within floating point roundoff."""
    kept = [0]
    for i in range(1, len(rows)-1):
        left, middle, right = rows[kept[-1]], rows[i], rows[i+1]
        predicted = left[1] + (right[1]-left[1])*(middle[0]-left[0])/(right[0]-left[0])
        if abs(predicted-middle[1]) > 32*np.finfo(float).eps*max(1, abs(predicted), abs(middle[1])):
            kept.append(i)
    return rows[[*kept, len(rows)-1]]


def _polyline_fit(rows: np.ndarray, scale: float, check: Callable) -> tuple[dict, float]:
    """Exact straight runs first; bounded scalar PCHIP handles only at the limits.

    The cubic-minus-polyline maximum checks every original interval's ends and
    derivative roots. The reported curve error is independent of the wall grid.
    """
    from hornlab_mesher.freeform import MIN_INTERIOR_CORNER_SPACING_MM
    original = rows
    rows = _merge_straight_rows(rows)
    length = float(rows[-1, 0])
    # The mesher's spacing is measured in finished millimetres.
    spacing = MIN_INTERIOR_CORNER_SPACING_MM + 1e-10
    knot_spacing = length*1.1e-7
    exact = len(rows) <= 64 and np.min(np.diff(rows[:, 0])) >= knot_spacing and (len(rows) <= 3 or np.min(np.diff(rows[1:-1, 0])) >= spacing-2e-10)
    if exact:
        selected = list(range(len(rows)))
        corners = set(selected)
    else:
        angles = np.arctan2(np.diff(rows[:, 1]), np.diff(rows[:, 0]))
        strength = abs(np.diff(angles))
        corners = {0, len(rows)-1}
        for i in np.argsort(-strength)+1:
            if (strength[i-1] < math.radians(1) or len(corners) >= 32
                or min(rows[i, 0], length-rows[i, 0]) < knot_spacing):
                continue
            if all(abs(rows[i, 0]-rows[j, 0]) >= spacing for j in corners if j not in (0, len(rows)-1)):
                corners.add(int(i))
        selected = sorted(corners)
    def fit():
        check()
        chosen = rows[selected].copy()
        previous_corner = None
        for j, index in enumerate(selected[1:-1], 1):
            if index not in corners:
                continue
            # Normalized t -> physical z can round an exactly 1 mm gap just
            # below the mesher guard. Correct only floating-point roundoff.
            position = chosen[j, 0]/length*(length/scale)*scale
            if previous_corner is not None:
                gap = previous_corner+MIN_INTERIOR_CORNER_SPACING_MM-position
                if 0 <= gap < 1e-8:
                    chosen[j, 0] += gap+1e-10
                    position = chosen[j, 0]
            previous_corner = position
        points = [{'t': float(z/length), 'r': float(r/scale),
                   'join': 'corner' if i in corners else 'smooth'} for i, (z, r) in zip(selected, chosen)]
        boundaries = [selected.index(i) for i in sorted(corners)]
        polynomials = []
        for lo, hi in zip(boundaries[:-1], boundaries[1:]):
            run = chosen[lo:hi+1]
            polynomial = PchipInterpolator(run[:, 0], run[:, 1])
            slopes = polynomial.derivative()(run[:, 0])
            for j in range(lo, hi):
                dz = (chosen[j+1, 0]-chosen[j, 0])/3/scale
                points[j]['handle_out'] = [float(dz), float(slopes[j-lo]*dz)]
                points[j+1]['handle_in'] = [-float(dz), -float(slopes[j-lo+1]*dz)]
                polynomials.append((chosen[j, 0], chosen[j+1, 0], polynomial.c[:, j-lo]))
        worst, worst_z = 0., float(rows[0, 0])
        for z0, z1, coefficients in polynomials:
            first = max(0, np.searchsorted(original[:, 0], z0, side='right')-1)
            last = min(len(original)-2, np.searchsorted(original[:, 0], z1, side='left'))
            for i in range(first, last+1):
                left, right = max(z0, original[i, 0]), min(z1, original[i+1, 0])
                if right < left:
                    continue
                slope = (original[i+1, 1]-original[i, 1])/(original[i+1, 0]-original[i, 0])
                difference = coefficients.copy()
                difference[2] -= slope
                difference[3] -= original[i, 1]+slope*(z0-original[i, 0])
                roots = np.roots(np.trim_zeros(np.array([3*difference[0], 2*difference[1], difference[2]]), 'f'))
                probes = [left-z0, right-z0, *[float(v.real) for v in roots if abs(v.imag) < 1e-10 and left-z0 <= v.real <= right-z0]]
                for value in probes:
                    error = abs(float(np.polyval(difference, value)))
                    if error > worst:
                        worst, worst_z = error, value+z0
        return {'points': points, 'throat_angle_deg': _end_angle(rows, linear=True),
                'mouth_angle_deg': _end_angle(rows, mouth=True, linear=True)}, worst, worst_z
    for _ in range(128):
        profile, error, position = fit()
        if error <= MATCH_MM or exact:
            return profile, error
        available = [i for i in range(1, len(rows)-1) if i not in selected
                     and all(abs(rows[i, 0]-rows[j, 0]) >= knot_spacing for j in selected)]
        if available and len(selected) < 64:
            selected = sorted([*selected, min(available, key=lambda i: abs(rows[i, 0]-position))])
            continue
        eligible = [i for i in selected[1:-1] if i not in corners and all(
            abs(rows[i, 0]-rows[j, 0]) >= spacing for j in corners if j not in (0, len(rows)-1))]
        if not eligible:
            return profile, error
        corners.add(min(eligible, key=lambda i: abs(rows[i, 0]-position)))
    return profile, error


def _fit_plane(samples: np.ndarray, seed_z: list[float], scale: float, check: Callable) -> dict:
    from hornlab_mesher import freeform_evaluate_profile
    from hornlab_mesher.freeform import MIN_INTERIOR_CORNER_SPACING_MM
    from server.preview.freeform import _params
    _, unique = np.unique(samples[:, 0], return_index=True)
    samples = samples[np.sort(unique)]
    raw_z, raw_r = samples.T
    length = raw_z[-1]
    z = np.unique(np.r_[raw_z, np.linspace(raw_z[0], length, 2049),
                        [v for v in seed_z if raw_z[0] <= v <= length]])
    r = np.interp(z, raw_z, raw_r)
    selected = sorted(set([0, len(z)-1, *[int(np.argmin(abs(z-v))) for v in seed_z]]))
    if len(selected) > 64:
        selected = [0, len(z)-1]
    throat, mouth = _end_angle(samples), _end_angle(samples, True)
    slopes = np.degrees(np.arctan(np.gradient(r, z, edge_order=2)))
    slopes[0], slopes[-1] = throat, mouth
    while True:
        check()
        points = [{'t': float(z[i]/length), 'r': float(r[i]/scale),
                   'join': 'corner' if any(abs(z[i]-v) < 1e-8 for v in seed_z) else 'smooth'} for i in selected]
        for i, point in zip(selected, points):
            if point['join'] == 'smooth':
                point['angle_deg'] = float(slopes[i])
        previous_corner = None
        for point in points[1:-1]:
            if point['join'] != 'corner':
                continue
            position = point['t']*length
            if previous_corner is not None:
                gap = previous_corner+MIN_INTERIOR_CORNER_SPACING_MM-position
                # t -> mm can round an exactly 1 mm corner gap below the
                # mesher's limit. Correct only that floating-point roundoff,
                # never a physically undersized span.
                if 0 <= gap < 1e-8:
                    position += gap+1e-10
                    point['t'] = float(position/length)
            previous_corner = position
        profile = {'points': points, 'throat_angle_deg': throat, 'mouth_angle_deg': mouth}
        # Use the edit API's translator and production spline inversion,
        # precedence, tangent shrinking and validation, in physical mm.
        physical_points = []
        for point in points:
            physical_point = {**point, 'r': point['r']*scale}
            for handle in ('handle_in', 'handle_out'):
                if handle in point:
                    physical_point[handle] = [component*scale for component in point[handle]]
            physical_points.append(physical_point)
        physical = {**profile, 'points': physical_points}
        trial = DesignConfig.model_validate({'formula': 'FREEFORM', 'length': length,
            'profile_h': physical, 'profile_v': physical, 'inflection_policy': 'warn',
            'cross_sections': [{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}]})
        evaluated = freeform_evaluate_profile(_params(trial), 'H', z)
        error = abs(evaluated-r)
        for chosen in selected:
            error[abs(z-z[chosen]) < length*1.1e-7] = 0
        if float(error.max()) <= MATCH_MM * .65 or len(selected) == 64:
            return profile
        selected.append(int(np.argmax(error)))
        selected.sort()


def _fit_stations(wall: np.ndarray, length: float, scale: float, check: Callable) -> list[dict]:
    """Fit each selected ring with the mesher's own station-outline evaluator."""
    from hornlab_mesher.freeform import _station_radius, _resolve_active_station_blend
    phi = np.radians(AZIMUTHS)
    z = np.unique(np.r_[wall[0, :, 0], wall[ANGULAR//4, :, 0], np.linspace(0, length, 129)])
    radii = np.array([np.interp(z, meridian[:, 0], meridian[:, 1]) for meridian in wall]).T
    h, v = radii[:, 0], radii[:, ANGULAR//4]
    descriptors: dict[int, dict] = {0: {'shape': 'ellipse'}}
    def descriptor(i: int) -> dict:
        if i not in descriptors:
            check()
            def error(station):
                return float(np.max(abs(_station_radius(station, phi, h[i], v[i])-radii[i])))
            candidates = [{'shape': 'ellipse'}]
            power = minimize_scalar(lambda n: error({'shape': 'superellipse', 'exponent': n}), bounds=(2,16), method='bounded')
            candidates.append({'shape': 'superellipse', 'exponent': float(power.x)})
            # Search the pinned mesher's documented station corner range.
            # The final production build still owns feasibility, including
            # the neighbouring station blend, and its deviation is measured.
            limit = min(h[i], v[i])
            corner = minimize_scalar(lambda c: error({'shape': 'rounded_rectangle', 'cornerRadiusMm': c}), bounds=(.02*limit, limit), method='bounded')
            candidates.append({'shape': 'rounded_rectangle', 'cornerRadiusMm': float(corner.x)})
            descriptors[i] = min(candidates, key=error)
        return descriptors[i]
    selected = [0, len(z)-1]
    while True:
        check()
        stations = [{'t': float(z[i]/length), **descriptor(i)} for i in selected]
        errors = []
        for i, zi in enumerate(z):
            blend = _resolve_active_station_blend(stations, float(zi/length))
            left = _station_radius(stations[blend.first_index], phi, h[i], v[i])
            right = _station_radius(stations[blend.second_index], phi, h[i], v[i])
            weight = blend.station_weight(blend.second_index)
            errors.append(float(np.max(abs((1-weight)*left + weight*right-radii[i]))))
        # Distinct angular shapes can be impossible for these station families;
        # the final generated comparison decides, never the outline fit alone.
        errors = np.array(errors)
        errors[selected] = 0
        if errors.max() <= MATCH_MM*.65 or len(selected) == 32:
            return [{**{'t': s['t'], 'shape': s['shape']},
                     **({'exponent': s['exponent']} if 'exponent' in s else {}),
                     **({'corner_radius_mm': s['cornerRadiusMm']/scale} if 'cornerRadiusMm' in s else {})} for s in stations]
        selected.append(int(errors.argmax()))
        selected.sort()


def _convert_design(source: DesignConfig, content_key: str, *, check: Callable,
                    progress: Callable, importing: bool = False) -> ConvertResponse:
    from hornlab_mesher.errors import ConfigError, UserRefusal
    if source.formula == 'FREEFORM':
        _refuse('This design is already a Custom profile.', 'Edit its existing profile anchors instead of converting it.')
    if source.formula == 'SOURCE-DISK':
        _refuse('A source body has no inner horn wall to convert to a Custom profile.',
                'Keep this source body, or choose a horn family before converting.')
    check()
    try:
        original = _sample(source)
    except (ConfigError, UserRefusal) as exc:
        if isinstance(exc, UserRefusal):
            raise
        _refuse('WG could not generate a conversion profile from this source.',
                'Keep its original family, or remove unsupported construction features and try again.')
    wall = _meridians(original)
    _monotone(wall, ' with the ATH Mesh.Roundover rollback lip' if source.root.mesh.ath_roundover else '')
    progress('sample')
    origins = wall[:, 0, 0].copy()
    if np.max(abs(origins)) > REFUSE_MM and not importing:
        _refuse('The generated throat is more than 0.5 mm from z = 0; a Custom profile starts at zero.',
                'Shift the imported table so its first z is zero, then convert again.')
    wall[:, :, 0] -= origins[:, None]
    length = float(wall[0, -1, 0])
    scale = _number(source.root.scale, 1)
    common, features = _preserve_shared(source)
    if np.max(abs(origins)) > 1e-8:
        features.append(FeatureChange(name='Axial origin', change='changed',
            message='The throat moves to z = 0; its translation is included in the axial extent and mouth-outline deviations.'))
    lookup = isinstance(source.root, LookupConfig)
    curve_error = None
    if lookup:
        from hornlab_mesher.config_builder import build_geometry_params
        from hornlab_mesher.preview.source_cap import _source_geometry
        from hornlab_mesher.builders.point_grid_sources import _source_cap_height, _source_cap_radius
        rows = np.asarray(source.root.lookup_profile, dtype=float)*scale
        rows[:, 0] -= rows[0, 0]
        length = float(rows[-1, 0])
        h, curve_error = _polyline_fit(rows, scale, check)
        # Keep the authored meridian, and freeze family-specific construction
        # settings explicitly instead of fitting a second morphed meridian.
        common['morph'] = source.root.morph.model_dump(mode='json')
        features = [f for f in features if f.name not in ('Mouth morph', 'Implicit mouth rounding')]
        config = _legacy_config(source)
        params, formula, _ = build_geometry_params(config)
        cap = _source_geometry(params, formula, original)
        throat = float(np.mean(np.linalg.norm(original[:, 0, :2], axis=1)))
        height, radius = _source_cap_height(throat, cap), _source_cap_radius(throat, cap)
        if height <= 1e-12 or not math.isfinite(radius):
            common['source']['shape'] = 2
        else:
            common['source']['radius'] = radius/scale
        features.append(FeatureChange(name='Source-cap angle', change='baked',
            message='Source shape preserved explicitly, including the resolved source-cap angle and curvature.'))
        if _active_morph(source):
            _, construction = _legacy_grid(source, authored=True)
            start = float(construction['morph_start'])
            common['morph']['fixed_part'] = start
            features.append(FeatureChange(name='Morph-start snapping', change='baked',
                message=f'Morph-start snapping is frozen at {start:.12g} of the authored axial table; Custom profile uses that continuous start.'))
            from hornlab_mesher.profile_morph import _resolve_morph_half_dimensions, _morph_target_shape
            shape_only = _morph_target_shape(params, 0) == 3
            half_width, half_height = _resolve_morph_half_dimensions(params, 0, rows[-1, 1], rows[-1, 1], round_implicit_up=not shape_only)
            for field, half in (('target_width', half_width), ('target_height', half_height)):
                value = getattr(source.root.morph, field)
                if value is None or value.constant_value() == 0:
                    common['morph'][field] = 2*half/scale
                    label = 'Implicit mouth dimensions' if shape_only else 'Implicit mouth rounding'
                    features.append(FeatureChange(name=label, change='baked',
                        message=f'{label} frozen as {field.replace("_", " ")} {2*half:.12g} mm.'))
        exponent = source.root.cross_section.exponent
        stations = [{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}]
        if exponent != 2:
            # The generic section scales only growth past a circular throat.
            # Reuse S4's station fitter against that actual construction rather
            # than pretending a linear exponent ramp is the same outline.
            base = DesignConfig.model_construct(root=source.root.model_copy(update={
                'morph': source.root.morph.model_copy(update={'target_shape': Expr(value=0)})}))
            base_wall = _meridians(_legacy_grid(base)[0])
            base_wall[:, :, 0] -= base_wall[:, :1, 0]
            stations = _fit_stations(base_wall, length, scale, check)
            features.append(FeatureChange(name='Cross-section exponent', change='baked',
                message='The cross-section exponent and circular-throat growth rule are fitted into the cross-section stations; the outline fit is included in the generated-grid comparison.'))
        if source.root.cross_section.aspect_ratio != 1:
            features.append(FeatureChange(name='Cross-section aspect', change='changed',
                message='The shared authored meridian is retained; the former aspect scaling differs and is included in the generated-wall comparison.'))
        features.append(FeatureChange(name='Axial map / interpolate fit', change='changed',
            message='The ATH solve z-map / interpolate surface fit is replaced by the Custom profile corner-wall fit; the generated-grid comparison is sampled, not an OCC wall-distance certificate.'))
        candidate = DesignConfig.model_validate({**common, 'formula': 'FREEFORM', 'length': length/scale,
            'profile_h': h, 'profile_v': h, 'shared_profile': True, 'cross_sections': stations, 'inflection_policy': 'warn'})
    else:
        shared = _axisymmetric(source)
        def seeds(phi: float) -> list[float]:
            # Evaluate the original expression with the production evaluator. The
            # planes can have different extension/slot crease positions.
            extension = _at(source.root.throat_ext_length, phi)*scale
            slot = _at(source.root.slot_length, phi)*scale
            return [v for v in (extension, extension+slot) if 0 < v < length]
        h = _fit_plane(wall[0], seeds(0), scale, check)
        v = h if shared else _fit_plane(wall[ANGULAR//4], seeds(math.pi/2), scale, check)
        v['points'][0]['r'] = h['points'][0]['r']
        if np.ptp(wall[:, 0, 1]) > 1e-8:
            features.append(FeatureChange(name='Throat outline', change='changed',
                message='The generated throat is fitted as one circle; its outline change is included in the wall and source-cap measurements.'))
        stations = ([{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}] if shared
                    else _fit_stations(wall, length, scale, check))
        candidate = DesignConfig.model_validate({**common, 'formula': 'FREEFORM', 'length': length/scale,
            'profile_h': h, 'profile_v': v, 'shared_profile': shared, 'cross_sections': stations, 'inflection_policy': 'warn'})
    progress('fit')
    check()
    report = compare_generated_designs(source, candidate, features=features, before_points=original, align_disjoint=importing)
    maximum = max(report.wall.max_deviation_mm, report.mouth.max_deviation_mm, report.axial_extent_deviation_mm)
    if curve_error is not None:
        report.curve_deviation_mm = curve_error
    if maximum > REFUSE_MM and not importing:
        _refuse(f'The best Custom profile differs by {maximum:.4f} mm, above the 0.5 mm conversion limit (up to 64 anchors and 32 stations).',
                'Keep the original family, or simplify the profile/morph and try again; no geometry was cropped or substituted.')
    progress('compare')
    same = maximum <= MATCH_MM and abs(report.source_cap_angle_before_deg-report.source_cap_angle_after_deg) <= .01 and not features
    progress('ready')
    return ConvertResponse(content_key=content_key, design=candidate, report=report, verdict='match' if same else 'approximate')


def convert_design(source: DesignConfig, content_key: str, *, check: Callable = lambda: None,
                   progress: Callable = lambda stage: None, importing: bool = False) -> ConvertResponse:
    try:
        return _convert_design(source, content_key, check=check, progress=progress, importing=importing)
    except ValueError as exc:
        # This mesher spacing refusal predates UserRefusal. Match only this
        # documented constraint; unexpected ValueErrors remain program faults.
        if str(exc).startswith('FREEFORM semantic axial features are closer than normalized-t tolerance'):
            _refuse('The generated profile needs anchors closer together than a Custom profile can build.',
                    'Keep the original family, or soften the nearly vertical end or tightly spaced features before converting again.')
        raise


def conversion_notice(result: ConvertResponse) -> str:
    report = result.report
    maximum = max(report.wall.max_deviation_mm, report.mouth.max_deviation_mm, report.axial_extent_deviation_mm)
    curve = report.curve_deviation_mm or 0
    fit = 'Profile curve unchanged.' if curve < 1e-9 else f'Maximum profile curve change {curve:.6g} mm.'
    limit = ' The fit exceeds the 0.01 mm profile tolerance.' if curve > MATCH_MM else ''
    refusal = ' This exceeds the 0.5 mm explicit-conversion limit; opened with the best fit.' if maximum > REFUSE_MM else ''
    return (f'Opened as Custom profile. {fit} Maximum generated-grid change {maximum:.6g} mm.'
            f'{limit}{refusal} ' + ' '.join(f.message for f in report.features))
