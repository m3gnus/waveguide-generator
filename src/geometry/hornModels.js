/**
 * MWG Horn Geometry Models
 * Contains logic for OSSE and R-OSSE profile calculations.
 */

/**
 * Validate horn parameters
 * @param {Object} params - Horn parameters to validate
 * @param {string} modelType - Type of model (OSSE, ROSSE)
 * @returns {{ valid: boolean, errors: string[] }} Validation result
 */
export function validateParameters(params, modelType) {
    const errors = [];

    if (params.a0 < 0 || params.a0 > 90) {
        errors.push('a0 must be between 0 and 90 degrees');
    }
    if (params.r0 <= 0) {
        errors.push('r0 must be positive');
    }
    if (params.k <= 0) {
        errors.push('k must be greater than 0');
    }
    if (params.tmax < 0 || params.tmax > 1) {
        errors.push('tmax must be between 0 and 1');
    }

    return { valid: errors.length === 0, errors };
}

/**
 * Calculate OSSE horn profile point at axial position z and angle p.
 * @param {number} z - Axial position (0 to L)
 * @param {number} p - Azimuthal angle in radians
 * @param {Object} params - Horn parameters
 * @returns {{ x: number, y: number }} Axial position (x=z) and radius (y)
 */
export function calculateOSSE(z, p, params) {
    const val = (v) => (typeof v === 'function' ? v(p) : v);

    const validationResult = validateParameters(params, 'OSSE');
    if (!validationResult.valid) {
        console.error('Validation failed:', validationResult.errors);
        return { x: NaN, y: NaN };
    }

    const L = params.L;
    const a = (val(params.a) * Math.PI) / 180;
    const s = params.s ? val(params.s) : 0;

    const a0 = (params.a0 * Math.PI) / 180;
    const r0 = params.r0;
    const k = params.k || 1;  // ATH default is 1
    const n = params.n || 4;  // Typical default
    const q = params.q || 1;  // Typical default

    const rGOS = Math.sqrt((k * r0) ** 2 + 2 * k * r0 * z * Math.tan(a0) + (z ** 2) * (Math.tan(a) ** 2)) + r0 * (1 - k);

    let rTERM = 0;
    if (z > 0 && n > 0 && q > 0) {
        const zNorm = q * z / L;
        if (zNorm <= 1.0) {
            rTERM = (s * L / q) * (1 - Math.pow(1 - Math.pow(zNorm, n), 1 / n));
        } else {
            rTERM = (s * L / q);
        }
    }

    return { x: z, y: rGOS + rTERM };
}

/**
 * Calculate R-OSSE horn profile point at normalized position t and angle p.
 * @param {number} t - Normalized position along horn (0=throat, 1=mouth). 
 *                     Note: Input t should already be scaled by tmax if needed.
 * @param {number} p - Azimuthal angle in radians (0=horizontal, PI/2=vertical)
 * @param {Object} params - Horn parameters
 * @returns {{ x: number, y: number }} Axial position (x) and radius (y) at (t, p)
 */
export function calculateROSSE(t, p, params) {
    const val = (v) => (typeof v === 'function' ? v(p) : v);

    const validationResult = validateParameters(params, 'ROSSE');
    if (!validationResult.valid) {
        console.error('Validation failed:', validationResult.errors);
        return { x: NaN, y: NaN };
    }

    const R = val(params.R);
    const a = (val(params.a) * Math.PI) / 180;
    const b = params.b ? val(params.b) : 0.2;

    const a0 = (params.a0 * Math.PI) / 180;
    const k = params.k;
    const m = params.m || 0.85;
    const r0 = params.r0;
    const r = params.r || 0.4;
    const q = params.q;

    // Auxiliary constants calculated for each angle p
    const c1 = (k * r0) ** 2;
    const c2 = 2 * k * r0 * Math.tan(a0);
    const c3 = Math.tan(a) ** 2;

    // Calculate L based on the mouth radius R(p)
    // Formula: L = (1/(2c3)) * [sqrt(c2^2 - 4c3(c1 - (R + r0(k-1))^2)) - c2]
    const termInsideSqrt = c2 ** 2 - 4 * c3 * (c1 - Math.pow(R + r0 * (k - 1), 2));
    const L = (1 / (2 * c3)) * (Math.sqrt(Math.max(0, termInsideSqrt)) - c2);

    const xt = L * (Math.sqrt(r ** 2 + m ** 2) - Math.sqrt(r ** 2 + (t - m) ** 2)) +
        b * L * (Math.sqrt(r ** 2 + (1 - m) ** 2) - Math.sqrt(r ** 2 + m ** 2)) * (t ** 2);

    const yt = (1 - Math.pow(t, q)) * (Math.sqrt(c1 + c2 * L * t + c3 * (L * t) ** 2) + r0 * (1 - k)) +
        Math.pow(t, q) * (R + L * (1 - Math.sqrt(1 + c3 * (t - 1) ** 2)));

    return { x: xt, y: yt };
}

