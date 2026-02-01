/**
 * BEM Solver Status Management
 * 
 * Handles connection status, job tracking, and UI state for BEM simulations.
 */

/**
 * BEM Solver Status Manager
 * 
 * Manages the connection state and job tracking for BEM simulations.
 */
export class BemStatusManager {
  constructor() {
    this.isConnected = false;
    this.connectionAttempts = 0;
    this.maxConnectionAttempts = 3;
    this.retryDelay = 2000; // 2 seconds between retries
    this.jobs = new Map(); // Track active jobs by ID
    this.connectionCallback = null;
  }

  /**
   * Set callback for connection status changes
   * @param {Function} callback - Function to call when connection status changes
   */
  setConnectionCallback(callback) {
    this.connectionCallback = callback;
  }

  /**
   * Check if BEM solver backend is available
   * @returns {Promise<boolean>} Whether the backend is accessible
   */
  async checkConnection() {
    try {
      const response = await fetch('http://localhost:8000/health');
      const isConnected = response.ok;
      
      if (isConnected !== this.isConnected) {
        this.isConnected = isConnected;
        if (this.connectionCallback) {
          this.connectionCallback(isConnected);
        }
      }
      
      return isConnected;
    } catch (error) {
      // If connection fails, mark as disconnected
      if (this.isConnected) {
        this.isConnected = false;
        if (this.connectionCallback) {
          this.connectionCallback(false);
        }
      }
      return false;
    }
  }

  /**
   * Attempt to establish connection with retry logic
   * @returns {Promise<boolean>} Whether connection was successful
   */
  async establishConnection() {
    if (this.connectionAttempts >= this.maxConnectionAttempts) {
      return false;
    }

    try {
      const isConnected = await this.checkConnection();
      
      if (isConnected) {
        this.connectionAttempts = 0;
        return true;
      } else {
        // Retry after delay
        this.connectionAttempts++;
        await new Promise(resolve => setTimeout(resolve, this.retryDelay));
        return await this.establishConnection();
      }
    } catch (error) {
      this.connectionAttempts++;
      if (this.connectionAttempts < this.maxConnectionAttempts) {
        await new Promise(resolve => setTimeout(resolve, this.retryDelay));
        return await this.establishConnection();
      }
      return false;
    }
  }

  /**
   * Add a new job to tracking
   * @param {string} jobId - Unique identifier for the job
   * @param {Object} jobInfo - Information about the job
   */
  addJob(jobId, jobInfo) {
    this.jobs.set(jobId, {
      ...jobInfo,
      status: 'pending',
      progress: 0,
      startTime: new Date()
    });
  }

  /**
   * Update job status
   * @param {string} jobId - Unique identifier for the job
   * @param {Object} statusInfo - Updated status information
   */
  updateJobStatus(jobId, statusInfo) {
    const job = this.jobs.get(jobId);
    if (job) {
      Object.assign(job, statusInfo);
      
      // If job is complete, remove from tracking
      if (statusInfo.status === 'complete' || statusInfo.status === 'error') {
        this.jobs.delete(jobId);
      }
    }
  }

  /**
   * Get job status
   * @param {string} jobId - Unique identifier for the job
   * @returns {Object|null} Job status information or null if not found
   */
  getJobStatus(jobId) {
    return this.jobs.get(jobId) || null;
  }

  /**
   * Get all active jobs
   * @returns {Array} Array of active job information
   */
  getActiveJobs() {
    return Array.from(this.jobs.values());
  }

  /**
   * Remove a job from tracking
   * @param {string} jobId - Unique identifier for the job
   */
  removeJob(jobId) {
    this.jobs.delete(jobId);
  }

  /**
   * Get connection status information
   * @returns {Object} Connection status details
   */
  getConnectionInfo() {
    return {
      isConnected: this.isConnected,
      connectionAttempts: this.connectionAttempts,
      maxConnectionAttempts: this.maxConnectionAttempts,
      jobs: this.getActiveJobs()
    };
  }

  /**
   * Reset connection state
   */
  reset() {
    this.isConnected = false;
    this.connectionAttempts = 0;
    this.jobs.clear();
  }
}