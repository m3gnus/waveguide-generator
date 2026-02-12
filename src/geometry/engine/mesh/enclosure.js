/* Enclosure mesh generation */
// ===========================================================================
// Enclosure Geometry (Rear Chamber for BEM Simulation)
// ===========================================================================

/**
 * Calculates the normal vector for a point on an ellipse.
 */
function getEllipseNormal(t, rx, ry, cosP, sinP) {
    const localNx = ry * Math.cos(t);
    const localNy = rx * Math.sin(t);
    const nx = localNx * cosP - localNy * sinP;
    const ny = localNx * sinP + localNy * cosP;
    const len = Math.hypot(nx, ny);
    return { nx: nx / len, ny: ny / len };
}

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
        const x = center.x + Math.cos(a) * Math.hypot(p1.x - center.x, p1.y - center.y);
        const y = center.y + Math.sin(a) * Math.hypot(p1.x - center.x, p1.y - center.y);
        const dx = x - center.x;
        const dy = y - center.y;
        const len = Math.hypot(dx, dy);
        out.push({ x, y, nx: dx / len, ny: dy / len });
    }
    return out;
}

function sampleEllipse(p1, center, major, p2, steps = 24) {
    const vx = major.x - center.x;
    const vy = major.y - center.y;
    const a = Math.hypot(vx, vy);
    if (a <= 0) return [{ ...p1, nx: 0, ny: 0 }, { ...p2, nx: 0, ny: 0 }];

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
        const lx = a * Math.cos(t);
        const ly = b * Math.sin(t);
        const x = center.x + cosP * lx - sinP * ly;
        const y = center.y + sinP * lx + cosP * ly;

        const { nx, ny } = getEllipseNormal(t, a, b, cosP, sinP);
        out.push({ x, y, nx, ny });
    }
    return out;
}

function sampleBezier(points, steps = 24) {
    const out = [];
    const n = points.length - 1;

    const evaluate = (t) => {
        let x = 0, y = 0;
        let dx = 0, dy = 0;

        const binom = (n, k) => {
            let res = 1;
            for (let i = 1; i <= k; i++) res = (res * (n - (k - i))) / i;
            return res;
        };
        const bernstein = (n, i, t) => binom(n, i) * Math.pow(1 - t, n - i) * Math.pow(t, i);

        for (let i = 0; i <= n; i++) {
            const b = bernstein(n, i, t);
            x += points[i].x * b;
            y += points[i].y * b;
        }

        if (n > 0) {
            for (let i = 0; i < n; i++) {
                const b = bernstein(n - 1, i, t);
                const weight = n * b;
                dx += weight * (points[i + 1].x - points[i].x);
                dy += weight * (points[i + 1].y - points[i].y);
            }
        }

        const len = Math.hypot(dx, dy);
        const nx = len > 0 ? -dy / len : 0;
        const ny = len > 0 ? dx / len : 0;

        return { x, y, nx, ny };
    };

    for (let s = 0; s <= steps; s++) {
        const t = s / steps;
        out.push(evaluate(t));
    }
    return out;
}

function sampleLine(p1, p2) {
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    const len = Math.hypot(dx, dy);
    const nx = len > 0 ? -dy / len : 0;
    const ny = len > 0 ? dx / len : 0;

    return [
        { x: p1.x, y: p1.y, nx, ny },
        { x: p2.x, y: p2.y, nx, ny }
    ];
}

// Enclosure Plan feature removed - always return null for user-defined plans
// The enclosure uses standard rounded box outline instead
function buildPlanOutline(params, quadrantInfo) {
    return null;
}

function generateRoundedBoxOutline(maxX, minX, maxZ, minZ, params, quadrantInfo) {
    const sL = parseFloat(params.encSpaceL) || 25;
    const sT = parseFloat(params.encSpaceT) || 25;
    const sR = parseFloat(params.encSpaceR) || 25;
    const sB = parseFloat(params.encSpaceB) || 25;
    const edgeR = parseFloat(params.encEdge) || 0;
    const cornerSegs = Math.max(4, parseInt(params.cornerSegments) || 4);
    const edgeType = parseInt(params.encEdgeType) || 1; // 1=Rounded, 2=Chamfered

    const quadrantKey = String(params.quadrants ?? '').trim();
    const restrictX = quadrantKey === '14' || quadrantKey === '1' || quadrantKey === '14.0' || quadrantKey === '1.0';
    const restrictZ = quadrantKey === '12' || quadrantKey === '1' || quadrantKey === '12.0' || quadrantKey === '1.0';
    const isRightHalf = restrictX && !restrictZ;
    const isTopHalf = restrictZ && !restrictX;
    const isQuarter = restrictX && restrictZ;

    let boxRight = maxX + sR;
    let boxLeft = restrictX ? 0 : minX - sL;
    let boxTop = maxZ + sT;
    let boxBot = restrictZ ? 0 : minZ - sB;

    const halfW = (boxRight - boxLeft) / 2;
    const halfH = (boxTop - boxBot) / 2;
    const cx = (boxRight + boxLeft) / 2;
    const cz = (boxTop + boxBot) / 2;

    const cr = Math.min(edgeR, halfW - 0.1, halfH - 0.1);
    const outline = [];

    const addCorner = (cornerCx, cornerCz, startAngle, endAngle) => {
        const sweep = endAngle - startAngle;
        const sx = cornerCx + cr * Math.cos(startAngle);
        const sz = cornerCz + cr * Math.sin(startAngle);
        const ex = cornerCx + cr * Math.cos(endAngle);
        const ez = cornerCz + cr * Math.sin(endAngle);

        for (let i = 0; i <= cornerSegs; i++) {
            const t = i / cornerSegs;
            let x, z, nx, nz;

            if (edgeType === 2) {
                // Chamfer: Linear position, Slerp normal (to maintain offset validity)
                x = sx + (ex - sx) * t;
                z = sz + (ez - sz) * t;
                const a = startAngle + t * sweep;
                nx = Math.cos(a);
                nz = Math.sin(a);
            } else {
                // Rounded: Arc position, Radial normal
                const a = startAngle + t * sweep;
                const ca = Math.cos(a);
                const sa = Math.sin(a);
                x = cornerCx + cr * ca;
                z = cornerCz + cr * sa;
                nx = ca;
                nz = sa;
            }
            outline.push({ x, z, nx, nz });
        }
    };

    if (isQuarter) {
        addCorner(cx + halfW - cr, cz + halfH - cr, 0, Math.PI / 2);
        outline.push({ x: 0, z: boxTop, nx: 0, nz: 1 });
        outline.push({ x: 0, z: 0, nx: 0, nz: 1 });
        outline.push({ x: boxRight, z: 0, nx: 1, nz: 0 });
    } else if (isRightHalf) {
        addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2, 0);
        addCorner(cx + halfW - cr, cz + halfH - cr, 0, Math.PI / 2);
        outline.push({ x: 0, z: boxTop, nx: 0, nz: 1 });
        outline.push({ x: 0, z: boxBot, nx: 0, nz: -1 });
    } else if (isTopHalf) {
        addCorner(cx + halfW - cr, cz + halfH - cr, 0, Math.PI / 2);
        addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2, Math.PI);
        outline.push({ x: boxLeft, z: 0, nx: -1, nz: 0 });
        outline.push({ x: boxRight, z: 0, nx: 1, nz: 0 });
    } else {
        addCorner(cx + halfW - cr, cz - halfH + cr, -Math.PI / 2, 0);       // BR
        addCorner(cx + halfW - cr, cz + halfH - cr, 0, Math.PI / 2);        // TR
        addCorner(cx - halfW + cr, cz + halfH - cr, Math.PI / 2, Math.PI);      // TL
        addCorner(cx - halfW + cr, cz - halfH + cr, Math.PI, Math.PI * 1.5);    // BL
        const start = outline[0];
        outline.push({ ...start });
    }

    return outline;
}

function dedupePerimeterPoints(outerPts, insetPts, eps = 1e-6) {
    if (!Array.isArray(outerPts) || !Array.isArray(insetPts) || outerPts.length !== insetPts.length) {
        return { outerPts, insetPts };
    }

    const kept = [];
    for (let i = 0; i < outerPts.length; i++) {
        if (kept.length === 0) {
            kept.push(i);
            continue;
        }
        const prev = kept[kept.length - 1];
        const dox = outerPts[i].x - outerPts[prev].x;
        const doz = outerPts[i].z - outerPts[prev].z;
        const dix = insetPts[i].x - insetPts[prev].x;
        const diz = insetPts[i].z - insetPts[prev].z;
        if (Math.hypot(dox, doz) > eps || Math.hypot(dix, diz) > eps) {
            kept.push(i);
        }
    }

    if (kept.length > 2) {
        const first = kept[0];
        const last = kept[kept.length - 1];
        const dox = outerPts[first].x - outerPts[last].x;
        const doz = outerPts[first].z - outerPts[last].z;
        const dix = insetPts[first].x - insetPts[last].x;
        const diz = insetPts[first].z - insetPts[last].z;
        if (Math.hypot(dox, doz) <= eps && Math.hypot(dix, diz) <= eps) {
            kept.pop();
        }
    }

    return {
        outerPts: kept.map((idx) => outerPts[idx]),
        insetPts: kept.map((idx) => insetPts[idx])
    };
}

function resampleClosedLoop(points, targetCount, cx, cz) {
    if (!Array.isArray(points) || points.length === 0 || targetCount <= 0) return [];
    if (points.length === targetCount) {
        return points.map((p) => ({ ...p }));
    }

    const cumulative = [0];
    let total = 0;
    for (let i = 0; i < points.length; i++) {
        const next = (i + 1) % points.length;
        const dx = points[next].x - points[i].x;
        const dz = points[next].z - points[i].z;
        total += Math.hypot(dx, dz);
        cumulative.push(total);
    }

    if (total <= 1e-9) {
        return Array.from({ length: targetCount }, () => ({ ...points[0] }));
    }

    const out = [];
    for (let k = 0; k < targetCount; k++) {
        const targetDist = (k / targetCount) * total;
        let seg = 0;
        while (seg + 1 < cumulative.length && cumulative[seg + 1] < targetDist) {
            seg++;
        }
        const p0 = points[seg % points.length];
        const p1 = points[(seg + 1) % points.length];
        const segStart = cumulative[seg];
        const segLen = Math.max(1e-12, cumulative[seg + 1] - segStart);
        const t = (targetDist - segStart) / segLen;
        const x = p0.x + (p1.x - p0.x) * t;
        const z = p0.z + (p1.z - p0.z) * t;
        const dx = x - cx;
        const dz = z - cz;
        const len = Math.hypot(dx, dz);
        const nx = len > 0 ? dx / len : 0;
        const nz = len > 0 ? dz / len : 0;
        out.push({ x, z, nx, nz });
    }

    return out;
}

export function addEnclosureGeometry(vertices, indices, params, verticalOffset = 0, quadrantInfo = null, groupInfo = null, ringCount = null, angleList = null) {
    const ringSize = Number.isFinite(ringCount) && ringCount > 0
        ? ringCount
        : Math.max(2, Math.round(params.angularSegments || 0));

    const lastRowStart = params.lengthSegments * ringSize;
    const mouthY = vertices[lastRowStart * 3 + 1];

    const depth = parseFloat(params.encDepth) || 0;
    const edgeR = parseFloat(params.encEdge) || 0;
    const interfaceOffset = parseFloat(params.interfaceOffset) || 0;
    const edgeType = parseInt(params.encEdgeType) || 1;
    const axialSegs = edgeR > 0 ? Math.max(4, parseInt(params.cornerSegments) || 4) : 1;
    const backY = mouthY - depth;

    let maxX = -Infinity, minX = Infinity, maxZ = -Infinity, minZ = Infinity;
    for (let i = 0; i < ringSize; i++) {
        const idx = lastRowStart + i;
        const mx = vertices[idx * 3];
        const mz = vertices[idx * 3 + 2];
        maxX = Math.max(maxX, mx);
        minX = Math.min(minX, mx);
        maxZ = Math.max(maxZ, mz);
        minZ = Math.min(minZ, mz);
    }

    if (params.useAthEnclosureRounding) {
        if (Number.isFinite(maxX)) maxX = Math.ceil(maxX);
        if (Number.isFinite(minX)) minX = Math.floor(minX);
        if (Number.isFinite(maxZ)) maxZ = Math.ceil(maxZ);
        if (Number.isFinite(minZ)) minZ = Math.floor(minZ);
    }

    const planOutline = buildPlanOutline(params, quadrantInfo);
    const outline = (planOutline && planOutline.length > 1)
        ? planOutline
        : generateRoundedBoxOutline(maxX, minX, maxZ, minZ, params, quadrantInfo);

    const cleanOutline = [];
    if (outline.length > 0) cleanOutline.push(outline[0]);
    for (let i = 1; i < outline.length; i++) {
        const p = outline[i];
        const prev = cleanOutline[cleanOutline.length - 1];
        const d = Math.hypot(p.x - prev.x, p.z - prev.z);
        if (d > 1e-4) cleanOutline.push(p);
    }
    if (cleanOutline.length > 2) {
        const first = cleanOutline[0];
        const last = cleanOutline[cleanOutline.length - 1];
        const dClose = Math.hypot(first.x - last.x, first.z - last.z);
        if (dClose <= 1e-4) cleanOutline.pop();
    }

    let centerSumX = 0, centerSumZ = 0;
    cleanOutline.forEach(p => { centerSumX += p.x; centerSumZ += p.z; });
    const cx = centerSumX / cleanOutline.length;
    const cz = centerSumZ / cleanOutline.length;

    let outerPts = [];
    let insetPts = [];

    for (let i = 0; i < cleanOutline.length; i++) {
        const pt = cleanOutline[i];

        let nx = pt.nx;
        let nz = pt.nz;

        const dx = pt.x - cx;
        const dz = pt.z - cz;
        if (!Number.isFinite(nx) || (nx === 0 && nz === 0)) {
            const len = Math.hypot(dx, dz);
            nx = len > 0 ? dx / len : 0;
            nz = len > 0 ? dz / len : 0;
        } else {
            if (nx * dx + nz * dz < 0) {
                nx = -nx;
                nz = -nz;
            }
        }

        outerPts.push({ x: pt.x, z: pt.z, nx, nz });

        insetPts.push({
            x: pt.x - nx * edgeR,
            z: pt.z - nz * edgeR,
            nx, nz
        });
    }

    // When offsetting a rounded rectangle by the same radius, corner sample runs
    // can collapse to repeated points. Remap those points onto the collapsed
    // inset polygon using radial projection so the perimeter remains well-behaved.
    const dedupedInset = [];
    const collapseEps = 1e-6;
    for (let i = 0; i < insetPts.length; i++) {
        const p = insetPts[i];
        if (dedupedInset.length === 0) {
            dedupedInset.push({ x: p.x, z: p.z });
            continue;
        }
        const prev = dedupedInset[dedupedInset.length - 1];
        const d = Math.hypot(p.x - prev.x, p.z - prev.z);
        if (d > collapseEps) dedupedInset.push({ x: p.x, z: p.z });
    }
    if (dedupedInset.length > 2) {
        const first = dedupedInset[0];
        const last = dedupedInset[dedupedInset.length - 1];
        const d = Math.hypot(first.x - last.x, first.z - last.z);
        if (d <= collapseEps) dedupedInset.pop();
    }

    const projectRayToPolygon = (dx, dz, polygon) => {
        const denomEps = 1e-10;
        let bestT = Infinity;
        let hit = null;

        for (let i = 0; i < polygon.length; i++) {
            const p1 = polygon[i];
            const p2 = polygon[(i + 1) % polygon.length];
            const ex = p2.x - p1.x;
            const ez = p2.z - p1.z;

            const det = dx * (-ez) - dz * (-ex);
            if (Math.abs(det) <= denomEps) continue;

            const rhsX = p1.x - cx;
            const rhsZ = p1.z - cz;

            const t = (rhsX * (-ez) - rhsZ * (-ex)) / det;
            const u = (dx * rhsZ - dz * rhsX) / det;

            if (t <= 0 || u < -1e-6 || u > 1 + 1e-6) continue;
            if (t < bestT) {
                bestT = t;
                hit = {
                    x: cx + dx * t,
                    z: cz + dz * t
                };
            }
        }
        return hit;
    };

    if (dedupedInset.length >= 3 && dedupedInset.length < insetPts.length) {
        for (let i = 0; i < insetPts.length; i++) {
            const opt = outerPts[i];
            const dx = opt.x - cx;
            const dz = opt.z - cz;
            const dirLen = Math.hypot(dx, dz);
            if (dirLen <= collapseEps) continue;
            const hit = projectRayToPolygon(dx / dirLen, dz / dirLen, dedupedInset);
            if (!hit) continue;
            insetPts[i].x = hit.x;
            insetPts[i].z = hit.z;
        }
    }

    const dedupedPerimeter = dedupePerimeterPoints(outerPts, insetPts, collapseEps);
    outerPts = dedupedPerimeter.outerPts;
    insetPts = dedupedPerimeter.insetPts;

    if (outerPts.length !== ringSize) {
        outerPts = resampleClosedLoop(outerPts, ringSize, cx, cz);
    }
    if (insetPts.length !== ringSize) {
        insetPts = resampleClosedLoop(insetPts, ringSize, cx, cz);
    }

    const totalPts = insetPts.length;
    if (totalPts < 3) return;

    const mouthRing = [];
    for (let i = 0; i < ringSize; i++) {
        const idx = lastRowStart + i;
        mouthRing.push({ x: vertices[idx * 3], y: vertices[idx * 3 + 1], z: vertices[idx * 3 + 2] });
    }

    const ring0Start = vertices.length / 3;
    for (let i = 0; i < totalPts; i++) {
        const ipt = insetPts[i];

        let bestY = mouthY;
        let bestDist = Infinity;
        const ang = Math.atan2(ipt.z - cz, ipt.x - cx);
        for (const mv of mouthRing) {
            const mang = Math.atan2(mv.z - cz, mv.x - cx);
            let diff = Math.abs(ang - mang);
            if (diff > Math.PI) diff = 2 * Math.PI - diff;
            if (diff < bestDist) { bestDist = diff; bestY = mv.y; }
        }

        vertices.push(ipt.x, bestY, ipt.z);
    }

    const roundoverStartRingIdx = ring0Start;

    const frontRings = [];
    if (edgeR > 0.001) {
        for (let s = 1; s <= axialSegs; s++) {
            const ringStart = vertices.length / 3;
            frontRings.push(ringStart);

            const t = s / axialSegs;
            const phi = t * Math.PI / 2;
            const c = Math.cos(phi);
            const s_ang = Math.sin(phi);

            for (let i = 0; i < totalPts; i++) {
                const ipt = insetPts[i];
                const nx = ipt.nx;
                const nz = ipt.nz;

                const yBase = vertices[(roundoverStartRingIdx + i) * 3 + 1];
                const opt = outerPts[i];

                let x, y, z;
                if (edgeType === 2) {
                    x = ipt.x + (opt.x - ipt.x) * t;
                    z = ipt.z + (opt.z - ipt.z) * t;
                    y = yBase - edgeR * t;
                } else {
                    x = ipt.x + (opt.x - ipt.x) * s_ang;
                    z = ipt.z + (opt.z - ipt.z) * s_ang;
                    y = yBase - edgeR * (1 - c);
                }
                vertices.push(x, y, z);
            }
        }
    }

    const backStartRing = vertices.length / 3;

    for (let i = 0; i < totalPts; i++) {
        const opt = outerPts[i];
        vertices.push(opt.x, backY + edgeR, opt.z);
    }

    const backRings = [];
    if (edgeR > 0.001) {
        for (let s = 1; s <= axialSegs; s++) {
            const ringStart = vertices.length / 3;
            backRings.push(ringStart);

            const t = s / axialSegs;
            const phi = t * Math.PI / 2;
            const c = Math.cos(phi);
            const s_ang = Math.sin(phi);

            for (let i = 0; i < totalPts; i++) {
                const opt = outerPts[i];
                const ipt = insetPts[i];
                const nx = opt.nx;
                const nz = opt.nz;

                let x, y, z;
                if (edgeType === 2) {
                    x = opt.x + (ipt.x - opt.x) * t;
                    z = opt.z + (ipt.z - opt.z) * t;
                    y = (backY + edgeR) - edgeR * t;
                } else {
                    x = opt.x + (ipt.x - opt.x) * (1 - c);
                    z = opt.z + (ipt.z - opt.z) * (1 - c);
                    y = (backY + edgeR) - edgeR * s_ang;
                }
                vertices.push(x, y, z);
            }
        }
    }

    const TRI_EPSILON = 1e-10;
    const pushTri = (a, b, c) => {
        if (a === b || b === c || c === a) return;

        const ax = vertices[a * 3];
        const ay = vertices[a * 3 + 1];
        const az = vertices[a * 3 + 2];
        const bx = vertices[b * 3];
        const by = vertices[b * 3 + 1];
        const bz = vertices[b * 3 + 2];
        const cx = vertices[c * 3];
        const cy = vertices[c * 3 + 1];
        const cz = vertices[c * 3 + 2];

        const abx = bx - ax;
        const aby = by - ay;
        const abz = bz - az;
        const acx = cx - ax;
        const acy = cy - ay;
        const acz = cz - az;

        const nx = aby * acz - abz * acy;
        const ny = abz * acx - abx * acz;
        const nz = abx * acy - aby * acx;
        const area2 = Math.hypot(nx, ny, nz);
        if (area2 <= TRI_EPSILON) return;

        indices.push(a, b, c);
    };

    const stitch = (r1Start, r2Start) => {
        for (let i = 0; i < totalPts; i++) {
            const i2 = (i + 1) % totalPts;
            pushTri(r2Start + i, r1Start + i, r1Start + i2);
            pushTri(r2Start + i, r1Start + i2, r2Start + i2);
        }
    };

    const enclosureStartTri = indices.length / 3;

    const fullCircle = !quadrantInfo || quadrantInfo.fullCircle;
    const mouthLoop = ringSize;

    // Greedy Stitching Logic Mouth -> Enclosure
    // Initialize start offset (find nearest Enclosure vertex to Mouth[0]).
    // Use the mouth ring centroid as the angular reference, not the enclosure
    // box centroid — the enclosure centroid shifts with asymmetric spacing
    // (e.g. large bottom margin) and produces wrong angular alignment.
    let mCx = 0, mCz = 0;
    for (const mv of mouthRing) { mCx += mv.x; mCz += mv.z; }
    mCx /= mouthRing.length;
    mCz /= mouthRing.length;

    let bestIdx = 0;
    let minDiff = Infinity;
    const m0 = mouthRing[0];
    const m0ang = Math.atan2(m0.z - mCz, m0.x - mCx);
    for (let j = 0; j < totalPts; j++) {
        const pt = outerPts[j];
        const ang = Math.atan2(pt.z - mCz, pt.x - mCx);
        let diff = Math.abs(m0ang - ang);
        if (diff > Math.PI) diff = 2 * Math.PI - diff;
        if (diff < minDiff) { minDiff = diff; bestIdx = j; }
    }
    const ePsi = bestIdx; // Enclosure Start Pointer

    const mLen = fullCircle ? mouthLoop : Math.max(0, mouthLoop - 1);
    const eLen = totalPts;

    const getM = (i) => lastRowStart + (i % mouthLoop);
    const getE = (i) => ring0Start + ((ePsi + i) % totalPts);
    let m = 0;
    let e = 0;
    const maxSteps = (mLen + eLen) * 2;
    let steps = 0;

    // For full circle, we stitch until we wrap around completely (e.g. M and E both complete loops)
    // For partial, we stitch until both reach end.

    // Condition handling:
    // If Full Circle: We repeat until both m and e have traversed at least one full loop.
    // Actually, simple greedy stitch for loop:
    // Continue until m >= mLen AND e >= eLen? 
    // Wait, m and e are loop counts.
    // If m wraps, m continues growing.

    const limitM = fullCircle ? mLen : mLen; // Effectively loop needs to clear this count
    const limitE = fullCircle ? eLen : eLen;
    // Actually if fullCircle is true, we want to close the loop.
    // The number of triangles is approx 2 * max(m, e).

    // Robust Terminator:
    // We stop when M has completed its loop AND E has completed its loop.
    // For full circle, "completed" means wrapped around to start.

    if (fullCircle && eLen === mouthLoop) {
        for (let i = 0; i < mouthLoop; i++) {
            const m0 = lastRowStart + i;
            const m1 = lastRowStart + ((i + 1) % mouthLoop);
            const e0 = ring0Start + ((ePsi + i) % totalPts);
            const e1 = ring0Start + ((ePsi + i + 1) % totalPts);
            // Shared mouth edges must run opposite orientation vs horn shell.
            pushTri(m0, m1, e0);
            pushTri(m1, e1, e0);
        }
    } else {
        while ((m < limitM || e < limitE) && steps++ < maxSteps) {
            const idxM = fullCircle ? getM(m) : (lastRowStart + m);
            const idxMNext = fullCircle ? getM(m + 1) : (lastRowStart + m + 1);
            const idxE = fullCircle ? getE(e) : (ring0Start + (ePsi + e) % totalPts);
            const idxENext = fullCircle ? getE(e + 1) : (ring0Start + (ePsi + e + 1) % totalPts);

            let advanceM = false;
            if (m >= limitM) {
                advanceM = false;
            } else if (e >= limitE) {
                advanceM = true;
            } else {
                // Deterministic zipper progress avoids skipped mouth edges.
                const mProgress = (m + 1) / Math.max(1, mLen);
                const eProgress = (e + 1) / Math.max(1, eLen);
                advanceM = mProgress < eProgress;
            }

            if (advanceM) {
                // Shared mouth edges must run opposite orientation vs horn shell.
                pushTri(idxM, idxMNext, idxE);
                m++;
            } else {
                pushTri(idxM, idxE, idxENext);
                e++;
            }
        }
    }


    const averageRingY = (ringStart) => {
        let sumY = 0;
        for (let i = 0; i < totalPts; i++) {
            sumY += vertices[(ringStart + i) * 3 + 1];
        }
        return sumY / totalPts;
    };

    const createInterpolatedRing = (fromStart, toStart, t) => {
        const ringStart = vertices.length / 3;
        for (let i = 0; i < totalPts; i++) {
            const fromIdx = (fromStart + i) * 3;
            const toIdx = (toStart + i) * 3;
            const x = vertices[fromIdx] + (vertices[toIdx] - vertices[fromIdx]) * t;
            const y = vertices[fromIdx + 1] + (vertices[toIdx + 1] - vertices[fromIdx + 1]) * t;
            const z = vertices[fromIdx + 2] + (vertices[toIdx + 2] - vertices[fromIdx + 2]) * t;
            vertices.push(x, y, z);
        }
        return ringStart;
    };

    let prevRing = ring0Start;
    let interfaceStartTri = null;
    let interfaceEndTri = null;

    for (const rid of frontRings) {
        stitch(prevRing, rid);
        prevRing = rid;
    }

    if (interfaceOffset > 0.001) {
        const frontY = averageRingY(prevRing);
        const backYRing = averageRingY(backStartRing);
        const availableDepth = frontY - backYRing;
        if (availableDepth > 1e-6) {
            const rawT = interfaceOffset / availableDepth;
            const t = Math.min(1 - 1e-4, Math.max(1e-4, rawT));
            const interfaceRingStart = createInterpolatedRing(prevRing, backStartRing, t);
            interfaceStartTri = indices.length / 3;
            stitch(prevRing, interfaceRingStart);
            interfaceEndTri = indices.length / 3;
            prevRing = interfaceRingStart;
        }
    }

    stitch(prevRing, backStartRing);

    prevRing = backStartRing;
    for (const rid of backRings) {
        stitch(prevRing, rid);
        prevRing = rid;
    }

    const backInnerStart = prevRing;
    let centerX = 0;
    let centerY = 0;
    let centerZ = 0;
    for (let i = 0; i < totalPts; i++) {
        const idx = backInnerStart + i;
        centerX += vertices[idx * 3];
        centerY += vertices[idx * 3 + 1];
        centerZ += vertices[idx * 3 + 2];
    }
    centerX /= totalPts;
    centerY /= totalPts;
    centerZ /= totalPts;

    const rearCenterIdx = vertices.length / 3;
    vertices.push(centerX, centerY, centerZ);

    const preCapTriEnd = indices.length / 3;
    const edgeMatchesOrientation = (fromIdx, toIdx) => {
        for (let t = enclosureStartTri; t < preCapTriEnd; t++) {
            const off = t * 3;
            const a = indices[off];
            const b = indices[off + 1];
            const c = indices[off + 2];
            if ((a === fromIdx && b === toIdx) || (b === fromIdx && c === toIdx) || (c === fromIdx && a === toIdx)) {
                return 1;
            }
            if ((a === toIdx && b === fromIdx) || (b === toIdx && c === fromIdx) || (c === toIdx && a === fromIdx)) {
                return -1;
            }
        }
        return 0;
    };

    let useForward = true;
    for (let i = 0; i < totalPts; i++) {
        const i2 = (i + 1) % totalPts;
        const idx1 = backInnerStart + i;
        const idx2 = backInnerStart + i2;
        const edgeOrientation = edgeMatchesOrientation(idx1, idx2);
        if (edgeOrientation === 1) {
            useForward = false;
            break;
        }
        if (edgeOrientation === -1) {
            useForward = true;
            break;
        }
    }

    for (let i = 0; i < totalPts; i++) {
        const i2 = (i + 1) % totalPts;
        const idx1 = backInnerStart + i;
        const idx2 = backInnerStart + i2;
        if (useForward) {
            pushTri(idx1, idx2, rearCenterIdx);
        } else {
            pushTri(idx2, idx1, rearCenterIdx);
        }
    }

    // No throat source in enclosure - it's now part of the horn geometry

    const enclosureEndTri = indices.length / 3;
    if (groupInfo) {
        groupInfo.enclosure = { start: enclosureStartTri, end: enclosureEndTri };
        if (interfaceStartTri !== null && interfaceEndTri !== null && interfaceEndTri > interfaceStartTri) {
            groupInfo.interface = { start: interfaceStartTri, end: interfaceEndTri };
        }
    }
}
