/**
 * BEM Solver Interface
 *
 * ╔════════════════════════════════════════════════════════════════════════════╗
 * ║  Backend-first solver client                                                ║
 * ╠════════════════════════════════════════════════════════════════════════════╣
 * ║  submitSimulation/getJobStatus/getResults call the Python backend API.     ║
 * ║  mockBEMSolver is a local fallback helper only and is not physics-based.   ║
 * ║                                                                            ║
 * ║  Runtime requirement: backend running at localhost:8000                    ║
 * ║                                                                            ║
 * ║  Use mock results only for UI/debug validation workflows.                  ║
 * ╚════════════════════════════════════════════════════════════════════════════╝
 *
 * This module provides the public API for BEM acoustic simulations.
 * It handles communication with the Python backend and manages
 * the simulation pipeline from geometry to acoustic results.
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
 * Mock BEM Backend for Validation Only (Phase 4.0)
 *
 * Returns deterministic fake data - NO numerical correctness validation yet
 */
export function mockBEMSolver(meshData) {
  console.warn('[BEM Solver] Using MOCK solver - results are NOT physics-based. Real BEM integration pending.');

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

export class BemSolver {
  constructor() {
    this.backendUrl = 'http://localhost:8000';
    this.isConnected = false;
  }

  /**
   * Fetch backend health payload.
   */
  async getHealthStatus() {
    const response = await fetch(`${this.backendUrl}/health`);
    if (!response.ok) {
      throw new Error(`Failed to fetch health: ${response.status}`);
    }
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
    validateCanonicalMeshPayload(meshData);

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
      sim_type: config.simulationType,
      options: options,
      polar_config: config.polarConfig || null,
      mesh_validation_mode: config.meshValidationMode || 'warn',
      frequency_spacing: config.frequencySpacing || 'log',
      device_mode: config.deviceMode || 'auto'
    };

    const response = await fetch(`${this.backendUrl}/api/solve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      let detail = '';
      try {
        const errorPayload = await response.json();
        if (typeof errorPayload?.detail === 'string') {
          detail = errorPayload.detail;
        } else if (Array.isArray(errorPayload?.detail)) {
          detail = JSON.stringify(errorPayload.detail);
        }
      } catch (_error) {
        // Ignore JSON decode failures and fallback to status-only message.
      }
      throw new Error(detail
        ? `BEM solver error: ${response.status} (${detail})`
        : `BEM solver error: ${response.status}`);
    }

    const result = await response.json();
    return result.job_id;
  }

  /**
   * Check the status of a simulation job
   */
  async getJobStatus(jobId) {
    const response = await fetch(`${this.backendUrl}/api/status/${jobId}`);
    
    if (!response.ok) {
      throw new Error(`Failed to get job status: ${response.status}`);
    }

    return await response.json();
  }

  /**
   * Retrieve simulation results
   */
  async getResults(jobId) {
    const response = await fetch(`${this.backendUrl}/api/results/${jobId}`);
    
    if (!response.ok) {
      throw new Error(`Failed to retrieve results: ${response.status}`);
    }

    return await response.json();
  }
}
