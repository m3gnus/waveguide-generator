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
  const depthRaw = parseFloat(params.encDepth);
  const depth = Number.isFinite(depthRaw) ? depthRaw : 0;
  const edgeR = parseFloat(params.encEdge) || 0;
  const cornerSegs = Math.max(4, params.cornerSegments || 4);

  // Determine if we're in symmetry mode (quadrant 14 = right half)
  const isRightHalf = quadrantInfo && (params.quadrants === '14' || params.quadrants === 14);

  // Find bounding box at the MOUTH ring (last row)
  let maxX = -Infinity,
    minX = Infinity,
    maxZ = -Infinity,
    minZ = Infinity;
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
  const frontInnerY = mouthY; // Front baffle inner edge (connects to mouth)
  const frontOuterY = mouthY + edgeR; // Front baffle outer edge (extends forward)
  const backInnerY = mouthY - depth; // Back baffle inner edge
  const backOuterY = mouthY - depth - edgeR; // Back baffle outer edge (extends backward)

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

  // 1. Front baffle face (between front inner and front outer)
  for (let i = 0; i < totalPts; i++) {
    const i2 = (i + 1) % totalPts;
    // Triangle 1
    indices.push(frontInnerStart + i, frontOuterStart + i, frontOuterStart + i2);
    // Triangle 2
    indices.push(frontInnerStart + i, frontOuterStart + i2, frontInnerStart + i2);
  }

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

  // 4. Rounded edges (between front outer and back outer)
  for (let i = 0; i < totalPts; i++) {
    const i2 = (i + 1) % totalPts;
    indices.push(frontOuterStart + i, frontOuterStart + i2, backOuterStart + i2);
    indices.push(frontOuterStart + i, backOuterStart + i2, backOuterStart + i);
  }

  // ==========================================
  // Connect mouth to enclosure front inner ring
  // ==========================================

  // Get mouth ring vertices from horn (last row of horn mesh)
  const mouthStart = lastRowStart;
  const mouthRing = [];
  for (let i = 0; i <= radialSteps; i++) {
    const idx = mouthStart + i;
    mouthRing.push({
      x: vertices[idx * 3],
      y: vertices[idx * 3 + 1],
      z: vertices[idx * 3 + 2]
    });
  }

  // Connect mouth ring to front inner ring
  const mouthLoop = mouthRing.length;
  const sideLoopEnd = usePlanMap ? totalPts : mouthLoop;

  for (let i = 0; i < sideLoopEnd; i++) {
    const i2 = (i + 1) % sideLoopEnd;

    // Map angle to enclosure outline index
    let ei = i;
    let ei2 = i2;

    if (usePlanMap) {
      const mouthVertex = mouthRing[i % mouthLoop];
      const mouthAngle = Math.atan2(mouthVertex.z, mouthVertex.x);
      ei = findNearestOutlineIndex(mouthAngle);

      const mouthVertex2 = mouthRing[i2 % mouthLoop];
      const mouthAngle2 = Math.atan2(mouthVertex2.z, mouthVertex2.x);
      ei2 = findNearestOutlineIndex(mouthAngle2);
    }

    const mi = mouthStart + (i % mouthLoop);
    const mi2 = mouthStart + (i2 % mouthLoop);

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
