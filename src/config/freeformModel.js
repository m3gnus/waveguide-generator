import { buildFreeformDisplayCurve } from '../geometry/freeformCurve.js';

export const MAX_INTERIOR_ANCHORS = 62;
const MAX_CROSS_SECTION_STATIONS = 32;
const VALID_STATION_SHAPES = new Set(['ellipse', 'superellipse', 'rounded_rectangle']);
const LEGACY_FREEFORM_KEYS = Object.freeze([
  'profileH',
  'profileV',
  'throatAngleH',
  'throatAngleV',
]);

function finiteNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function positiveNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback;
}

function optionalFiniteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function normalizeAnchor(anchor) {
  const source = Array.isArray(anchor)
    ? { z: anchor[0], r: anchor[1], angleDeg: anchor[2], strength: anchor[3] }
    : anchor;
  if (!source || typeof source !== 'object') return null;

  const z = Number(source.z);
  const r = Number(source.r);
  if (!Number.isFinite(z) || !Number.isFinite(r)) return null;

  const angleDeg = optionalFiniteNumber(source.angleDeg ?? source.angle_deg);
  const strength = optionalFiniteNumber(source.strength);
  return {
    z,
    r,
    angleDeg,
    strength: angleDeg === null ? null : strength,
  };
}

/**
 * Canonical interior-anchor policy: accept object or row input, discard malformed
 * rows, sort by z, clamp z into [1, length - 1], deduplicate clamped z values,
 * and retain at most maxAnchors (62 by default). The first row at a duplicated z wins.
 */
export function normalizeAnchorList(
  list,
  { length = 120, maxAnchors = MAX_INTERIOR_ANCHORS } = {}
) {
  if (!Array.isArray(list)) return [];
  const normalizedLength = positiveNumber(length, 120);
  const maximumZ = Math.max(1, normalizedLength - 1);
  const limit = Math.max(0, Math.floor(finiteNumber(maxAnchors, MAX_INTERIOR_ANCHORS)));
  if (limit === 0) return [];
  const anchors = list
    .map(normalizeAnchor)
    .filter(Boolean)
    .sort((left, right) => left.z - right.z);
  const result = [];
  for (const anchor of anchors) {
    const clamped = { ...anchor, z: Math.min(maximumZ, Math.max(1, anchor.z)) };
    if (result.at(-1)?.z === clamped.z) continue;
    result.push(clamped);
    if (result.length === limit) break;
  }
  return result;
}

function interpolateCurveRadius(curve, z) {
  if (!Array.isArray(curve) || curve.length === 0 || !Number.isFinite(z)) return null;
  for (let index = 0; index < curve.length - 1; index += 1) {
    const [z0, r0] = curve[index] || [];
    const [z1, r1] = curve[index + 1] || [];
    if (![z0, r0, z1, r1].every(Number.isFinite)) continue;
    if (z < Math.min(z0, z1) || z > Math.max(z0, z1)) continue;
    if (Math.abs(z1 - z0) <= Number.EPSILON) return r0;
    return r0 + ((z - z0) / (z1 - z0)) * (r1 - r0);
  }
  return null;
}

function cornerMigrationDimensions(params, t) {
  const length = positiveNumber(params?.length, 120);
  const throatRadius = positiveNumber(params?.throatRadius, 12.7);
  const radiusAt = (plane) => {
    try {
      const interior = normalizeAnchorList(params?.[`interior${plane}`], { length });
      const points = [
        [0, throatRadius],
        ...interior.map((anchor) => {
          const row = [anchor.z, anchor.r];
          if (anchor.angleDeg !== null) row.push(anchor.angleDeg);
          if (anchor.strength !== null) row.push(anchor.strength);
          return row;
        }),
        [length, positiveNumber(params?.[`mouthRadius${plane}`], throatRadius)],
      ];
      return interpolateCurveRadius(
        buildFreeformDisplayCurve({
          points,
          throatAngleDeg: finiteNumber(params?.throatAngle, 15.5),
          mouthAngleDeg: finiteNumber(params?.[`mouthAngle${plane}`], 60),
          throatTangentScale: finiteNumber(params?.[`throatTangentScale${plane}`], 1),
          mouthTangentScale: finiteNumber(params?.[`mouthTangentScale${plane}`], 1),
          sampleCount: 256,
        }),
        t * length
      );
    } catch {
      return null;
    }
  };

  const fallback = (plane) => {
    const mouthRadius = positiveNumber(params?.[`mouthRadius${plane}`], throatRadius);
    return throatRadius + t * (mouthRadius - throatRadius);
  };
  return [radiusAt('H') ?? fallback('H'), radiusAt('V') ?? fallback('V')];
}

function normalizeStation(station, params) {
  if (!station || typeof station !== 'object' || Array.isArray(station)) return null;
  const t = Number(station.t);
  if (!Number.isFinite(t)) return null;
  const normalized = { t: Math.min(1, Math.max(0, t)), shape: String(station.shape || 'ellipse') };
  const exponent = optionalFiniteNumber(station.exponent);
  const cornerRatio = optionalFiniteNumber(station.cornerRatio ?? station.corner_ratio);
  const cornerRadiusMm = optionalFiniteNumber(station.cornerRadiusMm ?? station.corner_radius_mm);
  if (exponent !== null) normalized.exponent = exponent;
  if (cornerRadiusMm !== null) {
    normalized.cornerRadiusMm = cornerRadiusMm;
  } else if (cornerRatio !== null) {
    const [radiusH, radiusV] = cornerMigrationDimensions(params, normalized.t);
    normalized.cornerRadiusMm = Math.round(cornerRatio * Math.min(radiusH, radiusV) * 10) / 10;
  }
  return normalized;
}

export function normalizeStations(value, { params = {} } = {}) {
  if (!Array.isArray(value) || value.length < 2) {
    return [
      { t: 0, shape: 'circle' },
      { t: 1, shape: 'ellipse' },
    ];
  }

  const sorted = value
    .map((station) => normalizeStation(station, params))
    .filter(Boolean)
    .sort((a, b) => a.t - b.t);
  if (sorted.length < 2) {
    return [
      { t: 0, shape: 'circle' },
      { t: 1, shape: 'ellipse' },
    ];
  }

  const deduped = [];
  for (const station of sorted) {
    if (deduped.at(-1)?.t === station.t) continue;
    deduped.push(station);
  }
  let stations = deduped;
  if (stations.length > MAX_CROSS_SECTION_STATIONS) {
    stations = [...stations.slice(0, MAX_CROSS_SECTION_STATIONS - 1), stations.at(-1)];
  }
  if (stations.length < 2) stations.push({ t: 1, shape: 'ellipse' });

  stations[0] = { t: 0, shape: 'circle' };
  for (let index = 1; index < stations.length; index += 1) {
    if (!VALID_STATION_SHAPES.has(stations[index].shape)) {
      stations[index] = { t: stations[index].t, shape: 'ellipse' };
    }
  }
  stations[stations.length - 1] = { ...stations.at(-1), t: 1 };
  return stations;
}

function finiteProfileRows(value) {
  if (!Array.isArray(value)) return [];
  return value.map(normalizeAnchor).filter(Boolean);
}

export function normalizeFreeformParams(params = {}) {
  const source = params && typeof params === 'object' && !Array.isArray(params) ? params : {};
  const normalized = { ...source };
  const legacyH = finiteProfileRows(source.profileH);
  const legacyV = finiteProfileRows(source.profileV);
  const legacyLength = legacyH.at(-1)?.z ?? legacyV.at(-1)?.z;
  const hasLegacyProfile = Object.hasOwn(source, 'profileH') || Object.hasOwn(source, 'profileV');

  if (hasLegacyProfile) {
    const profileH = legacyH.length >= 2 ? legacyH : legacyV.length >= 2 ? legacyV : [];
    const profileV = legacyV.length >= 2 ? legacyV : legacyH.length >= 2 ? legacyH : [];
    normalized.length = positiveNumber(source.length, positiveNumber(legacyLength, 120));
    normalized.throatRadius = finiteNumber(
      source.throatRadius,
      finiteNumber(profileH[0]?.r ?? profileV[0]?.r, 12.7)
    );
    normalized.throatAngle = finiteNumber(
      source.throatAngle,
      finiteNumber(source.throatAngleH, finiteNumber(source.throatAngleV, 15.5))
    );
    normalized.mouthRadiusH = finiteNumber(
      source.mouthRadiusH,
      finiteNumber(profileH.at(-1)?.r, 140)
    );
    normalized.mouthRadiusV = finiteNumber(
      source.mouthRadiusV,
      finiteNumber(profileV.at(-1)?.r, normalized.mouthRadiusH)
    );
    if (!Object.hasOwn(source, 'interiorH')) normalized.interiorH = profileH.slice(1, -1);
    if (!Object.hasOwn(source, 'interiorV')) normalized.interiorV = profileV.slice(1, -1);
  }

  const length = positiveNumber(normalized.length, 120);
  if (Object.hasOwn(normalized, 'interiorH')) {
    normalized.interiorH = normalizeAnchorList(normalized.interiorH, { length });
  }
  if (Object.hasOwn(normalized, 'interiorV')) {
    normalized.interiorV = normalizeAnchorList(normalized.interiorV, { length });
  }
  if (Object.hasOwn(normalized, 'crossSections')) {
    normalized.crossSections = normalizeStations(normalized.crossSections, { params: normalized });
  }
  if (String(normalized.inflectionPolicy).trim().toLowerCase() === 'allow') {
    normalized.inflectionPolicy = 'warn';
  }

  for (const key of LEGACY_FREEFORM_KEYS) delete normalized[key];
  return normalized;
}

function compactAnchor(anchor) {
  const row = [anchor.z, anchor.r];
  if (anchor.angleDeg === null) return row;
  row.push(anchor.angleDeg);
  if (anchor.strength !== null) row.push(anchor.strength);
  return row;
}

function wireStation(station) {
  const result = { t: station.t, shape: station.shape };
  if (station.exponent !== undefined) result.exponent = station.exponent;
  if (station.cornerRadiusMm !== undefined) result.corner_radius_mm = station.cornerRadiusMm;
  return result;
}

export function toWireFreeform(params = {}) {
  const normalized = normalizeFreeformParams(params);
  const length = positiveNumber(normalized.length, 120);
  const throatRadius = finiteNumber(normalized.throatRadius, 12.7);
  const throatAngle = finiteNumber(normalized.throatAngle, 15.5);
  const profile = (plane) => ({
    points: [
      [0, throatRadius],
      ...normalizeAnchorList(normalized[`interior${plane}`], { length }).map(compactAnchor),
      [length, finiteNumber(normalized[`mouthRadius${plane}`], 140)],
    ],
    throat_angle_deg: throatAngle,
    mouth_angle_deg: finiteNumber(normalized[`mouthAngle${plane}`], 60),
    throat_tangent_scale: finiteNumber(normalized[`throatTangentScale${plane}`], 1),
    mouth_tangent_scale: finiteNumber(normalized[`mouthTangentScale${plane}`], 1),
  });

  return {
    profile_h: profile('H'),
    profile_v: profile('V'),
    cross_sections: normalizeStations(normalized.crossSections).map(wireStation),
    overshoot_policy: String(normalized.overshootPolicy ?? 'reject'),
    inflection_policy: String(normalized.inflectionPolicy ?? 'warn'),
  };
}

function requireWireProfile(wire, key) {
  const profile = wire?.[key];
  if (!profile || typeof profile !== 'object' || !Array.isArray(profile.points)) {
    throw new Error(`FREEFORM wire model requires ${key}.points.`);
  }
  const points = profile.points.map((row, index) => {
    const point = normalizeAnchor(row);
    if (!point) throw new Error(`FREEFORM ${key}.points row ${index + 1} must be finite.`);
    return point;
  });
  if (points.length < 2) {
    throw new Error(`FREEFORM wire model requires at least two finite ${key}.points rows.`);
  }
  for (let index = 1; index < points.length; index += 1) {
    if (!(points[index].z > points[index - 1].z)) {
      throw new Error(
        `FREEFORM ${key}.points row ${index + 1} z must be strictly greater than the previous row.`
      );
    }
  }
  if (points.length - 2 > MAX_INTERIOR_ANCHORS) {
    throw new Error(
      `FREEFORM ${key}.points row ${MAX_INTERIOR_ANCHORS + 2} exceeds the ${MAX_INTERIOR_ANCHORS}-anchor interior budget.`
    );
  }
  return { profile, points };
}

export function fromWireFreeform(wire = {}) {
  const horizontal = requireWireProfile(wire, 'profile_h');
  const vertical = requireWireProfile(wire, 'profile_v');
  const length = horizontal.points.at(-1).z;
  const throatRadius = horizontal.points[0].r;
  if (!(length > 0)) throw new Error('FREEFORM wire model requires a positive length.');
  if (
    Math.abs(vertical.points.at(-1).z - length) > 1e-9 ||
    Math.abs(vertical.points[0].r - throatRadius) > 1e-9
  ) {
    throw new Error('FREEFORM wire profiles must share throat radius and length.');
  }

  return normalizeFreeformParams({
    length,
    throatRadius,
    throatAngle: finiteNumber(horizontal.profile.throat_angle_deg, 15.5),
    mouthRadiusH: horizontal.points.at(-1).r,
    mouthAngleH: finiteNumber(horizontal.profile.mouth_angle_deg, 60),
    interiorH: horizontal.points.slice(1, -1),
    throatTangentScaleH: finiteNumber(horizontal.profile.throat_tangent_scale, 1),
    mouthTangentScaleH: finiteNumber(horizontal.profile.mouth_tangent_scale, 1),
    mouthRadiusV: vertical.points.at(-1).r,
    mouthAngleV: finiteNumber(vertical.profile.mouth_angle_deg, 60),
    interiorV: vertical.points.slice(1, -1),
    throatTangentScaleV: finiteNumber(vertical.profile.throat_tangent_scale, 1),
    mouthTangentScaleV: finiteNumber(vertical.profile.mouth_tangent_scale, 1),
    crossSections: wire.cross_sections,
    overshootPolicy: String(wire.overshoot_policy ?? 'reject'),
    inflectionPolicy: String(wire.inflection_policy ?? 'warn'),
  });
}
