/**
 * Reference horn data for validation
 * Based on well-documented acoustic horn designs
 */
export const REFERENCE_HORNS = {
  // Standard 1-inch compression driver on exponential horn
  exponential_1inch: {
    name: 'Exponential Horn (1" throat, 60° coverage)',
    throatDiameter: 25.4, // mm
    mouthDiameter: 300, // mm
    length: 400, // mm
    coverageAngle: 60, // degrees
    // Expected behavior at key frequencies
    expected: {
      // Cutoff frequency (below this, rapid rolloff)
      cutoffFrequency: 500, // Hz
      // SPL should be within this range at 1kHz (1W/1m equivalent)
      spl1kHz: { min: 105, max: 115 }, // dB
      // DI should increase with frequency
      di1kHz: { min: 6, max: 12 }, // dB
      di4kHz: { min: 10, max: 18 }, // dB
      // Impedance at throat (normalized to ρc)
      impedanceReal1kHz: { min: 300, max: 600 } // acoustic ohms
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
      impedanceReal1kHz: { min: 250, max: 500 }
    }
  }
};

/**
 * Physical constraints for acoustic horn behavior
 * Based on acoustic theory and empirical measurements
 */
export const PHYSICAL_CONSTRAINTS = {
  // SPL constraints
  spl: {
    minRealistic: 60, // dB - anything below is likely numerical error
    maxRealistic: 150, // dB - anything above is unrealistic for horns
    maxVariation: 30, // dB - max realistic variation across frequency
    smoothnessThreshold: 6 // dB - max jump between adjacent frequency points
  },

  // Directivity Index constraints
  di: {
    minRealistic: 0, // dB - omnidirectional
    maxRealistic: 25, // dB - very narrow beam
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
    rolloffBelowCutoff: 6, // dB/octave minimum expected
    // Above cutoff: should be relatively flat
    flatnessAboveCutoff: 6 // dB max ripple in passband
  }
};
