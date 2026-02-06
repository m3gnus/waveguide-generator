/**
 * MWG Horn Geometry Models
 * Contains logic for OSSE and R-OSSE profile calculations.
 */

import { evalParam, toRad, clamp, parseNumberList } from './common.js';


function getGuidingCurveRadius(p, params) {
    const type = Number(params.gcurveType || 0);
    if (!type) return null;

    const width = evalParam(params.gcurveWidth || 0, p);
    if (!Number.isFinite(width) || width <= 0) return null;

    const aspect = evalParam(params.gcurveAspectRatio || 1, p);
    const rot = toRad(evalParam(params.gcurveRot || 0, p));
    const pr = p - rot;
    const cosP = Math.cos(pr);
    const sinP = Math.sin(pr);

    if (type === 1) {
        const n = Math.max(2, evalParam(params.gcurveSeN || 3, p));
        const a = width / 2;
        const b = a * aspect;
        const term = Math.pow(Math.abs(cosP / a), n) + Math.pow(Math.abs(sinP / b), n);
        if (term <= 0) return null;
        return Math.pow(term, -1 / n);
    }

    if (type === 2) {
        let a = evalParam(params.gcurveSfA || 1, p);
        let b = evalParam(params.gcurveSfB || 1, p);
        let m1 = evalParam(params.gcurveSfM1 || 0, p);
        let m2 = evalParam(params.gcurveSfM2 || 0, p);
        let n1 = evalParam(params.gcurveSfN1 || 1, p);
        let n2 = evalParam(params.gcurveSfN2 || 1, p);
        let n3 = evalParam(params.gcurveSfN3 || 1, p);

        const list = parseNumberList(params.gcurveSf);
        if (list && list.length >= 6) {
            [a, b, m1, n1, n2, n3] = list;
            m2 = m1;
        }

        const t1 = Math.pow(Math.abs(Math.cos((m1 * pr) / 4) / a), n2);
        const t2 = Math.pow(Math.abs(Math.sin((m2 * pr) / 4) / b), n3);
        const rNorm = Math.pow(t1 + t2, -1 / n1);
        if (!Number.isFinite(rNorm)) return null;

        const sx = (width / 2);
        const sy = (width / 2) * aspect;
        const x = rNorm * cosP * sx;
        const y = rNorm * sinP * sy;
        return Math.sqrt(x * x + y * y);
    }

    return null;
}

function computeOsseRadius(z, p, params, overrides = {}) {
    const L = overrides.L ?? evalParam(params.L, p);
    const aDeg = overrides.aDeg ?? evalParam(params.a, p);
    const a0Deg = overrides.a0Deg ?? evalParam(params.a0, p);
    const r0 = overrides.r0 ?? evalParam(params.r0, p);

    const a = toRad(aDeg);
    const a0 = toRad(a0Deg);
    const s = params.s !== undefined ? evalParam(params.s, p) : 0;
    const k = params.k === undefined ? 1 : evalParam(params.k, p);
    const n = params.n === undefined ? 4 : evalParam(params.n, p);
    const q = params.q === undefined ? 1 : evalParam(params.q, p);

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

    return rGOS + rTERM;
}

function computeCoverageAngleFromGuidingCurve(p, params, totalLength, extLen, slotLen, r0Base, extAngleRad, a0Deg, L) {
    const targetR = getGuidingCurveRadius(p, params);
    if (!Number.isFinite(targetR)) return evalParam(params.a, p);

    const distParam = evalParam(params.gcurveDist || 0, p);
    const distRaw = distParam <= 1 ? totalLength * distParam : distParam;
    const dist = clamp(distRaw, 0, totalLength);
    if (!Number.isFinite(dist) || dist <= 0) return evalParam(params.a, p);

    const r0Main = r0Base + extLen * Math.tan(extAngleRad);
    const zMain = Math.max(0, dist - extLen - slotLen);

    let low = 0.5;
    let high = 89;
    for (let i = 0; i < 24; i++) {
        const mid = (low + high) / 2;
        const rMid = computeOsseRadius(zMain, p, params, {
            L,
            aDeg: mid,
            a0Deg,
            r0: r0Main
        });
        if (!Number.isFinite(rMid)) break;
        if (rMid < targetR) {
            low = mid;
        } else {
            high = mid;
        }
    }

    return clamp((low + high) / 2, 0.5, 89);
}

/**
 * Validate horn parameters
 * @param {Object} params - Horn parameters to validate
 * @param {string} modelType - Type of model (OSSE, ROSSE)
 * @returns {{ valid: boolean, errors: string[] }} Validation result
 */
export function validateParameters(params, modelType) {
    const errors = [];

    const sampleP = 0;
    const a0 = params.a0 !== undefined ? evalParam(params.a0, sampleP) : undefined;
    const r0 = params.r0 !== undefined ? evalParam(params.r0, sampleP) : undefined;
    const k = params.k !== undefined ? evalParam(params.k, sampleP) : undefined;
    const tmax = params.tmax !== undefined ? evalParam(params.tmax, sampleP) : undefined;

    if (a0 !== undefined) {
        if (!Number.isFinite(a0)) {
            errors.push('a0 must be a finite number');
        } else if (a0 < 0 || a0 > 90) {
            errors.push('a0 must be between 0 and 90 degrees');
        }
    }
    if (r0 !== undefined) {
        if (!Number.isFinite(r0)) {
            errors.push('r0 must be a finite number');
        } else if (r0 <= 0) {
            errors.push('r0 must be positive');
        }
    }
    if (k !== undefined) {
        if (!Number.isFinite(k)) {
            errors.push('k must be a finite number');
        } else if (k <= 0) {
            errors.push('k must be greater than 0');
        }
    }
    if (tmax !== undefined) {
        if (!Number.isFinite(tmax)) {
            errors.push('tmax must be a finite number');
        } else if (tmax < 0 || tmax > 1) {
            errors.push('tmax must be between 0 and 1');
        }
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
export function calculateOSSE(z, p, params, options = {}) {
    const validationResult = validateParameters(params, 'OSSE');
    if (!validationResult.valid) {
        console.error('Validation failed:', validationResult.errors);
        return { x: NaN, y: NaN };
    }

    const L = options.L ?? evalParam(params.L, p);
    const extLen = Math.max(0, evalParam(params.throatExtLength || 0, p));
    const slotLen = Math.max(0, evalParam(params.slotLength || 0, p));
    const totalLength = L + extLen + slotLen;
    const extAngleRad = toRad(evalParam(params.throatExtAngle || 0, p));

    const r0Base = evalParam(params.r0, p);
    const a0Deg = evalParam(params.a0, p);
    const r0Main = r0Base + extLen * Math.tan(extAngleRad);

    const throatProfile = Number(params.throatProfile || 1);
    let aDeg = options.coverageAngle ?? evalParam(params.a, p);

    if (Number(params.gcurveType || 0) !== 0) {
        if (!params.__gcurveCache) params.__gcurveCache = new Map();
        const key = p.toFixed(6);
        if (params.__gcurveCache.has(key)) {
            aDeg = params.__gcurveCache.get(key);
        } else {
            const computed = computeCoverageAngleFromGuidingCurve(
                p,
                params,
                totalLength,
                extLen,
                slotLen,
                r0Base,
                extAngleRad,
                a0Deg,
                L
            );
            params.__gcurveCache.set(key, computed);
            aDeg = computed;
        }
    }

    let radius;
    if (z <= extLen) {
        radius = r0Base + z * Math.tan(extAngleRad);
    } else if (z <= extLen + slotLen) {
        radius = r0Main;
    } else {
        const zMain = z - extLen - slotLen;
        if (throatProfile === 3) {
            const aRad = toRad(aDeg);
            const mouthR = r0Main + L * Math.tan(aRad);
            const explicitRadius = evalParam(params.circArcRadius || 0, p);
            let arcRadius = explicitRadius;
            let centerX = null;
            let centerY = null;

            if (Number.isFinite(arcRadius) && arcRadius > 0) {
                const p1 = { x: 0, y: r0Main };
                const p2 = { x: L, y: mouthR };
                const dx = p2.x - p1.x;
                const dy = p2.y - p1.y;
                const d = Math.sqrt(dx * dx + dy * dy);
                if (d > 0 && arcRadius >= d / 2) {
                    const midX = (p1.x + p2.x) / 2;
                    const midY = (p1.y + p2.y) / 2;
                    const h = Math.sqrt(Math.max(0, arcRadius * arcRadius - (d / 2) ** 2));
                    const nx = -dy / d;
                    const ny = dx / d;
                    const c1x = midX + nx * h;
                    const c1y = midY + ny * h;
                    const c2x = midX - nx * h;
                    const c2y = midY - ny * h;
                    const pick1 = c1y >= c2y;
                    centerX = pick1 ? c1x : c2x;
                    centerY = pick1 ? c1y : c2y;
                }
            }

            if (centerX === null || centerY === null) {
                const termAngle = toRad(evalParam(params.circArcTermAngle || 1, p));
                const p1 = { x: 0, y: r0Main };
                const p2 = { x: L, y: mouthR };
                const t = { x: Math.cos(termAngle), y: Math.sin(termAngle) };
                const n = { x: -t.y, y: t.x };
                const dx = p2.x - p1.x;
                const dy = p2.y - p1.y;
                const dDotN = dx * n.x + dy * n.y;
                if (Math.abs(dDotN) > 1e-6) {
                    arcRadius = -((dx * dx + dy * dy) / (2 * dDotN));
                    centerX = L + n.x * arcRadius;
                    centerY = mouthR + n.y * arcRadius;
                } else {
                    arcRadius = 0;
                }
            }

            if (Number.isFinite(arcRadius) && arcRadius !== 0 && centerX !== null && centerY !== null) {
                const dx = zMain - centerX;
                const under = arcRadius * arcRadius - dx * dx;
                const sign = Math.sign(mouthR - centerY) || 1;
                radius = under >= 0 ? centerY + sign * Math.sqrt(under) : mouthR;
            } else {
                radius = mouthR;
            }
        } else {
            radius = computeOsseRadius(zMain, p, params, {
                L,
                aDeg,
                a0Deg,
                r0: r0Main
            });
        }
    }

    let x = z;
    let y = radius;

    const rotDeg = evalParam(params.rot || 0, p);
    if (Number.isFinite(rotDeg) && rotDeg !== 0) {
        const rotRad = toRad(rotDeg);
        const dx = x - 0;
        const dy = y - r0Base;
        const xr = dx * Math.cos(rotRad) - dy * Math.sin(rotRad);
        const yr = dx * Math.sin(rotRad) + dy * Math.cos(rotRad);
        x = xr;
        y = r0Base + yr;
    }

    return { x, y };
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
    const validationResult = validateParameters(params, 'ROSSE');
    if (!validationResult.valid) {
        console.error('Validation failed:', validationResult.errors);
        return { x: NaN, y: NaN };
    }

    const R = evalParam(params.R, p);
    const a = (evalParam(params.a, p) * Math.PI) / 180;
    const b = params.b === undefined ? 0.2 : evalParam(params.b, p);

    const a0 = (evalParam(params.a0, p) * Math.PI) / 180;
    const k = evalParam(params.k, p);
    const m = params.m === undefined ? 0.85 : evalParam(params.m, p);
    const r0 = evalParam(params.r0, p);
    const r = params.r === undefined ? 0.4 : evalParam(params.r, p);
    const q = evalParam(params.q, p);

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
