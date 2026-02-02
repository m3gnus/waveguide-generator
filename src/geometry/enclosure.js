
/**
 * Add enclosure geometry for BEM simulation.
 *
 * ATH Enclosure Architecture:
 * - The front baffle is at the MOUTH Y position (not throat)
 * - The horn connects directly to the front baffle inner edge
 * - The enclosure extends BACKWARD from the front baffle by the depth parameter
 * - Edge radius creates rounded edges on both front and back
 *
 * Key positions (Y-axis):
 *   - Throat Y = verticalOffset (e.g., 80mm)
 *   - Mouth Y = verticalOffset + horn length (e.g., 80 + 130 = 210mm)
 *   - Front baffle inner edge Y = Mouth Y
 *   - Front baffle outer edge Y = Mouth Y + edgeRadius
 *   - Back baffle inner edge Y = Mouth Y - depth
 *   - Back baffle outer edge Y = Mouth Y - depth - edgeRadius
 *
 * Spacing parameters define how far the baffle extends from the MOUTH outline:
 *   - L(eft): extends in -X direction from mouth
 *   - R(ight): extends in +X direction from mouth
 *   - T(op): extends in +Z direction from mouth
 *   - B(ottom): extends in -Z direction from mouth
 *
 * EdgeRadius creates rounded edges on:
 *   - The front baffle outer corners (extending forward from mouth)
 *   - The back panel outer corners (extending backward)
 *
 * @param {number[]} vertices - Vertex array to append to
 * @param {number[]} indices - Index array to append to
 * @param {Object} params - Parameter object
 * @param {number} verticalOffset - Y offset applied to the mesh (throat Y position)
 * @param {Object} quadrantInfo - Quadrant information for symmetry meshes
 */
export function addEnclosureGeometry(vertices, indices, params, verticalOffset = 0, quadrantInfo = null) {
    const radialSteps = params.angularSegments;

    // MOUTH is at the last row - the front baffle inner edge connects here
    const lastRowStart = params.lengthSegments * (radialSteps + 1);

    // Get mouth Y position from vertex data
    const mouthY = vertices[lastRowStart * 3 + 1];

    // Spacing: L(eft), T(op), R(ight), B(ottom) - extends from MOUTH outline
    const sL = parseFloat(params.encSpaceL) || 25;
    const sT = parseFloat(params.encSpaceT) || 25;
    const sR = parseFloat(params.encSpaceR) || 25;
    const sB = parseFloat(params.encSpaceB) || 25;
    const depth = parseFloat(params.encDepth);
    const edgeR = parseFloat(params.encEdge) || 0;

    // Determine if we're in symmetry mode (quadrant 14 = right half)
    const isRightHalf = quadrantInfo && (params.quadrants === '14' || params.quadrants === 14);

    // Find bounding box at the MOUTH ring (last row)
    let maxX = -Infinity, minX = Infinity, maxZ = -Infinity, minZ = Infinity;
    for (let i = 0; i <= radialSteps; i++) {
        const idx = lastRowStart + i;
        const mx = vertices[idx * 3];
        const mz = vertices[idx * 3 + 2];
        if (mx > maxX) maxX = mx;
        if (mx < minX) minX = mx;
        if (mz > maxZ) maxZ = mz;
        if (mz < minZ) minZ = mz;
    }

    // Calculate enclosure outer bounds based on mouth dimensions + spacing
    let boxRight, boxLeft, boxTop, boxBot;

    if (isRightHalf) {
        // Symmetric mode: enclosure extends from x=0 (symmetry plane)
        boxRight = maxX + sR;
        boxLeft = 0;  // Symmetry plane
        boxTop = maxZ + sT;
        boxBot = minZ - sB;
    } else {
        // Full mode: enclosure extends around full mouth
        boxRight = maxX + sR;
        boxLeft = minX - sL;
        boxTop = maxZ + sT;
        boxBot = minZ - sB;
    }

    const startIdx = vertices.length / 3;

    // Build rounded rectangle profile for the outer baffle edge
    const halfW = (boxRight - boxLeft) / 2;
    const halfH = (boxTop - boxBot) / 2;
    const cx = (boxRight + boxLeft) / 2;
    const cz = (boxTop + boxBot) / 2;
    // Ensure radius doesn't degrade into degenerate geometry
    const cr = Math.min(edgeR, halfW - 0.1, halfH - 0.1);

    const cornerSegs = Math.max(4, params.cornerSegments || 4);

    // Generate outline for baffle outer edge (includes rounded corners)
    const outline = [];

    if (isRightHalf) {
        // For right half symmetry, generate outline from bottom-right going counterclockwise
        // but only the right side (x >= 0)
        const addCorner = (cornerCx, cornerCz, startAngle) => {
            for (let i = 0; i <= cornerSegs; i++) {
                const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                const x = cornerCx + cr * Math.cos(a);
                const z = cornerCz + cr * Math.sin(a);
                if (x >= -0.001) {
                    outline.push({ x: Math.max(0, x), z });
                }
            }
        };

        // Bottom-right corner (rounded)
        addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2);
        // Top-right corner (rounded)
        addCorner(cx + halfW - cr, cz + halfH - cr, 0);
        // Symmetry plane edges (straight line at x=0)
        outline.push({ x: 0, z: cz + halfH });
        outline.push({ x: 0, z: cz - halfH });

    } else {
        // Full enclosure outline (BR -> TR -> TL -> BL)
        const addCorner = (cornerCx, cornerCz, startAngle) => {
            for (let i = 0; i <= cornerSegs; i++) {
                const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                outline.push({ x: cornerCx + cr * Math.cos(a), z: cornerCz + cr * Math.sin(a) });
            }
        };

        addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2);  // BR
        addCorner(cx + halfW - cr, cz + halfH - cr, 0);             // TR
        addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2);   // TL
        addCorner(cx - halfW + cr, cz - halfH + cr, Math.PI);       // BL
    }

    const totalPts = outline.length;

    // ==========================================
    // Key Y positions
    // ==========================================
    const frontInnerY = mouthY;                    // Front baffle inner edge (connects to mouth)
    const frontOuterY = mouthY + edgeR;            // Front baffle outer edge (extends forward)
    const backInnerY = mouthY - depth;             // Back baffle inner edge
    const backOuterY = mouthY - depth - edgeR;     // Back baffle outer edge (extends backward)

    // ==========================================
    // Create vertices for the enclosure
    // ==========================================

    // Generate inset outline for the rounded edge portions
    const insetOutline = [];
    for (let i = 0; i < totalPts; i++) {
        const pt = outline[i];
        const dx = cx - pt.x;
        const dz = cz - pt.z;
        const dist = Math.sqrt(dx * dx + dz * dz);
        if (dist > 0.001) {
            const nx = dx / dist;
            const nz = dz / dist;
            insetOutline.push({
                x: pt.x + nx * edgeR,
                z: pt.z + nz * edgeR
            });
        } else {
            insetOutline.push({ x: pt.x, z: pt.z });
        }
    }

    // 1. Front Baffle Inner Ring (at mouth Y - connects to horn mouth)
    for (let i = 0; i < totalPts; i++) {
        vertices.push(outline[i].x, frontInnerY, outline[i].z);
    }

    // 2. Front Baffle Outer Ring (at mouth Y + edgeR)
    for (let i = 0; i < totalPts; i++) {
        vertices.push(insetOutline[i].x, frontOuterY, insetOutline[i].z);
    }

    // 3. Back Baffle Inner Ring (at mouth Y - depth)
    for (let i = 0; i < totalPts; i++) {
        vertices.push(outline[i].x, backInnerY, outline[i].z);
    }

    // 4. Back Baffle Outer Ring (at mouth Y - depth - edgeR)
    for (let i = 0; i < totalPts; i++) {
        vertices.push(insetOutline[i].x, backOuterY, insetOutline[i].z);
    }

    const frontInnerStart = startIdx;
    const frontOuterStart = startIdx + totalPts;
    const backInnerStart = startIdx + totalPts * 2;
    const backOuterStart = startIdx + totalPts * 3;

    // ==========================================
    // Create triangles
    // ==========================================

    const sideLoopEnd = isRightHalf ? totalPts - 1 : totalPts;

    // Front edge: connect front inner to front outer (rounded front edge)
    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(frontInnerStart + i, frontInnerStart + i2, frontOuterStart + i2);
        indices.push(frontInnerStart + i, frontOuterStart + i2, frontOuterStart + i);
    }

    // Side walls: connect front inner ring to back inner ring
    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(frontInnerStart + i, backInnerStart + i, backInnerStart + i2);
        indices.push(frontInnerStart + i, backInnerStart + i2, frontInnerStart + i2);
    }

    // Back edge: connect back inner to back outer (beveled back edge)
    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(backInnerStart + i, backInnerStart + i2, backOuterStart + i2);
        indices.push(backInnerStart + i, backOuterStart + i2, backOuterStart + i);
    }

    // Front baffle face — connect MOUTH ring to enclosure front inner ring
    // This creates the surface between the horn mouth and the baffle outer edge
    for (let i = 0; i < radialSteps; i++) {
        const angleRange = quadrantInfo ?
            (quadrantInfo.endAngle - quadrantInfo.startAngle) : (Math.PI * 2);
        const startAngle = quadrantInfo ? quadrantInfo.startAngle : 0;

        const p = startAngle + (i / radialSteps) * angleRange;
        const p2 = startAngle + ((i + 1) / radialSteps) * angleRange;

        // Map angle to enclosure outline index
        let ei, ei2;
        if (isRightHalf) {
            // Map angle (-π/2 to π/2) to outline index
            const normalizedAngle1 = (p + Math.PI / 2) / Math.PI;
            const normalizedAngle2 = (p2 + Math.PI / 2) / Math.PI;
            const rightSidePts = 2 * (cornerSegs + 1);
            ei = Math.round(normalizedAngle1 * (rightSidePts - 1));
            ei2 = Math.round(normalizedAngle2 * (rightSidePts - 1));
            ei = Math.min(ei, rightSidePts - 1);
            ei2 = Math.min(ei2, rightSidePts - 1);
        } else {
            ei = Math.round((p / (2 * Math.PI)) * totalPts) % totalPts;
            ei2 = Math.round((p2 / (2 * Math.PI)) * totalPts) % totalPts;
        }

        // Connect to MOUTH ring (last row)
        const mi = lastRowStart + i;
        const mi2 = lastRowStart + i + 1;

        // Triangle from mouth to enclosure front inner ring
        indices.push(mi, mi2, frontInnerStart + ei2);
        indices.push(mi, frontInnerStart + ei2, frontInnerStart + ei);
    }

    // Back cap — fan from center to back outer ring
    const backCenterIdx = vertices.length / 3;
    vertices.push(cx, backOuterY, cz);

    for (let i = 0; i < sideLoopEnd; i++) {
        const i2 = (i + 1) % totalPts;
        // Winding for back cap facing outwards (away from front)
        indices.push(backOuterStart + i, backOuterStart + i2, backCenterIdx);
    }

    // For symmetric mesh, add closing triangles along symmetry plane
    if (isRightHalf) {
        // Close the side at x=0
        const topFrontInner = frontInnerStart + totalPts - 2;
        const botFrontInner = frontInnerStart + totalPts - 1;
        const topFrontOuter = frontOuterStart + totalPts - 2;
        const botFrontOuter = frontOuterStart + totalPts - 1;
        const topBackInner = backInnerStart + totalPts - 2;
        const botBackInner = backInnerStart + totalPts - 1;
        const topBackOuter = backOuterStart + totalPts - 2;
        const botBackOuter = backOuterStart + totalPts - 1;

        // Front edge along symmetry plane
        indices.push(topFrontInner, botFrontInner, botFrontOuter);
        indices.push(topFrontInner, botFrontOuter, topFrontOuter);

        // Side wall along symmetry plane
        indices.push(topFrontInner, topBackInner, botBackInner);
        indices.push(topFrontInner, botBackInner, botFrontInner);

        // Back edge along symmetry plane
        indices.push(topBackInner, botBackInner, botBackOuter);
        indices.push(topBackInner, botBackOuter, topBackOuter);
    }
}
