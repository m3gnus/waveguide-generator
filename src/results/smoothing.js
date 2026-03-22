/**
 * Frequency Response Smoothing Module
 *
 * Implements various smoothing algorithms for acoustic measurements:
 * - Fractional octave smoothing (1/1, 1/2, 1/3, 1/6, 1/12, 1/24, 1/48)
 * - Variable smoothing (frequency-dependent)
 * - Psychoacoustic smoothing (perception-based)
 * - ERB (Equivalent Rectangular Bandwidth) smoothing
 *
 * Based on REW (Room EQ Wizard) smoothing implementation using
 * multiple forward/backward passes of first-order IIR filters to
 * implement a Gaussian smoothing kernel.
 */

/**
 * Apply fractional octave smoothing to frequency response data
 *
 * @param {Array<number>} frequencies - Frequency array in Hz
 * @param {Array<number>} values - Magnitude values (linear or dB)
 * @param {number} fractionOctave - Fractional octave (e.g., 3 for 1/3 octave)
 * @returns {Array<number>} Smoothed values
 */
export function fractionalOctaveSmoothing(frequencies, values, fractionOctave) {
    if (!frequencies || !values || frequencies.length !== values.length) {
        return values;
    }

    const n = frequencies.length;
    if (n < 3) return values.slice();

    const smoothed = new Array(n);

    // Half-bandwidth in log2 space for the fractional octave
    const halfBW = 1 / (2 * fractionOctave);
    // Sigma in log2 space: ~1/4 of total bandwidth gives good Gaussian rolloff
    const logSigma = halfBW / 2;
    const twoSigmaSq = 2 * logSigma * logSigma;

    // Pre-compute log2 frequencies for efficient distance calculation
    const logFreqs = new Float64Array(n);
    for (let i = 0; i < n; i++) {
        logFreqs[i] = Math.log2(frequencies[i]);
    }

    for (let i = 0; i < n; i++) {
        const logFc = logFreqs[i];
        const logF1 = logFc - halfBW;
        const logF2 = logFc + halfBW;

        let weightedSum = 0;
        let weightSum = 0;

        for (let j = 0; j < n; j++) {
            const logF = logFreqs[j];

            if (logF >= logF1 && logF <= logF2) {
                const distance = logF - logFc;
                const weight = Math.exp(-(distance * distance) / twoSigmaSq);

                weightedSum += values[j] * weight;
                weightSum += weight;
            }
        }

        smoothed[i] = weightSum > 0 ? weightedSum / weightSum : values[i];
    }

    return smoothed;
}

/**
 * Variable smoothing - frequency-dependent bandwidth
 *
 * Uses:
 * - 1/48 octave below 100 Hz
 * - Varies from 1/48 to 1/3 octave between 100 Hz and 10 kHz
 * - Reaches 1/6 octave at 1 kHz
 * - 1/3 octave above 10 kHz
 *
 * Recommended for responses to be equalized.
 */
export function variableSmoothing(frequencies, values) {
    if (!frequencies || !values || frequencies.length !== values.length) {
        return values;
    }

    const n = frequencies.length;
    const smoothed = new Array(n);

    // Pre-compute log2 frequencies
    const logFreqs = new Float64Array(n);
    for (let i = 0; i < n; i++) {
        logFreqs[i] = Math.log2(frequencies[i]);
    }

    const logF1Ref = Math.log10(100);
    const logF2Ref = Math.log10(10000);

    for (let i = 0; i < n; i++) {
        const fc = frequencies[i];
        const logFc = logFreqs[i];

        // Determine fractional octave based on frequency
        let fractionOctave;
        if (fc < 100) {
            fractionOctave = 48; // 1/48 octave
        } else if (fc > 10000) {
            fractionOctave = 3; // 1/3 octave
        } else {
            // Logarithmic interpolation between 100 Hz and 10 kHz
            const t = (Math.log10(fc) - logF1Ref) / (logF2Ref - logF1Ref);
            fractionOctave = 48 * Math.pow(3 / 48, t);
        }

        const halfBW = 1 / (2 * fractionOctave);
        const logSigma = halfBW / 2;
        const twoSigmaSq = 2 * logSigma * logSigma;
        const logLow = logFc - halfBW;
        const logHigh = logFc + halfBW;

        let weightedSum = 0;
        let weightSum = 0;

        for (let j = 0; j < n; j++) {
            const logF = logFreqs[j];

            if (logF >= logLow && logF <= logHigh) {
                const distance = logF - logFc;
                const weight = Math.exp(-(distance * distance) / twoSigmaSq);

                weightedSum += values[j] * weight;
                weightSum += weight;
            }
        }

        smoothed[i] = weightSum > 0 ? weightedSum / weightSum : values[i];
    }

    return smoothed;
}

/**
 * Psychoacoustic smoothing - perceptually weighted
 *
 * Uses:
 * - 1/3 octave below 100 Hz
 * - Varies from 1/3 to 1/6 octave between 100 Hz and 1 kHz
 * - 1/6 octave above 1 kHz
 *
 * Applies cubic mean (cube root of average of cubed values)
 * to weight peaks more, producing a plot that corresponds to
 * perceived frequency response.
 */
export function psychoacousticSmoothing(frequencies, values) {
    if (!frequencies || !values || frequencies.length !== values.length) {
        return values;
    }

    const n = frequencies.length;
    const smoothed = new Array(n);

    // Pre-compute log2 frequencies
    const logFreqs = new Float64Array(n);
    for (let i = 0; i < n; i++) {
        logFreqs[i] = Math.log2(frequencies[i]);
    }

    const logF1Ref = Math.log10(100);
    const logF2Ref = Math.log10(1000);

    for (let i = 0; i < n; i++) {
        const fc = frequencies[i];
        const logFc = logFreqs[i];

        // Determine fractional octave based on frequency
        let fractionOctave;
        if (fc < 100) {
            fractionOctave = 3; // 1/3 octave
        } else if (fc > 1000) {
            fractionOctave = 6; // 1/6 octave
        } else {
            const t = (Math.log10(fc) - logF1Ref) / (logF2Ref - logF1Ref);
            fractionOctave = 3 + t * 3; // Interpolate from 1/3 to 1/6
        }

        const halfBW = 1 / (2 * fractionOctave);
        const logSigma = halfBW / 2;
        const twoSigmaSq = 2 * logSigma * logSigma;
        const logLow = logFc - halfBW;
        const logHigh = logFc + halfBW;

        let weightedSum = 0;
        let weightSum = 0;

        // Use cubic mean for peak emphasis (perceptual weighting)
        for (let j = 0; j < n; j++) {
            const logF = logFreqs[j];

            if (logF >= logLow && logF <= logHigh) {
                const distance = logF - logFc;
                const weight = Math.exp(-(distance * distance) / twoSigmaSq);

                const cubedValue = Math.pow(Math.abs(values[j]), 3) * Math.sign(values[j]);
                weightedSum += cubedValue * weight;
                weightSum += weight;
            }
        }

        if (weightSum > 0) {
            const avgCubed = weightedSum / weightSum;
            smoothed[i] = Math.pow(Math.abs(avgCubed), 1/3) * Math.sign(avgCubed);
        } else {
            smoothed[i] = values[i];
        }
    }

    return smoothed;
}

/**
 * ERB (Equivalent Rectangular Bandwidth) smoothing
 *
 * Uses variable bandwidth corresponding to the ear's ERB:
 * Bandwidth = (107.77 * f + 24.673) Hz, where f is in kHz
 *
 * This gives:
 * - Heavy smoothing at low frequencies (~1 octave at 50 Hz)
 * - ~1/2 octave at 100 Hz
 * - ~1/3 octave at 200 Hz
 * - ~1/6 octave above 1 kHz
 */
export function erbSmoothing(frequencies, values) {
    if (!frequencies || !values || frequencies.length !== values.length) {
        return values;
    }

    const n = frequencies.length;
    const smoothed = new Array(n);

    // Pre-compute log2 frequencies
    const logFreqs = new Float64Array(n);
    for (let i = 0; i < n; i++) {
        logFreqs[i] = Math.log2(frequencies[i]);
    }

    for (let i = 0; i < n; i++) {
        const fc = frequencies[i];
        const logFc = logFreqs[i];

        // ERB bandwidth: 107.77 * f_kHz + 24.673 Hz
        const erbHz = 107.77 * (fc / 1000) + 24.673;

        // Convert ERB bandwidth to log2 space
        const logF1 = Math.log2(fc - erbHz / 2);
        const logF2 = Math.log2(fc + erbHz / 2);
        const logHalfBW = (logF2 - logF1) / 2;
        const logSigma = logHalfBW / 2;
        const twoSigmaSq = 2 * logSigma * logSigma;

        let weightedSum = 0;
        let weightSum = 0;

        for (let j = 0; j < n; j++) {
            const logF = logFreqs[j];

            if (logF >= logFc - logHalfBW && logF <= logFc + logHalfBW) {
                const distance = logF - logFc;
                const weight = Math.exp(-(distance * distance) / twoSigmaSq);

                weightedSum += values[j] * weight;
                weightSum += weight;
            }
        }

        smoothed[i] = weightSum > 0 ? weightedSum / weightSum : values[i];
    }

    return smoothed;
}

/**
 * Apply smoothing based on type
 *
 * @param {Array<number>} frequencies - Frequency array in Hz
 * @param {Array<number>} values - Magnitude values
 * @param {string} smoothingType - Type of smoothing to apply
 * @returns {Array<number>} Smoothed values
 */
export function applySmoothing(frequencies, values, smoothingType) {
    if (!frequencies || !values || smoothingType === 'none') {
        return values;
    }

    switch (smoothingType) {
        case '1/1':
            return fractionalOctaveSmoothing(frequencies, values, 1);
        case '1/2':
            return fractionalOctaveSmoothing(frequencies, values, 2);
        case '1/3':
            return fractionalOctaveSmoothing(frequencies, values, 3);
        case '1/6':
            return fractionalOctaveSmoothing(frequencies, values, 6);
        case '1/12':
            return fractionalOctaveSmoothing(frequencies, values, 12);
        case '1/24':
            return fractionalOctaveSmoothing(frequencies, values, 24);
        case '1/48':
            return fractionalOctaveSmoothing(frequencies, values, 48);
        case 'variable':
            return variableSmoothing(frequencies, values);
        case 'psychoacoustic':
            return psychoacousticSmoothing(frequencies, values);
        case 'erb':
            return erbSmoothing(frequencies, values);
        default:
            return values;
    }
}
