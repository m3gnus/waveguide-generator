/**
 * BEM Result Parser
 * 
 * Parses raw BEM solver results into structured acoustic data for visualization.
 */

/**
 * Parse BEM simulation results into structured acoustic metrics
 * 
 * @param {Object} rawResults - Raw results from BEM solver
 * @returns {Object} Structured acoustic data ready for visualization
 */
export function parseBemResults(rawResults) {
  // In a real implementation, this would process the actual BEM output
  // and convert it into structured data for the UI
  
  const parsedResults = {
    // On-axis frequency response
    onAxisFrequencyResponse: {
      frequencies: rawResults.frequencies || [],
      spl: rawResults.splOnAxis?.spl || [],
      phase: rawResults.phase?.phase || []
    },
    
    // Polar/directivity data
    polarData: {
      horizontal: rawResults.directivity?.horizontal || [],
      vertical: rawResults.directivity?.vertical || [],
      diagonal: rawResults.directivity?.diagonal || []
    },
    
    // Impedance data (optional)
    impedance: {
      frequencies: rawResults.frequencies || [],
      real: rawResults.impedance?.real || [],
      imaginary: rawResults.impedance?.imaginary || []
    },
    
    // Directivity Index (optional)
    directivityIndex: {
      frequencies: rawResults.frequencies || [],
      di: rawResults.di?.di || []
    },
    
    // Additional acoustic metrics
    acousticMetrics: {
      // Calculate derived metrics from raw data
      maxSPL: Math.max(...(rawResults.splOnAxis?.spl || [])),
      minSPL: Math.min(...(rawResults.splOnAxis?.spl || [])),
      frequencyRange: {
        min: rawResults.frequencies?.[0] || 0,
        max: rawResults.frequencies?.[rawResults.frequencies.length - 1] || 0
      }
    }
  };
  
  return parsedResults;
}

/**
 * Validate BEM results for consistency and quality
 * 
 * @param {Object} results - Parsed BEM results to validate
 * @returns {Object} Validation report with any issues found
 */
export function validateBemResults(results) {
  const validation = {
    isValid: true,
    issues: []
  };
  
  // Check for basic data consistency
  if (!results.onAxisFrequencyResponse.frequencies || 
      results.onAxisFrequencyResponse.frequencies.length === 0) {
    validation.isValid = false;
    validation.issues.push('No frequency data found');
  }
  
  // Check that all arrays have the same length
  const frequenciesLength = results.onAxisFrequencyResponse.frequencies.length;
  if (frequenciesLength > 0) {
    const splLength = results.onAxisFrequencyResponse.spl.length;
    const phaseLength = results.onAxisFrequencyResponse.phase.length;
    
    if (splLength !== frequenciesLength) {
      validation.isValid = false;
      validation.issues.push('SPL data length does not match frequency data');
    }
    
    if (phaseLength !== frequenciesLength) {
      validation.isValid = false;
      validation.issues.push('Phase data length does not match frequency data');
    }
  }
  
  // Check for valid numeric values
  const allValues = [
    ...results.onAxisFrequencyResponse.spl,
    ...results.onAxisFrequencyResponse.phase,
    ...results.impedance.real,
    ...results.impedance.imaginary
  ];
  
  for (const value of allValues) {
    if (typeof value !== 'number' || isNaN(value)) {
      validation.isValid = false;
      validation.issues.push('Invalid numeric value found in results');
      break;
    }
  }
  
  return validation;
}

/**
 * Generate acoustic performance summary
 * 
 * @param {Object} parsedResults - Parsed BEM results
 * @returns {Object} Summary of key acoustic performance metrics
 */
export function generateAcousticSummary(parsedResults) {
  const summary = {
    // Frequency response characteristics
    frequencyResponse: {
      bandwidth: parsedResults.acousticMetrics.frequencyRange.max - 
                 parsedResults.acousticMetrics.frequencyRange.min,
      peakSPL: parsedResults.acousticMetrics.maxSPL,
      minSPL: parsedResults.acousticMetrics.minSPL
    },
    
    // Directivity characteristics  
    directivity: {
      maxDirectivity: Math.max(...parsedResults.polarData.horizontal),
      minDirectivity: Math.min(...parsedResults.polarData.horizontal),
      directivityVariation: Math.max(...parsedResults.polarData.horizontal) - 
                           Math.min(...parsedResults.polarData.horizontal)
    },
    
    // Impedance characteristics (if available)
    impedance: {
      maxReal: Math.max(...parsedResults.impedance.real),
      minReal: Math.min(...parsedResults.impedance.real),
      maxImaginary: Math.max(...parsedResults.impedance.imaginary),
      minImaginary: Math.min(...parsedResults.impedance.imaginary)
    }
  };
  
  return summary;
}

/**
 * Format results for export or storage
 * 
 * @param {Object} parsedResults - Parsed BEM results
 * @returns {Object} Results formatted for storage or export
 */
export function formatForExport(parsedResults) {
  return {
    // JSON-serializable version of the results
    timestamp: new Date().toISOString(),
    frequencyRange: parsedResults.acousticMetrics.frequencyRange,
    onAxisResponse: parsedResults.onAxisFrequencyResponse,
    polarData: parsedResults.polarData,
    impedance: parsedResults.impedance,
    directivityIndex: parsedResults.directivityIndex,
    summary: generateAcousticSummary(parsedResults)
  };
}