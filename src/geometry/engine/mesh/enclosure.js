/* Enclosure mesh generation */
// ===========================================================================
// Enclosure Geometry (Rear Chamber for BEM Simulation)
// ===========================================================================

// Legacy plan-outline helpers were removed in Session 9 because the current
// enclosure pipeline only uses angular ray-casting against a rounded box.

// ---------------------------------------------------------------------------
// Angular-distribution-based enclosure point generation.
//
// The front roundover uses the mouth's angle list for a smooth baffle
// transition.  At the flat sidewall, a fan stitch transitions to a refined
// angle set that adds extra points at the enclosure's rounded corners.
// Corner subdivision density matches the Y-axis roundover arc step
// (edgeDepth × π/2 / edgeSlices), giving all four corners uniform curvature.
// ---------------------------------------------------------------------------

/**
 * Compute the intersection of a ray from (cx, cz) at angle `angle` with a
 * rounded rectangle defined by (boxLeft, boxRight, boxBot, boxTop) with
 * corner radius `cr` and optional chamfer (edgeType === 2).
 */
function intersectRayWithRoundedBox(
    angle, cx, cz,
    boxLeft, boxRight, boxBot, boxTop,
    cr, edgeType,
    params
) {
    const cosA = Math.cos(angle);
    const sinA = Math.sin(angle);

    const scale = params.scale || 1;
    const EPS = 1e-12 * scale;
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
    const minDim = Math.min(halfW, halfH);
    const r = Math.min(cr, halfW - 1e-4 * scale, halfH - 1e-4 * scale);
    const useCorners = r > 1e-3 * scale;

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
    edgeR, edgeType,
    params
) {
    const ringSize = angleList.length;
    const outerPts = [];
    const insetPts = [];
    const halfW = (boxRight - boxLeft) / 2;
    const halfH = (boxTop - boxBot) / 2;
    const boxCR = parseFloat(edgeR) || 0;
    const scale = params.scale || 1;
    const clampedBoxCR = Math.min(boxCR, halfW - 1e-4 * scale, halfH - 1e-4 * scale);

    for (let i = 0; i < ringSize; i++) {
        const angle = angleList[i];
        const hit = intersectRayWithRoundedBox(
            angle, cx, cz,
            boxLeft, boxRight, boxBot, boxTop,
            clampedBoxCR, edgeType,
            params
        );
        outerPts.push({ x: hit.x, z: hit.z, nx: hit.nx, nz: hit.nz });
        insetPts.push({
            x: hit.x - hit.nx * clampedBoxCR,
            z: hit.z - hit.nz * clampedBoxCR,
            nx: hit.nx,
            nz: hit.nz
        });
    }

    // For chamfered corners, snap inset points to the arc center so the
    // chamfer surface forms a proper triangle instead of a small rectangle.
    // Rounded corners already converge naturally (radial normals → arc center),
    // but chamfer normals are fixed (the midpoint angle of the corner), so
    // the normal-based offset produces a parallel line instead of a point.
    if (edgeType === 2 && clampedBoxCR > 1e-6) {
        const bCx = (boxRight + boxLeft) / 2;
        const bCz = (boxTop + boxBot) / 2;
        const r = clampedBoxCR;
        const arcCenters = [
            { x: bCx + halfW - r, z: bCz - halfH + r },
            { x: bCx + halfW - r, z: bCz + halfH - r },
            { x: bCx - halfW + r, z: bCz + halfH - r },
            { x: bCx - halfW + r, z: bCz - halfH + r },
        ];
        for (let i = 0; i < ringSize; i++) {
            const nx = outerPts[i].nx;
            const nz = outerPts[i].nz;
            // Chamfer hits have non-axis-aligned normals (both components > 0)
            if (Math.abs(nx) > 0.01 && Math.abs(nz) > 0.01) {
                let bestDist = Infinity;
                let best = arcCenters[0];
                for (const c of arcCenters) {
                    const d = Math.hypot(outerPts[i].x - c.x, outerPts[i].z - c.z);
                    if (d < bestDist) { bestDist = d; best = c; }
                }
                insetPts[i].x = best.x;
                insetPts[i].z = best.z;
            }
        }
    }

    return { outerPts, insetPts, clampedBoxCR };
}

/**
 * Refine mouth angles for enclosure corners.
 *
 * Flat edges keep the original mouth-angle spacing (for smooth baffle
 * transition).  Corner arcs — detected by a change in the outward normal
 * between consecutive outer points — are subdivided so that the XZ arc step
 * matches the Y-axis roundover arc step (edgeDepth × π/2 / edgeSlices).
 *
 * Returns { refined, mapping } where `refined` is the new angle array and
 * `mapping[i]` gives the index in `refined` that corresponds to mouth angle i.
 */
function refineAnglesForEnclosure(mouthAngles, outerPts, edgeSlices, edgeDepth, cx, cz) {
    const n = mouthAngles.length;
    const roundoverArcStep = edgeSlices > 0 && edgeDepth > 0
        ? (edgeDepth * Math.PI / 2) / edgeSlices
        : Infinity;

    const refined = [];
    const mapping = [0];

    for (let i = 0; i < n; i++) {
        refined.push(mouthAngles[i]);
        const j = (i + 1) % n;

        // Detect corner vs flat edge by checking normal change
        const ndx = Math.abs(outerPts[i].nx - outerPts[j].nx);
        const ndz = Math.abs(outerPts[i].nz - outerPts[j].nz);
        const isCorner = (ndx + ndz) > 0.01;
        const avgNx = (outerPts[i].nx + outerPts[j].nx) * 0.5;
        const avgNz = (outerPts[i].nz + outerPts[j].nz) * 0.5;
        const isTopOrBottom = Math.abs(avgNz) > 0.9 && Math.abs(avgNx) < 0.3;
        const isBottom = avgNz < -0.9 && Math.abs(avgNx) < 0.3;

        if (roundoverArcStep < Infinity) {
            const dist = Math.hypot(outerPts[j].x - outerPts[i].x, outerPts[j].z - outerPts[i].z);
            // Corners keep the original fine step. Top/bottom spans get a slightly
            // coarser adaptive split so their front/back roundover reads smoother
            // without globally increasing enclosure density.
            const targetStep = isCorner
                ? roundoverArcStep
                : (isBottom
                    ? roundoverArcStep * 0.7
                    : (isTopOrBottom ? roundoverArcStep * 1.25 : Infinity));
            const subdivs = targetStep < Infinity
                ? Math.max(0, Math.ceil(dist / targetStep) - 1)
                : 0;
            for (let k = 1; k <= subdivs; k++) {
                const t = k / (subdivs + 1);
                // Interpolate position along the boundary segment, then compute
                // angle from centroid.  This produces evenly-spaced boundary
                // points regardless of how far the segment is from the centroid,
                // fixing non-uniform density with asymmetric enclosure spacing.
                const px = outerPts[i].x + (outerPts[j].x - outerPts[i].x) * t;
                const pz = outerPts[i].z + (outerPts[j].z - outerPts[i].z) * t;
                refined.push(Math.atan2(pz - cz, px - cx));
            }
        }
        // Preserve direct index mapping from mouth-angle index -> refined index.
        if (i < n - 1) mapping.push(refined.length);
    }
    return { refined, mapping };
}

/**
 * Fan-stitch between a small ring (sSize points) and a larger ring (lSize
 * points).  `mapping[i]` gives the index in the large ring corresponding to
 * small-ring point i.  Between mapping[i] and mapping[i+1] there may be
 * extra large-ring vertices that fan out from small-ring vertex i.
 */
function fanStitchRings(indices, pushTri, sStart, sSize, lStart, lSize, mapping, wrap = true) {
    const limit = wrap ? sSize : sSize - 1;
    for (let i = 0; i < limit; i++) {
        const i2 = (i + 1) % sSize;
        const lS = mapping[i];
        const lE = mapping[i2]; // Use mapping for wrap-around (mapping[0]=0 triggers wrap path)
        const sA = sStart + i;
        const sB = sStart + i2;

        if (lE <= lS) {
            // Wrap-around: lS → end of ring, then 0 → lE
            for (let k = lS; k < lSize - 1; k++) {
                pushTri(sA, lStart + k + 1, lStart + k);
            }
            // Bridge from last large-ring vertex to first
            pushTri(sA, lStart + 0, lStart + lSize - 1);
            // Continue from 0 to lE
            for (let k = 0; k < lE; k++) {
                pushTri(sA, lStart + k + 1, lStart + k);
            }
            pushTri(sA, sB, lStart + lE);
        } else {
            for (let k = lS; k < lE; k++) {
                pushTri(sA, lStart + k + 1, lStart + k);
            }
            pushTri(sA, sB, lStart + lE);
        }
    }
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

    const scale = params.scale || 1;
    if (params.useAthEnclosureRounding) {
        if (Number.isFinite(maxX)) maxX = Math.ceil(maxX / scale) * scale;
        if (Number.isFinite(minX)) minX = Math.floor(minX / scale) * scale;
        if (Number.isFinite(maxZ)) maxZ = Math.ceil(maxZ / scale) * scale;
        if (Number.isFinite(minZ)) minZ = Math.floor(minZ / scale) * scale;
    }

    // Compute enclosure box boundaries
    const sL = parseFloat(params.encSpaceL) || 25;
    const sT = parseFloat(params.encSpaceT) || 25;
    const sR = parseFloat(params.encSpaceR) || 25;
    const sB = parseFloat(params.encSpaceB) || 25;

    let boxRight = maxX + sR;
    let boxLeft = minX - sL;
    let boxTop = maxZ + sT + verticalOffset;
    let boxBot = minZ - sB + verticalOffset;

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

    // --- Step 3: Generate enclosure points using mouth angles ---
    // Ring 0 always uses the mouth's angle list for a 1:1 baffle stitch.
    const mouthAngles = (Array.isArray(angleList) && angleList.length === ringSize)
        ? angleList
        : Array.from({ length: ringSize }, (_, i) => (i / ringSize) * Math.PI * 2);

    const mouthResult = generateEnclosurePointsFromAngles(
        mouthAngles, cx, cz,
        boxLeft, boxRight, boxBot, boxTop,
        edgeR, edgeType,
        params
    );
    const clampedEdgeR = mouthResult.clampedBoxCR;
    const edgeDepth = Math.min(clampedEdgeR || 0, Math.max(0, depth * 0.49));
    // Roundovers need multiple slices to approximate the arc curvature.
    // Chamfers are flat — a single slice is the correct geometry and avoids
    // smooth-shading interpolation that would visually round the edge.
    const edgeSlices = edgeDepth > 0
        ? (edgeType === 2 ? 1 : Math.max(1, axialSegs))
        : 0;

    // --- Step 3b: Refine angles at corners to match Y-axis roundover ---
    const { refined: refinedAngles, mapping: mouthToRefinedMap } =
        refineAnglesForEnclosure(mouthAngles, mouthResult.outerPts, edgeSlices, edgeDepth, cx, cz);
    const refinedSize = refinedAngles.length;
    const addedPts = refinedSize - ringSize;

    // Generate enclosure points for the refined angle set (used for all body rings)
    let outerPts, insetPts;
    if (addedPts > 0) {
        const refinedResult = generateEnclosurePointsFromAngles(
            refinedAngles, cx, cz,
            boxLeft, boxRight, boxBot, boxTop,
            edgeR, edgeType,
            params
        );
        outerPts = refinedResult.outerPts;
        insetPts = refinedResult.insetPts;
    } else {
        outerPts = mouthResult.outerPts;
        insetPts = mouthResult.insetPts;
    }

    const bodySize = refinedSize;
    const mouthOuterPts = mouthResult.outerPts;
    const mouthInsetPts = mouthResult.insetPts;

    // --- Helpers (needed before any geometry) ---
    const triangleArea2 = (a, b, c) => {
        const ax = vertices[a * 3], ay = vertices[a * 3 + 1], az = vertices[a * 3 + 2];
        const bx = vertices[b * 3], by = vertices[b * 3 + 1], bz = vertices[b * 3 + 2];
        const ccx = vertices[c * 3], cy = vertices[c * 3 + 1], cz = vertices[c * 3 + 2];
        const abx = bx - ax, aby = by - ay, abz = bz - az;
        const acx = ccx - ax, acy = cy - ay, acz = cz - az;
        const nx = aby * acz - abz * acy;
        const ny = abz * acx - abx * acz;
        const nz = abx * acy - aby * acx;
        return Math.hypot(nx, ny, nz);
    };
    const pushTri = (a, b, c) => {
        if (a === b || b === c || c === a) return;
        if (triangleArea2(a, b, c) <= 1e-10) return;
        indices.push(a, b, c);
    };
    const fullCircle = !quadrantInfo || quadrantInfo.fullCircle;
    const stitchRing = (r1Start, r2Start, size) => {
        const limit = fullCircle ? size : size - 1;
        for (let i = 0; i < limit; i++) {
            const i2 = (i + 1) % size;
            pushTri(r2Start + i, r1Start + i, r1Start + i2);
            pushTri(r2Start + i, r1Start + i2, r2Start + i2);
        }
    };

    // --- Step 4: Ring 0 — mouth-aligned inset ring for baffle stitch ---
    const mergeEps = 1e-6;
    let reuseMouthAsRing0 = true;
    for (let i = 0; i < ringSize; i++) {
        const ipt = mouthInsetPts[i];
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
        // When corner refinement adds extra points (addedPts > 0), ring0 uses
        // the refined bodySize inset set so all subsequent rings share the same
        // point count.  The fan-stitch between mouth (ringSize) and ring0
        // (bodySize) is placed on the coplanar front baffle where it is invisible.
        const ring0Size = addedPts > 0 ? bodySize : ringSize;
        const ring0Pts = addedPts > 0 ? insetPts : mouthInsetPts;
        for (let i = 0; i < ring0Size; i++) {
            const ipt = ring0Pts[i];
            vertices.push(
                ipt.x - (ipt.nx || 0) * seamNudge,
                mouthY,
                ipt.z - (ipt.nz || 0) * seamNudge
            );
        }
    }

    const enclosureStartTri = indices.length / 3;

    // Mouth to ring 0 stitch
    if (!reuseMouthAsRing0) {
        if (addedPts > 0) {
            // Fan-stitch from mouth (ringSize) to ring0 (bodySize) on the
            // coplanar front baffle plane where fan triangles are invisible.
            fanStitchRings(indices, pushTri, lastRowStart, ringSize, ring0Start, bodySize, mouthToRefinedMap, fullCircle);
        } else {
            const limit = fullCircle ? ringSize : ringSize - 1;
            for (let i = 0; i < limit; i++) {
                const i2 = (i + 1) % ringSize;
                pushTri(lastRowStart + i, lastRowStart + i2, ring0Start + i);
                pushTri(lastRowStart + i2, ring0Start + i2, ring0Start + i);
            }
        }
    }
    const flatFrontEndTri = indices.length / 3;

    // --- Step 5: Front roundover rings ---
    // When corner refinement is active (addedPts > 0), use the refined bodySize
    // point set so the front roundover has the same density as the sidewalls and
    // back roundover, eliminating the fan-stitch corner artifacts that occurred
    // when bridging between differently-sized rings on the sidewall.
    let prevRing = ring0Start;
    const frontRingSize = addedPts > 0 ? bodySize : ringSize;
    const frontInsetPts = addedPts > 0 ? insetPts : mouthInsetPts;
    const frontOuterPts = addedPts > 0 ? outerPts : mouthOuterPts;
    for (let j = 1; j <= edgeSlices; j++) {
        const ringIdx = vertices.length / 3;
        const t = j / edgeSlices;
        let axialT = t, radialT = t;
        if (edgeType === 1) {
            const angle = t * (Math.PI / 2);
            axialT = 1 - Math.cos(angle);
            radialT = Math.sin(angle);
        }
        const y = mouthY - (axialT * edgeDepth);
        for (let i = 0; i < frontRingSize; i++) {
            const ipt = frontInsetPts[i];
            const opt = frontOuterPts[i];
            vertices.push(
                ipt.x + (opt.x - ipt.x) * radialT,
                y,
                ipt.z + (opt.z - ipt.z) * radialT
            );
        }
        stitchRing(prevRing, ringIdx, frontRingSize);
        prevRing = ringIdx;
    }
    const frontRoundoverEndTri = indices.length / 3;
    const frontRoundoverEnd = prevRing;

    // --- Step 6: Sidewall (1:1 stitch, uniform ring size) ---
    // Both the front roundover end and back ring now use bodySize points,
    // so a simple 1:1 stitch replaces the previous fan-stitch.  The fan-stitch
    // (when needed) has been moved to the coplanar front baffle where it is
    // invisible, eliminating corner artifacts on the curved sidewall surface.
    const outerBackY = edgeDepth > 0 ? backY + edgeDepth : backY;
    const backRingStart = vertices.length / 3;
    for (let i = 0; i < bodySize; i++) {
        const opt = outerPts[i];
        vertices.push(opt.x, outerBackY, opt.z);
    }
    stitchRing(frontRoundoverEnd, backRingStart, bodySize);
    const sideWallEndTri = indices.length / 3;

    // --- Step 7: Back roundover rings (refined ring size) ---
    let currentRingStart = backRingStart;
    for (let j = 1; j <= edgeSlices; j++) {
        const t = j / edgeSlices;
        let axialT = t;
        let radialT = 1 - t;
        if (edgeType === 1) {
            const angle = t * (Math.PI / 2);
            axialT = Math.sin(angle);
            radialT = Math.cos(angle);
        }
        if (j === edgeSlices) {
            radialT = Math.max(radialT, 1e-3);
        }
        const y = backY + (1 - axialT) * edgeDepth;
        const ringStart = vertices.length / 3;
        for (let i = 0; i < bodySize; i++) {
            const ipt = insetPts[i];
            const opt = outerPts[i];
            vertices.push(
                ipt.x + (opt.x - ipt.x) * radialT,
                y,
                ipt.z + (opt.z - ipt.z) * radialT
            );
        }
        stitchRing(currentRingStart, ringStart, bodySize);
        currentRingStart = ringStart;
    }
    const backRoundoverEndTri = indices.length / 3;

    // --- Step 8: Back Cap ---
    let avgX = 0, avgZ = 0;
    const capBoundary = Array.from({ length: bodySize }, (_, i) => ({
        x: vertices[(currentRingStart + i) * 3],
        z: vertices[(currentRingStart + i) * 3 + 2]
    }));
    for (let i = 0; i < bodySize; i++) {
        avgX += capBoundary[i].x;
        avgZ += capBoundary[i].z;
    }
    avgX /= bodySize;
    avgZ /= bodySize;

    let capRingStart = currentRingStart;
    if (fullCircle) {
        const capSlices = 3;
        for (let s = 1; s < capSlices; s++) {
            const blend = s / capSlices;
            const ringStart = vertices.length / 3;
            for (let i = 0; i < bodySize; i++) {
                const cp = capBoundary[i];
                vertices.push(
                    cp.x + (avgX - cp.x) * blend,
                    backY,
                    cp.z + (avgZ - cp.z) * blend
                );
            }
            stitchRing(capRingStart, ringStart, bodySize);
            capRingStart = ringStart;
        }
    }

    const capStart = vertices.length / 3;
    vertices.push(avgX, backY, avgZ);
    const capLimit = fullCircle ? bodySize : bodySize - 1;
    for (let i = 0; i < capLimit; i++) {
        const i2 = (i + 1) % bodySize;
        pushTri(capRingStart + i, capRingStart + i2, capStart);
    }
    const backCapEndTri = indices.length / 3;

    const enclosureEndTri = indices.length / 3;
    if (groupInfo) {
        groupInfo.enclosure = { start: enclosureStartTri, end: enclosureEndTri };
        groupInfo.enc_front = { start: enclosureStartTri, end: flatFrontEndTri };
        groupInfo.enc_edge = [
            { start: flatFrontEndTri, end: frontRoundoverEndTri },
            { start: sideWallEndTri, end: backRoundoverEndTri }
        ];
        groupInfo.enc_side = { start: frontRoundoverEndTri, end: sideWallEndTri };
        groupInfo.enc_rear = { start: backRoundoverEndTri, end: backCapEndTri };
    }
}
