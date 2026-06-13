import { evalParam } from '../../common.js';
import { DEFAULTS } from '../constants.js';
import { applyMorphing, hasConfiguredMorphDimension, isMorphActive } from '../morphing.js';
import { calculateOSSE, resolveOsseLengthConfig } from '../profiles/osse.js';
import { calculateROSSE } from '../profiles/rosse.js';

function computeRosseProfileAt(t, p, params) {
  const tmax = params.tmax === undefined ? DEFAULTS.TMAX : evalParam(params.tmax, p);
  return calculateROSSE(t * tmax, p, params);
}

export function computeOsseProfileAt(t, p, params, context) {
  const { totalLength } = resolveOsseLengthConfig(params, p);

  const profile = calculateOSSE(t * totalLength, p, params, {
    gcurveCache: context?.coverageCache || null,
  });

  const h = params.h === undefined ? 0 : evalParam(params.h, p);
  if (h > 0) {
    profile.y += h * Math.sin(t * Math.PI);
  }

  return profile;
}

export function evaluateInnerProfileAt(t, p, params, context) {
  if (params.type === 'R-OSSE') {
    return computeRosseProfileAt(t, p, params);
  }
  return computeOsseProfileAt(t, p, params, context);
}

function resolveMorphProgress(t) {
  // Morph progress is the global normalized axial position, identical for every
  // azimuth, matching the canonical mesher
  // (hornlab_mesher.profile_sampling.build_point_grid). The throat-extension /
  // slot region is kept unmorphed by snapping the morph onset to a reserved
  // grid slice (see resolveMorphStart), not by shifting the blend per azimuth.
  return t;
}

export function computeMouthExtents(params, context) {
  const sampleCount = Math.max(
    360,
    Math.round((params.angularSegments || DEFAULTS.ANGULAR_SEGMENTS) * 4)
  );
  const needsTarget = isMorphActive(params, 0);
  const needsImplicitDimension =
    !hasConfiguredMorphDimension(params, 'morphWidth') ||
    !hasConfiguredMorphDimension(params, 'morphHeight');

  let rawMaxX = 0;
  let rawMaxZ = 0;

  const evaluateAt = (p) => evaluateInnerProfileAt(1, p, params, context);

  for (let i = 0; i < sampleCount; i += 1) {
    const p = (i / sampleCount) * Math.PI * 2;
    const profile = evaluateAt(p);
    const r = profile.y;
    rawMaxX = Math.max(rawMaxX, Math.abs(r * Math.cos(p)));
    rawMaxZ = Math.max(rawMaxZ, Math.abs(r * Math.sin(p)));
  }

  const morphTargetInfo =
    needsTarget && needsImplicitDimension ? { halfW: rawMaxX, halfH: rawMaxZ } : null;

  if (!needsTarget) {
    return { halfW: rawMaxX, halfH: rawMaxZ, morphTargetInfo };
  }

  let maxX = 0;
  let maxZ = 0;
  for (let i = 0; i < sampleCount; i += 1) {
    const p = (i / sampleCount) * Math.PI * 2;
    const profile = evaluateAt(p);
    const r = applyMorphing(profile.y, profile.y, 1, p, params, morphTargetInfo);
    maxX = Math.max(maxX, Math.abs(r * Math.cos(p)));
    maxZ = Math.max(maxZ, Math.abs(r * Math.sin(p)));
  }

  return { halfW: maxX, halfH: maxZ, morphTargetInfo };
}

/**
 * Resolve the morph-target half-dimensions exactly as the canonical mesher
 * (hornlab_mesher.profile_sampling.build_point_grid) does:
 *   - explicit dimension      -> dimension / 2
 *   - implicit (0) dimension  -> ceil(raw mouth half-extent)
 *   - no-shrinkage            -> max(resolved, raw mouth half-extent) per axis
 * Returns null when morph is inactive.
 */
export function resolveMorphDimensions(params, angleList, context) {
  if (!isMorphActive(params, 0)) return null;

  const safeAngles =
    Array.isArray(angleList) && angleList.length > 0 ? angleList : [0, Math.PI / 2];

  let rawHalfW = 0;
  let rawHalfH = 0;
  for (const p of safeAngles) {
    const r = evaluateInnerProfileAt(1, p, params, context).y;
    rawHalfW = Math.max(rawHalfW, Math.abs(r * Math.cos(p)));
    rawHalfH = Math.max(rawHalfH, Math.abs(r * Math.sin(p)));
  }

  const morphWidth = evalParam(params.morphWidth || 0, 0);
  const morphHeight = evalParam(params.morphHeight || 0, 0);
  let halfW = morphWidth > 0 ? morphWidth / 2 : Math.ceil(rawHalfW - 1e-9);
  let halfH = morphHeight > 0 ? morphHeight / 2 : Math.ceil(rawHalfH - 1e-9);

  const allowShrinkage = params.morphAllowShrinkage === 1 || params.morphAllowShrinkage === true;
  if (!allowShrinkage) {
    halfW = Math.max(halfW, rawHalfW);
    halfH = Math.max(halfH, rawHalfH);
  }

  return { halfW, halfH };
}

/**
 * Snap the configured morph onset to the grid slice at or after morphFixed and
 * past any reserved throat-extension/slot slices, matching the canonical mesher
 * (hornlab_mesher.profile_sampling.build_point_grid). Returns the snapped start
 * in normalized-axial (t) space.
 */
export function resolveMorphStart(params, lengthSteps, sliceMap, angleList) {
  const tValues = Array.from({ length: lengthSteps + 1 }, (_, j) =>
    sliceMap ? sliceMap[j] : j / lengthSteps
  );

  const configuredStart = evalParam(params.morphFixed || 0, 0);
  let idx = tValues.findIndex((t) => t >= configuredStart - 1e-12);
  if (idx < 0) idx = tValues.length - 1;
  let snapped = tValues[idx];

  // ATH reserves ceil(n * (ext + slot) / totalLength) axial slices for the
  // unmorphed throat-extension/slot region and starts the morph on that slice.
  if (params.type !== 'R-OSSE') {
    const angles = Array.isArray(angleList) && angleList.length > 0 ? angleList : [0, Math.PI / 2];
    let maxFixedLen = 0;
    let maxTotalLen = 0;
    for (const p of angles) {
      const { totalLength, extLen, slotLen } = resolveOsseLengthConfig(params, p);
      maxFixedLen = Math.max(maxFixedLen, extLen + slotLen);
      maxTotalLen = Math.max(maxTotalLen, totalLength);
    }
    if (maxTotalLen > 1e-12 && maxFixedLen > 0) {
      const reservedIdx = Math.min(
        lengthSteps,
        Math.ceil((lengthSteps * maxFixedLen) / maxTotalLen - 1e-9)
      );
      snapped = Math.max(snapped, tValues[reservedIdx]);
    }
  }

  return snapped;
}

export function buildMorphTargets(params, lengthSteps, angleList, sliceMap, context) {
  const resolved = resolveMorphDimensions(params, angleList, context);
  const morphStart = resolveMorphStart(params, lengthSteps, sliceMap, angleList);
  const mouthTargetInfo = { ...(resolved || { halfW: 0, halfH: 0 }), morphStart };
  return Array.from({ length: lengthSteps + 1 }, () => mouthTargetInfo);
}

export function createRingVertices(
  params,
  sliceMap,
  angleList,
  morphTargets,
  ringCount,
  lengthSteps,
  context
) {
  const vertices = [];

  for (let j = 0; j <= lengthSteps; j += 1) {
    const t = sliceMap ? sliceMap[j] : j / lengthSteps;

    for (let i = 0; i < ringCount; i += 1) {
      const p = angleList[i];
      const profile = evaluateInnerProfileAt(t, p, params, context);
      const mouthProfile =
        j === lengthSteps ? profile : evaluateInnerProfileAt(1, p, params, context);

      const morphTargetInfo = morphTargets?.[j] || null;
      const morphT = resolveMorphProgress(t);
      const r = applyMorphing(profile.y, mouthProfile.y, morphT, p, params, morphTargetInfo);

      vertices.push(r * Math.cos(p), profile.x, r * Math.sin(p));
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

// ---------------------------------------------------------------------------
// Adaptive phi-count mesh: variable phi samples per ring for near-isotropic
// triangles throughout the horn surface (throat → mouth).
// Only used for full-circle renders without enclosure/wall geometry.
// ---------------------------------------------------------------------------

function _sampleEffectiveRadius(params, t, context) {
  // Estimate the effective circumference radius at axial position t.
  // For circular profiles this equals R(t, 0).
  // For morphed/non-circular profiles we sample the perimeter with 16 chords.
  const NSAMPLE = 16;
  let circumference = 0;
  let prevX = null;
  let prevZ = null;

  for (let i = 0; i <= NSAMPLE; i += 1) {
    const p = (i / NSAMPLE) * Math.PI * 2;
    const profile = evaluateInnerProfileAt(t, p, params, context);

    const r = profile.y; // pre-morph radius (morph is minor near throat)
    const x = r * Math.cos(p);
    const z = r * Math.sin(p);

    if (prevX !== null) {
      circumference += Math.hypot(x - prevX, z - prevZ);
    }
    prevX = x;
    prevZ = z;
  }

  return circumference / (Math.PI * 2);
}

/**
 * Compute per-ring phi counts so that circumferential edge ≈ axialStep × targetAspect.
 * Returns an array of length (lengthSteps + 1), each value a multiple of 4, in
 * [12, userMax]. Values are monotonically non-decreasing (throat → mouth).
 */
export function computeAdaptivePhiCounts(params, lengthSteps, sliceMap, userMax, profileContext) {
  const { totalLength } = resolveOsseLengthConfig(params, 0);
  const TARGET_ASPECT = 1.5;
  const MIN_PHI = 12;

  const counts = [];

  for (let j = 0; j <= lengthSteps; j += 1) {
    const t = sliceMap ? sliceMap[j] : j / lengthSteps;
    const rEff = _sampleEffectiveRadius(params, t, profileContext);

    // Axial step via central difference in t, converted to length units.
    const jPrev = Math.max(0, j - 1);
    const jNext = Math.min(lengthSteps, j + 1);
    const tPrev = sliceMap ? sliceMap[jPrev] : jPrev / lengthSteps;
    const tNext = sliceMap ? sliceMap[jNext] : jNext / lengthSteps;
    const dt = (tNext - tPrev) / 2;
    const axialStep = dt * totalLength;

    let n;
    if (axialStep > 0 && rEff > 0) {
      const targetEdge = axialStep * TARGET_ASPECT;
      n = Math.round((2 * Math.PI * rEff) / targetEdge);
    } else {
      n = userMax;
    }

    // Snap to multiple of 4 and clamp.
    const snapped = Math.max(MIN_PHI, Math.min(userMax, Math.round(n / 4) * 4));
    counts.push(snapped);
  }

  // Enforce monotonically non-decreasing (horn only expands toward mouth).
  for (let j = 1; j <= lengthSteps; j += 1) {
    if (counts[j] < counts[j - 1]) counts[j] = counts[j - 1];
  }

  return counts;
}

/**
 * Like createRingVertices but each ring uses its own phi count from phiCounts[].
 * Angles are uniformly distributed: phi_i = (i / N) * 2π.
 */
export function createAdaptiveRingVertices(
  params,
  sliceMap,
  morphTargets,
  phiCounts,
  lengthSteps,
  context
) {
  const vertices = [];

  for (let j = 0; j <= lengthSteps; j += 1) {
    const t = sliceMap ? sliceMap[j] : j / lengthSteps;
    const N = phiCounts[j];

    for (let i = 0; i < N; i += 1) {
      const p = (i / N) * Math.PI * 2;
      const profile = evaluateInnerProfileAt(t, p, params, context);
      const mouthProfile =
        j === lengthSteps ? profile : evaluateInnerProfileAt(1, p, params, context);

      const morphTargetInfo = morphTargets?.[j] || null;
      const morphT = resolveMorphProgress(t);
      const r = applyMorphing(profile.y, mouthProfile.y, morphT, p, params, morphTargetInfo);

      vertices.push(r * Math.cos(p), profile.x, r * Math.sin(p));
    }
  }

  return vertices;
}

/**
 * Generate indices for a variable-phi-count horn mesh (full circle only).
 *
 * Between ring j (N1 phi) and ring j+1 (N2 phi, N2 >= N1):
 *   - If N1 === N2: standard 2-triangle quads.
 *   - If N2 > N1: fan triangles from each ring-j vertex to the ring-j+1 vertices
 *     spanning the same phi sector. Total triangles per ring-pair = N1 + N2.
 *
 * Winding convention matches createHornIndices (CCW when viewed from inside horn).
 */
export function createAdaptiveFanIndices(phiCounts, lengthSteps) {
  const indices = [];

  // Pre-compute the vertex offset of the first vertex in each ring.
  const ringStart = [0];
  for (let j = 0; j < lengthSteps; j += 1) {
    ringStart.push(ringStart[j] + phiCounts[j]);
  }

  for (let j = 0; j < lengthSteps; j += 1) {
    const N1 = phiCounts[j];
    const N2 = phiCounts[j + 1];
    const base1 = ringStart[j];
    const base2 = ringStart[j + 1];

    for (let i = 0; i < N1; i += 1) {
      const a = base1 + i;
      const b = base1 + ((i + 1) % N1);

      // Find the ring-j+1 sector boundaries for this phi interval.
      const kLo = Math.round((i * N2) / N1);
      const kHi = Math.round(((i + 1) * N2) / N1);

      // Triangle 1: close the right boundary (matches existing winding for K=1).
      indices.push(a, b, base2 + (kHi % N2));

      // Fan triangles from a back to kLo (inclusive).
      // For K=1 this produces exactly the second standard triangle.
      for (let k = kHi - 1; k >= kLo; k -= 1) {
        indices.push(a, base2 + ((k + 1) % N2), base2 + (k % N2));
      }
    }
  }

  return indices;
}
