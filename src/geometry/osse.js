
/**
 * Calculate OSSE horn profile point at axial position z and angle p.
 * @param {number} z - Axial position (0 to L)
 * @param {number} p - Azimuthal angle in radians
 * @param {Object} params - Horn parameters
 * @returns {{ x: number, y: number }} Axial position (x=z) and radius (y)
 */
export function calculateOSSE(z, p, params) {
    const val = (v) => (typeof v === 'function' ? v(p) : v);

    const L = params.L;
    const a = (val(params.a) * Math.PI) / 180;
    const s = params.s ? val(params.s) : 0;

    const a0 = (params.a0 * Math.PI) / 180;
    const r0 = params.r0;
    const k = params.k;
    const n = params.n;
    const q = params.q;

    const rGOS = Math.sqrt((k * r0) ** 2 + 2 * k * r0 * z * Math.tan(a0) + (z ** 2) * (Math.tan(a) ** 2)) + r0 * (1 - k);

    let rTERM = 0;
    if (z > 0 && n > 0 && q > 0) {
        const val = q * z / L;
        if (val <= 1.0) {
            rTERM = (s * L / q) * (1 - Math.pow(1 - Math.pow(val, n), 1 / n));
        } else {
            rTERM = (s * L / q);
        }
    }

    return { x: z, y: rGOS + rTERM };
}
