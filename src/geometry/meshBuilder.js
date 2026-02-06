import { calculateROSSE, calculateOSSE } from './hornModels.js';
import {
  evalParam,
  parseList,
  parseQuadrants
} from './common.js';
import { applyMorphing } from './morphing.js';
import { addRollbackGeometry, addRearShapeGeometry } from './rollback.js';
import { addEnclosureGeometry } from './enclosure.js';


// ===========================================================================
// ATH Z-Mapping and Slice Distribution
// ===========================================================================

const ATH_ZMAP_20 = [
  0.0,
  0.01319,
  0.03269,
  0.05965,
  0.094787,
  0.139633,
  0.195959,
  0.263047,
  0.340509,
  0.427298,
  0.518751,
  0.610911,
  0.695737,
  0.770223,
  0.833534,
  0.88547,
  0.925641,
  0.955904,
  0.977809,
  0.992192,
  1.0
];

const resampleZMap = (map, lengthSteps) => {
  if (!map || map.length < 2 || lengthSteps <= 0) return null;
  const maxIndex = map.length - 1;
  if (maxIndex === lengthSteps) return map.slice();
  const out = new Array(lengthSteps + 1);
  for (let j = 0; j <= lengthSteps; j++) {
    const t = (j / lengthSteps) * maxIndex;
    const idx = Math.floor(t);
    const frac = t - idx;
    const v0 = map[idx];
    const v1 = map[Math.min(idx + 1, maxIndex)];
    out[j] = v0 + (v1 - v0) * frac;
  }
  return out;
};

const buildSliceMap = (params, lengthSteps) => {
  // 1. Explicit zMapPoints takes top priority
  const zMap = parseList(params.zMapPoints);
  if (zMap && zMap.length === lengthSteps + 1) {
    const maxVal = Math.max(...zMap);
    if (maxVal > 1.0) {
      return zMap.map((z) => z / maxVal);
    }
    return zMap.map((z) => Math.max(0, Math.min(1, z)));
  }

  // 2. Resolution-based grading (throatResolution vs mouthResolution)
  // This allows the viewport to reflect the density intended for Gmsh
  const resT = Number(params.throatResolution);
  const resM = Number(params.mouthResolution);
  if (Number.isFinite(resT) && Number.isFinite(resM) && resT > 0 && resM > 0 && Math.abs(resT - resM) > 0.01) {
    const map = new Array(lengthSteps + 1);
    // We want a distribution where the segment length changes from resT to resM.
    // segment_length(t) = resT + (resM - resT) * t
    // Integral(segment_length) = resT*t + 0.5*(resM - resT)*t^2 
    // Normalized: f(t) = (resT*t + 0.5*(resM - resT)*t^2) / (0.5*(resT + resM))
    const avgRes = 0.5 * (resT + resM);
    for (let j = 0; j <= lengthSteps; j++) {
      const t = j / lengthSteps;
      map[j] = (resT * t + 0.5 * (resM - resT) * t * t) / avgRes;
    }
    return map;
  }

  // 3. Fallback to ATH defaults
  if (params.useAthZMap) {
    const athMap = resampleZMap(ATH_ZMAP_20, lengthSteps);
    if (athMap) return athMap;
  }

  // 4. Throat extension/segment splitting
  const throatSegments = Number(params.throatSegments || 0);
  const extLen = Math.max(0, evalParam(params.throatExtLength || 0, 0));
  const slotLen = Math.max(0, evalParam(params.slotLength || 0, 0));
  const L = Math.max(0, evalParam(params.L || 0, 0));
  const totalLength = L + extLen + slotLen;

  if (totalLength > 0 && throatSegments > 0 && throatSegments < lengthSteps) {
    const extFraction = (extLen + slotLen) / totalLength;
    if (extFraction > 0 && extFraction < 1) {
      const map = new Array(lengthSteps + 1);
      for (let j = 0; j <= lengthSteps; j++) {
        if (j <= throatSegments) {
          map[j] = extFraction * (j / throatSegments);
        } else {
          const t = (j - throatSegments) / (lengthSteps - throatSegments);
          map[j] = extFraction + (1 - extFraction) * t;
        }
      }
      return map;
    }
  }

  return null;
};

const computeOsseProfileAt = (t, p, params) => {
  const L = evalParam(params.L, p);
  const extLen = Math.max(0, evalParam(params.throatExtLength || 0, p));
  const slotLen = Math.max(0, evalParam(params.slotLength || 0, p));
  const totalLength = L + extLen + slotLen;
  const profile = calculateOSSE(t * totalLength, p, params);
  const h = params.h === undefined ? 0 : evalParam(params.h, p);
  if (h > 0) {
    profile.y += h * Math.sin(t * Math.PI);
  }
  return profile;
};

const buildMorphTargets = (params, lengthSteps, angleList, sliceMap) => {
  const targets = new Array(lengthSteps + 1);
  const safeAngles = Array.isArray(angleList) && angleList.length > 0
    ? angleList
    : [0, Math.PI / 2];
  for (let j = 0; j <= lengthSteps; j++) {
    const t = sliceMap ? sliceMap[j] : j / lengthSteps;
    let maxX = 0;
    let maxZ = 0;
    for (let i = 0; i < safeAngles.length; i++) {
      const p = safeAngles[i];
      const profile = computeOsseProfileAt(t, p, params);
      const r = profile.y;
      const x = Math.abs(r * Math.cos(p));
      const z = Math.abs(r * Math.sin(p));
      if (x > maxX) maxX = x;
      if (z > maxZ) maxZ = z;
    }
    targets[j] = { halfW: maxX, halfH: maxZ };
  }
  return targets;
};

const computeMouthExtents = (params) => {
  const t = 1;
  const sampleCount = Math.max(360, Math.round((params.angularSegments || 80) * 4));
  const needsTarget = params.morphTarget !== undefined && Number(params.morphTarget) !== 0;
  const hasExplicit = (params.morphWidth && params.morphWidth > 0) || (params.morphHeight && params.morphHeight > 0);

  let rawMaxX = 0;
  let rawMaxZ = 0;

  const rawAt = (p) => {
    if (params.type === 'R-OSSE') {
      const tmax = params.tmax === undefined ? 1.0 : evalParam(params.tmax, p);
      return calculateROSSE(t * tmax, p, params);
    }
    return computeOsseProfileAt(t, p, params);
  };

  for (let i = 0; i < sampleCount; i++) {
    const p = (i / sampleCount) * Math.PI * 2;
    const profile = rawAt(p);
    const r = profile.y;
    const x = Math.abs(r * Math.cos(p));
    const z = Math.abs(r * Math.sin(p));
    if (x > rawMaxX) rawMaxX = x;
    if (z > rawMaxZ) rawMaxZ = z;
  }

  let morphTargetInfo = null;
  if (needsTarget && !hasExplicit) {
    morphTargetInfo = { halfW: rawMaxX, halfH: rawMaxZ };
  }

  if (!needsTarget) {
    return { halfW: rawMaxX, halfH: rawMaxZ, morphTargetInfo };
  }

  let maxX = 0;
  let maxZ = 0;
  for (let i = 0; i < sampleCount; i++) {
    const p = (i / sampleCount) * Math.PI * 2;
    const profile = rawAt(p);
    const r = applyMorphing(profile.y, t, p, params, morphTargetInfo);
    const x = Math.abs(r * Math.cos(p));
    const z = Math.abs(r * Math.sin(p));
    if (x > maxX) maxX = x;
    if (z > maxZ) maxZ = z;
  }

  return {
    halfW: maxX,
    halfH: maxZ,
    morphTargetInfo
  };
};

const buildQuadrantAngles = (params, pointsPerQuadrant, mouthExtents) => {
  const halfW = mouthExtents?.halfW ?? 0;
  const halfH = mouthExtents?.halfH ?? 0;
  const cornerRaw = evalParam(params.morphCorner || 0, 0);
  const cornerPoints = Math.max(1, Math.round(params.cornerSegments || 4));
  const cornerSegments = Math.max(0, cornerPoints - 1);

  if (!Number.isFinite(halfW) || !Number.isFinite(halfH) || halfW <= 0 || halfH <= 0) {
    return null;
  }

  let cornerR = Number.isFinite(cornerRaw) ? cornerRaw : 0;
  if (cornerR < 0) cornerR = 0;
  const maxCorner = Math.max(0, Math.min(halfW, halfH) - 1e-6);
  if (cornerR > maxCorner) cornerR = maxCorner;

  if (cornerR <= 0 || cornerPoints <= 1) {
    const angles = [];
    for (let i = 0; i <= pointsPerQuadrant; i++) {
      angles.push((Math.PI / 2) * (i / pointsPerQuadrant));
    }
    return angles;
  }

  const theta1 = Math.atan2(halfH - cornerR, halfW);
  const theta2 = Math.atan2(halfH, halfW - cornerR);
  const remainingSegments = Math.max(1, pointsPerQuadrant - cornerSegments);
  const side1Span = theta1;
  const side2Span = Math.max(0, (Math.PI / 2) - theta2);
  let side1Seg = Math.round(remainingSegments * side1Span / (side1Span + side2Span));
  side1Seg = Math.max(1, Math.min(remainingSegments - 1, side1Seg));
  const side2Seg = Math.max(1, remainingSegments - side1Seg);

  const angles = [];
  for (let i = 0; i <= side1Seg; i++) {
    angles.push(theta1 * (i / side1Seg));
  }

  const cx = halfW - cornerR;
  const cy = halfH - cornerR;
  for (let i = 1; i < cornerPoints; i++) {
    const phi = (i / (cornerPoints - 1)) * (Math.PI / 2);
    const x = cx + cornerR * Math.cos(phi);
    const z = cy + cornerR * Math.sin(phi);
    angles.push(Math.atan2(z, x));
  }

  for (let i = 1; i <= side2Seg; i++) {
    angles.push(theta2 + ((Math.PI / 2) - theta2) * (i / side2Seg));
  }

  return angles;
};

const buildAngleList = (params) => {
  const angularSegments = Number(params.angularSegments || 0);
  if (!Number.isFinite(angularSegments) || angularSegments < 4) {
    return { fullAngles: [0], pointsPerQuadrant: 0 };
  }
  if (angularSegments % 4 !== 0) {
    const uniform = [];
    for (let i = 0; i < angularSegments; i++) {
      uniform.push((i / angularSegments) * Math.PI * 2);
    }
    return { fullAngles: uniform, pointsPerQuadrant: 0 };
  }

  const pointsPerQuadrant = angularSegments / 4;
  const mouthExtents = computeMouthExtents(params);
  const quadrantAngles = buildQuadrantAngles(params, pointsPerQuadrant, mouthExtents);
  if (!quadrantAngles || quadrantAngles.length !== pointsPerQuadrant + 1) {
    const uniform = [];
    for (let i = 0; i < angularSegments; i++) {
      uniform.push((i / angularSegments) * Math.PI * 2);
    }
    return { fullAngles: uniform, pointsPerQuadrant: 0 };
  }

  const fullAngles = [];
  // Quadrant 1: 0 -> π/2
  fullAngles.push(...quadrantAngles);
  // Quadrant 2: π/2 -> π (exclude π/2)
  for (let i = quadrantAngles.length - 2; i >= 0; i--) {
    fullAngles.push(Math.PI - quadrantAngles[i]);
  }
  // Quadrant 3: π -> 3π/2 (exclude π)
  for (let i = 1; i < quadrantAngles.length; i++) {
    fullAngles.push(Math.PI + quadrantAngles[i]);
  }
  // Quadrant 4: 3π/2 -> 2π (exclude 3π/2 and 2π)
  for (let i = quadrantAngles.length - 2; i > 0; i--) {
    fullAngles.push((Math.PI * 2) - quadrantAngles[i]);
  }

  return { fullAngles, pointsPerQuadrant };
};

const selectAnglesForQuadrants = (fullAngles, quadrants) => {
  const q = String(quadrants ?? '1234').trim();
  if (q === '' || q === '1234') return fullAngles;

  const eps = 1e-9;
  if (q === '14') {
    const positive = fullAngles.filter((a) => a >= -eps && a <= Math.PI / 2 + eps);
    const negative = [];
    for (const a of fullAngles) {
      if (a >= Math.PI * 1.5 - eps) negative.push(a - Math.PI * 2);
    }
    return [...positive, ...negative];
  }
  if (q === '12') {
    return fullAngles.filter((a) => a >= -eps && a <= Math.PI + eps);
  }
  if (q === '1') {
    return fullAngles.filter((a) => a >= -eps && a <= Math.PI / 2 + eps);
  }
  return fullAngles;
};


/**
 * Build the horn mesh geometry (vertices and indices) from the given parameters.
 * @param {Object} params - The complete parameter object.
 * @returns {{ vertices: number[], indices: number[] }} The flat arrays of coordinates and indices.
 */
export function buildHornMesh(params, options = {}) {
  const includeEnclosure = options.includeEnclosure !== false;
  const includeRearShape = options.includeRearShape !== false;
  const groupInfo = options.groupInfo ?? (options.collectGroups ? {} : null);
  const radialSteps = params.angularSegments;
  const lengthSteps = params.lengthSegments;
  const sliceMap = buildSliceMap(params, lengthSteps);

  const vertices = [];
  const indices = [];

  // Support for variable mesh resolution
  const throatResolution = params.throatResolution || radialSteps;
  const mouthResolution = params.mouthResolution || radialSteps;

  // Vertical offset (ATH Mesh.VerticalOffset) applied to Z axis (vertical)
  const verticalOffset = parseFloat(params.verticalOffset) || 0;

  // Quadrant support for symmetry meshes
  const quadrantInfo = parseQuadrants(params.quadrants);
  const { fullAngles } = buildAngleList(params);
  const angleList = selectAnglesForQuadrants(fullAngles, params.quadrants);
  const ringCount = angleList.length;
  const morphTarget = Number(params.morphTarget || 0);
  const needsMorphTargets = params.type === 'OSSE' && morphTarget !== 0
    && (!params.morphWidth || !params.morphHeight);
  const morphTargets = needsMorphTargets
    ? buildMorphTargets(params, lengthSteps, angleList, sliceMap)
    : null;

  // Parse subdomain info
  const subdomainSlices = parseList(params.subdomainSlices);
  const interfaceOffset = parseList(params.interfaceOffset);
  const interfaceDraw = parseList(params.interfaceDraw);

  for (let j = 0; j <= lengthSteps; j++) {
    const t = sliceMap ? sliceMap[j] : j / lengthSteps;

    // Check if this slice is a subdomain boundary
    let zOffset = 0;
    if (subdomainSlices) {
      const sdIdx = subdomainSlices.indexOf(j);
      if (sdIdx !== -1) {
        const offset = interfaceOffset ? (interfaceOffset[sdIdx] || 0) : 0;
        const draw = interfaceDraw ? (interfaceDraw[sdIdx] || 0) : 0;
        // For visualization, we'll combine offset and draw into a protrusion
        zOffset = offset + draw;
      }
    }

    for (let i = 0; i < ringCount; i++) {
      const p = angleList[i];
      const tmax = params.type === 'R-OSSE'
        ? (params.tmax === undefined ? 1.0 : evalParam(params.tmax, p))
        : 1.0;
      const tActual = params.type === 'R-OSSE' ? t * tmax : t;

      let profile;
      if (params.type === 'R-OSSE') {
        profile = calculateROSSE(tActual, p, params);
      } else {
        const L = evalParam(params.L, p);
        const extLen = Math.max(0, evalParam(params.throatExtLength || 0, p));
        const slotLen = Math.max(0, evalParam(params.slotLength || 0, p));
        const totalLength = L + extLen + slotLen;
        profile = calculateOSSE(tActual * totalLength, p, params);
        const h = params.h === undefined ? 0 : evalParam(params.h, p);
        if (h > 0) {
          profile.y += h * Math.sin(tActual * Math.PI);
        }
      }

      let x = profile.x;
      let r = profile.y;

      // Apply morphing (only affects OSSE radius)
      const morphTargetInfo = morphTargets ? morphTargets[j] : null;
      r = applyMorphing(r, t, p, params, morphTargetInfo);

      const vx = r * Math.cos(p);
      const vy = x + zOffset; // axial position (Y axis) + interface offset
      const vz = r * Math.sin(p) + verticalOffset; // vertical offset (Z axis)

      vertices.push(vx, vy, vz);
    }
  }

  // Add Rollback for R-OSSE
  if (params.type === 'R-OSSE' && params.rollback) {
    addRollbackGeometry(vertices, indices, params, lengthSteps, angleList, quadrantInfo.fullCircle);
  }

  // Add Enclosure for OSSE
  if (includeEnclosure && params.encDepth > 0) {
    addEnclosureGeometry(vertices, indices, params, verticalOffset, quadrantInfo, groupInfo, ringCount, angleList);
  } else if (includeRearShape && params.rearShape !== 0) {
    addRearShapeGeometry(vertices, indices, params, lengthSteps, angleList, quadrantInfo.fullCircle);
  }

  // Generate indices for the main horn body
  // For partial meshes, don't wrap around
  const indexRadialSteps = quadrantInfo.fullCircle ? ringCount : Math.max(0, ringCount - 1);
  for (let j = 0; j < lengthSteps; j++) {
    for (let i = 0; i < indexRadialSteps; i++) {
      const row1 = j * ringCount;
      const row2 = (j + 1) * ringCount;
      const i2 = quadrantInfo.fullCircle ? (i + 1) % ringCount : i + 1;

      indices.push(row1 + i, row1 + i2, row2 + i2);
      indices.push(row1 + i, row2 + i2, row2 + i);
    }
  }

  // Validate mesh integrity before returning
  const vertexCount = vertices.length / 3;
  const maxIndex = Math.max(...indices);
  if (maxIndex >= vertexCount) {
    console.error(`[MeshBuilder] Invalid mesh generated: max index ${maxIndex} >= vertex count ${vertexCount}`);
    console.error(`[MeshBuilder] Parameters: lengthSteps=${lengthSteps}, radialSteps=${radialSteps}, type=${params.type}`);
    console.error(`[MeshBuilder] Rollback enabled: ${params.rollback}, RearShape: ${params.rearShape}`);
  }

  const result = { vertices, indices, ringCount, fullCircle: quadrantInfo.fullCircle };
  if (groupInfo) {
    result.groups = groupInfo;
  }
  return result;
}
// End of file
