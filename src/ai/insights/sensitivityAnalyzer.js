/**
 * AI Sensitivity Analysis Module
 * 
 * Analyzes parameter sensitivity to provide insights into design robustness.
 */

/**
 * Analyzes parameter sensitivity from historical data
 * 
 * @param {Array} historicalDesigns - Array of historical design knowledge records
 * @returns {Object} Sensitivity analysis results with parameter importance scores
 */
export function analyzeSensitivity(historicalDesigns) {
  const sensitivityResults = {
    parameters: {},
    overallImportance: {}
  };
  
  if (!historicalDesigns || historicalDesigns.length === 0) {
    return sensitivityResults;
  }
  
  // For demonstration, we'll analyze parameter variation patterns
  const paramVariations = {};
  const paramCorrelations = {};
  
  // Extract parameter data from historical designs
  const designSamples = historicalDesigns.slice(0, Math.min(historicalDesigns.length, 50)); // Limit for performance
  
  // Analyze each parameter across designs
  designSamples.forEach(design => {
    if (design.data && design.data.config && design.data.config.parameters) {
      const params = design.data.config.parameters;
      
      Object.keys(params).forEach(paramName => {
        if (!paramVariations[paramName]) {
          paramVariations[paramName] = [];
        }
        
        const value = params[paramName];
        if (typeof value === 'number') {
          paramVariations[paramName].push(value);
        }
      });
    }
  });
  
  // Calculate parameter importance based on variation and correlation
  Object.keys(paramVariations).forEach(paramName => {
    const values = paramVariations[paramName];
    
    if (values.length > 1) {
      // Calculate variation coefficient (coefficient of variation)
      const mean = values.reduce((sum, val) => sum + val, 0) / values.length;
      const variance = values.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / values.length;
      const stdDev = Math.sqrt(variance);
      const coefficientOfVariation = stdDev / mean;
      
      // For demonstration, we'll assign importance based on coefficient of variation
      sensitivityResults.parameters[paramName] = {
        coefficientOfVariation,
        importance: Math.min(1, coefficientOfVariation * 5), // Normalize to 0-1 scale
        mean: mean,
        min: Math.min(...values),
        max: Math.max(...values),
        count: values.length
      };
    } else {
      // If only one value, assign low importance
      sensitivityResults.parameters[paramName] = {
        coefficientOfVariation: 0,
        importance: 0.1,
        mean: values[0] || 0,
        min: values[0] || 0,
        max: values[0] || 0,
        count: values.length
      };
    }
  });
  
  // Calculate overall importance scores (weighted by variation)
  const totalImportance = Object.values(sensitivityResults.parameters).reduce((sum, param) => sum + param.importance, 0);
  
  if (totalImportance > 0) {
    Object.keys(sensitivityResults.parameters).forEach(paramName => {
      sensitivityResults.overallImportance[paramName] = 
        sensitivityResults.parameters[paramName].importance / totalImportance;
    });
  }
  
  return sensitivityResults;
}

/**
 * Gets parameter impact ranking based on historical data
 * 
 * @param {Array} historicalDesigns - Array of historical design knowledge records
 * @returns {Array} Sorted array of parameter names by importance
 */
export function getParameterImpactRanking(historicalDesigns) {
  const sensitivity = analyzeSensitivity(historicalDesigns);
  
  // Sort parameters by importance score
  const rankedParams = Object.keys(sensitivity.parameters)
    .map(paramName => ({
      name: paramName,
      importance: sensitivity.parameters[paramName].importance
    }))
    .sort((a, b) => b.importance - a.importance);
  
  return rankedParams;
}

/**
 * Generates sensitivity summary for UI display
 * 
 * @param {Array} historicalDesigns - Array of historical design knowledge records
 * @returns {Object} Summary of parameter sensitivities
 */
export function generateSensitivitySummary(historicalDesigns) {
  const sensitivity = analyzeSensitivity(historicalDesigns);
  
  const summary = {
    totalParameters: Object.keys(sensitivity.parameters).length,
    mostSensitive: [],
    leastSensitive: [],
    parameterDetails: {}
  };
  
  // Get sorted parameters by importance
  const sortedParams = Object.keys(sensitivity.parameters)
    .map(paramName => ({
      name: paramName,
      importance: sensitivity.parameters[paramName].importance,
      coefficientOfVariation: sensitivity.parameters[paramName].coefficientOfVariation
    }))
    .sort((a, b) => b.importance - a.importance);
  
  // Get top 5 most sensitive parameters
  summary.mostSensitive = sortedParams.slice(0, 5).map(param => ({
    name: param.name,
    importance: param.importance.toFixed(3),
    coefficientOfVariation: param.coefficientOfVariation.toFixed(3)
  }));
  
  // Get bottom 5 least sensitive parameters
  summary.leastSensitive = sortedParams.slice(-5).map(param => ({
    name: param.name,
    importance: param.importance.toFixed(3),
    coefficientOfVariation: param.coefficientOfVariation.toFixed(3)
  }));
  
  // Detailed parameter information
  Object.keys(sensitivity.parameters).forEach(paramName => {
    const paramData = sensitivity.parameters[paramName];
    summary.parameterDetails[paramName] = {
      importance: paramData.importance.toFixed(3),
      coefficientOfVariation: paramData.coefficientOfVariation.toFixed(3),
      mean: paramData.mean.toFixed(2),
      range: (paramData.max - paramData.min).toFixed(2)
    };
  });
  
  return summary;
}

/**
 * Determines if a parameter is highly sensitive based on historical data
 * 
 * @param {Array} historicalDesigns - Array of historical design knowledge records
 * @param {string} paramName - Name of the parameter to analyze
 * @returns {boolean} True if parameter is highly sensitive (importance > 0.7)
 */
export function isParameterHighlySensitive(historicalDesigns, paramName) {
  const sensitivity = analyzeSensitivity(historicalDesigns);
  
  if (sensitivity.parameters[paramName]) {
    return sensitivity.parameters[paramName].importance > 0.7;
  }
  
  return false;
}

/**
 * Calculates parameter robustness score
 * 
 * @param {Array} historicalDesigns - Array of historical design knowledge records
 * @param {string} paramName - Name of the parameter to analyze
 * @returns {Object} Robustness analysis with score and explanation
 */
export function calculateParameterRobustness(historicalDesigns, paramName) {
  const sensitivity = analyzeSensitivity(historicalDesigns);
  
  if (!sensitivity.parameters[paramName]) {
    return {
      score: 0.5, // Default score if parameter not found
      explanation: `Parameter ${paramName} not found in historical data`,
      isRobust: true
    };
  }
  
  const importance = sensitivity.parameters[paramName].importance;
  const coefficientOfVariation = sensitivity.parameters[paramName].coefficientOfVariation;
  
  // Robustness score: higher importance = less robust
  const robustnessScore = 1 - importance;
  
  let explanation = "";
  if (robustnessScore > 0.8) {
    explanation = "Parameter shows low sensitivity to variations, making the design robust.";
  } else if (robustnessScore > 0.6) {
    explanation = "Parameter shows moderate sensitivity to variations, design is somewhat robust.";
  } else {
    explanation = "Parameter shows high sensitivity to variations, design may be sensitive to parameter changes.";
  }
  
  return {
    score: robustnessScore,
    explanation,
    isRobust: robustnessScore > 0.7,
    coefficientOfVariation
  };
}