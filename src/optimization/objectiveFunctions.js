/**
 * Objective functions for horn acoustic optimization.
 * @module objectiveFunctions
 */

/**
 * Creates a weighted multi-objective function for horn optimization.
 * 
 * @param {Array<Object>} objectives - Array of objective configurations
 * @param {string} objectives[].name - Name of the objective (e.g., 'smoothResponse', 'directivityControl')
 * @param {number} objectives[].weight - Weight for this objective (0-1)
 * @param {Function} objectives[].scoringFunction - Function that takes acoustic data and returns a score (0-1)
 * @returns {Function} Combined objective function
 */
export function createObjectiveFunction(objectives) {
  // Validate objectives
  if (!Array.isArray(objectives)) {
    throw new Error('Objectives must be an array');
  }
  
  // Normalize weights to sum to 1
  const totalWeight = objectives.reduce((sum, obj) => sum + (obj.weight || 0), 0);
  
  if (totalWeight <= 0) {
    throw new Error('At least one objective must have a positive weight');
  }
  
  const normalizedObjectives = objectives.map(obj => ({
    ...obj,
    weight: obj.weight / totalWeight
  }));
  
  /**
   * Evaluate a set of acoustic results against all objectives.
   * 
   * @param {Object} acousticData - Acoustic results from BEM simulation
   * @param {Object} acousticData.frequencyResponse - Frequency response data (on-axis)
   * @param {Object} acousticData.directivity - Directivity data (horizontal, vertical, diagonal)
   * @param {Object} acousticData.phase - Phase response data
   * @param {Object} acousticData.impedance - Impedance data (optional)
   * @returns {number} Combined objective score (0-1)
   */
  return function evaluate(acousticData) {
    let totalScore = 0;
    
    for (const obj of normalizedObjectives) {
      if (typeof obj.scoringFunction !== 'function') {
        throw new Error(`Objective ${obj.name} must have a scoring function`);
      }
      
      const score = obj.scoringFunction(acousticData);
      
      // Ensure score is between 0 and 1
      const boundedScore = Math.max(0, Math.min(1, score));
      
      totalScore += boundedScore * obj.weight;
    }
    
    return totalScore;
  };
}

/**
 * Smooth on-axis frequency response objective.
 * Minimizes ripple and maximizes flatness in the target frequency range.
 * 
 * @param {Object} acousticData - Acoustic results from BEM simulation
 * @returns {number} Score (0-1) where 1 is best (smoothest response)
 */
export function smoothFrequencyResponse(acousticData) {
  if (!acousticData.frequencyResponse || !acousticData.frequencyResponse.spl) {
    return 0;
  }
  
  const spl = acousticData.frequencyResponse.spl;
  const frequencies = acousticData.frequencyResponse.frequencies;
  
  // Focus on target frequency range (e.g., 200Hz to 8kHz)
  const targetRange = { min: 200, max: 8000 };
  
  // Calculate standard deviation of SPL in target range
  const targetValues = [];
  for (let i = 0; i < frequencies.length; i++) {
    if (frequencies[i] >= targetRange.min && frequencies[i] <= targetRange.max) {
      targetValues.push(spl[i]);
    }
  }
  
  if (targetValues.length < 2) {
    return 0;
  }
  
  // Calculate standard deviation (lower is better)
  const mean = targetValues.reduce((a, b) => a + b, 0) / targetValues.length;
  const variance = targetValues.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / targetValues.length;
  const stdDev = Math.sqrt(variance);
  
  // Convert to score (0-1) where lower stdDev = better
  // We'll define a maximum acceptable deviation (e.g., 3dB)
  const maxAcceptableDeviation = 3;
  const score = Math.max(0, 1 - (stdDev / maxAcceptableDeviation));
  
  return score;
}

/**
 * Directivity control objective.
 * Targets specific beamwidth characteristics across frequencies.
 * 
 * @param {Object} acousticData - Acoustic results from BEM simulation
 * @returns {number} Score (0-1) where 1 is best (desired directivity)
 */
export function directivityControl(acousticData) {
  if (!acousticData.directivity) {
    return 0;
  }
  
  const directivity = acousticData.directivity;
  const horizontal = directivity.horizontal || [];
  
  // For simplicity, we'll check if the response is reasonably focused
  // This would be more sophisticated in a real implementation
  
  // Simple check: ensure SPL doesn't drop too much at off-axis angles
  if (horizontal.length < 2) {
    return 0;
  }
  
  // Calculate average SPL at off-axis angles vs on-axis
  const onAxisSPL = horizontal[0]?.spl || 0;
  
  // Simple score based on how much off-axis response drops
  let totalDrop = 0;
  for (let i = 1; i < horizontal.length; i++) {
    const drop = Math.max(0, onAxisSPL - (horizontal[i]?.spl || 0));
    totalDrop += drop;
  }
  
  // Normalize to a score between 0 and 1
  const avgDrop = totalDrop / (horizontal.length - 1);
  const maxAcceptableDrop = 10; // dB
  const score = Math.max(0, 1 - (avgDrop / maxAcceptableDrop));
  
  return score;
}

/**
 * Diffraction/ripple minimization objective.
 * Minimizes SPL ripple in the target frequency range.
 * 
 * @param {Object} acousticData - Acoustic results from BEM simulation
 * @returns {number} Score (0-1) where 1 is best (minimal ripple)
 */
export function minimizeRipple(acousticData) {
  if (!acousticData.frequencyResponse || !acousticData.frequencyResponse.spl) {
    return 0;
  }
  
  const spl = acousticData.frequencyResponse.spl;
  const frequencies = acousticData.frequencyResponse.frequencies;
  
  // Focus on target frequency range (e.g., 200Hz to 8kHz)
  const targetRange = { min: 200, max: 8000 };
  
  // Find local maxima and minima to detect ripple
  const targetValues = [];
  for (let i = 0; i < frequencies.length; i++) {
    if (frequencies[i] >= targetRange.min && frequencies[i] <= targetRange.max) {
      targetValues.push(spl[i]);
    }
  }
  
  if (targetValues.length < 3) {
    return 0;
  }
  
  // Simple ripple detection: look for local maxima/minima
  let rippleCount = 0;
  
  // Check for local extrema (simple approach)
  for (let i = 1; i < targetValues.length - 1; i++) {
    const prev = targetValues[i - 1];
    const current = targetValues[i];
    const next = targetValues[i + 1];
    
    // Local maximum or minimum
    if ((current > prev && current > next) || (current < prev && current < next)) {
      rippleCount++;
    }
  }
  
  // Normalize to score (lower ripple count = better)
  const maxRipples = 10; // arbitrary maximum
  const score = Math.max(0, 1 - (rippleCount / maxRipples));
  
  return score;
}

/**
 * Phase smoothness objective.
 * Ensures consistent group delay or phase response across frequencies.
 * 
 * @param {Object} acousticData - Acoustic results from BEM simulation
 * @returns {number} Score (0-1) where 1 is best (smooth phase)
 */
export function phaseSmoothness(acousticData) {
  if (!acousticData.phase || !acousticData.phase.frequencies) {
    return 0;
  }
  
  const phase = acousticData.phase;
  const frequencies = phase.frequencies;
  const phaseValues = phase.phase;
  
  if (phaseValues.length < 2) {
    return 0;
  }
  
  // Calculate the derivative (rate of change) of phase
  let totalChange = 0;
  
  for (let i = 1; i < phaseValues.length; i++) {
    const deltaFreq = frequencies[i] - frequencies[i - 1];
    const deltaPhase = Math.abs(phaseValues[i] - phaseValues[i - 1]);
    
    // Normalize by frequency spacing
    if (deltaFreq > 0) {
      totalChange += deltaPhase / deltaFreq;
    }
  }
  
  // Convert to score (lower total change = smoother phase)
  const maxAcceptableChange = 10; // arbitrary threshold
  const score = Math.max(0, 1 - (totalChange / maxAcceptableChange));
  
  return score;
}

/**
 * Throat impedance matching objective (optional).
 * 
 * @param {Object} acousticData - Acoustic results from BEM simulation
 * @returns {number} Score (0-1) where 1 is best (good impedance match)
 */
export function throatImpedanceMatching(acousticData) {
  if (!acousticData.impedance) {
    return 0; // If no impedance data, return neutral score
  }
  
  const impedance = acousticData.impedance;
  const frequencies = impedance.frequencies;
  const real = impedance.real;
  const imag = impedance.imaginary;
  
  // Simple check: ensure real part is reasonably flat in target range
  // This would be more sophisticated in a real implementation
  
  if (real.length < 2) {
    return 0;
  }
  
  // Calculate standard deviation of real impedance (lower is better)
  const mean = real.reduce((a, b) => a + b, 0) / real.length;
  const variance = real.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / real.length;
  const stdDev = Math.sqrt(variance);
  
  // Convert to score (0-1) where lower stdDev = better
  const maxAcceptableDeviation = 50; // arbitrary threshold for impedance
  const score = Math.max(0, 1 - (stdDev / maxAcceptableDeviation));
  
  return score;
}