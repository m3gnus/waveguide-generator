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
}
