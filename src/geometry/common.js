/**
 * Shared geometry utilities
 */

export const EPS = 1e-9;

export const toRad = (deg) => (deg * Math.PI) / 180;
export const toDeg = (rad) => (rad * 180) / Math.PI;

export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export const evalParam = (value, p = 0) => (typeof value === 'function' ? value(p) : value);

export const parseNumberList = (value) => {
    if (value === undefined || value === null) return null;
    if (Array.isArray(value)) {
        const out = value.map((v) => Number(v)).filter((v) => Number.isFinite(v));
        return out.length ? out : null;
    }
    if (typeof value === 'string') {
        const parts = value.split(',').map((v) => v.trim()).filter((v) => v.length > 0);
        const nums = parts.map((v) => Number(v)).filter((v) => Number.isFinite(v));
        return nums.length ? nums : null;
    }
    return null;
};

// Alias for compatibility
export const parseList = parseNumberList;

export const isFullCircle = (quadrants) => {
    const q = String(quadrants ?? '1234').trim();
    return q === '' || q === '1234';
};

/**
 * Parse quadrants parameter to get angular range.
 * ATH quadrants convention:
 *   1 = +X, +Z quadrant (0 to π/2)
 *   2 = -X, +Z quadrant (π/2 to π)
 *   3 = -X, -Z quadrant (π to 3π/2)
 *   4 = +X, -Z quadrant (3π/2 to 2π)
 * Common values: '1234' (full), '14' (right half, x≥0), '12' (top half, z≥0), '1' (single quadrant)
 * @param {string|number} quadrants - Quadrant specification
 * @returns {{ startAngle: number, endAngle: number, fullCircle: boolean }}
 */
export function parseQuadrants(quadrants) {
    const q = String(quadrants || '1234');

    if (q === '1234' || q === '') {
        return { startAngle: 0, endAngle: Math.PI * 2, fullCircle: true };
    }

    // For half symmetry models (common in ATH/ABEC)
    if (q === '14') {
        // Right half: x ≥ 0, which is -π/2 to π/2 (or equivalently 3π/2 to π/2 wrapping)
        // In our coordinate system: p=0 is +X axis, p increases counterclockwise
        // Quadrant 1 (+X,+Z): 0 to π/2
        // Quadrant 4 (+X,-Z): 3π/2 to 2π (or -π/2 to 0)
        return { startAngle: -Math.PI / 2, endAngle: Math.PI / 2, fullCircle: false };
    }

    if (q === '12') {
        // Top half: z ≥ 0
        return { startAngle: 0, endAngle: Math.PI, fullCircle: false };
    }

    if (q === '1') {
        // Single quadrant
        return { startAngle: 0, endAngle: Math.PI / 2, fullCircle: false };
    }

    // Default to full circle for unrecognized values
    return { startAngle: 0, endAngle: Math.PI * 2, fullCircle: true };
}

export const cleanNumber = (value) => (Math.abs(value) < EPS ? 0 : value);
export const formatNumber = (value) => {
    const v = cleanNumber(value);
    return Number.isFinite(v) ? String(v) : '0';
};
