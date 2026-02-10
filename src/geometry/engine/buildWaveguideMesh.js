import { parseQuadrants } from '../common.js';
import { validateMeshQuality } from '../quality.js';
import { DEFAULTS, MORPH_TARGETS } from './constants.js';
import { buildAngleList, selectAnglesForQuadrants } from './mesh/angles.js';
import { addEnclosureGeometry } from './mesh/enclosure.js';
import { addFreestandingWallGeometry } from './mesh/freestandingWall.js';
import {
  buildMorphTargets,
  computeMouthExtents,
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

  const quadrantInfo = parseQuadrants(meshParams.quadrants);
  const angleListData = buildAngleList(meshParams, mouthExtents);
  const angleList = selectAnglesForQuadrants(angleListData.fullAngles, meshParams.quadrants);
  const ringCount = angleList.length;

  const morphTarget = Number(meshParams.morphTarget || MORPH_TARGETS.NONE);
  const needsMorphTargets = meshParams.type === 'OSSE'
    && morphTarget !== MORPH_TARGETS.NONE
    && (!meshParams.morphWidth || !meshParams.morphHeight);
  const morphTargets = needsMorphTargets
    ? buildMorphTargets(meshParams, lengthSteps, angleList, sliceMap, profileContext)
    : null;

  const vertices = createRingVertices(
    meshParams,
    sliceMap,
    angleList,
    morphTargets,
    ringCount,
    lengthSteps,
    profileContext
  );

  const indices = createHornIndices(ringCount, lengthSteps, quadrantInfo.fullCircle);

  const hornEndTri = indices.length / 3;
  if (groupInfo) {
    groupInfo.horn = { start: 0, end: hornEndTri };
  }

  if (includeEnclosure && Number(meshParams.encDepth || 0) > 0) {
    addEnclosureGeometry(
      vertices,
      indices,
      meshParams,
      0,
      quadrantInfo,
      groupInfo,
      ringCount,
      angleList
    );
  } else if (Number(meshParams.encDepth || 0) <= 0 && Number(meshParams.wallThickness || 0) > 0) {
    addFreestandingWallGeometry(vertices, indices, meshParams, {
      ringCount,
      lengthSteps,
      fullCircle: quadrantInfo.fullCircle,
      groupInfo
    });
  }

  const sourceStartTri = indices.length / 3;
  const hasThroatDisk = meshParams.type !== 'R-OSSE' && meshParams.type !== 'OSSE';
  if (hasThroatDisk) {
    const throatSource = generateThroatSource(vertices, ringCount, quadrantInfo.fullCircle);
    if (throatSource.center) {
      const centerIdx = vertices.length / 3;
      vertices.push(...throatSource.center);
      for (const [a, b] of throatSource.edges) {
        indices.push(centerIdx, a, b);
      }
    }
  }
  const sourceEndTri = indices.length / 3;

  if (groupInfo && sourceEndTri > sourceStartTri) {
    groupInfo.source = { start: sourceStartTri, end: sourceEndTri };
  }

  const vertexCount = vertices.length / 3;
  const maxIndex = Math.max(...indices, -1);
  if (maxIndex >= vertexCount) {
    console.error(`[Geometry] Invalid mesh generated: max index ${maxIndex} >= vertex count ${vertexCount}`);
  }

  const quality = validateMeshQuality(vertices, indices, groupInfo);
  logQualityIssues(quality);

  const result = {
    vertices,
    indices,
    ringCount,
    fullCircle: quadrantInfo.fullCircle
  };

  if (groupInfo) {
    result.groups = groupInfo;
  }

  return result;
}

export const buildHornMesh = buildWaveguideMesh;
