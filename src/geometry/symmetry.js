/**
 * Geometry symmetry auto-detection for waveguide export.
 *
 * Analyzes phi-dependent formula parameters to determine the maximum valid
 * geometric symmetry, then returns the most restrictive valid quadrants value.
 *
 * Symmetry planes in ATH coordinate system:
 *   XZ-plane (y=0): f(phi) == f(-phi)  → quadrants '14' (right half, Q1+Q4)
 *   YZ-plane (x=0): f(phi) == f(π-phi) → quadrants '12' (top half,  Q1+Q2)
 *   Both planes:    quarter symmetry    → quadrants '1'  (Q1 only)
 */
import { evalParam } from './common.js';

const SYMMETRY_EPS = 1e-5;

// Sample angles away from the principal axes to avoid trivial matches
const SAMPLE_PHIS = [
  Math.PI / 8,
  Math.PI / 4,
  3 * Math.PI / 8,
  Math.PI / 2,
  5 * Math.PI / 8,
  3 * Math.PI / 4,
  7 * Math.PI / 8,
  Math.PI,
];

function approxEqual(a, b) {
  return Math.abs(a - b) < SYMMETRY_EPS * (Math.abs(a) + Math.abs(b) + 1.0);
}

function safeEval(param, phi) {
  try {
    const val = evalParam(param, phi);
    return typeof val === 'number' && isFinite(val) ? val : null;
  } catch {
    return null;
  }
}

/** Check if param satisfies f(phi) == f(-phi) for all sample angles (XZ-plane symmetry). */
function isXZSymmetric(param) {
  for (const phi of SAMPLE_PHIS) {
    const v1 = safeEval(param, phi);
    const v2 = safeEval(param, -phi);
    if (v1 === null || v2 === null) return false;
    if (!approxEqual(v1, v2)) return false;
  }
  return true;
}

/** Check if param satisfies f(phi) == f(π-phi) for all sample angles (YZ-plane symmetry). */
function isYZSymmetric(param) {
  for (const phi of SAMPLE_PHIS) {
    const v1 = safeEval(param, phi);
    const v2 = safeEval(param, Math.PI - phi);
    if (v1 === null || v2 === null) return false;
    if (!approxEqual(v1, v2)) return false;
  }
  return true;
}

/**
 * Analyze formula parameters and return the most restrictive valid quadrants value.
 *
 * Returns '1' (quarter), '14' (right half), '12' (top half), or '1234' (full).
 *
 * Conservative rules:
 * - Guiding curves (gcurveType != 0) → always '1234' (symmetry too complex to infer)
 * - All constant params → '1' (fully symmetric)
 * - Otherwise checks XZ and YZ symmetry numerically
 *
 * @param {object} preparedParams - Config params after prepareGeometryParams
 * @returns {string} quadrants value: '1' | '14' | '12' | '1234'
 */
export function detectGeometrySymmetry(preparedParams) {
  // Guiding curves may have complex asymmetric shapes — skip detection
  if (Number(preparedParams.gcurveType || 0) !== 0) return '1234';

  const type = preparedParams.type;

  // Collect the phi-dependent parameters for this formula type
  let paramsToCheck;
  if (type === 'R-OSSE') {
    paramsToCheck = [preparedParams.R, preparedParams.a];
  } else if (type === 'OSSE') {
    paramsToCheck = [preparedParams.a];
    // Include L and s if they are function-type (expression-based)
    if (typeof preparedParams.L === 'function') paramsToCheck.push(preparedParams.L);
    if (typeof preparedParams.s === 'function') paramsToCheck.push(preparedParams.s);
  } else {
    return '1234';
  }

  // Only function-type params can break symmetry; constants are always symmetric
  const funcParams = paramsToCheck.filter(p => typeof p === 'function');
  if (funcParams.length === 0) {
    return '1'; // All constants → full quarter symmetry
  }

  const xzSym = funcParams.every(isXZSymmetric);
  const yzSym = funcParams.every(isYZSymmetric);

  if (xzSym && yzSym) return '1';   // Quarter symmetry (smallest valid domain)
  if (xzSym)           return '14'; // Right half: symmetric about XZ plane
  if (yzSym)           return '12'; // Top half:   symmetric about YZ plane
  return '1234';                     // No detectable symmetry
}
