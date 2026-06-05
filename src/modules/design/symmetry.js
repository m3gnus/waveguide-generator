const FULL_QUADRANTS = 1234;
const HALF_TOP_QUADRANTS = 12;
const HALF_RIGHT_QUADRANTS = 14;
const QUARTER_Q1_QUADRANTS = 1;

const VALID_QUADRANTS = new Set([
  FULL_QUADRANTS,
  HALF_TOP_QUADRANTS,
  HALF_RIGHT_QUADRANTS,
  QUARTER_Q1_QUADRANTS,
]);

const ANGULAR_SYMMETRY_KEYS = Object.freeze([
  'R',
  'a',
  'a0',
  'r0',
  'k',
  'm',
  'b',
  'r',
  'q',
  'tmax',
  'L',
  's',
  'n',
  'h',
  'throatExtAngle',
  'throatExtLength',
  'slotLength',
  'rot',
  'gcurveDist',
  'gcurveWidth',
  'gcurveAspectRatio',
  'gcurveSeN',
  'circArcTermAngle',
  'circArcRadius',
]);

const SAMPLE_COUNT = 64;
const ABS_TOLERANCE = 1e-6;
const REL_TOLERANCE = 1e-6;

function asFiniteNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function nearlyEqual(a, b) {
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  const scale = Math.max(1, Math.abs(a), Math.abs(b));
  return Math.abs(a - b) <= ABS_TOLERANCE + REL_TOLERANCE * scale;
}

function normalizeAngle(p) {
  const tau = Math.PI * 2;
  const wrapped = p % tau;
  return wrapped < 0 ? wrapped + tau : wrapped;
}

function evaluateParamValue(value, p) {
  if (value === undefined || value === null || value === '') return 0;
  if (typeof value === 'function') {
    const evaluated = Number(value(normalizeAngle(p)));
    return Number.isFinite(evaluated) ? evaluated : null;
  }
  return asFiniteNumber(value);
}

function valueKeepsMirror(value, mirrorFn) {
  if (typeof value !== 'function') {
    return evaluateParamValue(value, 0) !== null;
  }

  for (let i = 0; i < SAMPLE_COUNT; i += 1) {
    const p = (Math.PI * 2 * i) / SAMPLE_COUNT;
    const a = evaluateParamValue(value, p);
    const b = evaluateParamValue(value, mirrorFn(p));
    if (a === null || b === null || !nearlyEqual(a, b)) {
      return false;
    }
  }
  return true;
}

function splitResolutionList(value) {
  if (value === undefined || value === null || value === '') return null;
  const parts = String(value)
    .split(',')
    .map((part) => Number(part.trim()));
  return parts.length === 4 && parts.every(Number.isFinite) ? parts : null;
}

function resolutionListKeepsX(value) {
  const parts = splitResolutionList(value);
  if (!parts) return true;
  return nearlyEqual(parts[0], parts[1]) && nearlyEqual(parts[3], parts[2]);
}

function resolutionListKeepsZ(value) {
  const parts = splitResolutionList(value);
  if (!parts) return true;
  return nearlyEqual(parts[0], parts[3]) && nearlyEqual(parts[1], parts[2]);
}

function enclosureSymmetry(params) {
  const hasEnclosure = asFiniteNumber(params.encDepth) > 0;
  if (!hasEnclosure) return { x: true, z: true };

  const left = asFiniteNumber(params.encSpaceL);
  const right = asFiniteNumber(params.encSpaceR);
  const top = asFiniteNumber(params.encSpaceT);
  const bottom = asFiniteNumber(params.encSpaceB);

  return {
    x:
      left !== null &&
      right !== null &&
      nearlyEqual(left, right) &&
      resolutionListKeepsX(params.encFrontResolution) &&
      resolutionListKeepsX(params.encBackResolution),
    z:
      top !== null &&
      bottom !== null &&
      nearlyEqual(top, bottom) &&
      resolutionListKeepsZ(params.encFrontResolution) &&
      resolutionListKeepsZ(params.encBackResolution),
  };
}

export function normalizeQuadrants(value, fallback = FULL_QUADRANTS) {
  const text = String(value ?? '').trim();
  const numeric = Number(text);
  return VALID_QUADRANTS.has(numeric) ? numeric : fallback;
}

export function resolveAutoQuadrants(params = {}) {
  const sourceContours = String(params.sourceContours ?? '').trim();
  if (sourceContours) return FULL_QUADRANTS;

  const guideRotation = asFiniteNumber(params.gcurveRot);
  if (guideRotation !== null && !nearlyEqual(guideRotation, 0)) {
    return FULL_QUADRANTS;
  }

  let xSymmetric = true;
  let zSymmetric = true;
  for (const key of ANGULAR_SYMMETRY_KEYS) {
    const value = params[key];
    xSymmetric = xSymmetric && valueKeepsMirror(value, (p) => Math.PI - p);
    zSymmetric = zSymmetric && valueKeepsMirror(value, (p) => -p);
    if (!xSymmetric && !zSymmetric) return FULL_QUADRANTS;
  }

  const enclosure = enclosureSymmetry(params);
  xSymmetric = xSymmetric && enclosure.x;
  zSymmetric = zSymmetric && enclosure.z;

  const verticalOffset = asFiniteNumber(params.verticalOffset);
  if (verticalOffset !== null && !nearlyEqual(verticalOffset, 0)) {
    zSymmetric = false;
  }

  if (xSymmetric && zSymmetric) return QUARTER_Q1_QUADRANTS;
  if (zSymmetric) return HALF_TOP_QUADRANTS;
  if (xSymmetric) return HALF_RIGHT_QUADRANTS;
  return FULL_QUADRANTS;
}

export const QUADRANT_OPTIONS = Object.freeze({
  full: FULL_QUADRANTS,
  halfTop: HALF_TOP_QUADRANTS,
  halfRight: HALF_RIGHT_QUADRANTS,
  quarterQ1: QUARTER_Q1_QUADRANTS,
});
