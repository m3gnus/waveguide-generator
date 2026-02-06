import { calculateROSSE, calculateOSSE } from './hornModels.js';
import { evalParam } from './common.js';

// ===========================================================================
// Rollback Geometry (R-OSSE Throat Extension)
// ===========================================================================

export function addRollbackGeometry(vertices, indices, params, lengthSteps, angleList, fullCircle = true) {
    const ringCount = Array.isArray(angleList) ? angleList.length : 0;
    if (ringCount <= 1) return;
    const lastRowStart = lengthSteps * ringCount;
    const startIdx = vertices.length / 3;
    const rollbackAngle = (params.rollbackAngle || 180) * (Math.PI / 180);
    const rollbackSteps = 12;
    const startAt = Math.max(0.01, Math.min(0.99, params.rollbackStart || 0.5));

    for (let j = 1; j <= rollbackSteps; j++) {
        const t = j / rollbackSteps;
        const angle = t * rollbackAngle;

        for (let i = 0; i < ringCount; i++) {
            const p = angleList[i];
            const mouthIdx = lastRowStart + i;
            const mx = vertices[mouthIdx * 3];
            const my = vertices[mouthIdx * 3 + 1];
            const mz = vertices[mouthIdx * 3 + 2];
            const r_mouth = Math.sqrt(mx * mx + mz * mz);

            let profileAtStart;
            if (params.type === 'R-OSSE') {
                const tmax = params.tmax === undefined ? 1.0 : evalParam(params.tmax, p);
                profileAtStart = calculateROSSE(startAt * tmax, p, params);
            } else {
                const L = evalParam(params.L, p);
                profileAtStart = calculateOSSE(startAt * L, p, params);
            }
            const roll_r = Math.max(5, (r_mouth - profileAtStart.y) * 0.5);

            const r = r_mouth + roll_r * (1 - Math.cos(angle));
            const y = my - roll_r * Math.sin(angle);

            vertices.push(r * Math.cos(p), y, r * Math.sin(p));
        }
    }

    for (let j = 0; j < rollbackSteps; j++) {
        const row1Offset = j === 0 ? lastRowStart : startIdx + (j - 1) * ringCount;
        const row2Offset = startIdx + j * ringCount;
        const segmentCount = fullCircle ? ringCount : Math.max(0, ringCount - 1);

        for (let i = 0; i < segmentCount; i++) {
            const i2 = fullCircle ? (i + 1) % ringCount : i + 1;
            indices.push(row1Offset + i, row1Offset + i2, row2Offset + i2);
            indices.push(row1Offset + i, row2Offset + i2, row2Offset + i);
        }
    }
}

// ===========================================================================
// Rear Shape Geometry (Alternative Mouth Caps)
// ===========================================================================

export function addRearShapeGeometry(vertices, indices, params, lengthSteps, angleList, fullCircle = true) {
    const ringCount = Array.isArray(angleList) ? angleList.length : 0;
    if (ringCount <= 1) return;
    const lastRowStart = lengthSteps * ringCount;
    const mouthY = vertices[lastRowStart * 3 + 1];

    if (params.rearShape === 2) {
        const centerIdx = vertices.length / 3;
        vertices.push(0, mouthY, 0);
        const segmentCount = fullCircle ? ringCount : Math.max(0, ringCount - 1);
        for (let i = 0; i < segmentCount; i++) {
            const i2 = fullCircle ? (i + 1) % ringCount : i + 1;
            const mouthIdx = lastRowStart + i;
            indices.push(mouthIdx, lastRowStart + i2, centerIdx);
        }
    } else if (params.rearShape === 1) {
        const thickness = params.wallThickness || 5;
        const startIdx = vertices.length / 3;

        for (let i = 0; i < ringCount; i++) {
            const p = angleList[i];
            const mouthIdx = lastRowStart + i;
            const mx = vertices[mouthIdx * 3];
            const mz = vertices[mouthIdx * 3 + 2];

            const r = Math.sqrt(mx * mx + mz * mz) + thickness;
            vertices.push(r * Math.cos(p), mouthY - thickness, r * Math.sin(p));
        }

        const segmentCount = fullCircle ? ringCount : Math.max(0, ringCount - 1);
        for (let i = 0; i < segmentCount; i++) {
            const i2 = fullCircle ? (i + 1) % ringCount : i + 1;
            const mouthIdx = lastRowStart + i;
            const rimIdx = startIdx + i;
            indices.push(mouthIdx, lastRowStart + i2, startIdx + i2);
            indices.push(mouthIdx, startIdx + i2, rimIdx);
        }

        const rearCenterIdx = vertices.length / 3;
        vertices.push(0, mouthY - thickness, 0);
        for (let i = 0; i < segmentCount; i++) {
            const i2 = fullCircle ? (i + 1) % ringCount : i + 1;
            indices.push(startIdx + i, startIdx + i2, rearCenterIdx);
        }
    }
}
