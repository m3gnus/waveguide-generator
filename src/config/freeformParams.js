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

function normalizedInteriorPoint(point) {
  const source = Array.isArray(point)
    ? { z: point[0], r: point[1], angleDeg: point[2], strength: point[3] }
    : point;
  if (!source || typeof source !== 'object') return null;
  const z = Number(source.z);
  const r = Number(source.r);
  if (!Number.isFinite(z) || !Number.isFinite(r)) return null;
  const angle = source.angleDeg;
  const angleDeg = angle === null || angle === undefined || angle === '' ? null : Number(angle);
  const rawStrength = source.strength;
  const strength =
    rawStrength === null || rawStrength === undefined || rawStrength === ''
      ? null
      : Number(rawStrength);
  return {
    z,
    r,
    angleDeg: Number.isFinite(angleDeg) ? angleDeg : null,
    strength: Number.isFinite(strength) && Number.isFinite(angleDeg) ? strength : null,
  };
}

function finiteProfilePoints(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((point) => Array.isArray(point) && point.length >= 2)
    .map((point) => ({ row: point, interior: normalizedInteriorPoint(point) }))
    .filter(({ interior }) => interior !== null);
}

function normalizeInteriorCollection(value, length) {
  if (!Array.isArray(value)) return value;
  const maximumZ = length - 1;
  const normalized = value
    .map(normalizedInteriorPoint)
    .filter(Boolean)
    .sort((left, right) => left.z - right.z);
  if (!Number.isFinite(maximumZ) || maximumZ < 1) return normalized;

  const clamped = [];
  for (const point of normalized) {
    const nextPoint = {
      ...point,
      z: Math.min(maximumZ, Math.max(1, point.z)),
    };
    if (clamped.at(-1)?.z === nextPoint.z) continue;
    clamped.push(nextPoint);
  }
  return clamped;
}

/**
 * Convert the first FREEFORM frontend model into the endpoint-plus-interior model.
 * The conversion is deliberately defensive because persisted browser state may
 * contain partially edited or otherwise malformed point collections.
 */
export function migrateLegacyFreeformParams(params = {}) {
  const source = params && typeof params === 'object' && !Array.isArray(params) ? params : {};
  const migrated = { ...source };
  const hasLength = Object.prototype.hasOwnProperty.call(source, 'length');
  const hasLegacyProfile =
    Object.prototype.hasOwnProperty.call(source, 'profileH') ||
    Object.prototype.hasOwnProperty.call(source, 'profileV');

  if (!hasLength && hasLegacyProfile) {
    const horizontal = finiteProfilePoints(source.profileH);
    const vertical = finiteProfilePoints(source.profileV);
    const horizontalProfile = horizontal.length >= 2 ? horizontal : [];
    const verticalProfile = vertical.length >= 2 ? vertical : [];
    const lengthPoint = horizontalProfile.at(-1) || verticalProfile.at(-1);
    const throatPoint = horizontal[0] || vertical[0];
    const horizontalMouth = horizontalProfile.at(-1) || verticalProfile.at(-1);
    const verticalMouth = verticalProfile.at(-1) || horizontalProfile.at(-1);

    migrated.length = positiveNumber(lengthPoint?.interior.z, 120);
    migrated.throatRadius = finiteNumber(throatPoint?.interior.r, 12.7);
    migrated.throatAngle = finiteNumber(
      source.throatAngle,
      finiteNumber(source.throatAngleH, finiteNumber(source.throatAngleV, 15.5))
    );
    migrated.mouthRadiusH = finiteNumber(horizontalMouth?.interior.r, 140);
    migrated.mouthRadiusV = finiteNumber(verticalMouth?.interior.r, migrated.mouthRadiusH);
    migrated.interiorH = horizontalProfile.slice(1, -1).map(({ interior }) => interior);
    migrated.interiorV = verticalProfile.slice(1, -1).map(({ interior }) => interior);
  }

  const normalizedLength = positiveNumber(migrated.length, 120);
  for (const key of ['interiorH', 'interiorV']) {
    if (Object.prototype.hasOwnProperty.call(migrated, key)) {
      migrated[key] = normalizeInteriorCollection(migrated[key], normalizedLength);
    }
  }

  for (const key of LEGACY_FREEFORM_KEYS) {
    delete migrated[key];
  }
  return migrated;
}
