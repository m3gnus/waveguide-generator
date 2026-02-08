import { evalParam, parseList } from '../../common.js';
import { DEFAULTS, MORPH_TARGETS } from '../constants.js';
import { applyMorphing } from '../morphing.js';
import { calculateOSSE } from '../profiles/osse.js';
import { calculateROSSE } from '../profiles/rosse.js';

export function computeOsseProfileAt(t, p, params, context) {
  const L = evalParam(params.L, p);
  const extLen = Math.max(0, evalParam(params.throatExtLength || 0, p));
  const slotLen = Math.max(0, evalParam(params.slotLength || 0, p));
  const totalLength = L + extLen + slotLen;

  const profile = calculateOSSE(t * totalLength, p, params, {
    gcurveCache: context?.coverageCache || null
  });

  const h = params.h === undefined ? 0 : evalParam(params.h, p);
  if (h > 0) {
    profile.y += h * Math.sin(t * Math.PI);
  }

  return profile;
}

export function computeMouthExtents(params, context) {
  const sampleCount = Math.max(360, Math.round((params.angularSegments || DEFAULTS.ANGULAR_SEGMENTS) * 4));
  const needsTarget = params.morphTarget !== undefined && Number(params.morphTarget) !== MORPH_TARGETS.NONE;
  const hasExplicit = (params.morphWidth > 0) || (params.morphHeight > 0);

  let rawMaxX = 0;
  let rawMaxZ = 0;

  const evaluateAt = (p) => (params.type === 'R-OSSE'
    ? calculateROSSE(evalParam(params.tmax || DEFAULTS.TMAX, p), p, params)
    : computeOsseProfileAt(1, p, params, context));

  for (let i = 0; i < sampleCount; i += 1) {
    const p = (i / sampleCount) * Math.PI * 2;
    const profile = evaluateAt(p);
    const r = profile.y;
    rawMaxX = Math.max(rawMaxX, Math.abs(r * Math.cos(p)));
    rawMaxZ = Math.max(rawMaxZ, Math.abs(r * Math.sin(p)));
  }

  const morphTargetInfo = (needsTarget && !hasExplicit) ? { halfW: rawMaxX, halfH: rawMaxZ } : null;

  if (!needsTarget) {
    return { halfW: rawMaxX, halfH: rawMaxZ, morphTargetInfo };
  }

  let maxX = 0;
  let maxZ = 0;
  for (let i = 0; i < sampleCount; i += 1) {
    const p = (i / sampleCount) * Math.PI * 2;
    const profile = evaluateAt(p);
    const r = applyMorphing(profile.y, 1, p, params, morphTargetInfo);
    maxX = Math.max(maxX, Math.abs(r * Math.cos(p)));
    maxZ = Math.max(maxZ, Math.abs(r * Math.sin(p)));
  }

  return { halfW: maxX, halfH: maxZ, morphTargetInfo };
}

export function buildMorphTargets(params, lengthSteps, angleList, sliceMap, context) {
  const safeAngles = Array.isArray(angleList) && angleList.length > 0 ? angleList : [0, Math.PI / 2];

  return Array.from({ length: lengthSteps + 1 }, (_, j) => {
    const t = sliceMap ? sliceMap[j] : j / lengthSteps;
    let maxX = 0;
    let maxZ = 0;

    for (const p of safeAngles) {
      const profile = computeOsseProfileAt(t, p, params, context);
      const r = profile.y;
      maxX = Math.max(maxX, Math.abs(r * Math.cos(p)));
      maxZ = Math.max(maxZ, Math.abs(r * Math.sin(p)));
    }

    return { halfW: maxX, halfH: maxZ };
  });
}

export function createRingVertices(params, sliceMap, angleList, morphTargets, ringCount, lengthSteps, context) {
  const vertices = [];

  const subdomainSlices = parseList(params.subdomainSlices);
  const interfaceOffset = parseList(params.interfaceOffset);
  const interfaceDraw = parseList(params.interfaceDraw);

  for (let j = 0; j <= lengthSteps; j += 1) {
    const t = sliceMap ? sliceMap[j] : j / lengthSteps;

    let zOffset = 0;
    if (subdomainSlices) {
      const sdIdx = subdomainSlices.indexOf(j);
      if (sdIdx !== -1) {
        zOffset = (interfaceOffset?.[sdIdx] || 0) + (interfaceDraw?.[sdIdx] || 0);
      }
    }

    for (let i = 0; i < ringCount; i += 1) {
      const p = angleList[i];
      const tmax = params.type === 'R-OSSE'
        ? (params.tmax === undefined ? DEFAULTS.TMAX : evalParam(params.tmax, p))
        : DEFAULTS.TMAX;
      const tActual = params.type === 'R-OSSE' ? t * tmax : t;

      const profile = params.type === 'R-OSSE'
        ? calculateROSSE(tActual, p, params)
        : computeOsseProfileAt(tActual, p, params, context);

      const morphTargetInfo = morphTargets?.[j] || null;
      const r = applyMorphing(profile.y, t, p, params, morphTargetInfo);

      vertices.push(
        r * Math.cos(p),
        profile.x + zOffset,
        r * Math.sin(p)
      );
    }
  }

  return vertices;
}

export function createHornIndices(ringCount, lengthSteps, fullCircle) {
  const indices = [];
  const radialSteps = fullCircle ? ringCount : Math.max(0, ringCount - 1);

  for (let j = 0; j < lengthSteps; j += 1) {
    for (let i = 0; i < radialSteps; i += 1) {
      const row1 = j * ringCount;
      const row2 = (j + 1) * ringCount;
      const i2 = fullCircle ? (i + 1) % ringCount : i + 1;

      indices.push(row1 + i, row1 + i2, row2 + i2);
      indices.push(row1 + i, row2 + i2, row2 + i);
    }
  }

  return indices;
}
