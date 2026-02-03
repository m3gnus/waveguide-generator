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

    // For each frequency point, apply Gaussian weighting within bandwidth
    for (let i = 0; i < n; i++) {
        const fc = frequencies[i]; // Center frequency

        // Calculate fractional octave bandwidth
        const f1 = fc / Math.pow(2, 1 / (2 * fractionOctave));
        const f2 = fc * Math.pow(2, 1 / (2 * fractionOctave));

        let weightedSum = 0;
        let weightSum = 0;

        // Apply Gaussian weighting to nearby points
        for (let j = 0; j < n; j++) {
            const f = frequencies[j];

            if (f >= f1 && f <= f2) {
                // Gaussian weight based on distance from center
                const sigma = (f2 - f1) / 4; // Standard deviation
                const distance = Math.log(f / fc);
                const weight = Math.exp(-(distance * distance) / (2 * sigma * sigma));

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

    for (let i = 0; i < n; i++) {
        const fc = frequencies[i];

        // Determine fractional octave based on frequency
        let fractionOctave;
        if (fc < 100) {
            fractionOctave = 48; // 1/48 octave
        } else if (fc > 10000) {
            fractionOctave = 3; // 1/3 octave
        } else {
            // Logarithmic interpolation between 100 Hz and 10 kHz
            const logF = Math.log10(fc);
            const logF1 = Math.log10(100);
            const logF2 = Math.log10(10000);
            const t = (logF - logF1) / (logF2 - logF1);

            // Interpolate from 1/48 at 100Hz to 1/3 at 10kHz
            // At 1kHz (t=0.5), should be 1/6
            fractionOctave = 48 * Math.pow(3/48, t);
        }

        // Apply fractional octave smoothing with variable bandwidth
        const f1 = fc / Math.pow(2, 1 / (2 * fractionOctave));
        const f2 = fc * Math.pow(2, 1 / (2 * fractionOctave));

        let weightedSum = 0;
        let weightSum = 0;

        for (let j = 0; j < n; j++) {
            const f = frequencies[j];

            if (f >= f1 && f <= f2) {
                const sigma = (f2 - f1) / 4;
                const distance = Math.log(f / fc);
                const weight = Math.exp(-(distance * distance) / (2 * sigma * sigma));

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

    for (let i = 0; i < n; i++) {
        const fc = frequencies[i];

        // Determine fractional octave based on frequency
        let fractionOctave;
        if (fc < 100) {
            fractionOctave = 3; // 1/3 octave
        } else if (fc > 1000) {
            fractionOctave = 6; // 1/6 octave
        } else {
            // Linear interpolation between 100 Hz and 1 kHz
            const logF = Math.log10(fc);
            const logF1 = Math.log10(100);
            const logF2 = Math.log10(1000);
            const t = (logF - logF1) / (logF2 - logF1);

            fractionOctave = 3 + t * 3; // Interpolate from 1/3 to 1/6
        }

        const f1 = fc / Math.pow(2, 1 / (2 * fractionOctave));
        const f2 = fc * Math.pow(2, 1 / (2 * fractionOctave));

        let weightedSum = 0;
        let weightSum = 0;

        // Use cubic mean for peak emphasis
        for (let j = 0; j < n; j++) {
            const f = frequencies[j];

            if (f >= f1 && f <= f2) {
                const sigma = (f2 - f1) / 4;
                const distance = Math.log(f / fc);
                const weight = Math.exp(-(distance * distance) / (2 * sigma * sigma));

                // Cubic mean: cube the values
                const cubedValue = Math.pow(Math.abs(values[j]), 3) * Math.sign(values[j]);
                weightedSum += cubedValue * weight;
                weightSum += weight;
            }
        }

        if (weightSum > 0) {
            // Cube root of the average
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

    for (let i = 0; i < n; i++) {
        const fc = frequencies[i];

        // Calculate ERB bandwidth in Hz
        const fKhz = fc / 1000;
        const erbHz = 107.77 * fKhz + 24.673;

        // Convert to frequency range
        const f1 = fc - erbHz / 2;
        const f2 = fc + erbHz / 2;

        let weightedSum = 0;
        let weightSum = 0;

        for (let j = 0; j < n; j++) {
            const f = frequencies[j];

            if (f >= f1 && f <= f2) {
                const sigma = erbHz / 4;
                const distance = f - fc;
                const weight = Math.exp(-(distance * distance) / (2 * sigma * sigma));

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
