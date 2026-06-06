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
  const hasNormal = (idx) =>
    Math.hypot(normals[idx * 3], normals[idx * 3 + 1], normals[idx * 3 + 2]) > 1e-12;

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
  lengthSteps,
  thickness,
  offsetSign,
  rearDiscY
) {
  const outerStart = vertices.length / 3;
  const outerRows = [];

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
      const radialLen = Math.hypot(nx, nz);
      const rx = radialLen > 1e-12 ? nx / radialLen : 0;
      const rz = radialLen > 1e-12 ? nz / radialLen : 0;
      outerRows.push(x + offsetSign * thickness * rx, y, z + offsetSign * thickness * rz);
    } else {
      outerRows.push(
        x + offsetSign * thickness * nx,
        y + offsetSign * thickness * ny,
        z + offsetSign * thickness * nz
      );
    }
  }

  for (let col = 0; col < ringCount; col += 1) {
    const base = col * 3;
    const x0 = outerRows[base];
    const y0 = outerRows[base + 1];
    const z0 = outerRows[base + 2];

    if (lengthSteps <= 0) {
      vertices.push(x0, rearDiscY, z0);
      continue;
    }

    const nextBase = (ringCount + col) * 3;
    const x1 = outerRows[nextBase];
    const y1 = outerRows[nextBase + 1];
    const z1 = outerRows[nextBase + 2];
    const dy = y1 - y0;
    if (Math.abs(dy) <= 1e-9) {
      vertices.push(x0, rearDiscY, z0);
      continue;
    }

    const t = (rearDiscY - y0) / dy;
    vertices.push(x0 + (x1 - x0) * t, rearDiscY, z0 + (z1 - z0) * t);
  }

  vertices.push(...outerRows);

  return {
    outerStart,
    outerThroatStart: outerStart + ringCount,
    outerRowCount: lengthSteps + 2,
  };
}

function stitchOuterShell(vertices, indices, ringCount, outerRowCount, fullCircle, outerStart) {
  const radialSteps = getRadialSteps(ringCount, fullCircle);
  for (let row = 0; row < outerRowCount - 1; row += 1) {
    for (let col = 0; col < radialSteps; col += 1) {
      const col2 = fullCircle ? (col + 1) % ringCount : col + 1;

      const o11 = outerStart + row * ringCount + col;
      const o12 = outerStart + row * ringCount + col2;
      const o21 = outerStart + (row + 1) * ringCount + col;
      const o22 = outerStart + (row + 1) * ringCount + col2;

      pushTri(vertices, indices, o11, o22, o12);
      pushTri(vertices, indices, o11, o21, o22);
    }
  }
}

function stitchMouthRim(vertices, indices, ringCount, lengthSteps, fullCircle, outerThroatStart) {
  const radialSteps = getRadialSteps(ringCount, fullCircle);
  const mouthInnerStart = lengthSteps * ringCount;
  const mouthOuterStart = outerThroatStart + mouthInnerStart;

  for (let col = 0; col < radialSteps; col += 1) {
    const col2 = fullCircle ? (col + 1) % ringCount : col + 1;

    const in1 = mouthInnerStart + col;
    const in2 = mouthInnerStart + col2;
    const out1 = mouthOuterStart + col;
    const out2 = mouthOuterStart + col2;

    pushTri(vertices, indices, in1, in2, out2);
    pushTri(vertices, indices, in1, out2, out1);
  }
}

function addRearCap(vertices, indices, ringCount, fullCircle, rearRimStart) {
  const rearCapStartTri = indices.length / 3;
  let centerX = 0;
  let centerY = 0;
  let centerZ = 0;

  for (let col = 0; col < ringCount; col += 1) {
    centerX += vertices[(rearRimStart + col) * 3];
    centerY += vertices[(rearRimStart + col) * 3 + 1];
    centerZ += vertices[(rearRimStart + col) * 3 + 2];
  }

  centerX /= ringCount;
  centerY /= ringCount;
  centerZ /= ringCount;

  const centerIdx = vertices.length / 3;
  vertices.push(centerX, centerY, centerZ);

  const radialSteps = getRadialSteps(ringCount, fullCircle);
  for (let col = 0; col < radialSteps; col += 1) {
    const col2 = fullCircle ? (col + 1) % ringCount : col + 1;
    pushTri(vertices, indices, rearRimStart + col, rearRimStart + col2, centerIdx);
  }
  const rearCapEndTri = indices.length / 3;

  return {
    rearCapStartTri,
    rearCapEndTri,
  };
}

export function addFreestandingWallGeometry(
  vertices,
  indices,
  params,
  { ringCount, lengthSteps, fullCircle, groupInfo }
) {
  const thickness = Number(params.wallThickness || 0);
  if (!Number.isFinite(thickness) || thickness <= 0) return;

  const innerVertexCount = (lengthSteps + 1) * ringCount;
  if (!Number.isFinite(innerVertexCount) || innerVertexCount <= 0) return;

  const wallStartTri = indices.length / 3;
  const innerNormals = computeInnerVertexNormals(vertices, indices, innerVertexCount);
  fillMissingNormals(innerNormals, vertices, ringCount, lengthSteps);
  const offsetSign = resolveOffsetSign(vertices, innerNormals, innerVertexCount);
  const throatY = computeThroatPlateY(vertices, ringCount);
  const rearDiscY = throatY - thickness;
  const { outerStart, outerThroatStart, outerRowCount } = appendOuterOffsetShell(
    vertices,
    innerNormals,
    innerVertexCount,
    ringCount,
    lengthSteps,
    thickness,
    offsetSign,
    rearDiscY
  );

  const outerWallStartTri = indices.length / 3;
  stitchOuterShell(vertices, indices, ringCount, outerRowCount, fullCircle, outerStart);
  const outerWallEndTri = indices.length / 3;

  stitchMouthRim(vertices, indices, ringCount, lengthSteps, fullCircle, outerThroatStart);
  const mouthRimEndTri = indices.length / 3;

  const { rearCapStartTri, rearCapEndTri } = addRearCap(
    vertices,
    indices,
    ringCount,
    fullCircle,
    outerStart
  );

  const wallEndTri = indices.length / 3;
  if (groupInfo && wallEndTri > wallStartTri) {
    groupInfo.freestandingWall = { start: wallStartTri, end: wallEndTri };
    groupInfo.outer_wall = { start: outerWallStartTri, end: outerWallEndTri };
    groupInfo.mouth_rim = { start: outerWallEndTri, end: mouthRimEndTri };
    groupInfo.rear_cap = { start: rearCapStartTri, end: rearCapEndTri };
  }
}
