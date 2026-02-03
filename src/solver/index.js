/**
 * BEM Solver Interface (Validation Phase)
 *
 * ╔════════════════════════════════════════════════════════════════════════════╗
 * ║  WARNING: MOCK SOLVER - NO REAL PHYSICS                                    ║
 * ╠════════════════════════════════════════════════════════════════════════════╣
 * ║  This module currently returns FAKE deterministic data.                    ║
 * ║  Results are NOT physics-based and NOT suitable for acoustic design.       ║
 * ║                                                                            ║
 * ║  Real BEM integration requires:                                            ║
 * ║  - Python backend with BEM library (e.g., bempp, pybem)                   ║
 * ║  - Server running at localhost:8000                                        ║
 * ║                                                                            ║
 * ║  Status: Phase 4 (BEM Integration) is DEFERRED                            ║
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
export class BemSolver {
  constructor() {
    this.backendUrl = 'http://localhost:8000';
    this.isConnected = false;
  }

  /**
   * Check if BEM solver backend is available
   */
  async checkConnection() {
    try {
      const response = await fetch(`${this.backendUrl}/health`);
      return response.ok;
    } catch (error) {
      return false;
    }
  }

  /**
   * Submit a horn geometry for BEM simulation
   */
  async submitSimulation(config, meshData, options = {}) {
    const payload = {
      mesh: meshData,
      frequency_range: [config.frequencyStart, config.frequencyEnd],
      num_frequencies: config.numFrequencies,
      sim_type: config.simulationType,
      options: options,
      polar_config: config.polarConfig || null
    };

    const response = await fetch(`${this.backendUrl}/api/solve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(`BEM solver error: ${response.status}`);
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