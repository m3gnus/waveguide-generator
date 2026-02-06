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