import { DesignModule } from '../design/index.js';
import { BemSolver } from '../../solver/index.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';
import { FACE_IDENTITY_ORDER, countFaceIdentityTriangles } from '../../geometry/tags.js';

const CANONICAL_TAG_ORDER = Object.freeze([1, 2, 3, 4]);
const HORNLAB_SOLVE_CONTRACT_MESH = Object.freeze({
  vertices: Object.freeze([0, 0, 0, 1, 0, 0, 0, 1, 0]),
  indices: Object.freeze([0, 1, 2]),
  surfaceTags: Object.freeze([2]),
  format: 'msh',
  boundaryConditions: Object.freeze({
    throat: Object.freeze({ type: 'velocity', surfaceTag: 2, value: 1.0 }),
    wall: Object.freeze({ type: 'neumann', surfaceTag: 1, value: 0.0 }),
    mouth: Object.freeze({ type: 'robin', surfaceTag: 1, impedance: 'spherical' }),
  }),
  metadata: Object.freeze({
    source: 'hornlab_mesher_contract_placeholder',
    viewportOnly: false,
  }),
});

function normalizeIdentityTriangleCounts(rawCounts = {}) {
  const counts = Object.fromEntries(FACE_IDENTITY_ORDER.map((identity) => [identity, 0]));
  if (!rawCounts || typeof rawCounts !== 'object') {
    return counts;
  }
  for (const identity of FACE_IDENTITY_ORDER) {
    const rawCount = Number(rawCounts[identity]);
    if (Number.isFinite(rawCount) && rawCount >= 0) {
      counts[identity] = Math.floor(rawCount);
    }
  }
  return counts;
}

function normalizeTagCounts(rawCounts = {}) {
  const counts = Object.fromEntries(CANONICAL_TAG_ORDER.map((tag) => [tag, 0]));
  if (!rawCounts || typeof rawCounts !== 'object') {
    return counts;
  }
  for (const tag of CANONICAL_TAG_ORDER) {
    const rawCount = Number(rawCounts[tag] ?? rawCounts[String(tag)]);
    if (Number.isFinite(rawCount) && rawCount >= 0) {
      counts[tag] = Math.floor(rawCount);
    }
  }
  return counts;
}

function createSimulationDesignTask(state) {
  return DesignModule.task(
    DesignModule.importState(state, {
      applyVerticalOffset: true,
    })
  );
}

function resolveSimulationType(options = {}, meshParams = {}, preparedParams = {}) {
  if (Object.prototype.hasOwnProperty.call(options, 'simType')) {
    return options.simType;
  }
  return meshParams.simType ?? preparedParams.simType ?? 2;
}

export function prepareHornlabSolveContractMesh() {
  return {
    vertices: Array.from(HORNLAB_SOLVE_CONTRACT_MESH.vertices),
    indices: Array.from(HORNLAB_SOLVE_CONTRACT_MESH.indices),
    surfaceTags: Array.from(HORNLAB_SOLVE_CONTRACT_MESH.surfaceTags),
    format: HORNLAB_SOLVE_CONTRACT_MESH.format,
    boundaryConditions: {
      throat: { ...HORNLAB_SOLVE_CONTRACT_MESH.boundaryConditions.throat },
      wall: { ...HORNLAB_SOLVE_CONTRACT_MESH.boundaryConditions.wall },
      mouth: { ...HORNLAB_SOLVE_CONTRACT_MESH.boundaryConditions.mouth },
    },
    metadata: { ...HORNLAB_SOLVE_CONTRACT_MESH.metadata },
  };
}

export function summarizeCanonicalSimulationMesh(meshData = {}) {
  const vertices = Array.isArray(meshData?.vertices) ? meshData.vertices : [];
  const indices = Array.isArray(meshData?.indices) ? meshData.indices : [];
  const surfaceTags = Array.isArray(meshData?.surfaceTags) ? meshData.surfaceTags : [];
  const triangleCount = Math.floor(indices.length / 3);
  const warnings = [];

  if (vertices.length % 3 !== 0) {
    warnings.push('Vertex array length is not divisible by 3.');
  }
  if (indices.length % 3 !== 0) {
    warnings.push('Triangle index array length is not divisible by 3.');
  }

  const vertexCount = Math.floor(vertices.length / 3);
  const tagCounts = normalizeTagCounts();
  const identityTriangleCounts = countFaceIdentityTriangles(meshData?.groups, triangleCount);
  const metadataIdentityTriangleCounts = meshData?.metadata?.identityTriangleCounts;
  const unsupportedTags = new Set();

  if (metadataIdentityTriangleCounts && typeof metadataIdentityTriangleCounts === 'object') {
    for (const identity of FACE_IDENTITY_ORDER) {
      const rawCount = Number(metadataIdentityTriangleCounts[identity]);
      if (Number.isFinite(rawCount) && rawCount >= 0) {
        identityTriangleCounts[identity] = Math.floor(rawCount);
      }
    }
  }

  for (const rawTag of surfaceTags) {
    const tag = Number(rawTag);
    if (Object.hasOwn(tagCounts, tag)) {
      tagCounts[tag] += 1;
    } else {
      unsupportedTags.add(tag);
    }
  }

  if (surfaceTags.length !== triangleCount) {
    warnings.push(
      `Surface tag count ${surfaceTags.length} does not match triangle count ${triangleCount}.`
    );
  }
  if (tagCounts[2] === 0) {
    warnings.push('Source surface tag (2) missing from the canonical simulation mesh.');
  }
  if (unsupportedTags.size > 0) {
    warnings.push(
      `Unsupported surface tags present: ${Array.from(unsupportedTags)
        .sort((a, b) => a - b)
        .join(', ')}.`
    );
  }

  return {
    vertexCount,
    triangleCount,
    tagCounts,
    identityTriangleCounts,
    warnings,
    ok: warnings.length === 0,
  };
}

export function summarizePersistedSimulationMeshStats(meshStats = {}) {
  const warnings = [];
  const rawVertexCount = Number(meshStats?.vertexCount ?? meshStats?.vertex_count);
  const rawTriangleCount = Number(meshStats?.triangleCount ?? meshStats?.triangle_count);
  const vertexCount =
    Number.isFinite(rawVertexCount) && rawVertexCount >= 0 ? Math.floor(rawVertexCount) : 0;
  const triangleCount =
    Number.isFinite(rawTriangleCount) && rawTriangleCount >= 0 ? Math.floor(rawTriangleCount) : 0;

  if (!Number.isFinite(rawVertexCount) || rawVertexCount < 0) {
    warnings.push('Backend mesh diagnostics reported an invalid vertex count.');
  }
  if (!Number.isFinite(rawTriangleCount) || rawTriangleCount < 0) {
    warnings.push('Backend mesh diagnostics reported an invalid triangle count.');
  }

  const tagCounts = normalizeTagCounts(meshStats?.tagCounts ?? meshStats?.tag_counts);
  const identityTriangleCounts = normalizeIdentityTriangleCounts(
    meshStats?.identityTriangleCounts ?? meshStats?.identity_triangle_counts
  );

  if (tagCounts[2] === 0) {
    warnings.push('Backend mesh diagnostics report no source surface tag (2).');
  }
  if (!Object.values(identityTriangleCounts).some((count) => count > 0)) {
    warnings.push('Backend mesh face-identity diagnostics are unavailable for this job.');
  }

  return {
    vertexCount,
    triangleCount,
    tagCounts,
    identityTriangleCounts,
    warnings,
    ok: warnings.length === 0,
    provenance: 'backend',
  };
}

export function prepareHornlabMesherSolveRequest(state, options = {}) {
  const designTask = createSimulationDesignTask(state);
  const preparedParams = DesignModule.output.simulationParams(designTask);
  const meshParams = DesignModule.output.backendMeshSimulationParams(designTask);
  const waveguidePayload = buildWaveguidePayload(meshParams, options.mshVersion || '2.2');
  waveguidePayload.sim_type = resolveSimulationType(options, meshParams, preparedParams);

  return {
    waveguidePayload,
    submitOptions: {
      mesh: {
        strategy: 'hornlab_mesher',
        waveguide_params: waveguidePayload,
      },
    },
    preparedParams,
    stateSnapshot: JSON.parse(JSON.stringify(state)),
  };
}

export function createSimulationClient() {
  return new BemSolver();
}

export function validateSimulationConfig(config = {}) {
  if (!Number.isFinite(config.frequencyStart) || !Number.isFinite(config.frequencyEnd)) {
    return 'Frequency range must contain valid numbers.';
  }
  if (!Number.isFinite(config.numFrequencies) || config.numFrequencies < 1) {
    return 'Number of frequencies must be at least 1.';
  }
  if (config.frequencyStart >= config.frequencyEnd) {
    return 'Start frequency must be less than end frequency.';
  }
  return null;
}
