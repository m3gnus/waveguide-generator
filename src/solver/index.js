/**
 * BEM Solver Interface
 *
 * ╔════════════════════════════════════════════════════════════════════════════╗
 * ║  Backend-first solver client                                                ║
 * ╠════════════════════════════════════════════════════════════════════════════╣
 * ║  submitSimulation/getJobStatus/getResults call the Python backend API.     ║
 * ║  mockBEMSolver is a local fallback helper only and is not physics-based.   ║
 * ║                                                                            ║
 * ║  Runtime requirement: backend running at localhost:8000 (default)          ║
 * ║                                                                            ║
 * ║  Use mock results only for UI/debug validation workflows.                  ║
 * ╚════════════════════════════════════════════════════════════════════════════╝
 *
 * This module provides the public API for BEM acoustic simulations.
 * It handles communication with the Python backend and manages
 * the simulation pipeline from geometry to acoustic results.
 */

import { DEFAULT_BACKEND_URL } from '../config/backendUrl.js';
import { createNetworkApiError, parseApiErrorResponse } from './apiErrors.js';

/**
 * @typedef {Object} BemSimulationConfig
 * @property {number} frequencyStart - Start frequency in Hz
 * @property {number} frequencyEnd - End frequency in Hz
 * @property {number} numFrequencies - Number of frequencies to simulate
 * @property {string} simulationType - Type of simulation (1: infinite baffle, 2: free-standing)
 * @property {Object} boundaryConditions - Boundary condition settings
 */

/**
 * Mock BEM Backend for Validation Only (Phase 4.0)
 *
 * Returns deterministic fake data - NO numerical correctness validation yet
 */
export function mockBEMSolver(meshData) {
  console.warn('[BEM Solver] Backend unavailable: using mock fallback (non-physics) results for UI/debug only.');

  // Generate deterministic fake frequency response (flat for validation)
  const numFrequencies = 50;
  
  return {
    frequencies: Array.from({ length: numFrequencies }, (_, i) => ({
      freqHz: 100 + (i * 50),
    })),
    
    // Flat response for validation - not numerically accurate
    frequencyResponse: Array.from({ length: numFrequencies }, (_, i) => ({
      freqHz: 100 + (i * 50),
      magnitudeDb: -20, // flat response
    })),
    
    directivityPattern: [[0, 30], [90, -15]],
  
};
}

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

export function validateSimulationPreflight(config, meshData) {
  validateCanonicalMeshPayload(meshData);

  const frequencyStart = Number(config?.frequencyStart);
  const frequencyEnd = Number(config?.frequencyEnd);
  const numFrequencies = Number(config?.numFrequencies);

  if (!Number.isFinite(frequencyStart) || !Number.isFinite(frequencyEnd)) {
    throw new Error('Simulation preflight failed: frequency range must contain valid numbers.');
  }
  if (frequencyStart >= frequencyEnd) {
    throw new Error('Simulation preflight failed: start frequency must be less than end frequency.');
  }
  if (!Number.isFinite(numFrequencies) || numFrequencies < 1) {
    throw new Error('Simulation preflight failed: numFrequencies must be at least 1.');
  }
  if (!meshData.surfaceTags.includes(2)) {
    throw new Error('Simulation preflight failed: source surface tag (2) missing from mesh payload.');
  }
}

async function fetchOrApiError(url, options, operation) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw createNetworkApiError(operation, error);
  }

  if (!response.ok) {
    throw await parseApiErrorResponse(response, { operation });
  }

  return response;
}

export class BemSolver {
  constructor() {
    this.backendUrl = DEFAULT_BACKEND_URL;
    this.isConnected = false;
  }

  /**
   * Fetch backend health payload.
   */
  async getHealthStatus() {
    const response = await fetchOrApiError(
      `${this.backendUrl}/health`,
      undefined,
      'Health check'
    );
    return await response.json();
  }

  /**
   * Check if adaptive BEM runtime is fully ready (solver + OCC mesher).
   */
  async checkConnection() {
    try {
      const health = await this.getHealthStatus();
      return Boolean(health?.solverReady) && Boolean(health?.occBuilderReady);
    } catch (error) {
      return false;
    }
  }

  /**
   * Submit a horn geometry for BEM simulation
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
        metadata: meshData.metadata || {}
      },
      frequency_range: [config.frequencyStart, config.frequencyEnd],
      num_frequencies: config.numFrequencies,
      sim_type: String(config.simulationType ?? '2'),
      options: options,
      polar_config: config.polarConfig || null,
      mesh_validation_mode: config.meshValidationMode || 'warn',
      frequency_spacing: config.frequencySpacing || 'log',
      device_mode: config.deviceMode || 'auto'
    };

    const response = await fetchOrApiError(`${this.backendUrl}/api/solve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }, 'Submit simulation');

    const result = await response.json();
    return result.job_id;
  }

  /**
   * Check the status of a simulation job
   */
  async getJobStatus(jobId) {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/status/${jobId}`,
      undefined,
      'Fetch simulation status'
    );

    return await response.json();
  }

  /**
   * Retrieve simulation results
   */
  async getResults(jobId) {
    const response = await fetchOrApiError(
      `${this.backendUrl}/api/results/${jobId}`,
      undefined,
      'Fetch simulation results'
    );

    return await response.json();
  }

  /**
   * List simulation jobs with optional filtering and pagination.
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
    return await response.json();
  }

  /**
   * Request cancellation of a queued/running simulation job.
   */
  async stopJob(jobId) {
    const response = await fetchOrApiError(`${this.backendUrl}/api/stop/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    }, 'Stop simulation');
    return await response.json();
  }

  /**
   * Delete terminal job metadata/results/artifacts.
   */
  async deleteJob(jobId) {
    const response = await fetchOrApiError(`${this.backendUrl}/api/jobs/${jobId}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' }
    }, 'Delete simulation job');
    return await response.json();
  }

  /**
   * Delete all failed jobs (status=error) from backend persistence.
   */
  async clearFailedJobs() {
    const response = await fetchOrApiError(`${this.backendUrl}/api/jobs/clear-failed`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' }
    }, 'Clear failed simulation jobs');
    return await response.json();
  }
}
