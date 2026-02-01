/**
 * AI Knowledge Schema Definition
 * 
 * Defines the structured format for storing design knowledge.
 */

/**
 * Design Knowledge Schema
 * 
 * This schema defines the structure for storing knowledge about horn designs.
 * It ensures consistency and enables learning from previous simulations.
 * 
 * @typedef {Object} DesignKnowledgeSchema
 * @property {Object} config - Configuration parameters used for the design
 * @property {string} config.modelType - Type of horn model (e.g., 'R-OSSE', 'OSSE')
 * @property {Object} config.parameters - All parameter values used in the design
 * @property {Object} mesh - Mesh generation parameters
 * @property {Object} solver - BEM solver settings
 * @property {Object} objectives - Objective scores and metrics
 * @property {Object} metrics - Derived acoustic metrics
 * @property {Object} metadata - Metadata about the design
 * @property {string} metadata.hornModelType - The specific horn model type
 * @property {Object} metadata.frequencyRange - Frequency range for the simulation
 * @property {string} metadata.driverAssumptions - Assumptions about driver behavior
 * @property {string} metadata.optimizationStrategy - Strategy used for optimization
 * @property {Object} metadata.version - Version information for the knowledge record
 */

/**
 * Creates a new design knowledge schema instance
 * 
 * @returns {DesignKnowledgeSchema} A new, empty design knowledge object
 */
export function createDesignKnowledgeSchema() {
  return {
    config: {
      modelType: '',
      parameters: {}
    },
    mesh: {
      angularSegments: 0,
      lengthSegments: 0,
      cornerSegments: 0,
      quadrants: '',
      wallThickness: 0,
      rearShape: 0
    },
    solver: {
      simType: 0,
      f1: 0,
      f2: 0,
      numFreq: 0
    },
    objectives: {
      score: 0,
      ripple: 0,
      directivityIndex: 0,
      impedanceMatch: 0
    },
    metrics: {
      diSlope: 0,
      rippleLikelihood: 0,
      bandwidth: 0,
      phaseSmoothness: 0
    },
    metadata: {
      hornModelType: '',
      frequencyRange: { min: 0, max: 0 },
      driverAssumptions: '',
      optimizationStrategy: '',
      version: '1.0.0'
    }
  };
}

/**
 * Validates a design knowledge object against the schema
 * 
 * @param {Object} knowledge - The design knowledge to validate
 * @returns {Object} Validation result with valid flag and error messages
 */
export function validateDesignKnowledge(knowledge) {
  const errors = [];
  
  // Check required top-level properties
  if (!knowledge.config) errors.push('Missing config');
  if (!knowledge.mesh) errors.push('Missing mesh');
  if (!knowledge.solver) errors.push('Missing solver');
  if (!knowledge.objectives) errors.push('Missing objectives');
  if (!knowledge.metrics) errors.push('Missing metrics');
  if (!knowledge.metadata) errors.push('Missing metadata');
  
  // Validate config structure
  if (knowledge.config && !knowledge.config.modelType) {
    errors.push('Config missing modelType');
  }
  
  // Validate metadata structure
  if (knowledge.metadata && !knowledge.metadata.hornModelType) {
    errors.push('Metadata missing hornModelType');
  }
  
  return {
    valid: errors.length === 0,
    errors
  };
}

/**
 * Normalizes design parameters for machine learning use
 * 
 * @param {Object} config - Configuration parameters to normalize
 * @returns {Object} Normalized feature vector
 */
export function normalizeDesignParameters(config) {
  // This is a simplified normalization - in practice, this would use 
  // min/max or z-score normalization based on historical data ranges
  const normalized = {};
  
  if (config && config.parameters) {
    // Normalize each parameter to [0,1] range based on typical ranges
    Object.keys(config.parameters).forEach(param => {
      const value = config.parameters[param];
      
      // Simple normalization - in real implementation, this would use
      // historical min/max values from the parameter space
      if (typeof value === 'number') {
        normalized[param] = Math.max(0, Math.min(1, value / 100)); // Assuming max of 100 for normalization
      } else {
        normalized[param] = value;
      }
    });
  }
  
  return normalized;
}

/**
 * Extracts derived metrics from acoustic data
 * 
 * @param {Object} acousticData - Raw acoustic simulation results
 * @returns {Object} Derived metrics for knowledge storage
 */
export function extractDerivedMetrics(acousticData) {
  // Extract key metrics that are useful for learning
  const metrics = {};
  
  if (acousticData && acousticData.directivity) {
    // Calculate DI slope from directivity data
    const diValues = acousticData.directivity.horizontal.map(point => point.spl);
    if (diValues.length > 1) {
      const slope = calculateSlope(diValues);
      metrics.diSlope = slope;
    }
    
    // Calculate ripple likelihood from SPL variations
    const ripple = calculateRippleLikelihood(diValues);
    metrics.rippleLikelihood = ripple;
  }
  
  // Calculate bandwidth from frequency response
  if (acousticData && acousticData.frequencyResponse) {
    const frequencies = acousticData.frequencyResponse.frequencies;
    const splValues = acousticData.frequencyResponse.spl;
    
    if (frequencies && splValues && frequencies.length > 0) {
      // Find bandwidth where SPL drops by 6dB from peak
      const maxSPL = Math.max(...splValues);
      const cutoffSPL = maxSPL - 6;
      
      let bandwidthStart = frequencies[0];
      let bandwidthEnd = frequencies[frequencies.length - 1];
      
      // Find the frequency range where SPL is within 6dB of peak
      for (let i = 0; i < splValues.length; i++) {
        if (splValues[i] >= cutoffSPL) {
          bandwidthStart = frequencies[i];
          break;
        }
      }
      
      for (let i = splValues.length - 1; i >= 0; i--) {
        if (splValues[i] >= cutoffSPL) {
          bandwidthEnd = frequencies[i];
          break;
        }
      }
      
      metrics.bandwidth = bandwidthEnd - bandwidthStart;
    }
  }
  
  // Calculate phase smoothness
  if (acousticData && acousticData.phase) {
    const phases = acousticData.phase.phase;
    if (phases && phases.length > 1) {
      const phaseDiff = calculatePhaseDifference(phases);
      metrics.phaseSmoothness = 1 - Math.abs(phaseDiff); // Invert so smoother is closer to 1
    }
  }
  
  return metrics;
}

/**
 * Calculates slope of a data series
 * 
 * @private
 * @param {Array} data - Data points to calculate slope for
 * @returns {number} Slope value
 */
function calculateSlope(data) {
  if (data.length < 2) return 0;
  
  // Simple linear regression slope calculation
  const n = data.length;
  let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
  
  for (let i = 0; i < n; i++) {
    sumX += i;
    sumY += data[i];
    sumXY += i * data[i];
    sumXX += i * i;
  }
  
  const slope = (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX);
  return slope;
}

/**
 * Calculates ripple likelihood from SPL data
 * 
 * @private
 * @param {Array} splValues - SPL values to analyze for ripple
 * @returns {number} Ripple likelihood (0-1)
 */
function calculateRippleLikelihood(splValues) {
  if (splValues.length < 3) return 0;
  
  // Simple analysis: look for variations in SPL that might indicate ripple
  let maxVariation = 0;
  
  // Calculate differences between consecutive values
  for (let i = 1; i < splValues.length; i++) {
    const diff = Math.abs(splValues[i] - splValues[i-1]);
    maxVariation = Math.max(maxVariation, diff);
  }
  
  // Normalize to 0-1 range (assuming max variation of 10dB as threshold)
  return Math.min(1, maxVariation / 10);
}

/**
 * Calculates phase difference for smoothness metric
 * 
 * @private
 * @param {Array} phases - Phase values to analyze
 * @returns {number} Average phase difference
 */
function calculatePhaseDifference(phases) {
  if (phases.length < 2) return 0;
  
  let totalDiff = 0;
  for (let i = 1; i < phases.length; i++) {
    totalDiff += Math.abs(phases[i] - phases[i-1]);
  }
  
  return totalDiff / (phases.length - 1);
}