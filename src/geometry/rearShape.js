
export function addRearShapeGeometry(vertices, indices, params, lengthSteps, radialSteps) {
    const lastRowStart = lengthSteps * (radialSteps + 1);
    const mouthY = vertices[lastRowStart * 3 + 1];

    if (params.rearShape === 2) { // Flat Disc (User 2)
        const centerIdx = vertices.length / 3;
        vertices.push(0, mouthY, 0);
        for (let i = 0; i <= radialSteps; i++) {
            const mouthIdx = lastRowStart + i;
            if (i < radialSteps) {
                indices.push(mouthIdx, mouthIdx + 1, centerIdx);
            }
        }
    } else if (params.rearShape === 1) { // Full Model (User 1 - Realistic wall/rear)
        // Implementation of wall thickness and rear cap
        const thickness = params.wallThickness || 5;
        const startIdx = vertices.length / 3;

        // Create a secondary mesh slightly offset
        for (let i = 0; i <= radialSteps; i++) {
            const p = (i / radialSteps) * Math.PI * 2;
            const mouthIdx = lastRowStart + i;
            const mx = vertices[mouthIdx * 3];
            const mz = vertices[mouthIdx * 3 + 2];

            // Simple outward extrusion for thickness
            const r = Math.sqrt(mx * mx + mz * mz) + thickness;
            vertices.push(r * Math.cos(p), mouthY - thickness, r * Math.sin(p));
        }

        // Connect mouth to outer rim
        for (let i = 0; i < radialSteps; i++) {
            const mouthIdx = lastRowStart + i;
            const rimIdx = startIdx + i;
            indices.push(mouthIdx, mouthIdx + 1, rimIdx + 1);
            indices.push(mouthIdx, rimIdx + 1, rimIdx);
        }

        // Cap the rear
        const rearCenterIdx = vertices.length / 3;
        vertices.push(0, mouthY - thickness, 0);
        for (let i = 0; i < radialSteps; i++) {
            indices.push(startIdx + i, startIdx + i + 1, rearCenterIdx);
        }
    }
}
