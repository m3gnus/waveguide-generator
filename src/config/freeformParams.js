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

function finitePoints(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((point) => Array.isArray(point) && point.length >= 2)
    .map((point) => [Number(point[0]), Number(point[1])])
    .filter((point) => point.every(Number.isFinite));
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
    const horizontal = finitePoints(source.profileH);
    const vertical = finitePoints(source.profileV);
    const horizontalProfile = horizontal.length >= 2 ? horizontal : [];
    const verticalProfile = vertical.length >= 2 ? vertical : [];
    const lengthPoint = horizontalProfile.at(-1) || verticalProfile.at(-1);
    const throatPoint = horizontal[0] || vertical[0];
    const horizontalMouth = horizontalProfile.at(-1) || verticalProfile.at(-1);
    const verticalMouth = verticalProfile.at(-1) || horizontalProfile.at(-1);

    migrated.length = positiveNumber(lengthPoint?.[0], 120);
    migrated.throatRadius = finiteNumber(throatPoint?.[1], 12.7);
    migrated.throatAngle = finiteNumber(
      source.throatAngle,
      finiteNumber(source.throatAngleH, finiteNumber(source.throatAngleV, 15.5))
    );
    migrated.mouthRadiusH = finiteNumber(horizontalMouth?.[1], 140);
    migrated.mouthRadiusV = finiteNumber(verticalMouth?.[1], migrated.mouthRadiusH);
    migrated.interiorH = horizontalProfile.slice(1, -1);
    migrated.interiorV = verticalProfile.slice(1, -1);
  }

  for (const key of LEGACY_FREEFORM_KEYS) {
    delete migrated[key];
  }
  return migrated;
}
