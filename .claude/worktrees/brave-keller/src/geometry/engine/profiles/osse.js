import { clamp, evalParam, toRad } from '../../common.js';
import { DEFAULTS, HORN_PROFILES } from '../constants.js';
import { getGuidingCurveRadius } from './guidingCurve.js';
import { validateParameters } from './validation.js';

function computeOsseBaseRadius(z, r0, k, a0, a) {
  const term1 = (k * r0) ** 2;
  const term2 = 2 * k * r0 * z * Math.tan(a0);
  const term3 = (z ** 2) * (Math.tan(a) ** 2);
  return Math.sqrt(term1 + term2 + term3) + r0 * (1 - k);
}

function computeOsseTermRadius(z, L, s, n, q) {
  if (z <= 0 || n <= 0 || q <= 0 || L <= 0) return 0;

  const zNorm = q * z / L;
  if (zNorm > 1.0) return (s * L / q);

  return (s * L / q) * (1 - Math.pow(1 - Math.pow(zNorm, n), 1 / n));
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

  return preferUpper ? (c1.y >= c2.y ? c1 : c2) : (c1.y < c2.y ? c1 : c2);
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
    radius: arcRadius
  };
}

function evaluateCircularArc(zMain, r0Main, mouthR, params, p, L) {
  const explicitRadius = evalParam(params.circArcRadius || 0, p);
  const p1 = { x: 0, y: r0Main };
  const p2 = { x: L, y: mouthR };

  let center = null;
  let arcRadius = explicitRadius;

  if (Number.isFinite(explicitRadius) && explicitRadius > 0) {
    center = calculateArcCenterFromRadius(p1, p2, explicitRadius, mouthR > r0Main);
  }

  if (!center) {
    const termAngle = toRad(evalParam(params.circArcTermAngle || 1, p));
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

function computeCoverageAngleFromGuidingCurve(p, params, config, coverageCache = null) {
  if (coverageCache instanceof Map) {
    const key = p.toFixed(6);
    if (coverageCache.has(key)) return coverageCache.get(key);

    const computed = computeCoverageAngleFromGuidingCurve(p, params, config, null);
    coverageCache.set(key, computed);
    return computed;
  }

  const {
    totalLength,
    extLen,
    slotLen,
    r0Base,
    extAngleRad,
    a0Deg,
    L
  } = config;

  const targetR = getGuidingCurveRadius(p, params);
  if (!Number.isFinite(targetR)) return evalParam(params.a, p);

  const distParam = evalParam(params.gcurveDist || 0, p);
  const distRaw = distParam <= 1 ? totalLength * distParam : distParam;
  const dist = clamp(distRaw, 0, totalLength);
  if (!Number.isFinite(dist) || dist <= 0) return evalParam(params.a, p);

  const r0Main = r0Base + extLen * Math.tan(extAngleRad);
  const zMain = Math.max(0, dist - extLen - slotLen);

  let low = 0.5;
  let high = 89;
  for (let i = 0; i < 24; i += 1) {
    const mid = (low + high) / 2;
    const rMid = computeOsseRadius(zMain, p, params, {
      L,
      aDeg: mid,
      a0Deg,
      r0: r0Main
    });
    if (!Number.isFinite(rMid)) break;
    if (rMid < targetR) {
      low = mid;
    } else {
      high = mid;
    }
  }

  return clamp((low + high) / 2, 0.5, 89);
}

export function calculateOSSE(z, p, params, options = {}) {
  const validation = validateParameters(params, 'OSSE');
  if (!validation.valid) {
    console.error('Validation failed:', validation.errors);
    return { x: NaN, y: NaN };
  }

  const L = options.L ?? evalParam(params.L, p);
  const extLen = Math.max(0, evalParam(params.throatExtLength || 0, p));
  const slotLen = Math.max(0, evalParam(params.slotLength || 0, p));
  const totalLength = L + extLen + slotLen;
  const extAngleRad = toRad(evalParam(params.throatExtAngle || 0, p));

  const r0Base = evalParam(params.r0, p);
  const a0Deg = evalParam(params.a0, p);
  const r0Main = r0Base + extLen * Math.tan(extAngleRad);

  const config = { totalLength, extLen, slotLen, r0Base, extAngleRad, a0Deg, L };

  const throatProfile = Number(params.throatProfile || HORN_PROFILES.STANDARD);
  const gcurveType = Number(params.gcurveType || 0);
  const coverageAngle = options.coverageAngle
    ?? (gcurveType === 0
      ? evalParam(params.a, p)
      : computeCoverageAngleFromGuidingCurve(p, params, config, options.gcurveCache));

  let radius;
  if (z <= extLen) {
    radius = r0Base + z * Math.tan(extAngleRad);
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
        r0: r0Main
      });
    }
  }

  let x = z;
  let y = radius;
  const rotDeg = evalParam(params.rot || 0, p);

  if (Number.isFinite(rotDeg) && rotDeg !== 0) {
    const rotRad = toRad(rotDeg);
    const dx = x;
    const dy = y - r0Base;
    x = dx * Math.cos(rotRad) - dy * Math.sin(rotRad);
    y = r0Base + dx * Math.sin(rotRad) + dy * Math.cos(rotRad);
  }

  return { x, y };
}
