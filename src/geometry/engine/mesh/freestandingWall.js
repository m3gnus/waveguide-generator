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
  const outerStart = vertices.length / 3;
  const innerNormals = computeInnerVertexNormals(vertices, indices, innerVertexCount);
  fillMissingNormals(innerNormals, vertices, ringCount, lengthSteps);
  const offsetSign = resolveOffsetSign(vertices, innerNormals, innerVertexCount);

  for (let idx = 0; idx < innerVertexCount; idx += 1) {
    const x = vertices[idx * 3];
    const y = vertices[idx * 3 + 1];
    const z = vertices[idx * 3 + 2];
    const [nx, ny, nz] = normalize3(
      innerNormals[idx * 3],
      innerNormals[idx * 3 + 1],
      innerNormals[idx * 3 + 2]
    );

    // Build the wall from a proper surface offset so the generated backside
    // stays one wall-thickness away from the original horn surface.
    vertices.push(
      x + offsetSign * thickness * nx,
      y + offsetSign * thickness * ny,
      z + offsetSign * thickness * nz
    );
  }

  const radialSteps = fullCircle ? ringCount : Math.max(0, ringCount - 1);

  for (let j = 0; j < lengthSteps; j += 1) {
    for (let i = 0; i < radialSteps; i += 1) {
      const i2 = fullCircle ? (i + 1) % ringCount : i + 1;

      const o11 = outerStart + j * ringCount + i;
      const o12 = outerStart + j * ringCount + i2;
      const o21 = outerStart + (j + 1) * ringCount + i;
      const o22 = outerStart + (j + 1) * ringCount + i2;

      // Outer shell winding opposite of inner shell.
      pushTri(vertices, indices, o11, o22, o12);
      pushTri(vertices, indices, o11, o21, o22);
    }
  }

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

  const throatOuterStart = outerStart;
  const throatY = vertices[1];
  const rearY = throatY - thickness;

  let centerX = 0;
  let centerZ = 0;
  for (let i = 0; i < ringCount; i += 1) {
    centerX += vertices[(throatOuterStart + i) * 3];
    centerZ += vertices[(throatOuterStart + i) * 3 + 2];
  }
  centerX /= ringCount;
  centerZ /= ringCount;

  const rearCenterIdx = vertices.length / 3;
  vertices.push(centerX, rearY, centerZ);

  for (let i = 0; i < radialSteps; i += 1) {
    const i2 = fullCircle ? (i + 1) % ringCount : i + 1;
    const r1 = throatOuterStart + i;
    const r2 = throatOuterStart + i2;
    pushTri(vertices, indices, rearCenterIdx, r2, r1);
  }

  const wallEndTri = indices.length / 3;
  if (groupInfo && wallEndTri > wallStartTri) {
    groupInfo.freestandingWall = { start: wallStartTri, end: wallEndTri };
  }
}
