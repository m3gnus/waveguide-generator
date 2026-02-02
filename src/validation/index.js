/**
 * Validation module for MWG - Mathematical Waveguide Generator.
 * Builds trust in solver and optimization results through reference comparisons
 * and physical sanity checks.
 *
 * @module validation
 */

/**
 * Reference horn data for validation
 * Based on well-documented acoustic horn designs
 */
const REFERENCE_HORNS = {
  // Standard 1-inch compression driver on exponential horn
  exponential_1inch: {
    name: 'Exponential Horn (1" throat, 60° coverage)',
    throatDiameter: 25.4, // mm
    mouthDiameter: 300,   // mm
    length: 400,          // mm
    coverageAngle: 60,    // degrees
    // Expected behavior at key frequencies
    expected: {
      // Cutoff frequency (below this, rapid rolloff)
      cutoffFrequency: 500, // Hz
      // SPL should be within this range at 1kHz (1W/1m equivalent)
      spl1kHz: { min: 105, max: 115 }, // dB
      // DI should increase with frequency
      di1kHz: { min: 6, max: 12 },     // dB
      di4kHz: { min: 10, max: 18 },    // dB
      // Impedance at throat (normalized to ρc)
      impedanceReal1kHz: { min: 300, max: 600 }, // acoustic ohms
    }
  },

  // Conical horn reference
  conical_1inch: {
    name: 'Conical Horn (1" throat)',
    throatDiameter: 25.4,
    mouthDiameter: 250,
    length: 350,
    coverageAngle: 90,
    expected: {
      cutoffFrequency: 700,
      spl1kHz: { min: 100, max: 112 },
      di1kHz: { min: 4, max: 10 },
      di4kHz: { min: 8, max: 14 },
      impedanceReal1kHz: { min: 250, max: 500 },
    }
  }
};

/**
 * Physical constraints for acoustic horn behavior
 * Based on acoustic theory and empirical measurements
 */
const PHYSICAL_CONSTRAINTS = {
  // SPL constraints
  spl: {
    minRealistic: 60,      // dB - anything below is likely numerical error
    maxRealistic: 150,     // dB - anything above is unrealistic for horns
    maxVariation: 30,      // dB - max realistic variation across frequency
    smoothnessThreshold: 6 // dB - max jump between adjacent frequency points
  },

  // Directivity Index constraints
  di: {
    minRealistic: 0,       // dB - omnidirectional
    maxRealistic: 25,      // dB - very narrow beam
    shouldIncreaseWithFreq: true,
    maxDecreasePerOctave: 3 // dB - DI shouldn't decrease much with frequency
  },

  // Impedance constraints (acoustic ohms)
  impedance: {
    realMinRealistic: 50,
    realMaxRealistic: 2000,
    imagMinRealistic: -1000,
    imagMaxRealistic: 1000,
    // At high frequencies, should approach ρc ≈ 415 acoustic ohms
    highFreqRealTarget: 415,
    highFreqTolerance: 200
  },

  // Frequency response shape constraints
  frequencyResponse: {
    // Below cutoff: expect rolloff
    rolloffBelowCutoff: 6,  // dB/octave minimum expected
    // Above cutoff: should be relatively flat
    flatnessAboveCutoff: 6  // dB max ripple in passband
  }
};

/**
 * Validation result structure
 * @typedef {Object} ValidationResult
 * @property {boolean} passed - Overall pass/fail
 * @property {string} severity - 'error' | 'warning' | 'info'
 * @property {string} message - Human-readable summary
 * @property {Array<ValidationCheck>} checks - Individual check results
 * @property {Object} diagnostics - Detailed diagnostic data
 */

/**
 * Individual validation check
 * @typedef {Object} ValidationCheck
 * @property {string} name - Check name
 * @property {boolean} passed - Pass/fail
 * @property {string} severity - 'error' | 'warning' | 'info'
 * @property {string} message - Description of result
 * @property {*} actual - Actual value found
 * @property {*} expected - Expected value/range
 */

/**
 * Create a validation manager for BEM simulation results
 * @returns {Object} Validation manager with comprehensive check methods
 */
export function createValidationManager() {

  /**
   * Run all physical sanity checks on simulation results
   * @param {Object} results - BEM simulation results
   * @param {Object} hornConfig - Horn configuration parameters
   * @returns {ValidationResult} Comprehensive validation result
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
    const errors = checks.filter(c => !c.passed && c.severity === 'error');
    const warnings = checks.filter(c => !c.passed && c.severity === 'warning');

    return {
      passed: errors.length === 0,
      severity: errors.length > 0 ? 'error' : (warnings.length > 0 ? 'warning' : 'info'),
      message: generateSummaryMessage(errors, warnings),
      checks,
      diagnostics
    };
  }

  /**
   * Check that required data is present
   */
  function checkDataPresence(frequencies, splValues, diValues) {
    const hasFrequencies = frequencies.length > 0;
    const hasSpl = splValues.length > 0;
    const lengthsMatch = frequencies.length === splValues.length;

    const passed = hasFrequencies && hasSpl && lengthsMatch;

    return {
      name: 'Data Presence',
      passed,
      severity: 'error',
      message: passed
        ? `Valid data: ${frequencies.length} frequency points`
        : `Missing or mismatched data: ${frequencies.length} frequencies, ${splValues.length} SPL values`,
      actual: { frequencies: frequencies.length, spl: splValues.length },
      expected: { frequencies: '>0', spl: 'same as frequencies' }
    };
  }

  /**
   * Check SPL values are within realistic bounds
   */
  function checkSplBounds(frequencies, splValues) {
    const checks = [];
    const { minRealistic, maxRealistic, maxVariation } = PHYSICAL_CONSTRAINTS.spl;

    const minSpl = Math.min(...splValues);
    const maxSpl = Math.max(...splValues);
    const splRange = maxSpl - minSpl;

    // Check minimum SPL
    checks.push({
      name: 'SPL Minimum Bound',
      passed: minSpl >= minRealistic,
      severity: minSpl < minRealistic - 20 ? 'error' : 'warning',
      message: minSpl >= minRealistic
        ? `Minimum SPL (${minSpl.toFixed(1)} dB) is realistic`
        : `Minimum SPL (${minSpl.toFixed(1)} dB) is below realistic threshold (${minRealistic} dB)`,
      actual: minSpl,
      expected: `>= ${minRealistic} dB`
    });

    // Check maximum SPL
    checks.push({
      name: 'SPL Maximum Bound',
      passed: maxSpl <= maxRealistic,
      severity: maxSpl > maxRealistic + 20 ? 'error' : 'warning',
      message: maxSpl <= maxRealistic
        ? `Maximum SPL (${maxSpl.toFixed(1)} dB) is realistic`
        : `Maximum SPL (${maxSpl.toFixed(1)} dB) exceeds realistic threshold (${maxRealistic} dB)`,
      actual: maxSpl,
      expected: `<= ${maxRealistic} dB`
    });

    // Check variation
    checks.push({
      name: 'SPL Variation',
      passed: splRange <= maxVariation,
      severity: 'warning',
      message: splRange <= maxVariation
        ? `SPL variation (${splRange.toFixed(1)} dB) is within normal range`
        : `SPL variation (${splRange.toFixed(1)} dB) exceeds expected maximum (${maxVariation} dB)`,
      actual: splRange,
      expected: `<= ${maxVariation} dB`
    });

    return checks;
  }

  /**
   * Check SPL curve is reasonably smooth
   */
  function checkSplSmoothness(frequencies, splValues) {
    const { smoothnessThreshold } = PHYSICAL_CONSTRAINTS.spl;
    const jumps = [];

    for (let i = 1; i < splValues.length; i++) {
      const diff = Math.abs(splValues[i] - splValues[i - 1]);
      if (diff > smoothnessThreshold) {
        jumps.push({
          index: i,
          freq: frequencies[i],
          jump: diff
        });
      }
    }

    return {
      name: 'SPL Smoothness',
      passed: jumps.length === 0,
      severity: jumps.length > 3 ? 'error' : 'warning',
      message: jumps.length === 0
        ? 'SPL curve is smooth (no sudden jumps)'
        : `Found ${jumps.length} sudden jumps in SPL (>${smoothnessThreshold} dB between points)`,
      actual: jumps.length > 0 ? `${jumps.length} jumps, max ${Math.max(...jumps.map(j => j.jump)).toFixed(1)} dB` : 'smooth',
      expected: `No jumps > ${smoothnessThreshold} dB`
    };
  }

  /**
   * Check Directivity Index behavior
   */
  function checkDirectivityBehavior(frequencies, diValues) {
    const checks = [];
    const { minRealistic, maxRealistic, maxDecreasePerOctave } = PHYSICAL_CONSTRAINTS.di;

    if (diValues.length === 0) {
      checks.push({
        name: 'DI Data Present',
        passed: false,
        severity: 'warning',
        message: 'No directivity index data available',
        actual: 'none',
        expected: 'DI values'
      });
      return checks;
    }

    const minDi = Math.min(...diValues);
    const maxDi = Math.max(...diValues);

    // Check DI bounds
    checks.push({
      name: 'DI Bounds',
      passed: minDi >= minRealistic && maxDi <= maxRealistic,
      severity: 'warning',
      message: (minDi >= minRealistic && maxDi <= maxRealistic)
        ? `DI range (${minDi.toFixed(1)} to ${maxDi.toFixed(1)} dB) is realistic`
        : `DI range (${minDi.toFixed(1)} to ${maxDi.toFixed(1)} dB) outside realistic bounds`,
      actual: { min: minDi, max: maxDi },
      expected: { min: minRealistic, max: maxRealistic }
    });

    // Check DI generally increases with frequency
    // Find the trend by comparing low vs high frequency averages
    const midIndex = Math.floor(diValues.length / 2);
    const lowFreqAvg = diValues.slice(0, midIndex).reduce((a, b) => a + b, 0) / midIndex;
    const highFreqAvg = diValues.slice(midIndex).reduce((a, b) => a + b, 0) / (diValues.length - midIndex);
    const diTrend = highFreqAvg - lowFreqAvg;

    checks.push({
      name: 'DI Frequency Trend',
      passed: diTrend >= -maxDecreasePerOctave,
      severity: 'warning',
      message: diTrend >= -maxDecreasePerOctave
        ? `DI trend is correct (increases ${diTrend.toFixed(1)} dB from low to high freq)`
        : `DI unexpectedly decreases with frequency (${diTrend.toFixed(1)} dB)`,
      actual: diTrend,
      expected: `>= -${maxDecreasePerOctave} dB (should generally increase)`
    });

    return checks;
  }

  /**
   * Check impedance behavior
   */
  function checkImpedanceBehavior(frequencies, realValues, imagValues) {
    const checks = [];
    const constraints = PHYSICAL_CONSTRAINTS.impedance;

    if (realValues.length === 0) {
      checks.push({
        name: 'Impedance Data Present',
        passed: false,
        severity: 'warning',
        message: 'No impedance data available',
        actual: 'none',
        expected: 'Impedance values'
      });
      return checks;
    }

    const minReal = Math.min(...realValues);
    const maxReal = Math.max(...realValues);
    const minImag = Math.min(...imagValues);
    const maxImag = Math.max(...imagValues);

    // Check real part bounds
    checks.push({
      name: 'Impedance Real Part Bounds',
      passed: minReal >= constraints.realMinRealistic && maxReal <= constraints.realMaxRealistic,
      severity: 'warning',
      message: (minReal >= constraints.realMinRealistic && maxReal <= constraints.realMaxRealistic)
        ? `Real impedance (${minReal.toFixed(0)} to ${maxReal.toFixed(0)} Ω) is within realistic range`
        : `Real impedance (${minReal.toFixed(0)} to ${maxReal.toFixed(0)} Ω) outside realistic range`,
      actual: { min: minReal, max: maxReal },
      expected: { min: constraints.realMinRealistic, max: constraints.realMaxRealistic }
    });

    // Check imaginary part bounds
    checks.push({
      name: 'Impedance Imaginary Part Bounds',
      passed: minImag >= constraints.imagMinRealistic && maxImag <= constraints.imagMaxRealistic,
      severity: 'warning',
      message: (minImag >= constraints.imagMinRealistic && maxImag <= constraints.imagMaxRealistic)
        ? `Imaginary impedance (${minImag.toFixed(0)} to ${maxImag.toFixed(0)} Ω) is within realistic range`
        : `Imaginary impedance (${minImag.toFixed(0)} to ${maxImag.toFixed(0)} Ω) outside realistic range`,
      actual: { min: minImag, max: maxImag },
      expected: { min: constraints.imagMinRealistic, max: constraints.imagMaxRealistic }
    });

    // Check high-frequency behavior (should approach ρc ≈ 415)
    const highFreqReal = realValues.slice(-5).reduce((a, b) => a + b, 0) / 5;
    const highFreqDeviation = Math.abs(highFreqReal - constraints.highFreqRealTarget);

    checks.push({
      name: 'High Frequency Impedance',
      passed: highFreqDeviation <= constraints.highFreqTolerance,
      severity: 'info',
      message: highFreqDeviation <= constraints.highFreqTolerance
        ? `High-freq impedance (${highFreqReal.toFixed(0)} Ω) approaches ρc (${constraints.highFreqRealTarget} Ω)`
        : `High-freq impedance (${highFreqReal.toFixed(0)} Ω) deviates from expected ρc (${constraints.highFreqRealTarget} Ω)`,
      actual: highFreqReal,
      expected: `${constraints.highFreqRealTarget} ± ${constraints.highFreqTolerance} Ω`
    });

    return checks;
  }

  /**
   * Check frequency response shape (rolloff below cutoff, flat above)
   */
  function checkFrequencyResponseShape(frequencies, splValues, cutoffFrequency) {
    // Find the index closest to cutoff frequency
    const cutoffIndex = frequencies.findIndex(f => f >= cutoffFrequency);

    if (cutoffIndex < 2 || cutoffIndex > frequencies.length - 3) {
      return {
        name: 'Frequency Response Shape',
        passed: true,
        severity: 'info',
        message: 'Cannot validate frequency response shape (cutoff outside frequency range)',
        actual: 'insufficient data',
        expected: 'cutoff within range'
      };
    }

    // Check rolloff below cutoff
    const belowCutoffSpl = splValues.slice(0, cutoffIndex);
    const rolloff = belowCutoffSpl[belowCutoffSpl.length - 1] - belowCutoffSpl[0];
    const octavesBelow = Math.log2(frequencies[cutoffIndex] / frequencies[0]);
    const rolloffPerOctave = rolloff / octavesBelow;

    // Check flatness above cutoff
    const aboveCutoffSpl = splValues.slice(cutoffIndex);
    const maxAbove = Math.max(...aboveCutoffSpl);
    const minAbove = Math.min(...aboveCutoffSpl);
    const ripple = maxAbove - minAbove;

    const { rolloffBelowCutoff, flatnessAboveCutoff } = PHYSICAL_CONSTRAINTS.frequencyResponse;

    const rolloffOk = rolloffPerOctave >= rolloffBelowCutoff - 3; // Allow some tolerance
    const flatnessOk = ripple <= flatnessAboveCutoff;

    return {
      name: 'Frequency Response Shape',
      passed: rolloffOk && flatnessOk,
      severity: (!rolloffOk || !flatnessOk) ? 'warning' : 'info',
      message: `Below cutoff: ${rolloffPerOctave.toFixed(1)} dB/octave rolloff. Above cutoff: ${ripple.toFixed(1)} dB ripple.`,
      actual: { rolloffPerOctave, ripple },
      expected: { rolloff: `>= ${rolloffBelowCutoff - 3} dB/oct`, ripple: `<= ${flatnessAboveCutoff} dB` }
    };
  }

  /**
   * Check for numerical issues (NaN, Infinity, etc.)
   */
  function checkNumericalValidity(results) {
    const issues = [];

    function checkArray(arr, name) {
      if (!Array.isArray(arr)) return;
      arr.forEach((val, i) => {
        if (typeof val !== 'number' || !isFinite(val)) {
          issues.push(`${name}[${i}] = ${val}`);
        }
      });
    }

    checkArray(results.spl_on_axis?.spl, 'SPL');
    checkArray(results.spl_on_axis?.frequencies, 'Frequencies');
    checkArray(results.di?.di, 'DI');
    checkArray(results.impedance?.real, 'Impedance Real');
    checkArray(results.impedance?.imaginary, 'Impedance Imag');

    return {
      name: 'Numerical Validity',
      passed: issues.length === 0,
      severity: 'error',
      message: issues.length === 0
        ? 'All values are valid numbers'
        : `Found ${issues.length} invalid values: ${issues.slice(0, 3).join(', ')}${issues.length > 3 ? '...' : ''}`,
      actual: issues.length === 0 ? 'all valid' : issues,
      expected: 'no NaN or Infinity values'
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

    const failed = checks.filter(c => !c.passed);

    return {
      passed: failed.length === 0,
      severity: failed.length > 2 ? 'error' : (failed.length > 0 ? 'warning' : 'info'),
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

  /**
   * Validate mesh quality for BEM accuracy
   */
  function validateMeshQuality(mesh) {
    const checks = [];
    const vertices = mesh.vertices || [];
    const indices = mesh.indices || [];

    if (vertices.length === 0 || indices.length === 0) {
      return {
        passed: false,
        severity: 'error',
        message: 'No mesh data available',
        checks: [],
        diagnostics: {}
      };
    }

    // Calculate triangle aspect ratios
    const aspectRatios = calculateAspectRatios(vertices, indices);
    const maxAspectRatio = Math.max(...aspectRatios);
    const avgAspectRatio = aspectRatios.reduce((a, b) => a + b, 0) / aspectRatios.length;

    checks.push({
      name: 'Maximum Aspect Ratio',
      passed: maxAspectRatio <= 5.0,
      severity: maxAspectRatio > 10 ? 'error' : 'warning',
      message: `Max aspect ratio: ${maxAspectRatio.toFixed(2)} (threshold: 5.0)`,
      actual: maxAspectRatio,
      expected: '<= 5.0'
    });

    checks.push({
      name: 'Average Aspect Ratio',
      passed: avgAspectRatio <= 2.5,
      severity: 'info',
      message: `Average aspect ratio: ${avgAspectRatio.toFixed(2)}`,
      actual: avgAspectRatio,
      expected: '<= 2.5'
    });

    // Check element count
    const elementCount = indices.length / 3;
    checks.push({
      name: 'Element Count',
      passed: elementCount >= 500,
      severity: elementCount < 200 ? 'error' : 'warning',
      message: `Mesh has ${elementCount} elements`,
      actual: elementCount,
      expected: '>= 500 for accurate BEM'
    });

    const failed = checks.filter(c => !c.passed && c.severity === 'error');

    return {
      passed: failed.length === 0,
      severity: failed.length > 0 ? 'error' : 'info',
      message: `Mesh quality: ${checks.filter(c => c.passed).length}/${checks.length} checks passed`,
      checks,
      diagnostics: { elementCount, maxAspectRatio, avgAspectRatio }
    };
  }

  // ============ Helper Functions ============

  function calculateStats(values) {
    if (!values || values.length === 0) return null;
    const validValues = values.filter(v => typeof v === 'number' && isFinite(v));
    if (validValues.length === 0) return null;

    const sum = validValues.reduce((a, b) => a + b, 0);
    const mean = sum / validValues.length;
    const variance = validValues.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / validValues.length;

    return {
      min: Math.min(...validValues),
      max: Math.max(...validValues),
      mean,
      stdDev: Math.sqrt(variance),
      count: validValues.length
    };
  }

  function findValueAtFrequency(frequencies, values, targetFreq) {
    if (!frequencies.length || !values.length) return null;

    // Find closest frequency
    let closestIndex = 0;
    let closestDiff = Math.abs(frequencies[0] - targetFreq);

    for (let i = 1; i < frequencies.length; i++) {
      const diff = Math.abs(frequencies[i] - targetFreq);
      if (diff < closestDiff) {
        closestDiff = diff;
        closestIndex = i;
      }
    }

    // Only return if reasonably close (within 20%)
    if (closestDiff / targetFreq > 0.2) return null;

    return values[closestIndex];
  }

  function calculateAspectRatios(vertices, indices) {
    const ratios = [];
    const numTriangles = indices.length / 3;

    for (let i = 0; i < numTriangles; i++) {
      const i0 = indices[i * 3];
      const i1 = indices[i * 3 + 1];
      const i2 = indices[i * 3 + 2];

      const v0 = [vertices[i0 * 3], vertices[i0 * 3 + 1], vertices[i0 * 3 + 2]];
      const v1 = [vertices[i1 * 3], vertices[i1 * 3 + 1], vertices[i1 * 3 + 2]];
      const v2 = [vertices[i2 * 3], vertices[i2 * 3 + 1], vertices[i2 * 3 + 2]];

      const e1 = Math.sqrt(Math.pow(v1[0] - v0[0], 2) + Math.pow(v1[1] - v0[1], 2) + Math.pow(v1[2] - v0[2], 2));
      const e2 = Math.sqrt(Math.pow(v2[0] - v1[0], 2) + Math.pow(v2[1] - v1[1], 2) + Math.pow(v2[2] - v1[2], 2));
      const e3 = Math.sqrt(Math.pow(v0[0] - v2[0], 2) + Math.pow(v0[1] - v2[1], 2) + Math.pow(v0[2] - v2[2], 2));

      const maxEdge = Math.max(e1, e2, e3);
      const minEdge = Math.min(e1, e2, e3);

      ratios.push(minEdge > 0 ? maxEdge / minEdge : 999);
    }

    return ratios;
  }

  function generateSummaryMessage(errors, warnings) {
    if (errors.length === 0 && warnings.length === 0) {
      return 'All physical sanity checks passed';
    }
    if (errors.length > 0) {
      return `${errors.length} critical issues found: ${errors.map(e => e.name).join(', ')}`;
    }
    return `${warnings.length} warnings: ${warnings.map(w => w.name).join(', ')}`;
  }

  function generateReport(report) {
    const lines = ['=== BEM Validation Report ===', ''];

    for (const [sectionName, section] of Object.entries(report.sections)) {
      lines.push(`## ${sectionName}`);
      lines.push(`Status: ${section.passed ? 'PASSED' : 'FAILED'} (${section.severity})`);
      lines.push(section.message);
      lines.push('');

      if (section.checks) {
        for (const check of section.checks) {
          const icon = check.passed ? '✓' : (check.severity === 'error' ? '✗' : '⚠');
          lines.push(`  ${icon} ${check.name}: ${check.message}`);
        }
      }
      lines.push('');
    }

    lines.push(`Overall: ${report.overallPassed ? 'PASSED' : 'FAILED'}`);
    return lines.join('\n');
  }

  // ============ Public API ============

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

// Export singleton instance
export default createValidationManager();
