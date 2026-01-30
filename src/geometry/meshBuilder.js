import { calculateROSSE, calculateOSSE, calculateOSGOS } from './hornModels.js';
import { applyMorphing } from './morphing.js';
import { addRollbackGeometry } from './rollback.js';
import { addEnclosureGeometry } from './enclosure.js';
import { addRearShapeGeometry } from './rearShape.js';

/**
 * Build the horn mesh geometry (vertices and indices) from the given parameters.
 * @param {Object} params - The complete parameter object.
 * @returns {{ vertices: number[], indices: number[] }} The flat arrays of coordinates and indices.
 */
export function buildHornMesh(params) {
    const radialSteps = params.angularSegments;
    const lengthSteps = params.lengthSegments;

    const vertices = [];
    const indices = [];

    // Support for variable mesh resolution
    const throatResolution = params.throatResolution || radialSteps;
    const mouthResolution = params.mouthResolution || radialSteps;

    for (let j = 0; j <= lengthSteps; j++) {
        const t = j / lengthSteps;
        const tActual = params.type === 'R-OSSE' ? t * (params.tmax || 1.0) : t;

        // Adjust radial steps based on axial position for variable resolution
        let currentRadialSteps = radialSteps;
        if (params.type === 'OS-GOS' && (params.throatResolution || params.mouthResolution)) {
            // Gradually transition from throat resolution to mouth resolution
            const ratio = t;
            currentRadialSteps = Math.round(throatResolution + (mouthResolution - throatResolution) * ratio);
            // Ensure we have at least 4 steps to avoid degenerate meshes
            currentRadialSteps = Math.max(4, currentRadialSteps);
        }

        for (let i = 0; i <= currentRadialSteps; i++) {
            const p = (i / currentRadialSteps) * Math.PI * 2;

            let profile;
            if (params.type === 'R-OSSE') {
                profile = calculateROSSE(tActual, p, params);
            } else if (params.type === 'OS-GOS') {
                profile = calculateOSGOS(tActual * params.L, p, params);
            } else {
                profile = calculateOSSE(tActual * params.L, p, params);
                if (params.h > 0) {
                    profile.y += params.h * Math.sin(tActual * Math.PI);
                }
            }

            let x = profile.x;
            let r = profile.y;

            // Apply morphing (only affects OSSE radius)
            r = applyMorphing(r, t, p, params);

            const vx = r * Math.cos(p);
            const vy = x;
            const vz = r * Math.sin(p);

            vertices.push(vx, vy, vz);
        }
    }

    // Add Rollback for R-OSSE
    if (params.type === 'R-OSSE' && params.rollback) {
        addRollbackGeometry(vertices, indices, params, lengthSteps, radialSteps);
    }

    // Add Enclosure for OSSE
    if (params.type === 'OSSE' && params.encDepth > 0) {
        addEnclosureGeometry(vertices, indices, params);
    } else if (params.rearShape !== 0) {
        addRearShapeGeometry(vertices, indices, params, lengthSteps, radialSteps);
    }

    // Generate indices for the main horn body
    for (let j = 0; j < lengthSteps; j++) {
        for (let i = 0; i < radialSteps; i++) {
            const row1 = j * (radialSteps + 1);
            const row2 = (j + 1) * (radialSteps + 1);

            indices.push(row1 + i, row1 + i + 1, row2 + i + 1);
            indices.push(row1 + i, row2 + i + 1, row2 + i);
        }
    }

    return { vertices, indices };
}
