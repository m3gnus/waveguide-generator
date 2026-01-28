import { calculateROSSE } from './rosse.js';
import { calculateOSSE } from './osse.js';

export function addRollbackGeometry(vertices, indices, params, lengthSteps, radialSteps) {
    const lastRowStart = lengthSteps * (radialSteps + 1);
    const startIdx = vertices.length / 3;
    const rollbackAngle = (params.rollbackAngle || 180) * (Math.PI / 180);
    const rollbackSteps = 12;
    const startAt = Math.max(0.01, Math.min(0.99, params.rollbackStart || 0.5));

    for (let j = 1; j <= rollbackSteps; j++) {
        const t = j / rollbackSteps;
        const angle = t * rollbackAngle;

        for (let i = 0; i <= radialSteps; i++) {
            const p = (i / radialSteps) * Math.PI * 2;
            const mouthIdx = lastRowStart + i;
            const mx = vertices[mouthIdx * 3];
            const my = vertices[mouthIdx * 3 + 1];
            const mz = vertices[mouthIdx * 3 + 2];
            const r_mouth = Math.sqrt(mx * mx + mz * mz);

            // Compute roll radius from profile difference at startAt vs mouth
            let profileAtStart;
            if (params.type === 'R-OSSE') {
                profileAtStart = calculateROSSE(startAt * (params.tmax || 1.0), p, params);
            } else {
                profileAtStart = calculateOSSE(startAt * params.L, p, params);
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
        const row1Offset = j === 0 ? lastRowStart : startIdx + (j - 1) * (radialSteps + 1);
        const row2Offset = startIdx + j * (radialSteps + 1);

        for (let i = 0; i < radialSteps; i++) {
            indices.push(row1Offset + i, row1Offset + i + 1, row2Offset + i + 1);
            indices.push(row1Offset + i, row2Offset + i + 1, row2Offset + i);
        }
    }
}
