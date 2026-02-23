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

// ---------------------------------------------------------------------------
// Angular-distribution-based enclosure point generation.
//
// For each angle in the angleList (the same angles used by the waveguide mouth
// ring), cast a ray from the enclosure center and intersect it with the
// rounded-box boundary. This produces exactly ringSize outer/inset points
// that are 1:1 aligned with the mouth vertices, eliminating the previous
// mismatch between angular and arc-length distributions.
// ---------------------------------------------------------------------------

/**
 * Compute the intersection of a ray from (cx, cz) at angle `angle` with a
 * rounded rectangle defined by (boxLeft, boxRight, boxBot, boxTop) with
 * corner radius `cr` and optional chamfer (edgeType === 2).
 */
function intersectRayWithRoundedBox(
    angle, cx, cz,
    boxLeft, boxRight, boxBot, boxTop,
    cr, edgeType
) {
    const cosA = Math.cos(angle);
    const sinA = Math.sin(angle);

    const EPS = 1e-12;
    let bestT = Infinity;
    let hitX = cx + cosA;
    let hitZ = cz + sinA;
    let hitNx = cosA;
    let hitNz = sinA;

    const trySegment = (x1, z1, x2, z2, nx, nz) => {
        const ex = x2 - x1;
        const ez = z2 - z1;
        const det = cosA * (-ez) - sinA * (-ex);
        if (Math.abs(det) <= EPS) return;

        const rhsX = x1 - cx;
        const rhsZ = z1 - cz;
        const t = (rhsX * (-ez) - rhsZ * (-ex)) / det;
        const u = (cosA * rhsZ - sinA * rhsX) / det;

        if (t > EPS && u >= -EPS && u <= 1 + EPS && t < bestT) {
            bestT = t;
            hitX = cx + cosA * t;
            hitZ = cz + sinA * t;
            hitNx = nx;
            hitNz = nz;
        }
    };

    const tryArc = (acx, acz, r, startAngle, endAngle) => {
        const ox = cx - acx;
        const oz = cz - acz;
        const A = 1;
        const B = 2 * (ox * cosA + oz * sinA);
        const C = ox * ox + oz * oz - r * r;
        const disc = B * B - 4 * A * C;
        if (disc < 0) return;

        const sqrtDisc = Math.sqrt(disc);
        for (const t of [(-B - sqrtDisc) / (2 * A), (-B + sqrtDisc) / (2 * A)]) {
            if (t <= EPS || t >= bestT) continue;
            const px = cx + cosA * t;
            const pz = cz + sinA * t;
            let pa = Math.atan2(pz - acz, px - acx);
            let swept = endAngle - startAngle;
            let relAngle = pa - startAngle;
            while (relAngle < -EPS) relAngle += Math.PI * 2;
            while (relAngle > Math.PI * 2 + EPS) relAngle -= Math.PI * 2;
            if (relAngle <= swept + EPS) {
                bestT = t;
                hitX = px;
                hitZ = pz;
                const dx = px - acx;
                const dz = pz - acz;
                const len = Math.hypot(dx, dz);
                hitNx = len > 0 ? dx / len : 0;
                hitNz = len > 0 ? dz / len : 0;
            }
        }
    };

    const tryChamfer = (acx, acz, r, startAngle, endAngle) => {
        const x1 = acx + r * Math.cos(startAngle);
        const z1 = acz + r * Math.sin(startAngle);
        const x2 = acx + r * Math.cos(endAngle);
        const z2 = acz + r * Math.sin(endAngle);
        const midA = (startAngle + endAngle) / 2;
        trySegment(x1, z1, x2, z2, Math.cos(midA), Math.sin(midA));
    };

    const halfW = (boxRight - boxLeft) / 2;
    const halfH = (boxTop - boxBot) / 2;
    const bCx = (boxRight + boxLeft) / 2;
    const bCz = (boxTop + boxBot) / 2;
    const r = Math.min(cr, halfW - 0.1, halfH - 0.1);
    const useCorners = r > 0.001;

    trySegment(boxRight, bCz - halfH + (useCorners ? r : 0), boxRight, bCz + halfH - (useCorners ? r : 0), 1, 0);
    trySegment(bCx + halfW - (useCorners ? r : 0), boxTop, bCx - halfW + (useCorners ? r : 0), boxTop, 0, 1);
    trySegment(boxLeft, bCz + halfH - (useCorners ? r : 0), boxLeft, bCz - halfH + (useCorners ? r : 0), -1, 0);
    trySegment(bCx - halfW + (useCorners ? r : 0), boxBot, bCx + halfW - (useCorners ? r : 0), boxBot, 0, -1);

    if (useCorners) {
        const corners = [
            { cx: bCx + halfW - r, cz: bCz - halfH + r, start: -Math.PI / 2, end: 0 },
            { cx: bCx + halfW - r, cz: bCz + halfH - r, start: 0, end: Math.PI / 2 },
            { cx: bCx - halfW + r, cz: bCz + halfH - r, start: Math.PI / 2, end: Math.PI },
            { cx: bCx - halfW + r, cz: bCz - halfH + r, start: Math.PI, end: Math.PI * 1.5 }
        ];
        for (const c of corners) {
            if (edgeType === 2) tryChamfer(c.cx, c.cz, r, c.start, c.end);
            else tryArc(c.cx, c.cz, r, c.start, c.end);
        }
    }
    return { x: hitX, z: hitZ, nx: hitNx, nz: hitNz };
}

function generateEnclosurePointsFromAngles(
    angleList, cx, cz,
    boxLeft, boxRight, boxBot, boxTop,
    edgeR, edgeType
) {
    const ringSize = angleList.length;
    const outerPts = [];
    const insetPts = [];
    const halfW = (boxRight - boxLeft) / 2;
    const halfH = (boxTop - boxBot) / 2;
    const boxCR = parseFloat(edgeR) || 0;
    const clampedBoxCR = Math.min(boxCR, halfW - 0.1, halfH - 0.1);

    for (let i = 0; i < ringSize; i++) {
        const angle = angleList[i];
        const hit = intersectRayWithRoundedBox(
            angle, cx, cz,
            boxLeft, boxRight, boxBot, boxTop,
            clampedBoxCR, edgeType
        );
        outerPts.push({ x: hit.x, z: hit.z, nx: hit.nx, nz: hit.nz });
        insetPts.push({
            x: hit.x - hit.nx * clampedBoxCR,
            z: hit.z - hit.nz * clampedBoxCR,
            nx: hit.nx,
            nz: hit.nz
        });
    }
    return { outerPts, insetPts };
}

export function addEnclosureGeometry(vertices, indices, params, verticalOffset = 0, quadrantInfo = null, groupInfo = null, ringCount = null, angleList = null) {
    const ringSize = Number.isFinite(ringCount) && ringCount > 0
        ? ringCount
        : Math.max(2, Math.round(params.angularSegments || 0));

    const lastRowStart = params.lengthSegments * ringSize;
    const mouthY = vertices[lastRowStart * 3 + 1];

    const depth = parseFloat(params.encDepth) || 0;
    const edgeR = parseFloat(params.encEdge) || 0;
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

    // Compute enclosure box boundaries
    const sL = parseFloat(params.encSpaceL) || 25;
    const sT = parseFloat(params.encSpaceT) || 25;
    const sR = parseFloat(params.encSpaceR) || 25;
    const sB = parseFloat(params.encSpaceB) || 25;

    let boxRight = maxX + sR;
    let boxLeft = minX - sL;
    let boxTop = maxZ + sT;
    let boxBot = minZ - sB;

    // Centroid for ray-casting
    let mCx = 0, mCz = 0;
    for (let i = 0; i < ringSize; i++) {
        const idx = lastRowStart + i;
        mCx += vertices[idx * 3];
        mCz += vertices[idx * 3 + 2];
    }
    mCx /= ringSize;
    mCz /= ringSize;

    const cx = mCx;
    const cz = mCz;

    // --- Step 3: Angular point generation ---
    let outerPts, insetPts;
    if (Array.isArray(angleList) && angleList.length === ringSize) {
        const result = generateEnclosurePointsFromAngles(angleList, cx, cz, boxLeft, boxRight, boxBot, boxTop, edgeR, edgeType);
        outerPts = result.outerPts;
        insetPts = result.insetPts;
    } else {
        const syntheticAngles = Array.from({ length: ringSize }, (_, i) => (i / ringSize) * Math.PI * 2);
        const result = generateEnclosurePointsFromAngles(syntheticAngles, cx, cz, boxLeft, boxRight, boxBot, boxTop, edgeR, edgeType);
        outerPts = result.outerPts;
        insetPts = result.insetPts;
    }

    const totalPts = ringSize;

    // --- Step 4: Align Enclosure Ring 0 with Mouth ---
    const mergeEps = 1e-6;
    let reuseMouthAsRing0 = true;
    for (let i = 0; i < totalPts; i++) {
        const ipt = insetPts[i];
        const mouthX = vertices[(lastRowStart + i) * 3];
        const mouthZ = vertices[(lastRowStart + i) * 3 + 2];
        if (Math.hypot(ipt.x - mouthX, ipt.z - mouthZ) > mergeEps) {
            reuseMouthAsRing0 = false;
            break;
        }
    }

    let ring0Start;
    if (reuseMouthAsRing0) {
        ring0Start = lastRowStart;
    } else {
        const seamNudge = 1e-4;
        ring0Start = vertices.length / 3;
        for (let i = 0; i < totalPts; i++) {
            const ipt = insetPts[i];
            const mouthVY = vertices[(lastRowStart + i) * 3 + 1];
            vertices.push(
                ipt.x - (ipt.nx || 0) * seamNudge,
                mouthVY,
                ipt.z - (ipt.nz || 0) * seamNudge
            );
        }
    }

    // Intermediate rings (roundover/chamfer)
    const frontRings = [ring0Start];

    const edgeSlices = edgeR > 0 ? Math.max(1, axialSegs) : 0;
    for (let j = 1; j <= edgeSlices; j++) {
        const ringIdx = vertices.length / 3;
        frontRings.push(ringIdx);
        const t = j / edgeSlices;
        let axialT = t, radialT = t;
        if (edgeType === 1) {
            const angle = t * (Math.PI / 2);
            axialT = 1 - Math.cos(angle);
            radialT = Math.sin(angle);
        }
        const y = mouthY - (axialT * edgeR);
        for (let i = 0; i < totalPts; i++) {
            const ipt = insetPts[i];
            const opt = outerPts[i];
            const px = ipt.x + (opt.x - ipt.x) * radialT;
            const pz = ipt.z + (opt.z - ipt.z) * radialT;
            vertices.push(px, y, pz);
        }
    }

    const mainFrontRing = frontRings[frontRings.length - 1];
    const backRingStart = vertices.length / 3;
    for (let i = 0; i < totalPts; i++) {
        const opt = outerPts[i];
        vertices.push(opt.x, backY, opt.z);
    }

    // Helpers
    const pushTri = (a, b, c) => { indices.push(a, b, c); };
    const stitch = (r1Start, r2Start) => {
        const limit = fullCircle ? totalPts : totalPts - 1;
        for (let i = 0; i < limit; i++) {
            const i2 = (i + 1) % totalPts;
            pushTri(r2Start + i, r1Start + i, r1Start + i2);
            pushTri(r2Start + i, r1Start + i2, r2Start + i2);
        }
    };

    const enclosureStartTri = indices.length / 3;
    const fullCircle = !quadrantInfo || quadrantInfo.fullCircle;

    // Mouth to Enclosure stitch (1:1 direct)
    if (!reuseMouthAsRing0) {
        if (fullCircle) {
            for (let i = 0; i < totalPts; i++) {
                const i2 = (i + 1) % totalPts;
                pushTri(lastRowStart + i, lastRowStart + i2, ring0Start + i);
                pushTri(lastRowStart + i2, ring0Start + i2, ring0Start + i);
            }
        } else {
            for (let i = 0; i < totalPts - 1; i++) {
                pushTri(lastRowStart + i, lastRowStart + i + 1, ring0Start + i);
                pushTri(lastRowStart + i + 1, ring0Start + i + 1, ring0Start + i);
            }
        }
    }

    // Front rings stitch
    for (let i = 0; i < frontRings.length - 1; i++) {
        stitch(frontRings[i], frontRings[i + 1]);
    }
    stitch(mainFrontRing, backRingStart);

    // Back Cap
    const capStart = vertices.length / 3;
    let avgX = 0, avgZ = 0;
    for (let i = 0; i < totalPts; i++) {
        const opt = outerPts[i];
        avgX += opt.x; avgZ += opt.z;
    }
    avgX /= totalPts; avgZ /= totalPts;
    vertices.push(avgX, backY, avgZ);

    const capLimit = fullCircle ? totalPts : totalPts - 1;
    for (let i = 0; i < capLimit; i++) {
        const i2 = (i + 1) % totalPts;
        pushTri(backRingStart + i, backRingStart + i2, capStart);
    }

    const enclosureEndTri = indices.length / 3;
    if (groupInfo) {
        groupInfo.enclosure = { start: enclosureStartTri, end: enclosureEndTri };
    }
}
