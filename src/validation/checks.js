import { PHYSICAL_CONSTRAINTS } from './referenceData.js';

export function checkDataPresence(frequencies, splValues, diValues) {
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

export function checkSplBounds(frequencies, splValues) {
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
    message:
      minSpl >= minRealistic
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
    message:
      maxSpl <= maxRealistic
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
    message:
      splRange <= maxVariation
        ? `SPL variation (${splRange.toFixed(1)} dB) is within normal range`
        : `SPL variation (${splRange.toFixed(1)} dB) exceeds expected maximum (${maxVariation} dB)`,
    actual: splRange,
    expected: `<= ${maxVariation} dB`
  });

  return checks;
}

export function checkSplSmoothness(frequencies, splValues) {
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
    message:
      jumps.length === 0
        ? 'SPL curve is smooth (no sudden jumps)'
        : `Found ${jumps.length} sudden jumps in SPL (>${smoothnessThreshold} dB between points)`,
    actual:
      jumps.length > 0
        ? `${jumps.length} jumps, max ${Math.max(...jumps.map((j) => j.jump)).toFixed(1)} dB`
        : 'smooth',
    expected: `No jumps > ${smoothnessThreshold} dB`
  };
}

export function checkDirectivityBehavior(frequencies, diValues) {
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
    message:
      minDi >= minRealistic && maxDi <= maxRealistic
        ? `DI range (${minDi.toFixed(1)} to ${maxDi.toFixed(1)} dB) is realistic`
        : `DI range (${minDi.toFixed(1)} to ${maxDi.toFixed(1)} dB) outside realistic bounds`,
    actual: { min: minDi, max: maxDi },
    expected: { min: minRealistic, max: maxRealistic }
  });

  // Check DI generally increases with frequency
  // Find the trend by comparing low vs high frequency averages
  const midIndex = Math.floor(diValues.length / 2);
  const lowFreqAvg = diValues.slice(0, midIndex).reduce((a, b) => a + b, 0) / midIndex;
  const highFreqAvg =
    diValues.slice(midIndex).reduce((a, b) => a + b, 0) / (diValues.length - midIndex);
  const diTrend = highFreqAvg - lowFreqAvg;

  checks.push({
    name: 'DI Frequency Trend',
    passed: diTrend >= -maxDecreasePerOctave,
    severity: 'warning',
    message:
      diTrend >= -maxDecreasePerOctave
        ? `DI trend is correct (increases ${diTrend.toFixed(1)} dB from low to high freq)`
        : `DI unexpectedly decreases with frequency (${diTrend.toFixed(1)} dB)`,
    actual: diTrend,
    expected: `>= -${maxDecreasePerOctave} dB (should generally increase)`
  });

  return checks;
}

export function checkImpedanceBehavior(frequencies, realValues, imagValues) {
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
    message:
      minReal >= constraints.realMinRealistic && maxReal <= constraints.realMaxRealistic
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
    message:
      minImag >= constraints.imagMinRealistic && maxImag <= constraints.imagMaxRealistic
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
    message:
      highFreqDeviation <= constraints.highFreqTolerance
        ? `High-freq impedance (${highFreqReal.toFixed(0)} Ω) approaches ρc (${constraints.highFreqRealTarget} Ω)`
        : `High-freq impedance (${highFreqReal.toFixed(0)} Ω) deviates from expected ρc (${constraints.highFreqRealTarget} Ω)`,
    actual: highFreqReal,
    expected: `${constraints.highFreqRealTarget} ± ${constraints.highFreqTolerance} Ω`
  });

  return checks;
}

export function checkFrequencyResponseShape(frequencies, splValues, cutoffFrequency) {
  // Find the index closest to cutoff frequency
  const cutoffIndex = frequencies.findIndex((f) => f >= cutoffFrequency);

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
    severity: !rolloffOk || !flatnessOk ? 'warning' : 'info',
    message: `Below cutoff: ${rolloffPerOctave.toFixed(1)} dB/octave rolloff. Above cutoff: ${ripple.toFixed(1)} dB ripple.`,
    actual: { rolloffPerOctave, ripple },
    expected: { rolloff: `>= ${rolloffBelowCutoff - 3} dB/oct`, ripple: `<= ${flatnessAboveCutoff} dB` }
  };
}

export function checkNumericalValidity(results) {
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
    message:
      issues.length === 0
        ? 'All values are valid numbers'
        : `Found ${issues.length} invalid values: ${issues.slice(0, 3).join(', ')}${issues.length > 3 ? '...' : ''}`,
    actual: issues.length === 0 ? 'all valid' : issues,
    expected: 'no NaN or Infinity values'
  };
}

export function validateMeshQuality(mesh) {
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

  const failed = checks.filter((c) => !c.passed && c.severity === 'error');

  return {
    passed: failed.length === 0,
    severity: failed.length > 0 ? 'error' : 'info',
    message: `Mesh quality: ${checks.filter((c) => c.passed).length}/${checks.length} checks passed`,
    checks,
    diagnostics: { elementCount, maxAspectRatio, avgAspectRatio }
  };
}

export function calculateAspectRatios(vertices, indices) {
  const ratios = [];
  const numTriangles = indices.length / 3;

  for (let i = 0; i < numTriangles; i++) {
    const i0 = indices[i * 3];
    const i1 = indices[i * 3 + 1];
    const i2 = indices[i * 3 + 2];

    const v0 = [vertices[i0 * 3], vertices[i0 * 3 + 1], vertices[i0 * 3 + 2]];
    const v1 = [vertices[i1 * 3], vertices[i1 * 3 + 1], vertices[i1 * 3 + 2]];
    const v2 = [vertices[i2 * 3], vertices[i2 * 3 + 1], vertices[i2 * 3 + 2]];

    const e1 = Math.sqrt(
      Math.pow(v1[0] - v0[0], 2) + Math.pow(v1[1] - v0[1], 2) + Math.pow(v1[2] - v0[2], 2)
    );
    const e2 = Math.sqrt(
      Math.pow(v2[0] - v1[0], 2) + Math.pow(v2[1] - v1[1], 2) + Math.pow(v2[2] - v1[2], 2)
    );
    const e3 = Math.sqrt(
      Math.pow(v0[0] - v2[0], 2) + Math.pow(v0[1] - v2[1], 2) + Math.pow(v0[2] - v2[2], 2)
    );

    const maxEdge = Math.max(e1, e2, e3);
    const minEdge = Math.min(e1, e2, e3);

    ratios.push(minEdge > 0 ? maxEdge / minEdge : 999);
  }

  return ratios;
}
