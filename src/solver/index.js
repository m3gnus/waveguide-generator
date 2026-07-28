// @ts-check

/**
 * BEM Solver Interface
 *
 * ╔════════════════════════════════════════════════════════════════════════════╗
 * ║  Backend-only solver client                                                 ║
 * ╠════════════════════════════════════════════════════════════════════════════╣
 * ║  submitSimulation/getJobStatus/getResults call the Python backend API.     ║
 * ║                                                                            ║
 * ║  Runtime requirement: backend running at localhost:8000 (default)          ║
 * ╚════════════════════════════════════════════════════════════════════════════╝
 *
 * This module provides the public API for BEM acoustic simulations.
 * It handles communication with the Python backend and manages
 * the simulation pipeline from geometry to acoustic results.
 */

import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';
import { createNetworkApiError, parseApiErrorResponse, parseApiJsonResponse } from './apiErrors.js';

const DEFAULT_TIMEOUT_MS = 30000;
const HEALTH_CHECK_TIMEOUT_MS = 5000;
const VALID_JOB_STATUSES = new Set(['queued', 'running', 'complete', 'error', 'cancelled']);
const VALID_STOP_STATUSES = new Set(['cancelled', 'cancelling']);

function createAbortController(timeoutMs) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  return { controller, timeoutId };
}

/**
 * @typedef {Object} CanonicalMeshPayload
 * @property {number[]} vertices
 * @property {number[]} indices
 * @property {number[]} surfaceTags
 * @property {string} format
 * @property {Record<string, unknown>} boundaryConditions
 * @property {Record<string, unknown>} [metadata]
 */

/**
 * @typedef {Object} SimulationPreflightConfig
 * @property {number|string} frequencyStart
 * @property {number|string} frequencyEnd
 * @property {number|string} numFrequencies
 * @property {number|string} [simulationType]
 * @property {Record<string, unknown>|null} [polarConfig]
 * @property {'strict'|'warn'|'off'} [meshValidationMode]
 * @property {'linear'|'log'} [frequencySpacing]
 * @property {'auto'|'metal'|'bempp'} [solverBackend]
 * @property {'full_3d'|'circsym'|'auto'} [solverMode]
 * @property {boolean} [verbose]
 */

/**
 * @typedef {Object} BemSimulationConfig
 * @property {number} frequencyStart - Start frequency in Hz
 * @property {number} frequencyEnd - End frequency in Hz
 * @property {number} numFrequencies - Number of frequencies to simulate
 * @property {string} simulationType - Type of simulation (1: infinite baffle, 2: free-standing)
 * @property {Object} boundaryConditions - Boundary condition settings
 */

/**
 * @typedef {Object} BemSimulationResult
 * @property {number[]} frequencies - Array of frequencies in Hz
 * @property {Object} directivity - Directivity data (horizontal, vertical, diagonal)
 * @property {Object} impedance - Impedance data (real, imaginary)
 * @property {Object} splOnAxis - SPL on-axis data
 * @property {Object} di - Directivity Index data
 */

/**
 * Main BEM solver API (Phase 4.1)
 */
/**
 * Validate canonical solver mesh payload shape before backend submission.
 * @param {CanonicalMeshPayload} meshData
 * @returns {true}
 */
export function validateCanonicalMeshPayload(meshData) {
  if (!meshData || typeof meshData !== 'object') {
    throw new Error('Invalid mesh payload: expected object.');
  }
  if (!Array.isArray(meshData.vertices) || !Array.isArray(meshData.indices)) {
    throw new Error('Invalid mesh payload: missing vertices/indices arrays.');
  }
  if (meshData.vertices.length % 3 !== 0) {
    throw new Error('Invalid mesh payload: vertices length must be divisible by 3.');
  }
  if (meshData.indices.length % 3 !== 0) {
    throw new Error('Invalid mesh payload: indices length must be divisible by 3.');
  }
  if (!Array.isArray(meshData.surfaceTags)) {
    throw new Error('Invalid mesh payload: missing surfaceTags array.');
  }
  if (meshData.surfaceTags.length !== meshData.indices.length / 3) {
    throw new Error('Invalid mesh payload: surfaceTags length must match triangle count.');
  }
  if (!meshData.format) {
    throw new Error('Invalid mesh payload: missing format.');
  }
  if (typeof meshData.boundaryConditions !== 'object' || meshData.boundaryConditions === null) {
    throw new Error('Invalid mesh payload: missing boundaryConditions object.');
  }
  return true;
}

/**
 * Validate simulation inputs before sending any backend request.
 * @param {SimulationPreflightConfig} config
 * @param {CanonicalMeshPayload} meshData
 */
export function validateSimulationPreflight(config, meshData) {
  validateCanonicalMeshPayload(meshData);

  const frequencyStart = Number(config?.frequencyStart);
  const frequencyEnd = Number(config?.frequencyEnd);
  const numFrequencies = Number(config?.numFrequencies);

  if (!Number.isFinite(frequencyStart) || !Number.isFinite(frequencyEnd)) {
    throw new Error('Simulation preflight failed: frequency range must contain valid numbers.');
  }
  if (frequencyStart >= frequencyEnd) {
    throw new Error(
      'Simulation preflight failed: start frequency must be less than end frequency.'
    );
  }
  if (!Number.isFinite(numFrequencies) || numFrequencies < 1) {
    throw new Error('Simulation preflight failed: numFrequencies must be at least 1.');
  }
  if (!meshData.surfaceTags.includes(2)) {
    throw new Error(
      'Simulation preflight failed: source surface tag (2) missing from mesh payload.'
    );
  }
}

/**
 * @param {string} url
 * @param {RequestInit|undefined} options
 * @param {string} operation
 * @param {number} [timeoutMs]
 * @returns {Promise<Response>}
 */
async function fetchOrApiError(url, options, operation, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const { controller, timeoutId } = createAbortController(timeoutMs);

  let response;
  try {
    response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
  } catch (error) {
    clearTimeout(timeoutId);
    throw createNetworkApiError(operation, error);
  }

  clearTimeout(timeoutId);

  if (!response.ok) {
    throw await parseApiErrorResponse(response, { operation });
  }

  return response;
}

const VALID_MESH_VALIDATION_MODES = new Set(['strict', 'warn', 'off']);
const VALID_FREQUENCY_SPACING = new Set(['linear', 'log']);
const VALID_SOLVER_BACKENDS = new Set(['auto', 'metal', 'bempp']);
const VALID_SOLVER_MODES = new Set(['full_3d', 'circsym', 'auto']);

function assignEnumSetting(payload, key, value, allowedValues) {
  if (typeof value !== 'string') {
    return;
  }
  const normalized = value.trim().toLowerCase();
  if (!normalized || !allowedValues.has(normalized)) {
    return;
  }
  payload[key] = normalized;
}

function assignBooleanSetting(payload, key, value) {
  if (typeof value === 'boolean') {
    payload[key] = value;
  }
}

function validateObjectResponse(payload, responseName) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return `Backend returned an invalid ${responseName} response.`;
  }
  return '';
}

function validateSubmitResponse(payload) {
  const objectError = validateObjectResponse(payload, 'simulation submission');
  if (objectError) return objectError;
  if (typeof payload.job_id !== 'string' || !payload.job_id.trim()) {
    return 'Backend simulation submission response is missing a valid job_id.';
  }
  return '';
}

function validateJobStatusResponse(payload) {
  const objectError = validateObjectResponse(payload, 'simulation status');
  if (objectError) return objectError;
  if (!VALID_JOB_STATUSES.has(payload.status)) {
    return 'Backend simulation status response contains an invalid status.';
  }
  if (!Number.isFinite(payload.progress) || payload.progress < 0 || payload.progress > 1) {
    return 'Backend simulation status response contains invalid progress.';
  }
  return '';
}

function validateJobListResponse(payload) {
  const objectError = validateObjectResponse(payload, 'simulation job list');
  if (objectError) return objectError;
  if (
    !Array.isArray(payload.items) ||
    payload.items.some(
      (item) =>
        !item ||
        typeof item !== 'object' ||
        Array.isArray(item) ||
        typeof item.id !== 'string' ||
        !item.id.trim()
    )
  ) {
    return 'Backend simulation job list response contains invalid items.';
  }
  if (!Number.isInteger(payload.total) || payload.total < 0) {
    return 'Backend simulation job list response contains an invalid total.';
  }
  return '';
}

function validateStopResponse(payload) {
  const objectError = validateObjectResponse(payload, 'simulation stop');
  if (objectError) return objectError;
  if (!VALID_STOP_STATUSES.has(payload.status)) {
    return 'Backend simulation stop response contains an invalid status.';
  }
  return '';
}

function validateDeleteResponse(payload, jobId) {
  const objectError = validateObjectResponse(payload, 'simulation deletion');
  if (objectError) return objectError;
  if (payload.deleted !== true || payload.job_id !== jobId) {
    return 'Backend simulation deletion response does not confirm the requested job.';
  }
  return '';
}

function validateClearFailedResponse(payload) {
  const objectError = validateObjectResponse(payload, 'failed-job deletion');
  if (objectError) return objectError;
  if (
    typeof payload.deleted !== 'boolean' ||
    !Number.isInteger(payload.deleted_count) ||
    payload.deleted_count < 0 ||
    !Array.isArray(payload.deleted_ids) ||
    payload.deleted_ids.some((jobId) => typeof jobId !== 'string' || !jobId.trim()) ||
    payload.deleted_ids.length !== payload.deleted_count
  ) {
    return 'Backend failed-job deletion response contains invalid deletion details.';
  }
  return '';
}

export class BemSolver {
  constructor() {
    /** @type {string} */
    this.backendUrl = DEFAULT_BACKEND_URL;
    /** @type {boolean} */
    this.isConnected = false;
  }

  /**
   * Fetch backend health payload.
   */
  async getHealthStatus() {
    const response = await fetchOrApiError(
      `${this.backendUrl}/health`,
      undefined,
      'Health check',
      HEALTH_CHECK_TIMEOUT_MS
    );
    return await parseApiJsonResponse(response, {
      operation: 'Health check',
      validate: (payload) => validateObjectResponse(payload, 'health check'),
    });
  }

  /**
   * Check if backend meshing and a BEM solver runtime are ready.
   */
  async checkConnection() {
    try {
      const health = await this.getHealthStatus();
      const solverReady = Boolean(
        health?.solverReady ||
        health?.solverBackends?.metal?.ready ||
        health?.solverBackends?.bempp?.ready
      );
      return solverReady && Boolean(health?.mesherReady);
    } catch {
      return false;
    }
  }

  /**
   * Submit a horn geometry for BEM simulation
   * @param {SimulationPreflightConfig} config
   * @param {CanonicalMeshPayload} meshData
   * @param {Record<string, unknown>} [options]
   * @returns {Promise<string>}
   */
  async submitSimulation(config, meshData, options = {}) {
    validateSimulationPreflight(config, meshData);

    const payload = {
      mesh: {
        vertices: meshData.vertices,
        indices: meshData.indices,
        surfaceTags: meshData.surfaceTags,
        format: meshData.format || 'msh',
        boundaryConditions: meshData.boundaryConditions || {},
        metadata: meshData.metadata || {},
      },
      frequency_range: [config.frequencyStart, config.frequencyEnd],
      num_frequencies: config.numFrequencies,
      sim_type: String(config.simulationType ?? '2'),
      options: options,
      polar_config: config.polarConfig || null,
    };

    assignEnumSetting(
      payload,
      'mesh_validation_mode',
      config.meshValidationMode,
      VALID_MESH_VALIDATION_MODES
    );
    assignEnumSetting(
      payload,
      'frequency_spacing',
      config.frequencySpacing,
      VALID_FREQUENCY_SPACING
    );
    assignEnumSetting(payload, 'solver_backend', config.solverBackend, VALID_SOLVER_BACKENDS);
    assignEnumSetting(payload, 'solver_mode', config.solverMode, VALID_SOLVER_MODES);
    assignBooleanSetting(payload, 'verbose', config.verbose);

    const response = await fetchOrApiError(
      `${this.backendUrl}/api/solve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
      'Submit simulation'
    );

    const result = await parseApiJsonResponse(response, {
      operation: 'Submit simulation',
      validate: validateSubmitResponse,
    });
    return result.job_id;
  }

  /**
   * Check the status of a simulation job
   * @param {string} jobId
   */
  async getJobStatus(jobId) {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/status/${jobId}`,
      undefined,
      'Fetch simulation status'
    );

    return await parseApiJsonResponse(response, {
      operation: 'Fetch simulation status',
      validate: validateJobStatusResponse,
    });
  }

  /**
   * Retrieve simulation results
   * @param {string} jobId
   */
  async getResults(jobId) {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/results/${jobId}`,
      undefined,
      'Fetch simulation results'
    );

    return await parseApiJsonResponse(response, {
      operation: 'Fetch simulation results',
      validate: (payload) => validateObjectResponse(payload, 'simulation results'),
    });
  }

  /**
   * Retrieve mesh artifact text (.msh) generated for a completed simulation.
   * @param {string} jobId
   */
  async getMeshArtifact(jobId) {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/mesh-artifact/${jobId}`,
      undefined,
      'Fetch simulation mesh artifact'
    );

    return await response.text();
  }

  /**
   * List simulation jobs with optional filtering and pagination.
   * @param {{ status?: string|null, limit?: number, offset?: number }} [query]
   */
  async listJobs({ status = null, limit = 50, offset = 0 } = {}) {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    if (typeof status === 'string' && status.trim()) {
      params.set('status', status.trim());
    }

    const response = await fetchOrApiError(
      `${this.backendUrl}/api/jobs?${params.toString()}`,
      undefined,
      'List simulation jobs'
    );
    return await parseApiJsonResponse(response, {
      operation: 'List simulation jobs',
      validate: validateJobListResponse,
    });
  }

  /**
   * Request cancellation of a queued/running simulation job.
   * @param {string} jobId
   */
  async stopJob(jobId) {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/stop/${jobId}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      },
      'Stop simulation'
    );
    return await parseApiJsonResponse(response, {
      operation: 'Stop simulation',
      validate: validateStopResponse,
    });
  }

  /**
   * Delete terminal job metadata/results/artifacts.
   * @param {string} jobId
   */
  async deleteJob(jobId) {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/jobs/${jobId}`,
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
      },
      'Delete simulation job'
    );
    return await parseApiJsonResponse(response, {
      operation: 'Delete simulation job',
      validate: (payload) => validateDeleteResponse(payload, jobId),
    });
  }

  /**
   * Delete all failed jobs (status=error) from backend persistence.
   */
  async clearFailedJobs() {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/jobs/clear-failed`,
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
      },
      'Clear failed simulation jobs'
    );
    return await parseApiJsonResponse(response, {
      operation: 'Clear failed simulation jobs',
      validate: validateClearFailedResponse,
    });
  }
}
