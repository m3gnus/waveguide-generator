import { DesignModule } from '../design/index.js';
import { SimulationModule } from './index.js';
import { FACE_IDENTITY_ORDER, countFaceIdentityTriangles } from '../../geometry/tags.js';

const CANONICAL_TAG_ORDER = Object.freeze([1, 2, 3, 4]);

function createSimulationDesignTask(state) {
  return DesignModule.task(
    DesignModule.importState(state, {
      applyVerticalOffset: true
    })
  );
}

export function prepareCanonicalSimulationMesh(state) {
  const designTask = createSimulationDesignTask(state);
  const preparedParams = DesignModule.output.simulationParams(designTask);
  const simulationTask = SimulationModule.task(SimulationModule.importDesign(designTask), {
    includeEnclosure: Number(preparedParams.encDepth || 0) > 0,
    adaptivePhi: false
  });
  const payload = SimulationModule.output.mesh(simulationTask);

  const vertexCount = payload.vertices.length / 3;
  const maxIndex = Math.max(...payload.indices);
  if (maxIndex >= vertexCount) {
    throw new Error(
      `Invalid mesh: max index ${maxIndex} >= vertex count ${vertexCount}. This indicates simulation mesh corruption.`
    );
  }

  return payload;
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
  const tagCounts = Object.fromEntries(CANONICAL_TAG_ORDER.map((tag) => [tag, 0]));
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
    warnings.push(`Unsupported surface tags present: ${Array.from(unsupportedTags).sort((a, b) => a - b).join(', ')}.`);
  }

  return {
    vertexCount,
    triangleCount,
    tagCounts,
    identityTriangleCounts,
    warnings,
    ok: warnings.length === 0
  };
}

export function prepareOccAdaptiveSolveRequest(state, options = {}) {
  const designTask = createSimulationDesignTask(state);
  const preparedParams = DesignModule.output.simulationParams(designTask);
  const simulationInput = SimulationModule.importDesign(designTask);

  const { waveguidePayload, submitOptions } = SimulationModule.output.occAdaptive(simulationInput, {
    mshVersion: options.mshVersion || '2.2',
    simType: options.simType ?? 2
  });

  return {
    waveguidePayload,
    submitOptions,
    preparedParams,
    stateSnapshot: JSON.parse(JSON.stringify(state))
  };
}

export function createSimulationClient() {
  return SimulationModule.output.client();
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
