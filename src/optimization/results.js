/**
 * Result management for horn optimization.
 * @module results
 */

/**
 * In-memory storage for optimization results.
 * In a real implementation, this might use IndexedDB or localStorage.
 * 
 * @private
 * @type {Array<Object>}
 */
let optimizationResults = [];

/**
 * Stores optimization results.
 * 
 * @param {Array<Object>} results - Array of optimization results to store
 * @param {string} runId - Unique identifier for this optimization run
 */
export function storeResults(results, runId = null) {
  if (!Array.isArray(results)) {
    throw new Error('Results must be an array');
  }
  
  // Add run ID if not present
  const resultsWithId = results.map((result, index) => ({
    ...result,
    runId: runId || `run_${Date.now()}_${index}`,
    // Ensure all results have a timestamp
    timestamp: result.timestamp || new Date().toISOString()
  }));
  
  optimizationResults = resultsWithId;
}

/**
 * Retrieves stored optimization results.
 * 
 * @param {string} runId - Specific run ID to retrieve (optional)
 * @returns {Array<Object>|Object|null} All results or specific run results
 */
export function getResults(runId = null) {
  if (runId) {
    return optimizationResults.find(result => result.runId === runId) || null;
  }
  
  return optimizationResults;
}

/**
 * Gets the best performing design from results.
 * 
 * @param {string} runId - Specific run ID to get best design from (optional)
 * @returns {Object|null} Best performing design or null if no results
 */
export function getBestDesign(runId = null) {
  const results = runId ? 
    optimizationResults.filter(r => r.runId === runId) : 
    optimizationResults;
    
  if (results.length === 0) {
    return null;
  }
  
  // Find the result with highest score
  return results.reduce((best, current) => 
    (current.score > best.score ? current : best), results[0]);
}

/**
 * Gets the top N designs by score.
 * 
 * @param {number} count - Number of top designs to return
 * @param {string} runId - Specific run ID to get top designs from (optional)
 * @returns {Array<Object>} Top N designs sorted by score
 */
export function getTopDesigns(count = 10, runId = null) {
  const results = runId ? 
    optimizationResults.filter(r => r.runId === runId) : 
    optimizationResults;
    
  return results
    .sort((a, b) => b.score - a.score)
    .slice(0, count);
}

/**
 * Clears all stored optimization results.
 */
export function clearResults() {
  optimizationResults = [];
}

/**
 * Gets statistics about stored results.
 * 
 * @returns {Object} Statistics about the optimization run
 */
export function getResultsStats() {
  if (optimizationResults.length === 0) {
    return {
      count: 0,
      bestScore: null,
      worstScore: null,
      averageScore: null
    };
  }
  
  const scores = optimizationResults.map(r => r.score);
  const bestScore = Math.max(...scores);
  const worstScore = Math.min(...scores);
  const averageScore = scores.reduce((a, b) => a + b, 0) / scores.length;
  
  return {
    count: optimizationResults.length,
    bestScore,
    worstScore,
    averageScore
  };
}

/**
 * Exports optimization results to a structured format.
 * 
 * @param {string} runId - Specific run ID to export (optional)
 * @returns {Object} Exported results in structured format
 */
export function exportResults(runId = null) {
  const results = runId ? 
    optimizationResults.filter(r => r.runId === runId) : 
    optimizationResults;
    
  return {
    metadata: {
      exportTime: new Date().toISOString(),
      runCount: results.length,
      bestScore: results.length > 0 ? Math.max(...results.map(r => r.score)) : null,
      worstScore: results.length > 0 ? Math.min(...results.map(r => r.score)) : null
    },
    runs: results.map(result => ({
      id: result.id,
      runId: result.runId,
      params: result.params,
      score: result.score,
      timestamp: result.timestamp,
      // Include only key acoustic metrics for export
      acousticMetrics: {
        frequencyResponse: result.acousticData?.frequencyResponse ? {
          frequencies: result.acousticData.frequencyResponse.frequencies,
          spl: result.acousticData.frequencyResponse.spl
        } : null,
        directivity: result.acousticData?.directivity ? {
          horizontal: result.acousticData.directivity.horizontal
        } : null,
        phase: result.acousticData?.phase ? {
          frequencies: result.acousticData.phase.frequencies,
          phase: result.acousticData.phase.phase
        } : null
      }
    }))
  };
}

/**
 * Imports optimization results from a structured format.
 * 
 * @param {Object} data - Exported results data to import
 * @param {string} runId - Run ID to assign to imported results (optional)
 */
export function importResults(data, runId = null) {
  if (!data.runs || !Array.isArray(data.runs)) {
    throw new Error('Invalid results data format');
  }
  
  const importedResults = data.runs.map(run => ({
    ...run,
    runId: runId || run.runId || `imported_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
  }));
  
  storeResults(importedResults);
}