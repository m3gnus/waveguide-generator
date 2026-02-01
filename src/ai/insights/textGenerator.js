/**
 * AI Text Generation for Design Insights
 * 
 * Generates human-readable explanations and insights about horn designs.
 */

/**
 * Generates design insights from acoustic data and configuration
 * 
 * @param {Object} acousticData - Raw acoustic simulation results
 * @param {Object} config - Configuration parameters used for the design
 * @param {Object} metrics - Derived metrics from the design
 * @returns {Array} Array of human-readable insight strings
 */
export function generateDesignInsights(acousticData, config, metrics) {
  const insights = [];
  
  // Analyze the acoustic data and generate relevant insights
  if (acousticData && acousticData.directivity) {
    const diInsights = analyzeDirectivityInsights(acousticData.directivity, metrics);
    insights.push(...diInsights);
  }
  
  if (acousticData && acousticData.frequencyResponse) {
    const frequencyInsights = analyzeFrequencyResponseInsights(acousticData.frequencyResponse, metrics);
    insights.push(...frequencyInsights);
  }
  
  if (config && config.parameters) {
    const parameterInsights = analyzeParameterInsights(config.parameters);
    insights.push(...parameterInsights);
  }
  
  if (metrics) {
    const metricInsights = analyzeMetricInsights(metrics);
    insights.push(...metricInsights);
  }
  
  // Ensure we have at least one insight
  if (insights.length === 0) {
    insights.push("No specific insights available for this design. Consider exploring parameter variations.");
  }
  
  return insights;
}

/**
 * Analyzes directivity data for design insights
 * 
 * @private
 * @param {Object} directivity - Directivity data from simulation
 * @param {Object} metrics - Derived metrics
 * @returns {Array} Array of directivity-related insights
 */
function analyzeDirectivityInsights(directivity, metrics) {
  const insights = [];
  
  if (directivity && directivity.horizontal) {
    const diValues = directivity.horizontal.map(point => point.spl);
    
    // Check for DI slope characteristics
    if (metrics && typeof metrics.diSlope === 'number') {
      const diSlope = metrics.diSlope;
      
      if (diSlope > 0.5) {
        insights.push("High directivity index slope above 0.5 dB/octave, indicating strong directional response.");
      } else if (diSlope < -0.5) {
        insights.push("Low directivity index slope below -0.5 dB/octave, suggesting broad radiation pattern.");
      } else {
        insights.push("Moderate directivity index slope, providing balanced radiation characteristics.");
      }
    }
    
    // Check for ripple in directivity
    if (metrics && typeof metrics.rippleLikelihood === 'number') {
      const ripple = metrics.rippleLikelihood;
      
      if (ripple > 0.7) {
        insights.push("High likelihood of directivity ripple, which may cause frequency response variations.");
      } else if (ripple > 0.3) {
        insights.push("Moderate likelihood of directivity ripple, potentially affecting frequency response smoothness.");
      } else {
        insights.push("Low likelihood of directivity ripple, indicating stable radiation characteristics.");
      }
    }
  }
  
  return insights;
}

/**
 * Analyzes frequency response data for design insights
 * 
 * @private
 * @param {Object} frequencyResponse - Frequency response data from simulation
 * @param {Object} metrics - Derived metrics
 * @returns {Array} Array of frequency response-related insights
 */
function analyzeFrequencyResponseInsights(frequencyResponse, metrics) {
  const insights = [];
  
  if (frequencyResponse && frequencyResponse.spl) {
    const splValues = frequencyResponse.spl;
    
    // Check for SPL variations that might indicate ripple
    if (splValues.length > 2) {
      const maxSPL = Math.max(...splValues);
      const minSPL = Math.min(...splValues);
      const ripple = maxSPL - minSPL;
      
      if (ripple > 6) {
        insights.push(`Significant SPL variation of ${ripple.toFixed(1)} dB, indicating potential ripple in frequency response.`);
      } else if (ripple > 3) {
        insights.push(`Moderate SPL variation of ${ripple.toFixed(1)} dB, suggesting some frequency response irregularities.`);
      } else {
        insights.push(`Low SPL variation of ${ripple.toFixed(1)} dB, indicating smooth frequency response.`);
      }
    }
    
    // Analyze bandwidth characteristics
    if (metrics && typeof metrics.bandwidth === 'number') {
      const bandwidth = metrics.bandwidth;
      
      if (bandwidth > 1000) {
        insights.push(`Wide bandwidth of ${bandwidth.toFixed(0)} Hz, indicating broad frequency response.`);
      } else if (bandwidth < 500) {
        insights.push(`Narrow bandwidth of ${bandwidth.toFixed(0)} Hz, suggesting limited frequency response.`);
      } else {
        insights.push(`Moderate bandwidth of ${bandwidth.toFixed(0)} Hz, providing balanced frequency response.`);
      }
    }
  }
  
  return insights;
}

/**
 * Analyzes parameter configurations for design insights
 * 
 * @private
 * @param {Object} parameters - Configuration parameters
 * @returns {Array} Array of parameter-related insights
 */
function analyzeParameterInsights(parameters) {
  const insights = [];
  
  if (parameters && parameters.a0 !== undefined) {
    const a0 = parameters.a0;
    
    if (a0 < 10) {
      insights.push(`Low throat angle of ${a0}°, which may result in reduced directivity at higher frequencies.`);
    } else if (a0 > 30) {
      insights.push(`High throat angle of ${a0}°, which may cause excessive directivity and potential phase issues.`);
    } else {
      insights.push(`Throat angle of ${a0}° is within optimal range for balanced directivity.`);
    }
  }
  
  if (parameters && parameters.r0 !== undefined) {
    const r0 = parameters.r0;
    
    if (r0 < 5) {
      insights.push(`Very small throat radius of ${r0}mm, which may limit acoustic efficiency.`);
    } else if (r0 > 20) {
      insights.push(`Large throat radius of ${r0}mm, which may increase horn volume and weight.`);
    } else {
      insights.push(`Throat radius of ${r0}mm is within typical range for efficient horn design.`);
    }
  }
  
  if (parameters && parameters.k !== undefined) {
    const k = parameters.k;
    
    if (k < 1) {
      insights.push(`Low expansion rate of ${k}, which may result in insufficient horn length.`);
    } else if (k > 10) {
      insights.push(`High expansion rate of ${k}, which may cause excessive horn length or sharp transitions.`);
    } else {
      insights.push(`Expansion rate of ${k} is within typical range for balanced horn geometry.`);
    }
  }
  
  return insights;
}

/**
 * Analyzes derived metrics for design insights
 * 
 * @private
 * @param {Object} metrics - Derived metrics from the design
 * @returns {Array} Array of metric-related insights
 */
function analyzeMetricInsights(metrics) {
  const insights = [];
  
  if (metrics && typeof metrics.phaseSmoothness === 'number') {
    const phaseSmoothness = metrics.phaseSmoothness;
    
    if (phaseSmoothness > 0.8) {
      insights.push("Excellent phase smoothness, indicating minimal group delay variations.");
    } else if (phaseSmoothness > 0.5) {
      insights.push("Good phase smoothness, suggesting reasonable group delay characteristics.");
    } else {
      insights.push("Poor phase smoothness, which may indicate significant group delay variations.");
    }
  }
  
  if (metrics && typeof metrics.diSlope === 'number') {
    const diSlope = metrics.diSlope;
    
    if (diSlope > 0.3) {
      insights.push("Strong directivity index slope, indicating good directional characteristics.");
    } else if (diSlope < -0.3) {
      insights.push("Weak directivity index slope, suggesting broad radiation pattern.");
    } else {
      insights.push("Moderate directivity index slope, providing balanced radiation characteristics.");
    }
  }
  
  return insights;
}

/**
 * Generates a specific insight about parameter influence
 * 
 * @param {string} paramName - Name of the parameter
 * @param {number} currentValue - Current value of the parameter
 * @param {Object} bounds - Parameter bounds (min, max)
 * @returns {string} Insight about parameter influence
 */
export function generateParameterInsight(paramName, currentValue, bounds) {
  const insight = `Parameter ${paramName} is set to ${currentValue}, which is `;
  
  const range = bounds.max - bounds.min;
  const positionInRange = (currentValue - bounds.min) / range;
  
  if (positionInRange < 0.2) {
    return insight + "at the lower end of its recommended range.";
  } else if (positionInRange > 0.8) {
    return insight + "at the upper end of its recommended range.";
  } else {
    return insight + "in the middle of its recommended range.";
  }
}

/**
 * Generates a trade-off explanation between design characteristics
 * 
 * @param {Object} metrics - Design metrics to analyze for trade-offs
 * @returns {Array} Array of trade-off explanations
 */
export function generateTradeOffInsights(metrics) {
  const insights = [];
  
  if (metrics && typeof metrics.diSlope === 'number' && typeof metrics.phaseSmoothness === 'number') {
    const diSlope = metrics.diSlope;
    const phaseSmoothness = metrics.phaseSmoothness;
    
    if (diSlope > 0.5 && phaseSmoothness < 0.5) {
      insights.push("High directivity index slope with poor phase smoothness - design may have sharp directivity transitions.");
    } else if (diSlope < -0.5 && phaseSmoothness > 0.8) {
      insights.push("Low directivity index slope with excellent phase smoothness - design has broad radiation pattern.");
    } else if (diSlope > 0.3 && diSlope < 0.5) {
      insights.push("Moderate directivity index slope with good phase smoothness - design provides balanced characteristics.");
    }
  }
  
  return insights;
}