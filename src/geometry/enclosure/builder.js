import { buildPlanOutline } from './plan.js';

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

  // Ring 0: Front Inner (at the mouth y position, inset outline)
  // This ring connects the mouth to the enclosure
  const frontInnerStart = vertices.length / 3;
  for (let i = 0; i < totalPts; i++) {
    const ipt = insetOutline[i];
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
    vertices.push(ipt.x, bestY, ipt.z);
  }

  // Front Roundover Rings
  // Curving from (insetPt, bestY) to (outerPt, bestY - edgeR)
  const frontRoundsStarts = [];
  for (let s = 1; s <= axialSegs; s++) {
    const startIdx = vertices.length / 3;
    frontRoundsStarts.push(startIdx);

    // phi: 0 (at inset outline, y=bestY) to PI/2 (at outer outline, y=bestY - edgeR)
    const phi = (s / axialSegs) * (Math.PI / 2);
    const sinP = Math.sin(phi);
    const cosP = Math.cos(phi);

    for (let i = 0; i < totalPts; i++) {
      const ipt = insetOutline[i];
      const opt = outerOutline[i];
      const yBase = vertices[(frontInnerStart + i) * 3 + 1];

      let x, y, z;
      if (edgeType === 2) { // Chamfer
        const t = s / axialSegs;
        x = ipt.x + (opt.x - ipt.x) * t;
        z = ipt.z + (opt.z - ipt.z) * t;
        y = yBase - edgeR * t;
      } else { // Rounded (Convex)
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
        x = opt.x + ipt.nx * edgeR * sinP;
        z = opt.z + ipt.nz * edgeR * sinP;
        y = (backY + edgeR) - edgeR * (1 - cosP);
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
    indices.push(backOuterStart + i, backCenterIdx, backOuterStart + i2);
  }

  // ==========================================
  // Connect mouth to enclosure front inner ring
  // ==========================================

  // Connect mouth ring to front inner ring using proper stitching
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

  // Stitch the two rings with proper triangulation to avoid degenerate triangles
  for (let i = 0; i < connectLoop; i++) {
    const i2 = fullCircle ? (i + 1) % mouthLoop : i + 1;
    if (i2 >= mouthLoop) continue;

    const mi = mouthStart + i;
    const mi2 = mouthStart + i2;
    const ei = mouthToEnc[i];
    const ei2 = mouthToEnc[i2];

    // Create triangles only if the enclosure indices differ
    // This avoids degenerate triangles when multiple mouth vertices map to same enclosure vertex
    if (ei !== ei2) {
      // Standard quad triangulation
      indices.push(mi, mi2, frontInnerStart + ei2);
      indices.push(mi, frontInnerStart + ei2, frontInnerStart + ei);
    } else {
      // Both mouth vertices map to the same enclosure vertex - create single triangle
      indices.push(mi, mi2, frontInnerStart + ei);
    }
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

  // NOTE: For symmetry meshes, ATH keeps the symmetry planes open.
}
