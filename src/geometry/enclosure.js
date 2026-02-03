
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

function parsePlanBlock(planBlock) {
    if (!planBlock || !planBlock._lines) return null;

    const points = new Map();
    const cpoints = new Map();
    const segments = [];

    const addPoint = (id, x, y) => {
        points.set(id, { x: Number(x), y: Number(y) });
    };

    const addCPoint = (id, x, y) => {
        cpoints.set(id, { x: Number(x), y: Number(y) });
    };

    for (const rawLine of planBlock._lines) {
        const line = rawLine.trim();
        if (!line) continue;
        const parts = line.split(/\s+/);
        const keyword = parts[0];
        if (keyword === 'point' && parts.length >= 4) {
            addPoint(parts[1], parts[2], parts[3]);
        } else if (keyword === 'cpoint' && parts.length >= 4) {
            addCPoint(parts[1], parts[2], parts[3]);
        } else if (keyword === 'line' && parts.length >= 3) {
            segments.push({ type: 'line', ids: [parts[1], parts[2]] });
        } else if (keyword === 'arc' && parts.length >= 4) {
            segments.push({ type: 'arc', ids: [parts[1], parts[2], parts[3]] });
        } else if (keyword === 'ellipse' && parts.length >= 5) {
            segments.push({ type: 'ellipse', ids: [parts[1], parts[2], parts[3], parts[4]] });
        } else if (keyword === 'bezier' && parts.length >= 3) {
            segments.push({ type: 'bezier', ids: parts.slice(1) });
        }
    }

    return { points, cpoints, segments };
}

function sampleArc(p1, center, p2, steps = 16) {
    const a1 = Math.atan2(p1.y - center.y, p1.x - center.x);
    const a2 = Math.atan2(p2.y - center.y, p2.x - center.x);
    let delta = a2 - a1;
    if (delta > Math.PI) delta -= Math.PI * 2;
    if (delta < -Math.PI) delta += Math.PI * 2;

    const out = [];
    for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        const a = a1 + delta * t;
        out.push({
            x: center.x + Math.cos(a) * Math.hypot(p1.x - center.x, p1.y - center.y),
            y: center.y + Math.sin(a) * Math.hypot(p1.x - center.x, p1.y - center.y)
        });
    }
    return out;
}

function sampleEllipse(p1, center, major, p2, steps = 24) {
    const vx = major.x - center.x;
    const vy = major.y - center.y;
    const a = Math.hypot(vx, vy);
    if (a <= 0) return [p1, p2];

    const phi = Math.atan2(vy, vx);
    const cosP = Math.cos(phi);
    const sinP = Math.sin(phi);

    const toLocal = (pt) => {
        const dx = pt.x - center.x;
        const dy = pt.y - center.y;
        return {
            x: cosP * dx + sinP * dy,
            y: -sinP * dx + cosP * dy
        };
    };

    const p1l = toLocal(p1);
    const p2l = toLocal(p2);

    const calcB = (pl) => {
        const denom = 1 - (pl.x / a) ** 2;
        if (denom <= 0) return null;
        return Math.abs(pl.y) / Math.sqrt(denom);
    };

    const b1 = calcB(p1l);
    const b2 = calcB(p2l);
    const b = Number.isFinite(b1) && Number.isFinite(b2) ? (b1 + b2) / 2 : (b1 || b2 || a);

    const t1 = Math.atan2(p1l.y / b, p1l.x / a);
    const t2 = Math.atan2(p2l.y / b, p2l.x / a);
    let delta = t2 - t1;
    if (delta > Math.PI) delta -= Math.PI * 2;
    if (delta < -Math.PI) delta += Math.PI * 2;

    const out = [];
    for (let i = 0; i <= steps; i++) {
        const t = t1 + delta * (i / steps);
        const x = a * Math.cos(t);
        const y = b * Math.sin(t);
        out.push({
            x: center.x + cosP * x - sinP * y,
            y: center.y + sinP * x + cosP * y
        });
    }
    return out;
}

function sampleBezier(points, steps = 24) {
    const out = [];
    const n = points.length - 1;
    const bernstein = (i, t) => {
        const binom = (n, k) => {
            let res = 1;
            for (let i = 1; i <= k; i++) {
                res = (res * (n - (k - i))) / i;
            }
            return res;
        };
        return binom(n, i) * Math.pow(1 - t, n - i) * Math.pow(t, i);
    };
    for (let s = 0; s <= steps; s++) {
        const t = s / steps;
        let x = 0;
        let y = 0;
        for (let i = 0; i <= n; i++) {
            const b = bernstein(i, t);
            x += points[i].x * b;
            y += points[i].y * b;
        }
        out.push({ x, y });
    }
    return out;
}

function buildPlanOutline(params, quadrantInfo) {
    let planName = params.encPlan;
    if (typeof planName === 'string') {
        const trimmed = planName.trim();
        planName = trimmed.replace(/^\"(.*)\"$/, '$1').replace(/^\'(.*)\'$/, '$1');
    }
    if (!planName || !params._blocks) return null;

    const planBlock = params._blocks[planName];
    const parsed = parsePlanBlock(planBlock);
    if (!parsed) return null;

    const { points, cpoints, segments } = parsed;
    if (!segments.length) return null;

    const outline = [];
    segments.forEach((seg, index) => {
        let pts = [];
        if (seg.type === 'line') {
            const p1 = points.get(seg.ids[0]);
            const p2 = points.get(seg.ids[1]);
            if (p1 && p2) pts = [p1, p2];
        } else if (seg.type === 'arc') {
            const p1 = points.get(seg.ids[0]);
            const c = cpoints.get(seg.ids[1]) || points.get(seg.ids[1]);
            const p2 = points.get(seg.ids[2]);
            if (p1 && c && p2) pts = sampleArc(p1, c, p2);
        } else if (seg.type === 'ellipse') {
            const p1 = points.get(seg.ids[0]);
            const c = cpoints.get(seg.ids[1]) || points.get(seg.ids[1]);
            const major = points.get(seg.ids[2]);
            const p2 = points.get(seg.ids[3]);
            if (p1 && c && major && p2) pts = sampleEllipse(p1, c, major, p2);
        } else if (seg.type === 'bezier') {
            const bezPoints = seg.ids.map((id) => points.get(id)).filter(Boolean);
            if (bezPoints.length >= 2) pts = sampleBezier(bezPoints);
        }

        if (!pts.length) return;
        if (index > 0) pts = pts.slice(1);
        outline.push(...pts);
    });

    if (outline.length < 2) return null;

    const sL = parseFloat(params.encSpaceL) || 0;
    const sT = parseFloat(params.encSpaceT) || 0;
    const sR = parseFloat(params.encSpaceR) || 0;
    const sB = parseFloat(params.encSpaceB) || 0;

    const applySpacing = (pt) => {
        const x = pt.x >= 0 ? pt.x + sR : pt.x - sL;
        const z = pt.y >= 0 ? pt.y + sT : pt.y - sB;
        return { x, z };
    };

    const quarter = outline.map(applySpacing);
    const qMode = String(params.quadrants || '1234');

    if (qMode === '14') {
        const bottom = [...quarter].reverse().map((pt) => ({ x: pt.x, z: -pt.z }));
        const half = bottom.concat(quarter.slice(1));
        half.push({ x: 0, z: quarter[quarter.length - 1].z });
        half.push({ x: 0, z: -quarter[quarter.length - 1].z });
        return half;
    }

    if (qMode === '12') {
        const left = [...quarter].reverse().map((pt) => ({ x: -pt.x, z: pt.z }));
        const half = quarter.concat(left.slice(1));
        half.push({ x: -quarter[0].x, z: 0 });
        half.push({ x: quarter[0].x, z: 0 });
        return half;
    }

    if (qMode === '1') {
        const out = [...quarter];
        out.push({ x: 0, z: quarter[quarter.length - 1].z });
        out.push({ x: 0, z: 0 });
        out.push({ x: quarter[0].x, z: 0 });
        return out;
    }

    const top = quarter.concat([...quarter].reverse().map((pt) => ({ x: -pt.x, z: pt.z })).slice(1));
    const bottom = [...top].reverse().map((pt) => ({ x: pt.x, z: -pt.z }));
    return top.concat(bottom.slice(1));
}
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
    const depthRaw = parseFloat(params.encDepth);
    const depth = Number.isFinite(depthRaw) ? depthRaw : 0;
    const edgeR = parseFloat(params.encEdge) || 0;
    const cornerSegs = Math.max(4, params.cornerSegments || 4);

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

    const planOutline = buildPlanOutline(params, quadrantInfo);
    const usePlanOutline = Array.isArray(planOutline) && planOutline.length > 1;

    let boxRight, boxLeft, boxTop, boxBot;

    if (isRightHalf) {
        boxRight = maxX + sR;
        boxLeft = 0;
        boxTop = maxZ + sT;
        boxBot = minZ - sB;
    } else {
        boxRight = maxX + sR;
        boxLeft = minX - sL;
        boxTop = maxZ + sT;
        boxBot = minZ - sB;
    }

    const startIdx = vertices.length / 3;
    const outline = [];
    let cx = 0;
    let cz = 0;
    let halfW = 0;
    let halfH = 0;

    if (usePlanOutline) {
        let maxPX = -Infinity, minPX = Infinity, maxPZ = -Infinity, minPZ = Infinity;
        planOutline.forEach((pt) => {
            if (pt.x > maxPX) maxPX = pt.x;
            if (pt.x < minPX) minPX = pt.x;
            if (pt.z > maxPZ) maxPZ = pt.z;
            if (pt.z < minPZ) minPZ = pt.z;
        });
        outline.push(...planOutline);
        cx = (maxPX + minPX) / 2;
        cz = (maxPZ + minPZ) / 2;
        halfW = (maxPX - minPX) / 2;
        halfH = (maxPZ - minPZ) / 2;
    } else {
        halfW = (boxRight - boxLeft) / 2;
        halfH = (boxTop - boxBot) / 2;
        cx = (boxRight + boxLeft) / 2;
        cz = (boxTop + boxBot) / 2;

        const cr = Math.min(edgeR, halfW - 0.1, halfH - 0.1);

        if (isRightHalf) {
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

            addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2);
            addCorner(cx + halfW - cr, cz + halfH - cr, 0);
            outline.push({ x: 0, z: cz + halfH });
            outline.push({ x: 0, z: cz - halfH });

        } else {
            const addCorner = (cornerCx, cornerCz, startAngle) => {
                for (let i = 0; i <= cornerSegs; i++) {
                    const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                    outline.push({ x: cornerCx + cr * Math.cos(a), z: cornerCz + cr * Math.sin(a) });
                }
            };

            addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2);
            addCorner(cx + halfW - cr, cz + halfH - cr, 0);
            addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2);
            addCorner(cx - halfW + cr, cz - halfH + cr, Math.PI);
        }
    }

    const totalPts = outline.length;
    const usePlanMap = usePlanOutline;
    const outlineAngles = usePlanMap ? outline.map((pt) => Math.atan2(pt.z, pt.x)) : null;

    const findNearestOutlineIndex = (angle) => {
        if (!outlineAngles) return 0;
        let bestIdx = 0;
        let bestDist = Infinity;
        for (let i = 0; i < outlineAngles.length; i++) {
            let delta = angle - outlineAngles[i];
            while (delta > Math.PI) delta -= Math.PI * 2;
            while (delta < -Math.PI) delta += Math.PI * 2;
            const dist = Math.abs(delta);
            if (dist < bestDist) {
                bestDist = dist;
                bestIdx = i;
            }
        }
        return bestIdx;
    };

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
        if (usePlanMap) {
            ei = findNearestOutlineIndex(p);
            ei2 = findNearestOutlineIndex(p2);
        } else if (isRightHalf) {
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
