
export function addEnclosureGeometry(vertices, indices, params) {
    // Revert: Enclosure built from MOUTH, extending backwards
    // The horn is mounted IN the cabinet, so the cabinet front is at the mouth plane
    const lastRowStart = params.lengthSegments * (params.angularSegments + 1);
    const mouthY = vertices[lastRowStart * 3 + 1];

    // Spacing: L(eft), T(op), R(ight), B(ottom)
    const sL = params.encSpaceL || 25;
    const sT = params.encSpaceT || 25;
    const sR = params.encSpaceR || 25;
    const sB = params.encSpaceB || 25;
    const depth = params.encDepth;
    const edgeR = params.encEdge || 0;

    // Find bounding box at the MOUTH ring
    let maxX = -Infinity, minX = Infinity, maxZ = -Infinity, minZ = Infinity;
    const radialSteps = params.angularSegments;
    for (let i = 0; i <= radialSteps; i++) {
        const idx = lastRowStart + i;
        const mx = vertices[idx * 3];
        const mz = vertices[idx * 3 + 2];
        if (mx > maxX) maxX = mx;
        if (mx < minX) minX = mx;
        if (mz > maxZ) maxZ = mz;
        if (mz < minZ) minZ = mz;
    }

    // Enclosure outer rectangle dimensions
    const boxRight = maxX + sR;
    const boxLeft = minX - sL;
    const boxTop = maxZ + sT;
    const boxBot = minZ - sB;

    const startIdx = vertices.length / 3;

    // Build rounded rectangle profile
    const halfW = (boxRight - boxLeft) / 2;
    const halfH = (boxTop - boxBot) / 2;
    const cx = (boxRight + boxLeft) / 2;
    const cz = (boxTop + boxBot) / 2;
    // Ensure radius doesn't degrade into degenerate geometry
    const cr = Math.min(edgeR, halfW - 0.1, halfH - 0.1);

    const cornerSegs = Math.max(4, params.cornerSegments || 4);

    const outline = [];
    const addCorner = (cx, cz, startAngle) => {
        for (let i = 0; i <= cornerSegs; i++) {
            const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
            outline.push({ x: cx + cr * Math.cos(a), z: cz + cr * Math.sin(a) });
        }
    };

    // BR -> TR -> TL -> BL
    addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2);
    addCorner(cx + halfW - cr, cz + halfH - cr, 0);
    addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2);
    addCorner(cx - halfW + cr, cz - halfH + cr, Math.PI);

    const totalPts = outline.length;

    // 1. Front Baffle Outer Ring (at Mouth Plane)
    // This forms the outer edge of the front baffle
    for (let i = 0; i < totalPts; i++) {
        vertices.push(outline[i].x, mouthY, outline[i].z);
    }

    // 2. Back Panel Outer Ring (at Mouth Plane - Depth)
    for (let i = 0; i < totalPts; i++) {
        vertices.push(outline[i].x, mouthY - depth, outline[i].z);
    }

    const frontStart = startIdx;
    const backStart = startIdx + totalPts;

    // Side walls: connect front ring to back ring
    for (let i = 0; i < totalPts; i++) {
        const i2 = (i + 1) % totalPts;
        const f1 = frontStart + i;
        const f2 = frontStart + i2;
        const b1 = backStart + i;
        const b2 = backStart + i2;
        indices.push(f1, f2, b2);
        indices.push(f1, b2, b1);
    }

    // Front baffle face — connect MOUTH ring to enclosure front ring
    for (let i = 0; i < radialSteps; i++) {
        const p = (i / radialSteps) * 2 * Math.PI;
        const p2 = ((i + 1) / radialSteps) * 2 * Math.PI;

        // Map angle to enclosure outline index
        const ei = Math.round((p / (2 * Math.PI)) * totalPts) % totalPts;
        const ei2 = Math.round((p2 / (2 * Math.PI)) * totalPts) % totalPts;

        const mi = lastRowStart + i;      // Connect to MOUTH
        const mi2 = lastRowStart + i + 1;

        // Triangle from mouth to enclosure front ring
        // Vertices: Mouth1, Mouth2, Enclosure2
        indices.push(mi, mi2, frontStart + ei2);
        // Vertices: Mouth1, Enclosure2, Enclosure1
        indices.push(mi, frontStart + ei2, frontStart + ei);
    }

    // Back cap — fan from center to back ring
    const backCenterIdx = vertices.length / 3;
    vertices.push(cx, mouthY - depth, cz);
    for (let i = 0; i < totalPts; i++) {
        const i2 = (i + 1) % totalPts;
        // Winding for back cap facing outwards (away from mouth)
        indices.push(backStart + i, backStart + i2, backCenterIdx);
    }
}
