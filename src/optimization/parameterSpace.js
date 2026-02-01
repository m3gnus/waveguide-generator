/**
 * Parameter space definition for horn optimization.
 * @module parameterSpace
 */

import { PARAM_SCHEMA } from '../config/schema.js';

/**
 * Defines the parameter space for optimization.
 * 
 * @param {string} modelType - The horn model type (e.g., 'R-OSSE', 'OSSE')
 * @param {Object} bounds - Parameter bounds with min/max values
 * @param {Object} constraints - Optional parameter constraints
 * @returns {Object} Parameter space definition
 */
export function defineParameterSpace(modelType, bounds, constraints = {}) {
  if (!PARAM_SCHEMA[modelType]) {
    throw new Error(`Unsupported model type: ${modelType}`);
  }

  const parameterSpace = {
    modelType,
    parameters: {},
    constraints
  };

  // Extract parameter definitions from schema and apply bounds
  for (const [paramName, paramDef] of Object.entries(PARAM_SCHEMA[modelType])) {
    if (bounds[paramName] !== undefined) {
      parameterSpace.parameters[paramName] = {
        name: paramName,
        min: bounds[paramName].min !== undefined ? bounds[paramName].min : paramDef.min,
        max: bounds[paramName].max !== undefined ? bounds[paramName].max : paramDef.max,
        step: bounds[paramName].step || paramDef.step || 0.1,
        type: paramDef.type,
        unit: paramDef.unit
      };
    } else {
      parameterSpace.parameters[paramName] = {
        name: paramName,
        min: paramDef.min,
        max: paramDef.max,
        step: paramDef.step || 0.1,
        type: paramDef.type,
        unit: paramDef.unit
      };
    }
  }

  return parameterSpace;
}

/**
 * Generate parameter combinations for grid search.
 * 
 * @param {Object} parameterSpace - The defined parameter space
 * @param {number} numPointsPerParam - Number of points to sample per parameter (for grid search)
 * @returns {Array<Object>} Array of parameter combinations
 */
export function generateGridSearchPoints(parameterSpace, numPointsPerParam = 10) {
  const points = [];
  const paramNames = Object.keys(parameterSpace.parameters);
  
  // Generate all combinations
  function generateCombinations(currentPoint, paramIndex) {
    if (paramIndex === paramNames.length) {
      points.push({ ...currentPoint });
      return;
    }
    
    const paramName = paramNames[paramIndex];
    const paramDef = parameterSpace.parameters[paramName];
    
    if (paramDef.type === 'number') {
      const step = (paramDef.max - paramDef.min) / (numPointsPerParam - 1);
      for (let i = 0; i < numPointsPerParam; i++) {
        const value = paramDef.min + (i * step);
        currentPoint[paramName] = value;
        generateCombinations(currentPoint, paramIndex + 1);
      }
    } else {
      // For non-number parameters, just use the single value
      currentPoint[paramName] = paramDef.min;
      generateCombinations(currentPoint, paramIndex + 1);
    }
  }
  
  generateCombinations({}, 0);
  return points;
}

/**
 * Generate random parameter combinations.
 * 
 * @param {Object} parameterSpace - The defined parameter space
 * @param {number} numSamples - Number of random samples to generate
 * @returns {Array<Object>} Array of random parameter combinations
 */
export function generateRandomPoints(parameterSpace, numSamples = 100) {
  const points = [];
  
  for (let i = 0; i < numSamples; i++) {
    const point = {};
    
    for (const [paramName, paramDef] of Object.entries(parameterSpace.parameters)) {
      if (paramDef.type === 'number') {
        // Generate random value within bounds
        point[paramName] = paramDef.min + Math.random() * (paramDef.max - paramDef.min);
      } else {
        // For non-number parameters, use default or first value
        point[paramName] = paramDef.default || paramDef.min;
      }
    }
    
    points.push(point);
  }
  
  return points;
}