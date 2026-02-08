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

function computeRadialDirection(x, z, fallbackAngle) {
  const len = Math.hypot(x, z);
  if (len > 1e-9) {
    return { x: x / len, z: z / len };
  }

  return {
    x: Math.cos(fallbackAngle),
    z: Math.sin(fallbackAngle)
  };
}

export function addFreestandingWallGeometry(
  vertices,
  indices,
  params,
  {
    ringCount,
    lengthSteps,
    fullCircle,
    angleList,
    groupInfo
  }
) {
  const thickness = Number(params.wallThickness || 0);
  if (!Number.isFinite(thickness) || thickness <= 0) return;

  const innerVertexCount = (lengthSteps + 1) * ringCount;
  if (!Number.isFinite(innerVertexCount) || innerVertexCount <= 0) return;

  const wallStartTri = indices.length / 3;
  const outerStart = vertices.length / 3;

  for (let idx = 0; idx < innerVertexCount; idx += 1) {
    const x = vertices[idx * 3];
    const y = vertices[idx * 3 + 1];
    const z = vertices[idx * 3 + 2];
    const ringIdx = idx % ringCount;
    const fallbackAngle = Array.isArray(angleList) && Number.isFinite(angleList[ringIdx])
      ? angleList[ringIdx]
      : (ringIdx / Math.max(1, ringCount)) * Math.PI * 2;

    const dir = computeRadialDirection(x, z, fallbackAngle);
    vertices.push(x + thickness * dir.x, y, z + thickness * dir.z);
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

  const rearRingStart = vertices.length / 3;
  for (let i = 0; i < ringCount; i += 1) {
    const x = vertices[(throatOuterStart + i) * 3];
    const z = vertices[(throatOuterStart + i) * 3 + 2];
    vertices.push(x, rearY, z);
  }

  for (let i = 0; i < radialSteps; i += 1) {
    const i2 = fullCircle ? (i + 1) % ringCount : i + 1;

    const o1 = throatOuterStart + i;
    const o2 = throatOuterStart + i2;
    const r1 = rearRingStart + i;
    const r2 = rearRingStart + i2;

    pushTri(vertices, indices, o1, o2, r2);
    pushTri(vertices, indices, o1, r2, r1);
  }

  let centerX = 0;
  let centerZ = 0;
  for (let i = 0; i < ringCount; i += 1) {
    centerX += vertices[(rearRingStart + i) * 3];
    centerZ += vertices[(rearRingStart + i) * 3 + 2];
  }
  centerX /= ringCount;
  centerZ /= ringCount;

  const rearCenterIdx = vertices.length / 3;
  vertices.push(centerX, rearY, centerZ);

  for (let i = 0; i < radialSteps; i += 1) {
    const i2 = fullCircle ? (i + 1) % ringCount : i + 1;
    const r1 = rearRingStart + i;
    const r2 = rearRingStart + i2;
    pushTri(vertices, indices, rearCenterIdx, r2, r1);
  }

  const wallEndTri = indices.length / 3;
  if (groupInfo && wallEndTri > wallStartTri) {
    groupInfo.freestandingWall = { start: wallStartTri, end: wallEndTri };
  }
}
