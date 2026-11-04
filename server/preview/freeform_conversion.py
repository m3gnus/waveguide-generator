"""Explicit conversion, measured on the profile export's generated inner grid.

This module fits authored data; production sampling and wall comparison stay
with the mesher. Coordinates and tolerances are finished (scaled) millimetres.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
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
AXIAL = 4096
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


def _expression_payload(value, transform):
    """Visit typed expressions only; unknown text/extension blocks stay text."""
    if isinstance(value, Expr):
        return transform(value)
    if isinstance(value, StrictModel):
        return {key: _expression_payload(getattr(value, key), transform) for key in value.model_dump(mode='json')}
    if isinstance(value, dict):
        return {key: _expression_payload(child, transform) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_expression_payload(child, transform) for child in value]
    return value


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
    if not authored and not _active_morph(source) and not (source.root.mesh.z_map_points or '').strip():
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


def _kept_wall(wall: np.ndarray) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Keep each meridian through its maximum z; retain the removed lip to report.

    Counts may differ between meridians. Keep the actual generated nodes rather
    than resampling away a crease or moving a meridian's fold to its neighbour's.
    """
    kept, lips = [], []
    for rows in wall:
        end = int(np.argmax(rows[:, 0]))
        lip = rows[end:] if end < len(rows)-1 else rows[:0]
        # Repeated identical endpoint samples are not a removed feature.
        if len(lip) and np.all(np.linalg.norm(lip-lip[0], axis=1) <= 1e-9):
            lip = rows[:0]
        span = rows[:end+1].copy() if len(lip) else rows.copy()
        dz, dr = np.diff(span, axis=0).T
        if len(span) < 2 or np.any((dz < -1e-10) | ((dz <= 0) & (abs(dr) > 1e-9))):
            _refuse('The kept generated wall does not advance in z before its maximum; a Custom profile cannot represent it.',
                    'Keep this horn in its original family, or simplify the folds before converting again.')
        kept.append(span)
        lips.append(lip)
    return kept, lips


def _lip_change(lips: Sequence[np.ndarray], mouth: np.ndarray) -> FeatureChange | None:
    removed = [(lip, end) for lip, end in zip(lips, mouth) if len(lip)]
    if not removed:
        return None
    axial = max(float(np.ptp(lip[:, 0])) for lip, _ in removed)
    radial = max(float(np.ptp(lip[:, 1])) for lip, _ in removed)
    distance = max(float(np.linalg.norm(lip-end, axis=1).max()) for lip, end in removed)
    return FeatureChange(name='Rolled-back lip removed', change='removed',
        message=f'Rolled-back lip removed: axial length {axial:.6g} mm; radial extent {radial:.6g} mm; '
                f'maximum sampled distance to the converted mouth at the same azimuth {distance:.6g} mm. '
                'Conversion ends at each meridian’s maximum z. Wall and mouth deviations measure only the kept part; '
                'the removed lip is excluded from the 0.5 mm wall limit. The verdict is approximate.')


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

    Comparison can inspect every branch of a rollback.
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
    before_wall: Sequence[np.ndarray] | None = None,
    align_disjoint: bool = False, include_construction: bool = True,
) -> GeneratedComparison:
    """Compare any two buildable ring-grid families using the export owner.

    No extent is silently cropped: radial comparisons cover the overlap and
    the independent extent error participates in both verdict and refusal.
    """
    first = _sample(before) if before_points is None else before_points
    second = _sample(after) if after_points is None else after_points
    a = _meridians(first) if before_wall is None else before_wall
    b = _meridians(second)
    deviations = []
    extent = 0.0
    for phi, left, right in zip(AZIMUTHS, a, b):
        extent = max(extent, abs(left[:, 0].min()-right[:, 0].min()), abs(left[:, 0].max()-right[:, 0].max()))
        grid, delta = _radial_difference(left, right, align_disjoint=align_disjoint)
        peak = int(np.argmax(delta))
        deviations.append(WallDeviation(max_deviation_mm=float(delta[peak]), azimuth_deg=float(phi),
                                        z_mm=float(grid[peak]), sample_count=len(grid)))
    mouth_delta = np.linalg.norm(np.array([rows[-1] for rows in a]) - b[:, -1, :], axis=1)
    peak = int(np.argmax(mouth_delta))
    return GeneratedComparison(
        H=deviations[0], V=deviations[ANGULAR // 4],
        off_axis=[d for d in deviations if d.azimuth_deg not in (0, 90)],
        wall=max(deviations, key=lambda d: d.max_deviation_mm),
        mouth=WallDeviation(max_deviation_mm=float(mouth_delta[peak]), azimuth_deg=float(AZIMUTHS[peak]),
                            z_mm=float(a[peak][-1, 0]), sample_count=ANGULAR),
        axial_extent_deviation_mm=float(extent),
        source_cap_angle_before_deg=_cap_angle(before, first), source_cap_angle_after_deg=_cap_angle(after, second),
        features=[*(_feature_changes(before, after) if features is None else features),
                  *(_construction_changes(before, after) if include_construction else [])], method=GeneratedComparisonMethod(
            angular_samples=ANGULAR, angular_samples_before=first.shape[0], angular_samples_after=second.shape[0],
            axial_segments_before=first.shape[1] - 1, axial_segments_after=second.shape[1] - 1,
            azimuths_deg=AZIMUTHS.tolist(), grid='Full 360° domain at 2.5°; union of both generated z grids and 2049 uniform overlap samples per meridian; polar-linear interpolation of exported rings.',
            limits=['Sampled estimate, not a bound between angular or axial samples and not a mesh/STEP surface-distance certificate.',
                    'Rear boundaries and outer walls are checked separately on the resolved construction grids and reported as feature changes; enclosure, baffle and source surface distances are not certified. Source-cap opening angle is checked separately.',
                    'Active morphs, explicit axial maps and ATH lips retain the authored axial count to preserve snapping and construction; other profiles use 4096 axial segments plus semantic anchors.',
                    'Native constructions use their resolved inner-wall grid at the native owner’s density; the actual axial count is reported above.',
                    'Radial errors use the common z range and all fold branches; axial extent error and 3D mouth-ring displacement at a common azimuth are reported separately.',
                    *(['Disjoint imported axial ranges are aligned at the throat for radial comparison; the actual translation remains in the extent and mouth measurements.'] if align_disjoint else [])]),
    )


def _construction_changes(before: DesignConfig, after: DesignConfig, *, removed_lip: bool = False) -> list[FeatureChange]:
    """Measure construction the bare bore deliberately omits, using its owner."""
    from hornlab_mesher.config_builder import resolve_geometry, resolve_enclosure_bounds
    from hornlab_mesher.rear_compatibility import geometry_rear_ring
    from hornlab_mesher.builders.point_grid_freestanding import _restored_outer_throat_points
    from hornlab_mesher.errors import UserRefusal
    from server.preview.errors import public_error_message

    configs = []
    for design in (before, after):
        # The resolver's structural controls require numbers. Evaluate scalar
        # expressions through Expr, preserving their physical value and dialect.
        payload = _expression_payload(design.root, lambda e: e.constant_value()
            if e.constant_value() is not None else e.model_dump(mode='json'))
        numeric = legacy_lookup_source(payload) if isinstance(design.root, LookupConfig) else DesignConfig.model_validate(payload)
        configs.append(_legacy_config(numeric) if isinstance(numeric.root, LookupConfig) else design_to_mesher_config(numeric))
    # Bare/infinite-baffle/enclosure designs have no offset outer shell/rear.
    if all(config['mode'] != 'freestanding' for config in configs) and not removed_lip:
        return []
    geometries = []
    resolutions = []
    problems = []
    for label, config in zip(('Original', 'Converted'), configs):
        # Keep the production sampling contract: rear normals and hence the
        # rear plane depend on it. Do not substitute legacy point-grid normals.
        try:
            resolved = resolve_geometry(config)
            resolutions.append(resolved)
            geometries.append(resolved.geometry)
        except UserRefusal as exc:
            words = public_error_message(exc)
            if words is None:
                raise
            problems.append(FeatureChange(name='Outer construction', change='changed',
                message=f'{label} enclosure/baffle/outer/rear construction could not be generated: {words} Its surface change is unmeasured.'))
    if problems:
        return problems
    changes = []
    if removed_lip and any(config['mode'] in ('enclosure', 'infinite-baffle') for config in configs):
        rings = [_meridians(g.inner_points)[:, -1] for g in geometries]
        displacement = float(np.linalg.norm(rings[0]-rings[1], axis=1).max())
        if displacement > MATCH_MM:
            changes.append(FeatureChange(name='Baffle opening', change='changed',
                message=f'The baffle opening follows the kept mouth after lip removal; maximum sampled opening displacement {displacement:.6g} mm.'))
        bounds = [resolve_enclosure_bounds(config, resolved=resolved) for config, resolved in zip(configs, resolutions)]
        if all(bound is not None for bound in bounds):
            moved = [f'{key} {bounds[0][key]:.6g} → {bounds[1][key]:.6g} mm'
                     for key in ('bx0', 'bx1', 'by0', 'by1', 'z_front', 'z_back')
                     if abs(bounds[0][key]-bounds[1][key]) > MATCH_MM]
            if moved:
                changes.append(FeatureChange(name='Enclosure / baffle bounds', change='changed',
                    message='Enclosure / baffle placement changes after lip removal (authored settings retained): '+ '; '.join(moved)+'.'))
    outer = [getattr(g, 'outer_points', None) for g in geometries]
    if any(points is None for points in outer):
        if not all(points is None for points in outer):
            changes.append(FeatureChange(name='Outer wall / rear body', change='changed',
                message='The resolved outer shell and rear body are present in only one design.'))
        return changes
    outer = [_restored_outer_throat_points(g.inner_points, points, wall_thickness_mm=g.wall_thickness_mm)
             for g, points in zip(geometries, outer)]
    a, b = [_meridians(points) for points in outer]
    maximum = 0.
    for left, right in zip(a, b):
        _, delta = _radial_difference(left, right, align_disjoint=True)
        maximum = max(maximum, float(delta.max()),
                      abs(left[:, 0].min()-right[:, 0].min()), abs(left[:, 0].max()-right[:, 0].max()))
    if maximum > MATCH_MM:
        changes.append(FeatureChange(name='Outer wall', change='changed',
            message=f'Maximum sampled outer-wall radial/axial-extent change {maximum:.6g} mm on the resolved construction grids.'))
    rear = [getattr(g, 'ath_rear_points', None) for g in geometries]
    rear = [geometry_rear_ring(g, g.inner_points, points) if ring is None else ring
            for g, points, ring in zip(geometries, outer, rear)]
    rings = [_meridians(np.asarray(ring)[:, None, :])[:, 0] for ring in rear]
    displacement = float(np.linalg.norm(rings[0]-rings[1], axis=1).max())
    for name in ('ath_rear_apex', 'ath_warped_rear_apex'):
        apex = [getattr(g, name, None) for g in geometries]
        if any(value is not None for value in apex):
            displacement = max(displacement, float(np.linalg.norm(
                np.asarray(apex[0] if apex[0] is not None else np.mean(rear[0], axis=0))-
                np.asarray(apex[1] if apex[1] is not None else np.mean(rear[1], axis=0)))))
    if displacement > MATCH_MM:
        def location(ring):
            lo, hi = np.min(ring[:, 2]), np.max(ring[:, 2])
            return f'{lo:.6g}' if hi-lo < 1e-8 else f'[{lo:.6g}, {hi:.6g}]'
        changes.append(FeatureChange(name='Rear construction', change='changed',
            message=f'Rear boundary z {location(rear[0])} → {location(rear[1])} mm; maximum sampled boundary/apex displacement {displacement:.6g} mm.'))
    return changes


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
    if source.root.text_import_version is not None:
        preserved = _expression_payload(source.root, lambda e: e.in_dialect('wg-v1').model_dump(mode='json'))
        common = {key: preserved[key] for key in common}
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
    if expr.constant_value() is not None:
        return expr.constant_value()/scale
    return {'value': expr.value / scale if expr.value is not None else None,
            'raw': f'({expr.raw})/{scale:.17g}' if expr.raw is not None else None,
            'dialect': expr.dialect}


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


def _fit_plane(samples: np.ndarray, seed_z: list[float], scale: float, check: Callable,
               *, anchor_z: Sequence[float] = (), explicit: bool = False) -> dict:
    from hornlab_mesher import freeform_evaluate_profile
    from hornlab_mesher.freeform import MIN_INTERIOR_CORNER_SPACING_MM
    from server.preview.freeform import _params
    _, unique = np.unique(samples[:, 0], return_index=True)
    samples = samples[np.sort(unique)]
    raw_z, raw_r = samples.T
    length = raw_z[-1]
    z = np.unique(np.r_[raw_z, np.linspace(raw_z[0], length, 2049),
                        [v for v in (*seed_z, *anchor_z) if raw_z[0] <= v <= length]])
    r = np.interp(z, raw_z, raw_r)
    arc = np.r_[0., np.cumsum(np.hypot(np.diff(z), np.diff(r)))]
    selected = sorted(set([0, len(z)-1, *[int(np.argmin(abs(z-v))) for v in (*seed_z, *anchor_z)]]))
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
            if point['join'] == 'smooth' and not explicit:
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
        if explicit:
            # Parametric shape-preserving cubics give nearly vertical spans
            # their own handle speeds, without the automatic tangent solver's
            # repeated global shrinking. Each smooth run keeps collinear joins.
            boundaries = [0, *[j for j, p in enumerate(points[1:-1], 1) if p['join'] == 'corner'], len(points)-1]
            chosen = np.column_stack((z[selected], r[selected]))
            for lo, hi in zip(boundaries[:-1], boundaries[1:]):
                run_arc = arc[np.asarray(selected[lo:hi+1])]
                derivatives = PchipInterpolator(run_arc, chosen[lo:hi+1]).derivative()(run_arc)
                for j in range(lo, hi):
                    step = (arc[selected[j+1]]-arc[selected[j]])/3/scale
                    points[j]['handle_out'] = (derivatives[j-lo]*step).tolist()
                    points[j+1]['handle_in'] = (-derivatives[j-lo+1]*step).tolist()
        profile = {'points': points, 'throat_angle_deg': throat, 'mouth_angle_deg': mouth}
        if explicit:
            # The feedback loop immediately builds and measures this candidate;
            # spend its next anchors on that generated error, not a second fit.
            return profile
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
            'shared_profile': True,
            'cross_sections': [{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}]})
        evaluated = freeform_evaluate_profile(_params(trial), 'H', z)
        error = abs(evaluated-r)
        for chosen in selected:
            error[abs(z-z[chosen]) < length*1.1e-7] = 0
        if float(error.max()) <= MATCH_MM * .65 or len(selected) == 64:
            return profile
        selected.append(int(np.argmax(error)))
        selected.sort()


def _refine_generated(candidate: DesignConfig, wall: Sequence[np.ndarray], scale: float, seeds: Callable,
                      check: Callable) -> tuple[DesignConfig, np.ndarray]:
    """Spend the remaining anchors where the generated grid actually differs."""
    length = _number(candidate.root.length)*scale
    payload = candidate.model_dump(mode='json')
    shared = candidate.root.shared_profile
    planes = [('profile_h', 0)] if shared else [('profile_h', 0), ('profile_v', ANGULAR//4)]
    for _ in range(64):
        check()
        generated = _sample(candidate)
        actual = _meridians(generated)
        changed = False
        for field, index in planes:
            profile = payload[field]
            if len(profile['points']) >= 64:
                continue
            grid, errors = _radial_difference(wall[index], actual[index])
            anchors = np.array([point['t']['value']*length for point in profile['points']])
            eligible = np.min(abs(grid[:, None]-anchors), axis=1) > length*1.1e-7
            errors[~eligible] = 0
            if errors.max() <= MATCH_MM*.65:
                continue
            # Refit with the old anchors retained and a new anchor exactly at
            # the measured peak; curve validation remains the mesher's job.
            intervals = np.searchsorted(anchors, grid, side='right')-1
            peaks = [int(indices[np.argmax(errors[indices])]) for interval in range(len(anchors)-1)
                     if len(indices := np.flatnonzero(intervals == interval))]
            peaks = sorted((i for i in peaks if errors[i] > MATCH_MM*.65), key=lambda i: -errors[i])
            additions = grid[peaks[:min(8, 64-len(anchors))]].tolist()
            payload[field] = _fit_plane(wall[index], seeds(index*math.tau/ANGULAR), scale, check,
                                        anchor_z=[*anchors, *additions], explicit=True)
            changed = True
        if not changed:
            break
        if shared:
            payload['profile_v'] = payload['profile_h']
        else:
            payload['profile_v']['points'][0]['r'] = payload['profile_h']['points'][0]['r']
        candidate = DesignConfig.model_validate(payload)
        payload = candidate.model_dump(mode='json')
    # A near-vertical end can vary substantially inside the minimum permitted
    # anchor spacing. Minimize the generated error there, allowing a measured
    # mouth-radius change within the existing refusal budget.
    for field, index in planes:
        check()
        sampled = _meridians(generated)[index]
        grid, errors = _radial_difference(wall[index], sampled)
        if errors.max() <= MATCH_MM:
            continue
        baseline = float(errors.max())
        mouth = _number(getattr(candidate.root, field).points[-1].r)
        best = (baseline, candidate)
        def objective(delta):
            nonlocal best
            from hornlab_mesher import freeform_evaluate_profile
            from server.preview.freeform import _params
            check()
            trial_payload = candidate.model_dump(mode='json')
            trial_payload[field]['points'][-1]['r'] = mouth+float(delta)/scale
            if shared:
                trial_payload['profile_v'] = trial_payload['profile_h']
            trial = DesignConfig.model_validate(trial_payload)
            # Mouth radius changes leave the generated axial sample positions
            # unchanged. Query those exact nodes through the production owner,
            # then rebuild the full generated grid for the selected candidate.
            radii = freeform_evaluate_profile(_params(trial), 'H' if index == 0 else 'V', sampled[:, 0]/scale)*scale
            actual = np.column_stack((sampled[:, 0], radii))
            _, deviations = _radial_difference(wall[index], actual)
            maximum = max(float(deviations.max()), abs(actual[-1, 1]-wall[index][-1, 1]))
            if maximum < best[0]:
                best = maximum, trial
            return maximum
        minimize_scalar(objective, bounds=(-min(REFUSE_MM, mouth*scale*.5), REFUSE_MM),
                        method='bounded', options={'xatol': 1e-5, 'maxiter': 28})
        if best[1] is not candidate:
            candidate = best[1]
            generated = _sample(candidate)
    return candidate, generated


def _fit_stations(wall: Sequence[np.ndarray], length: float, scale: float, check: Callable) -> list[dict]:
    """Fit each selected ring with the mesher's own station-outline evaluator."""
    from hornlab_mesher.freeform import _station_radius, _resolve_active_station_blend
    phi = np.radians(AZIMUTHS)
    z = np.unique(np.r_[wall[0][:, 0], wall[ANGULAR//4][:, 0], np.linspace(0, length, 129)])
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
    if source.formula == 'FREEFORM':
        _refuse('This design is already a Custom profile.', 'Edit its existing profile anchors instead of converting it.')
    if source.formula == 'SOURCE-DISK':
        _refuse('A source body has no inner horn wall to convert to a Custom profile.',
                'Keep this source body, or choose a horn family before converting.')
    check()
    original = _sample(source)
    kept, lips = _kept_wall(_meridians(original))
    # Preserve physical placement for comparison; fitting starts at z = 0.
    wall = [rows.copy() for rows in kept]
    progress('sample')
    origins = np.array([rows[0, 0] for rows in wall])
    if np.max(abs(origins)) > REFUSE_MM and not importing:
        _refuse('The generated throat is more than 0.5 mm from z = 0; a Custom profile starts at zero.',
                'Shift the imported table so its first z is zero, then convert again.')
    for rows, origin in zip(wall, origins):
        rows[:, 0] -= origin
    length = float(wall[0][-1, 0])
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
        from hornlab_mesher.profile_sampling import _cross_section_scaled, _superellipse_scale
        exponent = source.root.cross_section.exponent
        aspect = source.root.cross_section.aspect_ratio
        shared = exponent == 2 and aspect == 1
        # The old table is the base radius, before cross-section growth. Derive
        # both cardinal meridians from that same table and the production rule;
        # equal H/V samples do not establish a circular section.
        def cardinal_rows(phi):
            return np.column_stack((rows[:, 0], _cross_section_scaled(
                rows[:, 1], rows[:, 0]/length, 0, _superellipse_scale(phi, exponent, aspect))))
        h_rows, v_rows = cardinal_rows(0), cardinal_rows(math.pi/2)
        h, h_error = _polyline_fit(h_rows, scale, check)
        v, v_error = (h, h_error) if shared else _polyline_fit(v_rows, scale, check)
        curve_error = max(h_error, v_error)
        # Freeze family-specific construction instead of baking the morph twice.
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
            half_width, half_height = _resolve_morph_half_dimensions(params, 0, h_rows[-1, 1], v_rows[-1, 1], round_implicit_up=not shape_only)
            for field, half in (('target_width', half_width), ('target_height', half_height)):
                value = getattr(source.root.morph, field)
                if value is None or value.constant_value() == 0:
                    common['morph'][field] = 2*half/scale
                    label = 'Implicit mouth dimensions' if shape_only else 'Implicit mouth rounding'
                    features.append(FeatureChange(name=label, change='baked',
                        message=f'{label} frozen as {field.replace("_", " ")} {2*half:.12g} mm.'))
        stations = [{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}]
        if not shared:
            # The generic section scales only growth past a circular throat.
            # Reuse S4's station fitter against that actual construction rather
            # than pretending a linear exponent ramp is the same outline.
            base = DesignConfig.model_construct(root=source.root.model_copy(update={
                'morph': source.root.morph.model_copy(update={'target_shape': Expr(value=0)})}))
            base_wall = _meridians(_legacy_grid(base)[0])
            base_wall[:, :, 0] -= base_wall[:, :1, 0]
            stations = _fit_stations(base_wall, length, scale, check)
            features.append(FeatureChange(name='Cross-section exponent' if exponent != 2 else 'Cross-section outline', change='baked',
                message='The cross-section outline and circular-throat growth rule are fitted into the cross-section stations; the outline fit is included in the generated-grid comparison.'))
        features.append(FeatureChange(name='Axial map / interpolate fit', change='changed',
            message='The ATH solve z-map / interpolate surface fit is replaced by the Custom profile corner-wall fit; the generated-grid comparison is sampled, not an OCC wall-distance certificate.'))
        candidate = DesignConfig.model_validate({**common, 'formula': 'FREEFORM', 'length': length/scale,
            'profile_h': h, 'profile_v': v, 'shared_profile': shared, 'cross_sections': stations, 'inflection_policy': 'warn'})
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
        if np.ptp([rows[0, 1] for rows in wall]) > 1e-8:
            features.append(FeatureChange(name='Throat outline', change='changed',
                message='The generated throat is fitted as one circle; its outline change is included in the wall and source-cap measurements.'))
        stations = ([{'t': 0, 'shape': 'ellipse'}, {'t': 1, 'shape': 'ellipse'}] if shared
                    else _fit_stations(wall, length, scale, check))
        candidate = DesignConfig.model_validate({**common, 'formula': 'FREEFORM', 'length': length/scale,
            'profile_h': h, 'profile_v': v, 'shared_profile': shared, 'cross_sections': stations, 'inflection_policy': 'warn'})
    progress('fit')
    check()
    generated = None
    if not lookup:
        candidate, generated = _refine_generated(candidate, wall, scale, seeds, check)
    report = compare_generated_designs(source, candidate, features=features, before_points=original,
                                      before_wall=kept, after_points=generated, align_disjoint=importing, include_construction=False)
    if lip := _lip_change(lips, _meridians(_sample(candidate) if generated is None else generated)[:, -1, :]):
        report.features.append(lip)
        report.method.grid = 'Kept wall through each meridian’s maximum z. '+report.method.grid
        report.method.limits.append('This conversion compares only the wall kept through each meridian’s maximum z. '
                                    'The removed lip is measured separately as a named feature, outside the wall refusal limit.')
        counts = [len(rows)-1 for rows in kept]
        report.method.limits.append(f'Kept axial segments per meridian: {min(counts)}–{max(counts)}; '
                                    'axial_segments_before counts the complete original sampling, including the removed lip.')
    maximum = max(report.wall.max_deviation_mm, report.mouth.max_deviation_mm, report.axial_extent_deviation_mm)
    if curve_error is not None:
        report.curve_deviation_mm = curve_error
    if maximum > REFUSE_MM and not importing:
        _refuse(f'The best Custom profile differs by {maximum:.4f} mm, above the 0.5 mm conversion limit (up to 64 anchors and 32 stations).',
                'Keep the original family, or simplify the kept profile/morph and try again.')
    report.features.extend(_construction_changes(source, candidate, removed_lip=lip is not None))
    progress('compare')
    same = maximum <= MATCH_MM and abs(report.source_cap_angle_before_deg-report.source_cap_angle_after_deg) <= .01 and not report.features
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
    removed_lip = any(feature.name == 'Rolled-back lip removed' for feature in report.features)
    fit = ('Kept wall fitted through the fold.' if removed_lip else
           'Profile curve unchanged.' if curve < 1e-9 else f'Maximum profile curve change {curve:.6g} mm.')
    limit = ' The fit exceeds the 0.01 mm profile tolerance.' if curve > MATCH_MM else ''
    refusal = ' This exceeds the 0.5 mm explicit-conversion limit; opened with the best fit.' if maximum > REFUSE_MM else ''
    scope = 'kept ' if removed_lip else ''
    return (f'Opened as Custom profile. {fit} Maximum {scope}generated-grid change {maximum:.6g} mm.'
            f'{limit}{refusal} ' + ' '.join(f.message for f in report.features))
