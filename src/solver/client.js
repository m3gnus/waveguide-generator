/**
 * BEM Solver Client
 * 
 * Handles HTTP communication with the Python BEM solver backend.
 */

import { BemSolver } from './index.js';

/**
 * HTTP client for BEM solver backend
 */
export class BemClient extends BemSolver {
  constructor() {
    super();
  }

  /**
   * Initialize the client with a specific backend URL
   * @param {string} url - Backend server URL
   */
  setBackendUrl(url) {
    this.backendUrl = url;
  }

  /**
   * Submit a simulation job to the BEM solver
   * @param {Object} config - Simulation configuration
   * @param {string} meshData - Base64 encoded mesh data
   * @param {Object} options - Additional simulation options
   * @returns {Promise<string>} Job ID for tracking the simulation
   */
  async submitSimulation(config, meshData, options = {}) {
    try {
      const jobId = await super.submitSimulation(config, meshData, options);
      return jobId;
    } catch (error) {
      console.error('Failed to submit BEM simulation:', error);
      throw error;
    }
  }

  /**
   * Poll for job completion
   * @param {string} jobId - Job ID to poll
   * @param {Function} onProgress - Callback for progress updates
   * @returns {Promise<Object>} Final job result
   */
  async pollForCompletion(jobId, onProgress = null) {
    let status = await this.getJobStatus(jobId);
    
    if (onProgress) {
      onProgress(status);
    }

    while (status.status !== 'complete' && status.status !== 'error') {
      // Wait before polling again
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      status = await this.getJobStatus(jobId);
      
      if (onProgress) {
        onProgress(status);
      }
    }

    if (status.status === 'error') {
      throw new Error(`BEM simulation failed: ${status.error}`);
    }

    return await this.getResults(jobId);
  }

  /**
   * Generate a Gmsh-authored .msh from .geo text via backend service.
   * @param {{ geoText: string, mshVersion?: '2.2' | '4.1', binary?: boolean }} request
   * @returns {Promise<{ msh: string, generatedBy: string, stats: { nodeCount: number, elementCount: number } }>}
   */
  async generateMeshFromGeo(request) {
    const payload = {
      geoText: String(request?.geoText || ''),
      mshVersion: request?.mshVersion || '2.2',
      binary: Boolean(request?.binary)
    };

    const controller = new AbortController();
    const requestedTimeout = Number(request?.timeoutMs);
    const timeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
      ? Math.floor(requestedTimeout)
      : 90_000;
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    let response;
    try {
      response = await fetch(`${this.backendUrl}/api/mesh/generate-msh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
    } catch (err) {
      clearTimeout(timer);
      if (err.name === 'AbortError') {
        throw new Error(`Gmsh backend did not respond within ${timeoutMs / 1000}s. Is the server running?`);
      }
      throw new Error(`Cannot reach Gmsh backend at ${this.backendUrl}: ${err.message}`);
    }
    clearTimeout(timer);

    if (!response.ok) {
      let detail = `${response.status}`;
      try {
        const err = await response.json();
        if (err?.detail) detail = String(err.detail);
      } catch {
        // Keep default detail fallback.
      }
      throw new Error(`Gmsh mesh generation failed: ${detail}`);
    }

    return response.json();
  }
}

export async function generateMeshFromGeo(request, backendUrl = 'http://localhost:8000') {
  const client = new BemClient();
  client.setBackendUrl(backendUrl);
  return client.generateMeshFromGeo(request);
}
