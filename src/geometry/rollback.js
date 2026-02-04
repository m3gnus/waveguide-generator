import { calculateROSSE, calculateOSSE } from './hornModels.js';

const evalParam = (value, p = 0) => (typeof value === 'function' ? value(p) : value);

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

            // Compute roll radius from profile difference at startAt vs mouth
            let profileAtStart;
            if (params.type === 'R-OSSE') {
                const tmax = params.tmax === undefined ? 1.0 : evalParam(params.tmax, p);
                profileAtStart = calculateROSSE(startAt * tmax, p, params);
            } else {
                const L = evalParam(params.L, p);
                profileAtStart = calculateOSSE(startAt * L, p, params);
            }
            const roll_r = Math.max(5, (r_mouth - profileAtStart.y) * 0.5);

            // Toroidal rollback: curve inward and backward
            const r = r_mouth + roll_r * (1 - Math.cos(angle));
            const y = my - roll_r * Math.sin(angle);

            vertices.push(r * Math.cos(p), y, r * Math.sin(p));
        }
    }

    // Connect slices
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
