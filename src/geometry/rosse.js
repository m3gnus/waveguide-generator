
/**
 * Calculate R-OSSE horn profile point at normalized position t and angle p.
 * @param {number} t - Normalized position along horn (0=throat, 1=mouth). 
 *                     Note: Input t should already be scaled by tmax if needed.
 * @param {number} p - Azimuthal angle in radians (0=horizontal, PI/2=vertical)
 * @param {Object} params - Horn parameters
 * @returns {{ x: number, y: number }} Axial position (x) and radius (y) at (t, p)
 */
export function calculateROSSE(t, p, params) {
    // Handle functional parameters: if they are functions, call them with p, else use value
    const val = (v) => (typeof v === 'function' ? v(p) : v);

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
