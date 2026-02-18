function triangleArea2(vertices, a, b, c) {
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
  return Math.hypot(nx, ny, nz);
}

function pushTri(vertices, indices, a, b, c) {
  if (a === b || b === c || c === a) return;
  if (triangleArea2(vertices, a, b, c) <= 1e-10) return;
  indices.push(a, b, c);
}

function normalize3(x, y, z, fallback = [0, -1, 0]) {
  const len = Math.hypot(x, y, z);
  if (len <= 1e-12) return fallback;
  return [x / len, y / len, z / len];
}

function computeInnerVertexNormals(vertices, indices, innerVertexCount) {
  const normals = new Float64Array(innerVertexCount * 3);
  const triCount = indices.length / 3;

  for (let t = 0; t < triCount; t += 1) {
    const off = t * 3;
    const a = indices[off];
    const b = indices[off + 1];
    const c = indices[off + 2];
    if (a >= innerVertexCount || b >= innerVertexCount || c >= innerVertexCount) continue;

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

    normals[a * 3] += nx;
    normals[a * 3 + 1] += ny;
    normals[a * 3 + 2] += nz;
    normals[b * 3] += nx;
    normals[b * 3 + 1] += ny;
    normals[b * 3 + 2] += nz;
    normals[c * 3] += nx;
    normals[c * 3 + 1] += ny;
    normals[c * 3 + 2] += nz;
  }

  return normals;
}

function fillMissingNormals(normals, vertices, ringCount, lengthSteps) {
  const innerVertexCount = (lengthSteps + 1) * ringCount;
  const hasNormal = (idx) => Math.hypot(
    normals[idx * 3],
    normals[idx * 3 + 1],
    normals[idx * 3 + 2]
  ) > 1e-12;

  for (let idx = 0; idx < innerVertexCount; idx += 1) {
    if (hasNormal(idx)) continue;

    const row = Math.floor(idx / ringCount);
    const col = idx % ringCount;
    const neighborIndices = [];

    if (col > 0) neighborIndices.push(idx - 1);
    if (col < ringCount - 1) neighborIndices.push(idx + 1);
    if (row > 0) neighborIndices.push(idx - ringCount);
    if (row < lengthSteps) neighborIndices.push(idx + ringCount);

    let sx = 0;
    let sy = 0;
    let sz = 0;
    for (const nidx of neighborIndices) {
      if (!hasNormal(nidx)) continue;
      sx += normals[nidx * 3];
      sy += normals[nidx * 3 + 1];
      sz += normals[nidx * 3 + 2];
    }

    if (Math.hypot(sx, sy, sz) <= 1e-12) {
      const x = vertices[idx * 3];
      const z = vertices[idx * 3 + 2];
      [sx, sy, sz] = normalize3(x, 0, z);
    }

    normals[idx * 3] = sx;
    normals[idx * 3 + 1] = sy;
    normals[idx * 3 + 2] = sz;
  }
}

function resolveOffsetSign(vertices, normals, innerVertexCount) {
  const sampleStep = Math.max(1, Math.floor(innerVertexCount / 64));
  let dotSum = 0;
  let samples = 0;

  for (let idx = 0; idx < innerVertexCount; idx += sampleStep) {
    const x = vertices[idx * 3];
    const z = vertices[idx * 3 + 2];
    const radialLen = Math.hypot(x, z);
    if (radialLen <= 1e-9) continue;

    const nx = normals[idx * 3];
    const nz = normals[idx * 3 + 2];
    const normalLen = Math.hypot(nx, normals[idx * 3 + 1], nz);
    if (normalLen <= 1e-12) continue;

    const rx = x / radialLen;
    const rz = z / radialLen;
    dotSum += (nx / normalLen) * rx + (nz / normalLen) * rz;
    samples += 1;
  }

  if (samples === 0) return -1;
  return dotSum < 0 ? -1 : 1;
}

function getRadialSteps(ringCount, fullCircle) {
  return fullCircle ? ringCount : Math.max(0, ringCount - 1);
}

function computeThroatPlateY(vertices, ringCount) {
  let sumY = 0;
  for (let i = 0; i < ringCount; i += 1) {
    sumY += vertices[i * 3 + 1];
  }
  return sumY / ringCount;
}

function appendOuterOffsetShell(
  vertices,
  innerNormals,
  innerVertexCount,
  ringCount,
  thickness,
  offsetSign
) {
  const outerStart = vertices.length / 3;

  for (let idx = 0; idx < innerVertexCount; idx += 1) {
    const row = Math.floor(idx / ringCount);
    const x = vertices[idx * 3];
    const y = vertices[idx * 3 + 1];
    const z = vertices[idx * 3 + 2];
    const [nx, ny, nz] = normalize3(
      innerNormals[idx * 3],
      innerNormals[idx * 3 + 1],
      innerNormals[idx * 3 + 2]
    );

    if (row === 0) {
      // Throat row: offset XZ radially by thickness (using the XZ component of the
      // surface normal, renormalized). Y stays at throatPlateY — the outer throat ring
      // sits at the same axial position as the inner throat ring. The axial step to
      // rearDiscY is built as a separate strip in stitchRearPlate.
      const radialLen = Math.hypot(nx, nz);
      const rx = radialLen > 1e-12 ? nx / radialLen : 0;
      const rz = radialLen > 1e-12 ? nz / radialLen : 0;
      vertices.push(x + offsetSign * thickness * rx, y, z + offsetSign * thickness * rz);
    } else {
      vertices.push(
        x + offsetSign * thickness * nx,
        y + offsetSign * thickness * ny,
        z + offsetSign * thickness * nz
      );
    }
  }

  return outerStart;
}

function stitchOuterShell(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart) {
  const radialSteps = getRadialSteps(ringCount, fullCircle);
  for (let j = 0; j < lengthSteps; j += 1) {
    for (let i = 0; i < radialSteps; i += 1) {
      const i2 = fullCircle ? (i + 1) % ringCount : i + 1;

      const o11 = outerStart + j * ringCount + i;
      const o12 = outerStart + j * ringCount + i2;
      const o21 = outerStart + (j + 1) * ringCount + i;
      const o22 = outerStart + (j + 1) * ringCount + i2;

      pushTri(vertices, indices, o11, o22, o12);
      pushTri(vertices, indices, o11, o21, o22);
    }
  }
}

function stitchMouthBand(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart) {
  const radialSteps = getRadialSteps(ringCount, fullCircle);
  const mouthInnerStart = lengthSteps * ringCount;
  const mouthOuterStart = outerStart + mouthInnerStart;

  for (let i = 0; i < radialSteps; i += 1) {
    const i2 = fullCircle ? (i + 1) % ringCount : i + 1;

    const in1 = mouthInnerStart + i;
    const in2 = mouthInnerStart + i2;
    const out1 = mouthOuterStart + i;
    const out2 = mouthOuterStart + i2;

    pushTri(vertices, indices, in1, out2, in2);
    pushTri(vertices, indices, in1, out1, out2);
  }
}

/**
 * Build the rear plate: axial strip (outer throat ring → disc rim) + tessellated disc.
 *
 * The outer throat row (outerStart + 0..ringCount-1) sits at throatPlateY with a
 * purely radial offset. The rear disc rim is a copy of that ring moved to rearDiscY.
 * The axial strip between them forms the side face of the rear plate.
 *
 * The disc is tessellated with concentric rings at spacing ≈ thickness, keeping the
 * same angular positions as the rim ring at each radius. Ring count halves each step
 * when the circumference would produce triangles with aspect ratio > 2, producing
 * roughly equilateral triangles throughout.
 */
function addRearPlate(vertices, indices, ringCount, fullCircle, outerStart, throatPlateY, rearDiscY, thickness) {
  // The disc hangs directly off the outer throat ring (outerStart + 0..ringCount-1),
  // which already sits at throatPlateY with the correct radial offset. No axial
  // side strip is needed — the outer shell's j=0 strip already connects outer row 0
  // to row 1, sealing the throat end of the outer shell. The disc simply fills the
  // area below the outer throat ring at rearDiscY.

  // Collect rim positions from outer throat row
  let centerX = 0;
  let centerZ = 0;
  let discRadius = 0;
  const rimAngles = [];

  for (let i = 0; i < ringCount; i += 1) {
    const x = vertices[(outerStart + i) * 3];
    const z = vertices[(outerStart + i) * 3 + 2];
    centerX += x;
    centerZ += z;
    discRadius += Math.hypot(x, z);
  }
  centerX /= ringCount;
  centerZ /= ringCount;
  discRadius /= ringCount;

  for (let i = 0; i < ringCount; i += 1) {
    const x = vertices[(outerStart + i) * 3];
    const z = vertices[(outerStart + i) * 3 + 2];
    rimAngles.push(Math.atan2(z - centerZ, x - centerX));
  }

  // The disc rim IS the outer throat ring, just at rearDiscY.
  // Create separate vertices at rearDiscY so the disc is planar.
  const discRimStart = vertices.length / 3;
  for (let i = 0; i < ringCount; i += 1) {
    vertices.push(vertices[(outerStart + i) * 3], rearDiscY, vertices[(outerStart + i) * 3 + 2]);
  }

  // Axial strip: outer throat ring (throatPlateY) → disc rim (rearDiscY)
  // This seals the gap between the outer shell throat edge and the disc rim.
  const radialSteps = getRadialSteps(ringCount, fullCircle);
  for (let i = 0; i < radialSteps; i += 1) {
    const i2 = fullCircle ? (i + 1) % ringCount : i + 1;
    pushTri(vertices, indices, outerStart + i,    discRimStart + i,  outerStart + i2);
    pushTri(vertices, indices, outerStart + i2,   discRimStart + i,  discRimStart + i2);
  }

  // --- Tessellated disc at rearDiscY ---
  // Walk inward with concentric rings spaced ~thickness apart.

  let prevRingIndices = Array.from({ length: ringCount }, (_, i) => discRimStart + i);
  let prevAngles = rimAngles.slice();
  let prevCount = ringCount;
  let prevR = discRadius;

  const ringStep = Math.max(thickness, discRadius / 32);

  for (let r = discRadius - ringStep; ; r -= ringStep) {
    const isLast = r <= ringStep * 0.5;
    const effectiveR = isLast ? 0 : r;

    if (effectiveR === 0) {
      // Collapse to center point
      const cIdx = vertices.length / 3;
      vertices.push(centerX, rearDiscY, centerZ);
      const steps = fullCircle ? prevCount : prevCount - 1;
      for (let i = 0; i < steps; i += 1) {
        const i2 = fullCircle ? (i + 1) % prevCount : i + 1;
        pushTri(vertices, indices, cIdx, prevRingIndices[i], prevRingIndices[i2]);
      }
      break;
    }

    // Halve angular resolution if the arc length per segment would be < 0.6 × ringStep
    const arcPerSeg = (2 * Math.PI * effectiveR) / prevCount;
    let newCount = prevCount;
    let newAngles = prevAngles;
    if (!fullCircle) {
      // For partial circles keep count, just reduce when very small
      newCount = Math.max(2, prevCount);
      newAngles = prevAngles;
    } else if (arcPerSeg < ringStep * 0.6 && prevCount >= 6) {
      newCount = Math.max(3, Math.floor(prevCount / 2));
      // Pick every other angle from prevAngles
      newAngles = [];
      for (let i = 0; i < newCount; i += 1) {
        newAngles.push(prevAngles[Math.round(i * prevCount / newCount) % prevCount]);
      }
    }

    // Build new ring vertices
    const newRingStart = vertices.length / 3;
    const newRingIndices = [];
    for (let i = 0; i < newCount; i += 1) {
      const a = newAngles[i];
      vertices.push(centerX + effectiveR * Math.cos(a), rearDiscY, centerZ + effectiveR * Math.sin(a));
      newRingIndices.push(newRingStart + i);
    }

    // Stitch prev ring → new ring
    // Both rings share the same angular positions (or new is a subset).
    if (newCount === prevCount) {
      // 1:1 — simple quad strip
      const steps = fullCircle ? prevCount : prevCount - 1;
      for (let i = 0; i < steps; i += 1) {
        const i2 = fullCircle ? (i + 1) % prevCount : i + 1;
        pushTri(vertices, indices, prevRingIndices[i], newRingIndices[i],  prevRingIndices[i2]);
        pushTri(vertices, indices, prevRingIndices[i2], newRingIndices[i], newRingIndices[i2 % newCount]);
      }
    } else {
      // 2:1 reduction — every two prev segments map to one new segment
      const ratio = prevCount / newCount;
      const steps = fullCircle ? newCount : newCount - 1;
      for (let ni = 0; ni < steps; ni += 1) {
        const ni2 = fullCircle ? (ni + 1) % newCount : ni + 1;
        const piBase = Math.round(ni * ratio);
        const piNext = Math.round((ni + 1) * ratio);
        // Fan from new edge back to prev vertices
        pushTri(vertices, indices, newRingIndices[ni], prevRingIndices[piBase % prevCount], newRingIndices[ni2 % newCount]);
        for (let pi = piBase; pi < piNext; pi += 1) {
          const p1 = prevRingIndices[pi % prevCount];
          const p2 = prevRingIndices[(pi + 1) % prevCount];
          pushTri(vertices, indices, newRingIndices[ni2 % newCount], p1, p2);
        }
      }
    }

    prevRingIndices = newRingIndices;
    prevAngles = newAngles;
    prevCount = newCount;
    prevR = effectiveR;
  }
}

export function addFreestandingWallGeometry(
  vertices,
  indices,
  params,
  {
    ringCount,
    lengthSteps,
    fullCircle,
    groupInfo
  }
) {
  const thickness = Number(params.wallThickness || 0);
  if (!Number.isFinite(thickness) || thickness <= 0) return;

  const innerVertexCount = (lengthSteps + 1) * ringCount;
  if (!Number.isFinite(innerVertexCount) || innerVertexCount <= 0) return;

  const wallStartTri = indices.length / 3;
  const innerNormals = computeInnerVertexNormals(vertices, indices, innerVertexCount);
  fillMissingNormals(innerNormals, vertices, ringCount, lengthSteps);
  const offsetSign = resolveOffsetSign(vertices, innerNormals, innerVertexCount);
  const throatPlateY = computeThroatPlateY(vertices, ringCount);
  const rearDiscY = throatPlateY - thickness;
  const outerStart = appendOuterOffsetShell(
    vertices,
    innerNormals,
    innerVertexCount,
    ringCount,
    thickness,
    offsetSign
  );

  stitchOuterShell(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart);
  stitchMouthBand(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart);
  addRearPlate(vertices, indices, ringCount, fullCircle, outerStart, throatPlateY, rearDiscY, thickness);

  const wallEndTri = indices.length / 3;
  if (groupInfo && wallEndTri > wallStartTri) {
    groupInfo.freestandingWall = { start: wallStartTri, end: wallEndTri };
  }
}
