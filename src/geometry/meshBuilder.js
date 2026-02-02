import { calculateROSSE, calculateOSSE } from './hornModels.js';
import { applyMorphing } from './morphing.js';
import { addRollbackGeometry } from './rollback.js';
import { addEnclosureGeometry } from './enclosure.js';
import { addRearShapeGeometry } from './rearShape.js';

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
function parseQuadrants(quadrants) {
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

    // Vertical offset (ATH Mesh.VerticalOffset)
    const verticalOffset = parseFloat(params.verticalOffset) || 0;

    // Quadrant support for symmetry meshes
    const quadrantInfo = parseQuadrants(params.quadrants);
    const angleRange = quadrantInfo.endAngle - quadrantInfo.startAngle;

    // For partial meshes, we need to adjust how we generate angular segments
    // Full circle uses radialSteps segments with wraparound
    // Partial uses radialSteps segments without wraparound (needs +1 for end point)
    const effectiveRadialSteps = quadrantInfo.fullCircle ? radialSteps : radialSteps;

    for (let j = 0; j <= lengthSteps; j++) {
        const t = j / lengthSteps;
        const tActual = params.type === 'R-OSSE' ? t * (params.tmax || 1.0) : t;

        for (let i = 0; i <= effectiveRadialSteps; i++) {
            // Map i to angle within the quadrant range
            const p = quadrantInfo.startAngle + (i / effectiveRadialSteps) * angleRange;

            let profile;
            if (params.type === 'R-OSSE') {
                profile = calculateROSSE(tActual, p, params);
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
            const vy = x + verticalOffset;  // Apply vertical offset
            const vz = r * Math.sin(p);

            vertices.push(vx, vy, vz);
        }
    }

    // Add Rollback for R-OSSE
    if (params.type === 'R-OSSE' && params.rollback) {
        addRollbackGeometry(vertices, indices, params, lengthSteps, effectiveRadialSteps);
    }

    // Add Enclosure for OSSE
    if (params.type === 'OSSE' && params.encDepth > 0) {
        addEnclosureGeometry(vertices, indices, params, verticalOffset, quadrantInfo);
    } else if (params.rearShape !== 0) {
        addRearShapeGeometry(vertices, indices, params, lengthSteps, effectiveRadialSteps);
    }

    // Generate indices for the main horn body
    // For partial meshes, don't wrap around
    const indexRadialSteps = quadrantInfo.fullCircle ? radialSteps : effectiveRadialSteps;
    for (let j = 0; j < lengthSteps; j++) {
        for (let i = 0; i < indexRadialSteps; i++) {
            const row1 = j * (effectiveRadialSteps + 1);
            const row2 = (j + 1) * (effectiveRadialSteps + 1);

            indices.push(row1 + i, row1 + i + 1, row2 + i + 1);
            indices.push(row1 + i, row2 + i + 1, row2 + i);
        }
    }

    // Validate mesh integrity before returning
    const vertexCount = vertices.length / 3;
    const maxIndex = Math.max(...indices);
    if (maxIndex >= vertexCount) {
        console.error(`[MeshBuilder] Invalid mesh generated: max index ${maxIndex} >= vertex count ${vertexCount}`);
        console.error(`[MeshBuilder] Parameters: lengthSteps=${lengthSteps}, radialSteps=${radialSteps}, type=${params.type}`);
        console.error(`[MeshBuilder] Rollback enabled: ${params.rollback}, RearShape: ${params.rearShape}`);
    }

    return { vertices, indices };
}
