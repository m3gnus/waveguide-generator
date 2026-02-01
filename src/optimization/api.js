/**
 * Public API for horn optimization.
 * @module api
 */

import { defineParameterSpace } from './parameterSpace.js';
import { createObjectiveFunction, smoothFrequencyResponse, directivityControl, minimizeRipple, phaseSmoothness, throatImpedanceMatching } from './objectiveFunctions.js';
import { runOptimization } from './engine.js';

/**
 * Main optimization API function.
 * 
 * @param {Object} config - Optimization configuration
 * @param {string} config.modelType - The horn model type (e.g., 'R-OSSE', 'OSSE')
 * @param {Object} config.parameters - Parameter bounds and constraints
 * @param {Array<Object>} config.objectives - Array of objective configurations
 * @param {string} config.algorithm - Optimization algorithm ('grid', 'random')
 * @param {Object} config.options - Algorithm-specific options
 * @returns {Promise<Array<Object>>} Array of optimization results sorted by score
 */
export async function optimizeHorn(config) {
  const {
    modelType,
    parameters: bounds,
    objectives,
    algorithm = 'grid',
    options = {}
  } = config;

  // Validate required parameters
  if (!modelType) {
    throw new Error('modelType is required');
  }
  
  if (!bounds || Object.keys(bounds).length === 0) {
    throw new Error('parameters bounds are required');
  }
  
  if (!Array.isArray(objectives) || objectives.length === 0) {
    throw new Error('objectives array is required and must not be empty');
  }

  // Define parameter space
  const parameterSpace = defineParameterSpace(modelType, bounds, options.constraints || {});
  
  // Create objective function
  const objectiveFunction = createObjectiveFunction(objectives);
  
  // Run optimization with provided configuration
  const optimizationConfig = {
    modelType,
    parameterSpace,
    objectiveFunction,
    algorithm,
    options
  };
  
  return await runOptimization(optimizationConfig);
}

/**
 * Creates a default set of objectives for horn optimization.
 * 
 * @param {Object} weights - Optional custom weights for objectives (0-1)
 * @returns {Array<Object>} Default objectives with weights
 */
export function createDefaultObjectives(weights = {}) {
  return [
    {
      name: 'smoothFrequencyResponse',
      weight: weights.smoothFrequencyResponse || 0.3,
      scoringFunction: smoothFrequencyResponse
    },
    {
      name: 'directivityControl',
      weight: weights.directivityControl || 0.2,
      scoringFunction: directivityControl
    },
    {
      name: 'minimizeRipple',
      weight: weights.minimizeRipple || 0.2,
      scoringFunction: minimizeRipple
    },
    {
      name: 'phaseSmoothness',
      weight: weights.phaseSmoothness || 0.2,
      scoringFunction: phaseSmoothness
    },
    {
      name: 'throatImpedanceMatching',
      weight: weights.throatImpedanceMatching || 0.1,
      scoringFunction: throatImpedanceMatching
    }
  ];
}

/**
 * Runs a single optimization iteration with given parameters.
 * 
 * @param {Object} params - Parameter set to evaluate
 * @param {string} modelType - The horn model type (e.g., 'R-OSSE', 'OSSE')
 * @param {Array<Object>} objectives - Array of objective configurations
 * @returns {Promise<Object>} Optimization result
 */
export async function optimizeSingleIteration(params, modelType, objectives) {
  // Validate parameters
  if (!params || Object.keys(params).length === 0) {
    throw new Error('params are required');
  }
  
  if (!modelType) {
    throw new Error('modelType is required');
  }
  
  if (!Array.isArray(objectives) || objectives.length === 0) {
    throw new Error('objectives array is required and must not be empty');
  }

  // Create objective function
  const objectiveFunction = createObjectiveFunction(objectives);
  
  // Run single iteration (this would interface with actual geometry/solver in real implementation)
  const result = await runOptimization({
    modelType,
    parameterSpace: { parameters: {} }, // Placeholder - in real implementation this would be more complete
    objectiveFunction,
    algorithm: 'grid', // Not used for single iteration, but required by the function
    options: { numPointsPerParam: 1 }
  });
  
  // For single iteration, we just return the result directly
  return result[0];
}

/**
 * Runs optimization with default objectives.
 * 
 * @param {Object} config - Optimization configuration
 * @param {string} config.modelType - The horn model type (e.g., 'R-OSSE', 'OSSE')
 * @param {Object} config.parameters - Parameter bounds and constraints
 * @param {Object} config.weights - Optional custom weights for objectives (0-1)
 * @param {string} config.algorithm - Optimization algorithm ('grid', 'random')
 * @param {Object} config.options - Algorithm-specific options
 * @returns {Promise<Array<Object>>} Array of optimization results sorted by score
 */
export async function optimizeWithDefaults(config) {
  const { weights, ...restConfig } = config;
  
  const defaultObjectives = createDefaultObjectives(weights);
  
  return await optimizeHorn({
    ...restConfig,
    objectives: defaultObjectives
  });
}