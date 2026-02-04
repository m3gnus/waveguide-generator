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
  // Key Y positions
  // ==========================================
  const frontInnerY = mouthY; // Front baffle inner edge (connects to mouth)
  const frontOuterY = mouthY + frontOffset; // Front baffle outer edge (interface offset)
  const backInnerY = mouthY - depth; // Back baffle inner edge
  const backOuterY = mouthY - depth; // Back baffle outer edge (no axial extension in ATH)

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

  // Create 4 rings of vertices:
  // 1. Front inner (connects to mouth)
  // 2. Front outer (rounded edge front)
  // 3. Back inner (back panel)
  // 4. Back outer (rounded edge back)

  const frontInnerStart = vertices.length / 3;
  for (let i = 0; i < totalPts; i++) {
    const pt = outline[i];
    vertices.push(pt.x, frontInnerY, pt.z);
  }

  const frontOuterStart = vertices.length / 3;
  for (let i = 0; i < totalPts; i++) {
    const pt = insetOutline[i];
    vertices.push(pt.x, frontOuterY, pt.z);
  }

  const backInnerStart = vertices.length / 3;
  for (let i = 0; i < totalPts; i++) {
    const pt = outline[i];
    vertices.push(pt.x, backInnerY, pt.z);
  }

  const backOuterStart = vertices.length / 3;
  for (let i = 0; i < totalPts; i++) {
    const pt = insetOutline[i];
    vertices.push(pt.x, backOuterY, pt.z);
  }

  // ==========================================
  // Create faces for the enclosure
  // ==========================================

  const enclosureStartTri = indices.length / 3;

  // 1. Front baffle face (between front inner and front outer)
  const frontStartTri = indices.length / 3;
  for (let i = 0; i < totalPts; i++) {
    const i2 = (i + 1) % totalPts;
    // Triangle 1
    indices.push(frontInnerStart + i, frontOuterStart + i, frontOuterStart + i2);
    // Triangle 2
    indices.push(frontInnerStart + i, frontOuterStart + i2, frontInnerStart + i2);
  }
  const frontEndTri = indices.length / 3;

  // 2. Back panel face (between back inner and back outer)
  for (let i = 0; i < totalPts; i++) {
    const i2 = (i + 1) % totalPts;
    // Triangle 1
    indices.push(backInnerStart + i, backOuterStart + i2, backOuterStart + i);
    // Triangle 2
    indices.push(backInnerStart + i, backInnerStart + i2, backOuterStart + i2);
  }

  // 3. Side walls (between front inner and back inner)
  for (let i = 0; i < totalPts; i++) {
    const i2 = (i + 1) % totalPts;
    indices.push(frontInnerStart + i, backInnerStart + i, backInnerStart + i2);
    indices.push(frontInnerStart + i, backInnerStart + i2, frontInnerStart + i2);
  }


  // ==========================================
  // Connect mouth to enclosure front inner ring
  // ==========================================

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

  // Connect mouth ring to front inner ring using proper stitching
  // When the rings have different point counts, we need careful triangulation
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

  // Back cap — fan from center to back outer ring
  const backCenterIdx = vertices.length / 3;
  vertices.push(cx, backOuterY, cz);

  for (let i = 0; i < totalPts; i++) {
    const i2 = (i + 1) % totalPts;
    // Winding for back cap facing outwards (away from front)
    indices.push(backOuterStart + i, backOuterStart + i2, backCenterIdx);
  }

  const enclosureEndTri = indices.length / 3;
  if (groupInfo) {
    groupInfo.enclosure = { start: enclosureStartTri, end: enclosureEndTri };
    groupInfo.enclosureFront = { start: frontStartTri, end: frontEndTri };
  }

  // NOTE: For symmetry meshes, ATH keeps the symmetry planes open.
}
