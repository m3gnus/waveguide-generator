import { getDefaults } from '../../config/defaults.js';
import { PARAM_SCHEMA } from '../../config/schema.js';
import { prepareBackendViewportMesh } from '../geometry/useCases.js';
import { normalizeAnchorList, normalizeFreeformParams } from '../../config/freeformModel.js';
import { getViewportStateCacheKey } from '../geometry/viewportCacheKey.js';
import { buildFreeformDisplayCurve } from './freeformCurve.js';

const DEFAULT_TOLERANCE_MM = 0.15;
const DEFAULT_MAX_INTERIOR_ANCHORS = 62;
const FREEFORM_PARAM_GROUPS = ['FREEFORM', 'MESH', 'SOURCE', 'SIMULATION', 'ENCLOSURE'];
const DROPPED_PARAM_GROUPS = ['GEOMETRY'];

function finiteNumber(value, label) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    throw new Error(`Cannot convert design: ${label} must be a finite number.`);
  }
  return numeric;
}

function pointSegmentDistance(point, start, end) {
  const dz = end.z - start.z;
  const dr = end.r - start.r;
  const lengthSquared = dz * dz + dr * dr;
  if (lengthSquared <= 1e-18) {
    return Math.hypot(point.z - start.z, point.r - start.r);
  }
  const projection = Math.max(
    0,
    Math.min(1, ((point.z - start.z) * dz + (point.r - start.r) * dr) / lengthSquared)
  );
  return Math.hypot(point.z - (start.z + projection * dz), point.r - (start.r + projection * dr));
}

function farthestPoint(points, startIndex, endIndex) {
  let index = -1;
  let distance = -1;
  for (let i = startIndex + 1; i < endIndex; i += 1) {
    const candidate = pointSegmentDistance(points[i], points[startIndex], points[endIndex]);
    if (candidate > distance) {
      distance = candidate;
      index = i;
    }
  }
  return { index, distance };
}

/**
 * Douglas-Peucker decimation using normal distance, with a strict interior
 * anchor budget. Splitting the currently worst segment first is equivalent to
 * the recursive algorithm while making the cap deterministic.
 */
export function decimateMeridian(points, toleranceMm, maxInteriorAnchors) {
  if (points.length <= 2) return points.slice();
  const kept = new Set([0, points.length - 1]);
  const segments = [
    {
      start: 0,
      end: points.length - 1,
      ...farthestPoint(points, 0, points.length - 1),
    },
  ];

  while (segments.length > 0 && kept.size - 2 < maxInteriorAnchors) {
    segments.sort((left, right) => right.distance - left.distance);
    const segment = segments.shift();
    if (!segment || segment.index < 0 || segment.distance <= toleranceMm) break;
    kept.add(segment.index);
    if (segment.index - segment.start > 1) {
      segments.push({
        start: segment.start,
        end: segment.index,
        ...farthestPoint(points, segment.start, segment.index),
      });
    }
    if (segment.end - segment.index > 1) {
      segments.push({
        start: segment.index,
        end: segment.end,
        ...farthestPoint(points, segment.index, segment.end),
      });
    }
  }

  return [...kept].sort((left, right) => left - right).map((index) => points[index]);
}

function maximumCurveDeviation(dense, curve) {
  let maximum = 0;
  for (const point of dense) {
    let minimum = Infinity;
    for (let segment = 0; segment < curve.length - 1; segment += 1) {
      minimum = Math.min(
        minimum,
        pointSegmentDistance(
          point,
          { z: curve[segment][0], r: curve[segment][1] },
          { z: curve[segment + 1][0], r: curve[segment + 1][1] }
        )
      );
    }
    maximum = Math.max(maximum, minimum);
  }
  return maximum;
}

function displayCurveForPlane(params, plane, denseCount) {
  const points = [
    [0, params.throatRadius],
    ...params[`interior${plane}`].map((point) => {
      const row = [point.z, point.r];
      if (point.angleDeg !== null && point.angleDeg !== undefined) row.push(point.angleDeg);
      if (point.strength !== null && point.strength !== undefined) row.push(point.strength);
      return row;
    }),
    [params.length, params[`mouthRadius${plane}`]],
  ];
  return buildFreeformDisplayCurve({
    points,
    throatAngleDeg: params.throatAngle,
    mouthAngleDeg: params[`mouthAngle${plane}`],
    throatTangentScale: params[`throatTangentScale${plane}`],
    mouthTangentScale: params[`mouthTangentScale${plane}`],
    sampleCount: Math.max(192, denseCount * 4),
  });
}

function isMonotoneByZ(points) {
  for (let i = 1; i < points.length; i += 1) {
    if (points[i].z < points[i - 1].z - 1e-9) return false;
  }
  return true;
}

function truncateRollback(points) {
  if (isMonotoneByZ(points)) return { points, truncatedMm: 0 };
  let maximumIndex = 0;
  for (let i = 1; i < points.length; i += 1) {
    if (points[i].z > points[maximumIndex].z) maximumIndex = i;
  }
  const lastZ = points.at(-1).z;
  return {
    points: points.slice(0, maximumIndex + 1),
    // The rollback lip's final axial position is the useful number to show to
    // a designer (and is how the audit characterized the default R-OSSE lip).
    truncatedMm: Math.max(0, lastZ),
  };
}

function interpolateRadiusAtZ(points, z) {
  if (z <= points[0].z) return points[0].r;
  for (let i = 1; i < points.length; i += 1) {
    if (z <= points[i].z + 1e-9) {
      const previous = points[i - 1];
      const current = points[i];
      const span = current.z - previous.z;
      if (Math.abs(span) <= 1e-12) return current.r;
      const t = Math.max(0, Math.min(1, (z - previous.z) / span));
      return previous.r + (current.r - previous.r) * t;
    }
  }
  return points.at(-1).r;
}

function cropAtZ(points, z) {
  const cropped = points.filter((point) => point.z < z - 1e-9);
  cropped.push({ z, r: interpolateRadiusAtZ(points, z) });
  return cropped;
}

function slopeAngleDeg(points, atMouth = false) {
  if (points.length < 2) return 0;
  const start = atMouth ? points.at(-2) : points[0];
  const end = atMouth ? points.at(-1) : points[1];
  const angle = (Math.atan2(end.r - start.r, end.z - start.z) * 180) / Math.PI;
  return Math.max(-90, Math.min(90, angle));
}

function hasValidGridShape(grid) {
  const nPhi = Math.round(finiteNumber(grid.grid_n_phi, 'grid_n_phi'));
  const nLength = Math.round(finiteNumber(grid.grid_n_length, 'grid_n_length'));
  const points = grid.inner_points;
  const expectedFlatLength = nPhi * (nLength + 1) * 3;
  const nested = Array.isArray(points?.[0]);
  return (
    nPhi > 0 &&
    nLength >= 1 &&
    Array.isArray(grid.angle_list) &&
    grid.angle_list.length === nPhi &&
    Array.isArray(points) &&
    (nested
      ? points.length === nPhi &&
        points.every(
          (row) =>
            Array.isArray(row) &&
            row.length === nLength + 1 &&
            row.every((point) => Array.isArray(point) && point.length === 3)
        )
      : points.length === expectedFlatLength)
  );
}

function isReusableViewportMesh(viewportMesh, sourceState) {
  if (!viewportMesh || typeof viewportMesh !== 'object' || !viewportMesh.preparedParams)
    return false;
  const grid = viewportMesh.grid;
  if (!grid || typeof grid !== 'object') return false;
  try {
    if (!hasValidGridShape(grid)) return false;
  } catch {
    return false;
  }
  if (grid.full_circle !== true || Number(grid.quadrants) !== 1234) return false;
  return viewportMesh.stateKey === getViewportStateCacheKey(sourceState);
}

function reshapeMeridian(grid, rowIndex, scale, verticalOffsetMm) {
  const nPhi = Math.round(finiteNumber(grid.grid_n_phi, 'grid_n_phi'));
  const nLength = Math.round(finiteNumber(grid.grid_n_length, 'grid_n_length'));
  if (rowIndex < 0 || rowIndex >= nPhi || nLength < 1 || !hasValidGridShape(grid)) {
    throw new Error(
      'Cannot convert design: viewport grid dimensions or inner_points shape are invalid.'
    );
  }
  const points = grid.inner_points;
  const nested = Array.isArray(points?.[0]);

  const row = [];
  for (let j = 0; j <= nLength; j += 1) {
    const point = nested ? points[rowIndex]?.[j] : null;
    const base = (rowIndex * (nLength + 1) + j) * 3;
    const x = finiteNumber(nested ? point?.[0] : points[base], 'grid x') / scale;
    const y =
      (finiteNumber(nested ? point?.[1] : points[base + 1], 'grid y') - verticalOffsetMm) / scale;
    const z = finiteNumber(nested ? point?.[2] : points[base + 2], 'grid z') / scale;
    row.push({ z, r: Math.hypot(x, y) });
  }
  return row;
}

function seedCrossSections(sourceParams, denseH, denseV) {
  const mouthRadiusH = denseH.at(-1).r;
  const mouthRadiusV = denseV.at(-1).r;
  const target = Math.round(Number(sourceParams.morphTarget) || 0);
  const throat = { t: 0, shape: 'circle' };
  if (target === 1) {
    // The mesher floors rounded-rect corners at 2% of the local half-dimension
    // (sharp creases are a deferred feature), so a source design with
    // morphCorner 0 must seed a valid minimum radius, not 0.
    const minCorner = Math.max(1, 0.02 * Math.min(mouthRadiusH, mouthRadiusV));
    const minimumActiveRadius = Math.min(
      ...denseH.map((point) => point.r),
      ...denseV.map((point) => point.r)
    );
    const corner = Math.max(
      minCorner,
      Math.min(
        Number.isFinite(Number(sourceParams.morphCorner)) ? Number(sourceParams.morphCorner) : 0,
        mouthRadiusH,
        mouthRadiusV,
        minimumActiveRadius
      )
    );
    return [throat, { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: corner }];
  }
  return [throat, { t: 1, shape: 'ellipse' }];
}

function preserveSharedParams(sourceParams) {
  const preserved = {};
  for (const group of FREEFORM_PARAM_GROUPS) {
    for (const key of Object.keys(PARAM_SCHEMA[group] || {})) {
      if (Object.hasOwn(sourceParams, key)) preserved[key] = sourceParams[key];
    }
  }
  return preserved;
}

function equivalentParamValues(left, right) {
  if (Object.is(left, right)) return true;
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  return Number.isFinite(leftNumber) && Number.isFinite(rightNumber) && leftNumber === rightNumber;
}

function droppedSourceParams(sourceParams) {
  const defaults = getDefaults('FREEFORM');
  const dropped = [];
  for (const group of DROPPED_PARAM_GROUPS) {
    for (const key of Object.keys(PARAM_SCHEMA[group] || {})) {
      if (
        Object.hasOwn(sourceParams, key) &&
        !equivalentParamValues(sourceParams[key], defaults[key])
      ) {
        dropped.push(key);
      }
    }
  }
  return dropped;
}

/**
 * Convert the current parametric family into an editable FREEFORM design by
 * sampling the canonical full-domain backend viewport grid.
 */
export async function convertToFreeform(
  state,
  {
    viewportMesh = null,
    prepareViewportMesh = prepareBackendViewportMesh,
    validateCandidate = true,
    toleranceMm = DEFAULT_TOLERANCE_MM,
    maxInteriorAnchors = DEFAULT_MAX_INTERIOR_ANCHORS,
  } = {}
) {
  if (!state || typeof state !== 'object' || !state.params) {
    throw new Error('Cannot convert design: current state is missing.');
  }
  if (state.type === 'FREEFORM') {
    throw new Error('Cannot convert design: the current design is already FREEFORM.');
  }

  const sourceState = {
    ...state,
    params: { ...state.params, quadrants: 1234 },
  };
  const sampled = isReusableViewportMesh(viewportMesh, sourceState)
    ? viewportMesh
    : await prepareViewportMesh(sourceState, { variant: 'smooth' });
  const grid = sampled?.grid;
  const angles = grid?.angle_list;
  if (!grid || !Array.isArray(angles)) {
    throw new Error('Cannot convert design: viewport response is missing its angle grid.');
  }
  const horizontalIndex = angles.indexOf(0);
  const verticalIndex = angles.indexOf(Math.PI / 2);
  if (horizontalIndex < 0 || verticalIndex < 0) {
    throw new Error('Cannot convert design: viewport grid lacks exact 0 and pi/2 meridians.');
  }

  const scaleValue = Number(sampled.preparedParams?.scale);
  const scale = Number.isFinite(scaleValue) && scaleValue > 0 ? scaleValue : 1;
  const gridOffset = Number(grid.vertical_offset_mm);
  const preparedOffset = Number(sampled.preparedParams?.verticalOffset);
  const verticalOffsetMm = Number.isFinite(gridOffset)
    ? gridOffset
    : Number.isFinite(preparedOffset)
      ? preparedOffset
      : 0;
  const horizontalRollback = truncateRollback(
    reshapeMeridian(grid, horizontalIndex, scale, verticalOffsetMm)
  );
  const verticalRollback = truncateRollback(
    reshapeMeridian(grid, verticalIndex, scale, verticalOffsetMm)
  );
  const length = Math.min(horizontalRollback.points.at(-1).z, verticalRollback.points.at(-1).z);
  if (!Number.isFinite(length) || length <= 0) {
    throw new Error('Cannot convert design: sampled meridians have no positive shared length.');
  }

  const denseH = cropAtZ(horizontalRollback.points, length);
  const denseV = cropAtZ(verticalRollback.points, length);
  const anchorBudget = Math.max(0, Math.min(62, Math.floor(Number(maxInteriorAnchors) || 0)));
  const tolerance = Math.max(0, Number(toleranceMm) || 0);
  const anchorsH = decimateMeridian(denseH, tolerance, anchorBudget);
  const anchorsV = decimateMeridian(denseV, tolerance, anchorBudget);
  const mouthRadiusH = denseH.at(-1).r;
  const mouthRadiusV = denseV.at(-1).r;

  const params = normalizeFreeformParams({
    ...getDefaults('FREEFORM'),
    ...preserveSharedParams(state.params),
    scale: Number.isFinite(Number(state.params.scale)) ? Number(state.params.scale) : scale,
    length,
    throatRadius: denseH[0].r,
    throatAngle: slopeAngleDeg(denseH),
    mouthRadiusH,
    mouthAngleH: slopeAngleDeg(denseH, true),
    interiorH: normalizeAnchorList(anchorsH.slice(1, -1), { length }),
    mouthRadiusV,
    mouthAngleV: slopeAngleDeg(denseV, true),
    interiorV: normalizeAnchorList(anchorsV.slice(1, -1), { length }),
    crossSections: seedCrossSections(state.params, denseH, denseV),
    morphTarget: 0,
  });

  if (validateCandidate) {
    const validate =
      typeof validateCandidate === 'function' ? validateCandidate : prepareViewportMesh;
    await validate({ type: 'FREEFORM', params }, { variant: 'smooth' });
  }

  const displayCurveH = displayCurveForPlane(params, 'H', denseH.length);
  const displayCurveV = displayCurveForPlane(params, 'V', denseV.length);

  return {
    params,
    report: {
      maxDeviationMmH: maximumCurveDeviation(denseH, displayCurveH),
      maxDeviationMmV: maximumCurveDeviation(denseV, displayCurveV),
      truncatedMm: Math.max(horizontalRollback.truncatedMm, verticalRollback.truncatedMm),
      anchorCountH: params.interiorH.length,
      anchorCountV: params.interiorV.length,
      droppedParams: droppedSourceParams(state.params),
      offAxisMayDiffer: Math.round(Number(state.params.morphTarget) || 0) === 1,
    },
  };
}

export function formatConversionReport(report = {}) {
  const h = Number(report.maxDeviationMmH || 0).toFixed(2);
  const v = Number(report.maxDeviationMmV || 0).toFixed(2);
  const rollback = Number(report.truncatedMm || 0);
  const suffix = rollback > 0 ? `; rollback lip ${rollback.toFixed(1)} mm dropped` : '';
  const dropped = Array.isArray(report.droppedParams) ? report.droppedParams : [];
  const droppedSuffix = dropped.length > 0 ? `; dropped ${dropped.join(', ')}` : '';
  const offAxisSuffix = report.offAxisMayDiffer
    ? '; H/V meridians only; off-axis cross-sections may differ'
    : '';
  return `max deviation H ${h} mm, V ${v} mm${suffix}${droppedSuffix}${offAxisSuffix}`;
}
