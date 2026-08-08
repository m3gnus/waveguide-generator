import { clamp, evalParam, toRad } from '../../common.js';
import { debugError } from '../../../logging/debug.js';
import { DEFAULTS, HORN_PROFILES } from '../constants.js';
import { getGuidingCurveRadius } from './guidingCurve.js';
import { validateParameters } from './validation.js';

function paramOrDefault(value, fallback) {
  return value === undefined || value === null || value === '' ? fallback : value;
}

// Bracket for the guiding-curve coverage inversion. Kept in step with
// _COVERAGE_ANGLE_MIN/_MAX in hornlab_mesher.profile_morph.
const COVERAGE_ANGLE_MIN = 0.5;
const COVERAGE_ANGLE_MAX = 89;

function computeOsseBaseRadius(z, r0, k, a0, a) {
  const term1 = (k * r0) ** 2;
  const term2 = 2 * k * r0 * z * Math.tan(a0);
  const term3 = z ** 2 * Math.tan(a) ** 2;
  return Math.sqrt(term1 + term2 + term3) + r0 * (1 - k);
}

function computeOsseTermRadius(z, L, s, n, q) {
  if (z <= 0 || n <= 0 || q <= 0 || L <= 0) return 0;

  const zNorm = (q * z) / L;
  if (zNorm > 1.0) return (s * L) / q;

  return ((s * L) / q) * (1 - Math.pow(1 - Math.pow(zNorm, n), 1 / n));
}

export function resolveOsseLengthConfig(params, p, options = {}) {
  const rawL = options.L ?? evalParam(params.L, p);
  const extLen = Math.max(0, evalParam(paramOrDefault(params.throatExtLength, 0), p));
  const slotLen = Math.max(0, evalParam(paramOrDefault(params.slotLength, 0), p));
  const lengthMode = options.lengthMode ?? params._athLengthMode;

  if (lengthMode === 'total') {
    // ATH adds Throat.Ext.Length on TOP of Length but carves Slot.Length OUT of
    // it: the main section loses only the slot, and the total axial grows by the
    // extension. Mirrors hornlab_mesher.profile_formulas.osse_length_config.
    return {
      L: Math.max(0, rawL - slotLen),
      totalLength: Math.max(0, rawL + extLen),
      extLen,
      slotLen,
    };
  }

  return {
    L: Math.max(0, rawL),
    totalLength: Math.max(0, rawL + extLen + slotLen),
    extLen,
    slotLen,
  };
}

export function computeOsseRadius(z, p, params, overrides = {}) {
  const L = overrides.L ?? evalParam(params.L, p);
  const a = toRad(overrides.aDeg ?? evalParam(params.a, p));
  const a0 = toRad(overrides.a0Deg ?? evalParam(params.a0, p));
  const r0 = overrides.r0 ?? evalParam(params.r0, p);

  const s = params.s !== undefined ? evalParam(params.s, p) : 0;
  const k = params.k === undefined ? DEFAULTS.K : evalParam(params.k, p);
  const n = params.n === undefined ? DEFAULTS.N : evalParam(params.n, p);
  const q = params.q === undefined ? DEFAULTS.Q : evalParam(params.q, p);

  return computeOsseBaseRadius(z, r0, k, a0, a) + computeOsseTermRadius(z, L, s, n, q);
}

function calculateArcCenterFromRadius(p1, p2, arcRadius, preferUpper = true) {
  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  const d = Math.hypot(dx, dy);

  if (d <= 0 || arcRadius < d / 2) return null;

  const midX = (p1.x + p2.x) / 2;
  const midY = (p1.y + p2.y) / 2;
  const h = Math.sqrt(Math.max(0, arcRadius ** 2 - (d / 2) ** 2));

  const nx = -dy / d;
  const ny = dx / d;

  const c1 = { x: midX + nx * h, y: midY + ny * h };
  const c2 = { x: midX - nx * h, y: midY - ny * h };

  return preferUpper ? (c1.y >= c2.y ? c1 : c2) : c1.y < c2.y ? c1 : c2;
}

function calculateArcCenterFromTangent(p1, p2, tangentAngle) {
  const t = { x: Math.cos(tangentAngle), y: Math.sin(tangentAngle) };
  const n = { x: -t.y, y: t.x };

  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  const dDotN = dx * n.x + dy * n.y;

  if (Math.abs(dDotN) <= 1e-6) return null;

  const arcRadius = -((dx ** 2 + dy ** 2) / (2 * dDotN));
  return {
    x: p2.x + n.x * arcRadius,
    y: p2.y + n.y * arcRadius,
    radius: arcRadius,
  };
}

function evaluateCircularArc(zMain, r0Main, mouthR, params, p, L) {
  const explicitRadius = evalParam(paramOrDefault(params.circArcRadius, 0), p);
  const p1 = { x: 0, y: r0Main };
  const p2 = { x: L, y: mouthR };

  let center = null;
  let arcRadius = explicitRadius;

  if (Number.isFinite(explicitRadius) && explicitRadius > 0) {
    center = calculateArcCenterFromRadius(p1, p2, explicitRadius, mouthR > r0Main);
  }

  if (!center) {
    const termAngle = toRad(evalParam(paramOrDefault(params.circArcTermAngle, 1), p));
    const tangent = calculateArcCenterFromTangent(p1, p2, termAngle);
    if (tangent) {
      center = { x: tangent.x, y: tangent.y };
      arcRadius = tangent.radius;
    }
  }

  if (!center || !Number.isFinite(arcRadius) || arcRadius === 0) {
    return mouthR;
  }

  const dx = zMain - center.x;
  const under = arcRadius ** 2 - dx ** 2;
  if (under < 0) return mouthR;

  const sign = Math.sign(mouthR - center.y) || 1;
  return center.y + sign * Math.sqrt(under);
}

/**
 * Solve the coverage angle that puts the OSSE mouth on the guiding curve.
 *
 * Returns `{ angle, saturated, achieved, target }`. `saturated` is `'min'` or
 * `'max'` when the guiding curve is out of reach of the coverage bracket, in
 * which case `achieved` is the mouth radius the geometry actually gets and
 * `target` the one that was asked for. `saturated` is `null` both when the
 * curve is met and when no guiding curve applies (the explicit `a` is used).
 */
function solveCoverageFromGuidingCurve(p, params, config, coverageCache = null) {
  if (coverageCache instanceof Map) {
    const key = p.toFixed(6);
    if (coverageCache.has(key)) return coverageCache.get(key);

    const computed = solveCoverageFromGuidingCurve(p, params, config, null);
    coverageCache.set(key, computed);
    return computed;
  }

  const { totalLength, extLen, slotLen, r0Base, a0Deg, L } = config;
  const explicit = () => ({
    angle: evalParam(params.a, p),
    saturated: null,
    achieved: NaN,
    target: NaN,
    stationZ: NaN,
    atMouth: true,
  });

  const targetR = getGuidingCurveRadius(p, params);
  if (!Number.isFinite(targetR)) return explicit();

  // Canonical semantics (hornlab_mesher.profile_morph): gcurveDist is a
  // fraction of the MAIN OSSE length (throat extension/slot excluded);
  // absolute values are main-section z; unset/invalid falls back to the
  // mouth. The viewport previously mapped the fraction over the total
  // length and subtracted ext+slot, picking a different inversion point
  // than the solved mesh whenever an extension or slot was present.
  const distParam = evalParam(params.gcurveDist ?? 1, p);
  const mainLength = Math.max(0, totalLength - extLen - slotLen);
  let zMain = distParam > 0 && distParam <= 1 ? mainLength * distParam : distParam;
  if (!Number.isFinite(zMain) || zMain <= 0) zMain = mainLength;
  zMain = Math.min(mainLength, zMain);
  if (zMain <= 0) return explicit();

  // ATH anchors r0 at the MAIN throat; the extension tapers back from it.
  const r0Main = r0Base;

  const radiusAt = (aDeg) =>
    computeOsseRadius(zMain, p, params, { L, aDeg, a0Deg, r0: r0Main });

  const bisect = (lowStart, highStart) => {
    let low = lowStart;
    let high = highStart;
    for (let i = 0; i < 24; i += 1) {
      const mid = (low + high) / 2;
      const rMid = radiusAt(mid);
      if (!Number.isFinite(rMid)) break;
      if (rMid < targetR) {
        low = mid;
      } else {
        high = mid;
      }
    }
    return clamp((low + high) / 2, COVERAGE_ANGLE_MIN, COVERAGE_ANGLE_MAX);
  };

  const atMouth = Math.abs(zMain - mainLength) <= 1e-9;
  const settled = (angle, saturated, achieved) =>
    ({ angle, saturated, achieved, target: targetR, stationZ: zMain, atMouth });

  // Probe the bracket ends before bisecting. computeOsseRadius is monotonic in
  // the coverage angle, so a target outside [r(0.5), r(89)] is unreachable and
  // the bisection would silently converge onto the bracket end — the mouth
  // then misses the guiding curve entirely and no further parameter change can
  // bring it back. Mirrors hornlab_mesher.profile_morph so the local fallback
  // renderer and the backend mesher agree on both the angle and the diagnosis.
  const rLow = radiusAt(COVERAGE_ANGLE_MIN);
  const rHigh = radiusAt(COVERAGE_ANGLE_MAX);
  if (!Number.isFinite(rLow) || !Number.isFinite(rHigh)) {
    // The radius is undefined somewhere in the bracket, so the probe supports
    // no verdict. Bisect as before and report no diagnosis rather than putting
    // a NaN in front of the user.
    const angle = bisect(COVERAGE_ANGLE_MIN, COVERAGE_ANGLE_MAX);
    return settled(angle, null, radiusAt(angle));
  }
  // Strict comparisons: a target exactly equal to a bracket end is reachable
  // AT that end, not outside it.
  if (targetR < rLow) return settled(COVERAGE_ANGLE_MIN, 'min', rLow);
  if (targetR > rHigh) return settled(COVERAGE_ANGLE_MAX, 'max', rHigh);

  const angle = bisect(COVERAGE_ANGLE_MIN, COVERAGE_ANGLE_MAX);
  return settled(angle, null, radiusAt(angle));
}

/**
 * Reason the guiding curve cannot be met at azimuth `p`, or `null` if it can.
 *
 * Mirrors `osse_coverage_saturation` in hornlab_mesher.profile_formulas so the
 * local engine can diagnose the same condition as the backend mesher: the
 * coverage solver clamps to its bracket instead of failing, which presents to
 * the user as the mouth being stuck and every other parameter having no effect.
 */
export function getOsseCoverageSaturation(p, params) {
  if (Number(evalParam(paramOrDefault(params.gcurveType, 0), p)) === 0) return null;
  const { L, totalLength, extLen, slotLen } = resolveOsseLengthConfig(params, p);
  const r0Base = evalParam(params.r0, p);
  const a0Deg = evalParam(params.a0, p);
  const config = { totalLength, extLen, slotLen, r0Base, a0Deg, L };
  const solved = solveCoverageFromGuidingCurve(p, params, config);
  if (!solved.saturated) return null;
  const phiDeg = (((p * 180) / Math.PI) % 360 + 360) % 360;
  const remedy =
    solved.saturated === 'min'
      ? 'shorten the horn (Length), reduce the termination shape s, or widen the guiding curve'
      : 'lengthen the horn (Length) or narrow the guiding curve';
  // The inversion is solved where the guiding curve sits, which is the mouth
  // only when GCurve.Dist is 1 — and the schema still defaults it to 0.5.
  const station = solved.atMouth
    ? 'the mouth radius'
    : `the radius at the guiding-curve distance (z=${solved.stationZ.toFixed(1)} mm)`;
  return (
    `guiding curve unreachable at phi=${phiDeg.toFixed(1)} deg: the coverage angle ` +
    `is pinned at ${solved.angle} deg, so ${station} is ` +
    `${solved.achieved.toFixed(1)} mm instead of the requested ` +
    `${solved.target.toFixed(1)} mm; ${remedy}`
  );
}

export function calculateOSSE(z, p, params, options = {}) {
  const validation = validateParameters(params, 'OSSE');
  if (!validation.valid) {
    debugError('Validation failed:', validation.errors);
    return { x: NaN, y: NaN };
  }

  const { L, totalLength, extLen, slotLen } = resolveOsseLengthConfig(params, p, options);
  const extAngleRad = toRad(evalParam(paramOrDefault(params.throatExtAngle, 0), p));

  const r0Base = evalParam(params.r0, p);
  const a0Deg = evalParam(params.a0, p);
  // ATH anchors Throat.Diameter (r0) at the MAIN horn throat and tapers the
  // extension BACK from r0 to the driver end (r0 - ext*tan(angle)); it does not
  // enlarge the main throat. Mirrors hornlab_mesher.profile_formulas.calculate_osse.
  const r0Main = r0Base;
  const r0Throat = Math.max(0, r0Base - extLen * Math.tan(extAngleRad));

  const config = { totalLength, extLen, slotLen, r0Base, extAngleRad, a0Deg, L };

  const throatProfile = Number(paramOrDefault(params.throatProfile, HORN_PROFILES.STANDARD));
  const gcurveType = Number(evalParam(paramOrDefault(params.gcurveType, 0), p));
  const coverageAngle =
    options.coverageAngle ??
    (gcurveType === 0
      ? evalParam(params.a, p)
      : solveCoverageFromGuidingCurve(p, params, config, options.gcurveCache).angle);

  let radius;
  if (z <= extLen) {
    radius = r0Throat + z * Math.tan(extAngleRad);
  } else if (z <= extLen + slotLen) {
    radius = r0Main;
  } else {
    const zMain = z - extLen - slotLen;

    if (throatProfile === HORN_PROFILES.CIRCULAR_ARC) {
      const aRad = toRad(coverageAngle);
      const mouthR = r0Main + L * Math.tan(aRad);
      radius = evaluateCircularArc(zMain, r0Main, mouthR, params, p, L);
    } else {
      radius = computeOsseRadius(zMain, p, params, {
        L,
        aDeg: coverageAngle,
        a0Deg,
        r0: r0Main,
      });
    }
  }

  let x = z;
  let y = radius;
  const rotDeg = evalParam(paramOrDefault(params.rot, 0), p);

  if (Number.isFinite(rotDeg) && rotDeg !== 0) {
    const rotRad = toRad(rotDeg);
    const dx = x;
    const dy = y - r0Base;
    x = dx * Math.cos(rotRad) - dy * Math.sin(rotRad);
    y = r0Base + dx * Math.sin(rotRad) + dy * Math.cos(rotRad);
  }

  return { x, y };
}
