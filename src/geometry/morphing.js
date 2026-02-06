import * as THREE from 'three';
import { evalParam } from './common.js';

// ===========================================================================
// Morphing (Rectangular/Elliptical Mouth Shaping)
// ===========================================================================

export function getRoundedRectRadius(p, halfWidth, halfHeight, cornerRadius) {
    const absCos = Math.abs(Math.cos(p));
    const absSin = Math.abs(Math.sin(p));

    if (absCos < 1e-9) return halfHeight;
    if (absSin < 1e-9) return halfWidth;

    const r = Math.max(0, Math.min(cornerRadius, Math.min(halfWidth, halfHeight)));
    if (r <= 1e-9) {
        const tx = halfWidth / absCos;
        const ty = halfHeight / absSin;
        return Math.min(tx, ty);
    }

    const yAtX = (halfWidth * absSin) / absCos;
    if (yAtX <= halfHeight - r + 1e-9) {
        return halfWidth / absCos;
    }

    const xAtY = (halfHeight * absCos) / absSin;
    if (xAtY <= halfWidth - r + 1e-9) {
        return halfHeight / absSin;
    }

    const cx = halfWidth - r;
    const cy = halfHeight - r;
    const A = absCos * absCos + absSin * absSin;
    const B = -2 * (absCos * cx + absSin * cy);
    const C = cx * cx + cy * cy - r * r;
    const disc = Math.max(0, B * B - 4 * A * C);
    const t = (-B + Math.sqrt(disc)) / (2 * A);
    return t;
}

export function applyMorphing(currentR, t, p, params, morphTargetInfo = null) {
    const targetShape = Number(params.morphTarget || 0);
    if (targetShape !== 0) {
        let morphFactor = 0;
        if (t > params.morphFixed) {
            const tMorph = (t - params.morphFixed) / (1 - params.morphFixed);
            morphFactor = Math.pow(tMorph, params.morphRate || 3);
        }

        if (morphFactor > 0) {
            const widthValue = params.morphWidth;
            const heightValue = params.morphHeight;
            const hasExplicit = (widthValue !== undefined && widthValue > 0) || (heightValue !== undefined && heightValue > 0);
            const halfWidth = (widthValue && widthValue > 0)
                ? widthValue / 2
                : (morphTargetInfo ? morphTargetInfo.halfW : currentR);
            const halfHeight = (heightValue && heightValue > 0)
                ? heightValue / 2
                : (morphTargetInfo ? morphTargetInfo.halfH : currentR);

            let targetR = currentR;
            if (targetShape === 2) {
                const circleRadius = Math.sqrt(Math.max(0, halfWidth * halfHeight));
                targetR = circleRadius;
            } else {
                const rectR = getRoundedRectRadius(p, halfWidth, halfHeight, params.morphCorner || 0);
                targetR = rectR;
            }

            if (!hasExplicit && !morphTargetInfo) {
                targetR = currentR;
            }

            const allowShrinkage = params.morphAllowShrinkage === 1 || params.morphAllowShrinkage === true;
            const safeTarget = allowShrinkage ? targetR : Math.max(currentR, targetR);
            return THREE.MathUtils.lerp(currentR, safeTarget, morphFactor);
        }
    }

    return currentR;
}
