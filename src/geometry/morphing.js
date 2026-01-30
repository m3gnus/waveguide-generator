import * as THREE from 'three';

export function getRoundedRectRadius(p, width, height, radius) {
    // p is in [0, 2pi]
    const cos = Math.cos(p);
    const sin = Math.sin(p);
    const absCos = Math.abs(cos);
    const absSin = Math.abs(sin);

    const w = width / 2;
    const h = height / 2;
    const r = radius;

    let resR = 0;
    if (absCos * h > absSin * w) {
        resR = w / absCos;
    } else {
        resR = h / absSin;
    }

    // Simple smoothing for rounded corners
    const cornerDist = Math.sqrt(w * w + h * h) - r;
    if (resR > cornerDist) {
        // This logic is a simplified approximation as found in the original code
        // Ideally this would be replaced by a precise superellipse or rounded-rect polar function
    }

    return resR;
}

export function applyMorphing(currentR, t, p, params) {
    // Morphing for OSSE
    // t is normalized 0..1
    if (params.type === 'OSSE' && params.morphTarget !== 0) {
        let morphFactor = 0;
        if (t > params.morphFixed) {
            const tMorph = (t - params.morphFixed) / (1 - params.morphFixed);
            morphFactor = Math.pow(tMorph, params.morphRate || 3);
        }

        if (morphFactor > 0) {
            const targetWidth = params.morphWidth || currentR * 2;
            const targetHeight = params.morphHeight || currentR * 2;
            const rectR = getRoundedRectRadius(p, targetWidth, targetHeight, params.morphCorner || 35);
            return THREE.MathUtils.lerp(currentR, rectR, morphFactor);
        }
    }

    // Enhanced morphing for OS-GOS with variable curves
    if (params.type === 'OS-GOS' && params.morphTarget !== 0) {
        let morphFactor = 0;
        if (t > params.morphFixed) {
            const tMorph = (t - params.morphFixed) / (1 - params.morphFixed);
            // Support for variable morphing rate curves
            morphFactor = Math.pow(tMorph, params.morphRate || 3);
        }

        if (morphFactor > 0) {
            const targetWidth = params.morphWidth || currentR * 2;
            const targetHeight = params.morphHeight || currentR * 2;
            const rectR = getRoundedRectRadius(p, targetWidth, targetHeight, params.morphCorner || 35);
            return THREE.MathUtils.lerp(currentR, rectR, morphFactor);
        }
    }

    return currentR;
}
