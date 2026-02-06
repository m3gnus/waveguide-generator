
// ===========================================================================
// Enclosure Geometry (Rear Chamber for BEM Simulation)
// ===========================================================================

// Plan outline builder - parses enclosure plan definitions
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
    const b = Number.isFinite(b1) && Number.isFinite(b2) ? (b1 + b2) / 2 : b1 || b2 || a;

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

export function buildPlanOutline(params, quadrantInfo) {
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

    const top = quarter.concat(
        [...quarter].reverse().map((pt) => ({ x: -pt.x, z: pt.z })).slice(1)
    );
    const bottom = [...top].reverse().map((pt) => ({ x: pt.x, z: -pt.z }));
    return top.concat(bottom.slice(1));
}



/**
 * Add enclosure geometry for BEM simulation.
 *
 * ATH Enclosure Architecture:
 * - The front baffle is at the MOUTH Y position (not throat)
 * - The horn connects directly to the front baffle inner edge
 * - The enclosure extends BACKWARD from the front baffle by the depth parameter
 * - Edge radius creates rounded edges on both front and back
 *
 * Key positions (Y-axis = axial):
 *   - Throat Y = 0
 *   - Mouth Y = horn length
 *   - Front baffle inner edge Y = Mouth Y
 *   - Front baffle outer edge Y = Mouth Y + edgeRadius
 *   - Back baffle inner edge Y = Mouth Y - depth
 *   - Back baffle outer edge Y = Mouth Y - depth - edgeRadius
 *
 * Vertical offset is applied on the Z axis (handled upstream in meshBuilder),
 * so enclosure positions follow the mouth ring automatically.
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
 * @param {number} verticalOffset - Legacy parameter (kept for signature compatibility)
 * @param {Object} quadrantInfo - Quadrant information for symmetry meshes
 */
export function addEnclosureGeometry(vertices, indices, params, verticalOffset = 0, quadrantInfo = null, groupInfo = null, ringCount = null, angleList = null) {
    const ringSize = Number.isFinite(ringCount) && ringCount > 0
        ? ringCount
        : Math.max(2, Math.round(params.angularSegments || 0));

    // MOUTH is at the last row - the front baffle inner edge connects here
    const lastRowStart = params.lengthSegments * ringSize;

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
    const interfaceOffsetRaw = parseFloat(params.interfaceOffset);
    const frontOffset = Number.isFinite(interfaceOffsetRaw) && interfaceOffsetRaw > 0 ? interfaceOffsetRaw : 0;
    const cornerSegs = Math.max(4, params.cornerSegments || 4);

    const quadrantKey = String(params.quadrants ?? '').trim();
    const restrictX = quadrantKey === '14' || quadrantKey === '1' || quadrantKey === '14.0' || quadrantKey === '1.0';
    const restrictZ = quadrantKey === '12' || quadrantKey === '1' || quadrantKey === '12.0' || quadrantKey === '1.0';
    const isRightHalf = restrictX && !restrictZ;
    const isTopHalf = restrictZ && !restrictX;
    const isQuarter = restrictX && restrictZ;

    // Find bounding box at the MOUTH ring (last row)
    let maxX = -Infinity,
        minX = Infinity,
        maxZ = -Infinity,
        minZ = Infinity;
    for (let i = 0; i < ringSize; i++) {
        const idx = lastRowStart + i;
        const mx = vertices[idx * 3];
        const mz = vertices[idx * 3 + 2];
        if (mx > maxX) maxX = mx;
        if (mx < minX) minX = mx;
        if (mz > maxZ) maxZ = mz;
        if (mz < minZ) minZ = mz;
    }

    const useAthRounding = params.useAthEnclosureRounding ?? params.useAthZMap ?? false;
    if (useAthRounding) {
        if (Number.isFinite(maxX)) maxX = Math.ceil(maxX);
        if (Number.isFinite(minX)) minX = Math.floor(minX);
        if (Number.isFinite(maxZ)) maxZ = Math.ceil(maxZ);
        if (Number.isFinite(minZ)) minZ = Math.floor(minZ);
    }

    const planOutline = buildPlanOutline(params, quadrantInfo);
    const usePlanOutline = Array.isArray(planOutline) && planOutline.length > 1;

    let boxRight, boxLeft, boxTop, boxBot;

    boxRight = maxX + sR;
    boxLeft = restrictX ? 0 : minX - sL;
    boxTop = maxZ + sT;
    boxBot = restrictZ ? 0 : minZ - sB;

    const outline = [];
    let cx = 0;
    let cz = 0;
    let halfW = 0;
    let halfH = 0;

    if (usePlanOutline) {
        let maxPX = -Infinity,
            minPX = Infinity,
            maxPZ = -Infinity,
            minPZ = Infinity;
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

        if (isQuarter) {
            const addCorner = (cornerCx, cornerCz, startAngle) => {
                for (let i = 0; i <= cornerSegs; i++) {
                    const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                    const x = cornerCx + cr * Math.cos(a);
                    const z = cornerCz + cr * Math.sin(a);
                    if (x >= -0.001 && z >= -0.001) {
                        outline.push({ x: Math.max(0, x), z: Math.max(0, z) });
                    }
                }
            };

            addCorner(cx + halfW - cr, cz + halfH - cr, 0);
            outline.push({ x: 0, z: cz + halfH });
            outline.push({ x: 0, z: 0 });
            outline.push({ x: cx + halfW, z: 0 });
        } else if (isRightHalf) {
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
        } else if (isTopHalf) {
            const addCorner = (cornerCx, cornerCz, startAngle) => {
                for (let i = 0; i <= cornerSegs; i++) {
                    const a = startAngle + (i / cornerSegs) * (Math.PI / 2);
                    outline.push({ x: cornerCx + cr * Math.cos(a), z: cornerCz + cr * Math.sin(a) });
                }
            };

            addCorner(cx + halfW - cr, cz + halfH - cr, 0);
            addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2);
            outline.push({ x: cx - halfW, z: 0 });
            outline.push({ x: cx + halfW, z: 0 });
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
    const outlineAngles = outline.map((pt) => Math.atan2(pt.z, pt.x));

    const findNearestOutlineIndex = (angle) => {
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
    // Key Y positions and Ring Generation
    // ==========================================
    const backY = mouthY - depth;
    const edgeType = parseInt(params.encEdgeType) || 1; // 1=rounded, 2=chamfered
    const axialSegs = edgeR > 0 ? cornerSegs : 1;

    // Generate inset outline and outer outline
    // outline = outer boundary of the box
    // insetOutline = boundary where the roundover starts (inset by edgeR)
    const outerOutline = outline;
    const insetOutline = [];
    for (let i = 0; i < totalPts; i++) {
        const pt = outerOutline[i];
        const dx = cx - pt.x;
        const dz = cz - pt.z;
        const dist = Math.sqrt(dx * dx + dz * dz);
        if (dist > 0.001) {
            const nx = dx / dist;
            const nz = dz / dist;
            insetOutline.push({
                x: pt.x + nx * edgeR,
                z: pt.z + nz * edgeR,
                nx,
                nz
            });
        } else {
            insetOutline.push({ x: pt.x, z: pt.z, nx: 0, nz: 0 });
        }
    }

    // Get mouth ring vertices from horn (last row of horn mesh)
    const mouthStart = lastRowStart;
    const mouthRing = [];
    for (let i = 0; i < ringSize; i++) {
        const idx = mouthStart + i;
        mouthRing.push({
            x: vertices[idx * 3],
            y: vertices[idx * 3 + 1],
            z: vertices[idx * 3 + 2]
        });
    }

    // Ring -1: Mouth Projection Ring (projects mouth to match enclosure point count)
    // This intermediate ring eliminates the gap between horn mouth and enclosure
    // Positioned AT the mouth Y, using INSET outline (close to mouth, not far outer boundary)
    const mouthProjectionStart = vertices.length / 3;
    for (let i = 0; i < totalPts; i++) {
        const ipt = insetOutline[i];  // Use inset outline (edgeR from outer) - close to mouth
        // Find nearest mouth vertex to get its Y position
        const angle = Math.atan2(ipt.z - cz, ipt.x - cx);
        let bestDist = Infinity;
        let bestY = mouthY;
        for (const mv of mouthRing) {
            let ma = Math.atan2(mv.z - cz, mv.x - cx);
            let da = Math.abs(angle - ma);
            while (da > Math.PI) da -= Math.PI * 2;
            da = Math.abs(da);
            if (da < bestDist) {
                bestDist = da;
                bestY = mv.y;
            }
        }
        // Use INSET outline (not outer) - this is close to the mouth, not at the far boundary
        vertices.push(ipt.x, bestY, ipt.z);
    }

    // Ring 0: Front Inner (at mouth y + frontOffset, inset outline)
    // This ring is where the roundover begins, offset forward if frontOffset > 0
    const frontInnerStart = vertices.length / 3;
    for (let i = 0; i < totalPts; i++) {
        const ipt = insetOutline[i];
        // Get Y from mouth projection ring and add frontOffset
        const yBase = vertices[(mouthProjectionStart + i) * 3 + 1];
        vertices.push(ipt.x, yBase + frontOffset, ipt.z);
    }

    // Front Roundover Rings
    // Curving from Inset outline (at yBase) to Outer outline (at yBase - edgeR)
    // Creates an inward-facing dish (bezel) that is FLUSH with the mouth
    // and Recedes back to the side walls.
    const frontRoundsStarts = [];
    for (let s = 1; s <= axialSegs; s++) {
        const startIdx = vertices.length / 3;
        frontRoundsStarts.push(startIdx);

        // phi: 0 (at Inset) to PI/2 (at Outer)
        const phi = (s / axialSegs) * (Math.PI / 2);
        const sinP = Math.sin(phi);
        const cosP = Math.cos(phi);

        for (let i = 0; i < totalPts; i++) {
            const ipt = insetOutline[i];
            const opt = outerOutline[i];
            const yBase = vertices[(frontInnerStart + i) * 3 + 1];

            let x, y, z;
            if (edgeType === 2) { // Chamfer
                // Linear from Inset to Outer
                const t = s / axialSegs;
                x = ipt.x + (opt.x - ipt.x) * t;
                z = ipt.z + (opt.z - ipt.z) * t;
                y = yBase - edgeR * t;
            } else { // Rounded (Concave/Dish)
                // Start at Inset (phi=0, sin=0, cos=1) -> Pos = Inset, Y = yBase
                // End at Outer (phi=90, sin=1, cos=0) -> Pos = Outer, Y = yBase - edgeR
                // Note: ipt.nx points Inward. To go Outer, subtract nx.
                x = ipt.x - ipt.nx * edgeR * sinP;
                z = ipt.z - ipt.nz * edgeR * sinP;
                y = yBase - edgeR * (1 - cosP);
            }
            vertices.push(x, y, z);
        }
    }

    // Side Wall Back Ring (at back elevation before roundover starts)
    const backSideStart = vertices.length / 3;
    for (let i = 0; i < totalPts; i++) {
        const opt = outerOutline[i];
        vertices.push(opt.x, backY + edgeR, opt.z);
    }

    // Back Roundover Rings
    // Curving from (outerPt, backY + edgeR) to (insetPt, backY)
    const backRoundsStarts = [];
    for (let s = 1; s <= axialSegs; s++) {
        const startIdx = vertices.length / 3;
        backRoundsStarts.push(startIdx);

        // phi: 0 (at outer outline, y=backY + edgeR) to PI/2 (at inset outline, y=backY)
        const phi = (s / axialSegs) * (Math.PI / 2);
        const sinP = Math.sin(phi);
        const cosP = Math.cos(phi);

        for (let i = 0; i < totalPts; i++) {
            const ipt = insetOutline[i];
            const opt = outerOutline[i];

            let x, y, z;
            if (edgeType === 2) { // Chamfer
                const t = s / axialSegs;
                x = opt.x + (ipt.x - opt.x) * t;
                z = opt.z + (ipt.z - opt.z) * t;
                y = (backY + edgeR) - edgeR * t;
            } else { // Rounded (Convex)
                x = opt.x + ipt.nx * edgeR * (1 - cosP);
                z = opt.z + ipt.nz * edgeR * (1 - cosP);
                y = (backY + edgeR) - edgeR * sinP;
            }
            vertices.push(x, y, z);
        }
    }

    // ==========================================
    // Create faces for the enclosure
    // ==========================================

    const enclosureStartTri = indices.length / 3;

    // 1. Front Roundover Faces
    const frontStartTri = indices.length / 3;
    let prevRing = frontInnerStart;
    for (let s = 0; s < frontRoundsStarts.length; s++) {
        const currRing = frontRoundsStarts[s];
        for (let i = 0; i < totalPts; i++) {
            const i2 = (i + 1) % totalPts;
            indices.push(prevRing + i, currRing + i, currRing + i2);
            indices.push(prevRing + i, currRing + i2, prevRing + i2);
        }
        prevRing = currRing;
    }
    const frontEndTri = indices.length / 3;

    // 2. Side Walls
    const sideRing = frontRoundsStarts[frontRoundsStarts.length - 1];
    for (let i = 0; i < totalPts; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(sideRing + i, backSideStart + i, backSideStart + i2);
        indices.push(sideRing + i, backSideStart + i2, sideRing + i2);
    }

    // 3. Back Roundover Faces
    prevRing = backSideStart;
    for (let s = 0; s < backRoundsStarts.length; s++) {
        const currRing = backRoundsStarts[s];
        for (let i = 0; i < totalPts; i++) {
            const i2 = (i + 1) % totalPts;
            indices.push(prevRing + i, currRing + i, currRing + i2);
            indices.push(prevRing + i, currRing + i2, prevRing + i2);
        }
        prevRing = currRing;
    }

    // 4. Back Cap (fan from center)
    const backOuterStart = backRoundsStarts[backRoundsStarts.length - 1];
    const backCenterIdx = vertices.length / 3;
    vertices.push(cx, backY, cz);

    for (let i = 0; i < totalPts; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(backOuterStart + i, backOuterStart + i2, backCenterIdx);
    }

    // ==========================================
    // Connect mouth to enclosure via projection ring
    // ==========================================

    // Step 1: Connect mouth ring to mouth projection ring using proper stitching
    const mouthLoop = mouthRing.length;
    const fullCircle = !quadrantInfo || quadrantInfo.fullCircle;
    const connectLoop = fullCircle ? mouthLoop : Math.max(0, mouthLoop - 1);

    // Build mapping from mouth vertices to enclosure outline vertices
    // Each mouth vertex finds its nearest enclosure point
    const mouthToEnc = new Array(mouthLoop);
    for (let i = 0; i < mouthLoop; i++) {
        const mouthVertex = mouthRing[i];
        const mouthAngle = Math.atan2(mouthVertex.z - cz, mouthVertex.x - cx);
        mouthToEnc[i] = findNearestOutlineIndex(mouthAngle);
    }

    // Stitch mouth ring to mouth projection ring
    for (let i = 0; i < connectLoop; i++) {
        const i2 = fullCircle ? (i + 1) % mouthLoop : i + 1;
        if (i2 >= mouthLoop) continue;

        const mi = mouthStart + i;
        const mi2 = mouthStart + i2;
        const ei = mouthToEnc[i];
        const ei2 = mouthToEnc[i2];

        // Create triangles connecting to mouth projection ring
        if (ei !== ei2) {
            // Standard quad triangulation
            indices.push(mi, mi2, mouthProjectionStart + ei2);
            indices.push(mi, mouthProjectionStart + ei2, mouthProjectionStart + ei);
        } else {
            // Both mouth vertices map to the same enclosure vertex - create single triangle
            indices.push(mi, mi2, mouthProjectionStart + ei);
        }
    }

    // Step 2: Connect mouth projection ring to front inner ring (flat transition if frontOffset > 0)
    for (let i = 0; i < totalPts; i++) {
        const i2 = (i + 1) % totalPts;
        indices.push(mouthProjectionStart + i, mouthProjectionStart + i2, frontInnerStart + i2);
        indices.push(mouthProjectionStart + i, frontInnerStart + i2, frontInnerStart + i);
    }

    // Connect enclosure outline points that were skipped
    // Walk the enclosure outline and connect any gaps
    for (let e = 0; e < totalPts; e++) {
        const e2 = (e + 1) % totalPts;

        // Find if any mouth vertices map to e and e2
        let hasE = false, hasE2 = false;
        let lastMouthForE = -1, firstMouthForE2 = -1;

        for (let m = 0; m < mouthLoop; m++) {
            if (mouthToEnc[m] === e) {
                hasE = true;
                lastMouthForE = m;
            }
            if (mouthToEnc[m] === e2 && firstMouthForE2 === -1) {
                hasE2 = true;
                firstMouthForE2 = m;
            }
        }

        // If we have mouth vertices for e but not for e2 (or vice versa),
        // we need to add a connecting triangle
        if (hasE && hasE2 && lastMouthForE >= 0 && firstMouthForE2 >= 0) {
            // Check if there's a gap in mouth indices between lastMouthForE and firstMouthForE2
            const mGap = fullCircle
                ? (firstMouthForE2 - lastMouthForE + mouthLoop) % mouthLoop
                : firstMouthForE2 - lastMouthForE;

            if (mGap > 1) {
                // There are mouth vertices that don't have direct enclosure connections
                // Connect the enclosure edge through them
                const mi = mouthStart + lastMouthForE;
                const mi2 = mouthStart + firstMouthForE2;
                indices.push(frontInnerStart + e, mi, frontInnerStart + e2);
                indices.push(mi, mi2, frontInnerStart + e2);
            }
        }
    }

    const enclosureEndTri = indices.length / 3;
    if (groupInfo) {
        groupInfo.enclosure = { start: enclosureStartTri, end: enclosureEndTri };
        groupInfo.enclosureFront = { start: frontStartTri, end: frontEndTri };
    }
}
