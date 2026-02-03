import { REFERENCE_HORNS, PHYSICAL_CONSTRAINTS } from './referenceData.js';
import {
  checkDataPresence,
  checkSplBounds,
  checkSplSmoothness,
  checkDirectivityBehavior,
  checkImpedanceBehavior,
  checkFrequencyResponseShape,
  checkNumericalValidity,
  validateMeshQuality
} from './checks.js';
import { calculateStats, findValueAtFrequency } from './stats.js';
import { generateSummaryMessage, generateReport } from './report.js';

export function createValidationManager() {
  /**
   * Run all physical sanity checks on simulation results
   * @param {Object} results - BEM simulation results
   * @param {Object} hornConfig - Horn configuration parameters
   * @returns {Object} Comprehensive validation result
   */
  function validatePhysicalBehavior(results, hornConfig = {}) {
    const checks = [];
    const diagnostics = {};

    // Extract data
    const frequencies = results.spl_on_axis?.frequencies || results.frequencies || [];
    const splValues = results.spl_on_axis?.spl || [];
    const diValues = results.di?.di || [];
    const impedanceReal = results.impedance?.real || [];
    const impedanceImag = results.impedance?.imaginary || [];

    // 1. Check for valid data
    checks.push(checkDataPresence(frequencies, splValues, diValues));

    // 2. Check SPL is within realistic bounds
    checks.push(...checkSplBounds(frequencies, splValues));

    // 3. Check SPL smoothness (no wild jumps)
    checks.push(checkSplSmoothness(frequencies, splValues));

    // 4. Check DI behavior
    checks.push(...checkDirectivityBehavior(frequencies, diValues));

    // 5. Check impedance behavior
    checks.push(...checkImpedanceBehavior(frequencies, impedanceReal, impedanceImag));

    // 6. Check frequency response shape
    if (hornConfig.cutoffFrequency) {
      checks.push(checkFrequencyResponseShape(frequencies, splValues, hornConfig.cutoffFrequency));
    }

    // 7. Check for numerical issues (NaN, Infinity)
    checks.push(checkNumericalValidity(results));

    // Calculate diagnostics
    diagnostics.splStats = calculateStats(splValues);
    diagnostics.diStats = calculateStats(diValues);
    diagnostics.impedanceRealStats = calculateStats(impedanceReal);
    diagnostics.frequencyRange = {
      min: Math.min(...frequencies),
      max: Math.max(...frequencies),
      points: frequencies.length
    };

    // Determine overall result
    const errors = checks.filter((c) => !c.passed && c.severity === 'error');
    const warnings = checks.filter((c) => !c.passed && c.severity === 'warning');

    return {
      passed: errors.length === 0,
      severity: errors.length > 0 ? 'error' : warnings.length > 0 ? 'warning' : 'info',
      message: generateSummaryMessage(errors, warnings),
      checks,
      diagnostics
    };
  }

  /**
   * Compare results against a reference horn
   */
  function validateAgainstReference(results, referenceKey = 'exponential_1inch') {
    const reference = REFERENCE_HORNS[referenceKey];
    if (!reference) {
      return {
        passed: false,
        severity: 'error',
        message: `Unknown reference horn: ${referenceKey}`,
        checks: [],
        diagnostics: {}
      };
    }

    const checks = [];
    const frequencies = results.spl_on_axis?.frequencies || [];
    const splValues = results.spl_on_axis?.spl || [];
    const diValues = results.di?.di || [];
    const impedanceReal = results.impedance?.real || [];

    // Find values at key frequencies
    const find1kHz = findValueAtFrequency(frequencies, splValues, 1000);
    const find4kHz = findValueAtFrequency(frequencies, diValues, 4000);
    const findDi1kHz = findValueAtFrequency(frequencies, diValues, 1000);
    const findZ1kHz = findValueAtFrequency(frequencies, impedanceReal, 1000);

    // Check SPL at 1kHz
    if (find1kHz !== null) {
      const expected = reference.expected.spl1kHz;
      checks.push({
        name: 'SPL at 1kHz',
        passed: find1kHz >= expected.min && find1kHz <= expected.max,
        severity: 'warning',
        message: `SPL at 1kHz: ${find1kHz.toFixed(1)} dB (expected ${expected.min}-${expected.max} dB)`,
        actual: find1kHz,
        expected
      });
    }

    // Check DI at 1kHz
    if (findDi1kHz !== null) {
      const expected = reference.expected.di1kHz;
      checks.push({
        name: 'DI at 1kHz',
        passed: findDi1kHz >= expected.min && findDi1kHz <= expected.max,
        severity: 'warning',
        message: `DI at 1kHz: ${findDi1kHz.toFixed(1)} dB (expected ${expected.min}-${expected.max} dB)`,
        actual: findDi1kHz,
        expected
      });
    }

    // Check DI at 4kHz
    if (find4kHz !== null) {
      const expected = reference.expected.di4kHz;
      checks.push({
        name: 'DI at 4kHz',
        passed: find4kHz >= expected.min && find4kHz <= expected.max,
        severity: 'warning',
        message: `DI at 4kHz: ${find4kHz.toFixed(1)} dB (expected ${expected.min}-${expected.max} dB)`,
        actual: find4kHz,
        expected
      });
    }

    // Check impedance at 1kHz
    if (findZ1kHz !== null) {
      const expected = reference.expected.impedanceReal1kHz;
      checks.push({
        name: 'Impedance at 1kHz',
        passed: findZ1kHz >= expected.min && findZ1kHz <= expected.max,
        severity: 'warning',
        message: `Real impedance at 1kHz: ${findZ1kHz.toFixed(0)} Ω (expected ${expected.min}-${expected.max} Ω)`,
        actual: findZ1kHz,
        expected
      });
    }

    const failed = checks.filter((c) => !c.passed);

    return {
      passed: failed.length === 0,
      severity: failed.length > 2 ? 'error' : failed.length > 0 ? 'warning' : 'info',
      message: `Comparison against ${reference.name}: ${checks.length - failed.length}/${checks.length} checks passed`,
      checks,
      diagnostics: { reference: referenceKey, referenceName: reference.name }
    };
  }

  /**
   * Run all validations and generate comprehensive report
   */
  function runFullValidation(results, hornConfig = {}) {
    const report = {
      timestamp: new Date().toISOString(),
      overallPassed: true,
      sections: {}
    };

    // 1. Physical behavior checks
    report.sections.physicalBehavior = validatePhysicalBehavior(results, hornConfig);
    if (!report.sections.physicalBehavior.passed) {
      report.overallPassed = false;
    }

    // 2. Reference comparison (if applicable)
    if (hornConfig.referenceHorn) {
      report.sections.referenceComparison = validateAgainstReference(results, hornConfig.referenceHorn);
      if (!report.sections.referenceComparison.passed) {
        report.overallPassed = false;
      }
    }

    // 3. Mesh quality (if mesh data available)
    if (results.mesh) {
      report.sections.meshQuality = validateMeshQuality(results.mesh);
      if (!report.sections.meshQuality.passed) {
        report.overallPassed = false;
      }
    }

    // Generate summary
    report.summary = generateReport(report);

    return report;
  }

  return {
    // Main validation methods
    validatePhysicalBehavior,
    validateAgainstReference,
    validateMeshQuality,
    runFullValidation,

    // Reference data access
    getReferenceHorns: () => REFERENCE_HORNS,
    getPhysicalConstraints: () => PHYSICAL_CONSTRAINTS,

    // Utility methods
    calculateStats,
    findValueAtFrequency
  };
}
