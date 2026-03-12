import { orientMeshConsistently } from '../meshIntegrity.js';
import { validateMeshQuality } from '../quality.js';
import { DEFAULTS, MORPH_TARGETS } from './constants.js';
import { buildAngleList } from './mesh/angles.js';
import { addEnclosureGeometry } from './mesh/enclosure.js';
import { addFreestandingWallGeometry } from './mesh/freestandingWall.js';
import {
  buildMorphTargets,
  computeAdaptivePhiCounts,
  computeMouthExtents,
  createAdaptiveFanIndices,
  createAdaptiveRingVertices,
  createHornIndices,
  createRingVertices
} from './mesh/horn.js';
import { buildSliceMap } from './mesh/sliceMap.js';
import { generateThroatSource } from './mesh/source.js';

function clampSegmentCount(value, fallback, min) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < min) return fallback;
  return Math.round(n);
}

function logQualityIssues(quality) {
  if (quality.degenerateTriangles > 0) {
    console.error(`[Geometry] Degenerate triangles detected: ${quality.degenerateTriangles}`);
  }
  if (quality.nonManifoldEdges > 0) {
    console.error(`[Geometry] Non-manifold edges detected: ${quality.nonManifoldEdges}`);
  }
}

function resolveOuterBuildMode(params, options = {}) {
  const encDepth = Number(params.encDepth || 0);
  const wallThickness = Number(params.wallThickness || 0);
  const enclosureRequested = encDepth > 0;
  const enclosureEnabled = options.includeEnclosure !== false;

  if (params.type === 'R-OSSE' && enclosureRequested) {
    throw new Error(
      'R-OSSE enclosure is not supported by the default geometry contract. Use OSSE for enclosures or set encDepth=0.'
    );
  }

  if (enclosureRequested && enclosureEnabled) return 'enclosure';
  if (encDepth <= 0 && wallThickness > 0) return 'freestandingWall';
  return 'bare';
}

export function buildWaveguideMesh(params, options = {}) {
  const angularSegments = clampSegmentCount(params.angularSegments, DEFAULTS.ANGULAR_SEGMENTS, 4);
  const lengthSteps = clampSegmentCount(params.lengthSegments, DEFAULTS.LENGTH_SEGMENTS, 1);

  const includeEnclosure = options.includeEnclosure !== false;
  const groupInfo = options.groupInfo ?? (options.collectGroups ? {} : null);

  const meshParams = {
    ...params,
    angularSegments,
    lengthSegments: lengthSteps
  };

  const profileContext = {
    coverageCache: new Map()
  };

  const sliceMap = buildSliceMap(meshParams, lengthSteps);
  const mouthExtents = computeMouthExtents(meshParams, profileContext);

  const angleListData = buildAngleList(meshParams, mouthExtents);
  const angleList = angleListData.fullAngles;
  const ringCount = angleList.length;
  const fullCircle = true;

  const morphTarget = Number(meshParams.morphTarget || MORPH_TARGETS.NONE);
  const needsMorphTargets = morphTarget !== MORPH_TARGETS.NONE
    && (!meshParams.morphWidth || !meshParams.morphHeight);
  const morphTargets = needsMorphTargets
    ? buildMorphTargets(meshParams, lengthSteps, angleList, sliceMap, profileContext)
    : null;

  // Adaptive phi: only when the caller explicitly opts in AND the geometry is a plain
  // full-circle horn (no enclosure/wall). Enclosure/wall functions assume uniform ring
  // topology. ABEC/simulation exports rely on a consistent ringCount and must NOT opt in.
  const outerBuildMode = resolveOuterBuildMode(meshParams, { includeEnclosure });
  const hasEnclosure = outerBuildMode === 'enclosure';
  const hasWall = outerBuildMode === 'freestandingWall';
  const useAdaptivePhi = (options.adaptivePhi === true)
    && fullCircle
    && !hasEnclosure
    && !hasWall;

  let vertices;
  let indices;
  let mouthRingCount; // phi count of the outermost (mouth) ring
  let throatRingCount; // phi count of the innermost (throat) ring

  if (useAdaptivePhi) {
    const phiCounts = computeAdaptivePhiCounts(
      meshParams, lengthSteps, sliceMap, angularSegments, profileContext
    );
    vertices = createAdaptiveRingVertices(
      meshParams, sliceMap, morphTargets, phiCounts, lengthSteps, profileContext
    );
    indices = createAdaptiveFanIndices(phiCounts, lengthSteps);
    mouthRingCount = phiCounts[lengthSteps];
    throatRingCount = phiCounts[0];
  } else {
    vertices = createRingVertices(
      meshParams, sliceMap, angleList, morphTargets, ringCount, lengthSteps, profileContext
    );
    indices = createHornIndices(ringCount, lengthSteps, fullCircle);
    mouthRingCount = ringCount;
    throatRingCount = ringCount;
  }

  const hornEndTri = indices.length / 3;
  if (groupInfo) {
    groupInfo.horn = { start: 0, end: hornEndTri };
    groupInfo[hasEnclosure ? 'horn_wall' : 'inner_wall'] = { start: 0, end: hornEndTri };
  }

  if (hasEnclosure) {
    addEnclosureGeometry(
      vertices,
      indices,
      meshParams,
      0,
      null,
      groupInfo,
      mouthRingCount,
      angleList
    );
  } else if (hasWall) {
    addFreestandingWallGeometry(vertices, indices, meshParams, {
      ringCount: mouthRingCount,
      lengthSteps,
      fullCircle,
      groupInfo
    });
  }

  const sourceStartTri = indices.length / 3;
  const throatSource = generateThroatSource(vertices, throatRingCount, fullCircle);
  if (throatSource.center) {
    const centerIdx = vertices.length / 3;
    vertices.push(...throatSource.center);
    for (const [a, b] of throatSource.edges) {
      indices.push(centerIdx, a, b);
    }
  }
  const sourceEndTri = indices.length / 3;

  if (groupInfo && sourceEndTri > sourceStartTri) {
    groupInfo.source = { start: sourceStartTri, end: sourceEndTri };
    groupInfo.throat_disc = { start: sourceStartTri, end: sourceEndTri };
  }

  const vertexCount = vertices.length / 3;
  const maxIndex = Math.max(...indices, -1);
  if (maxIndex >= vertexCount) {
    console.error(`[Geometry] Invalid mesh generated: max index ${maxIndex} >= vertex count ${vertexCount}`);
  }

  if (options.useLegacyOrientationRepair === true || meshParams.useLegacyOrientationRepair === true) {
    orientMeshConsistently(vertices, indices, {
      preferOutward: fullCircle
    });
  }

  const quality = validateMeshQuality(vertices, indices, groupInfo);
  logQualityIssues(quality);

  const result = {
    vertices,
    indices,
    ringCount: mouthRingCount,
    fullCircle
  };

  if (groupInfo) {
    result.groups = groupInfo;
  }

  return result;
}

export const buildHornMesh = buildWaveguideMesh;
