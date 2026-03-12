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

function getRadialSteps(ringCount, fullCircle) {
  return fullCircle ? ringCount : Math.max(0, ringCount - 1);
}

function indexOf(row, col, ringCount) {
  return row * ringCount + col;
}

function getRadiusAt(vertices, idx) {
  const x = vertices[idx * 3];
  const z = vertices[idx * 3 + 2];
  return Math.hypot(x, z);
}

function getRadialDirection(vertices, idx, col, ringCount) {
  const x = vertices[idx * 3];
  const z = vertices[idx * 3 + 2];
  const radialLen = Math.hypot(x, z);
  if (radialLen > 1e-9) {
    return [x / radialLen, z / radialLen];
  }

  const phi = (col / ringCount) * Math.PI * 2;
  return [Math.cos(phi), Math.sin(phi)];
}

function getSectionNormal(vertices, row, col, ringCount, lengthSteps) {
  if (row === 0) return [0, 1];

  const prevRow = Math.max(0, row - 1);
  const nextRow = Math.min(lengthSteps, row + 1);
  if (prevRow === nextRow) return [0, 1];

  const prevIdx = indexOf(prevRow, col, ringCount);
  const nextIdx = indexOf(nextRow, col, ringCount);
  const dy = vertices[nextIdx * 3 + 1] - vertices[prevIdx * 3 + 1];
  const dr = getRadiusAt(vertices, nextIdx) - getRadiusAt(vertices, prevIdx);

  // 2D section normal in (axial y, radial r): tangent is (dy, dr), normal is (-dr, dy).
  let nY = -dr;
  let nR = dy;
  const len = Math.hypot(nY, nR);
  if (len <= 1e-12) return [0, 1];

  nY /= len;
  nR /= len;
  if (nR < 0) {
    nY *= -1;
    nR *= -1;
  }

  return [nY, nR];
}

function appendOuterSectionOffsetShell(vertices, ringCount, lengthSteps, thickness) {
  const outerStart = vertices.length / 3;

  for (let row = 0; row <= lengthSteps; row += 1) {
    for (let col = 0; col < ringCount; col += 1) {
      const idx = indexOf(row, col, ringCount);
      const x = vertices[idx * 3];
      const y = vertices[idx * 3 + 1];
      const z = vertices[idx * 3 + 2];
      const [rx, rz] = getRadialDirection(vertices, idx, col, ringCount);

      let nY = 0;
      let nR = 1;
      if (row > 0) {
        [nY, nR] = getSectionNormal(vertices, row, col, ringCount, lengthSteps);
      }

      vertices.push(
        x + thickness * nR * rx,
        y + thickness * nY,
        z + thickness * nR * rz
      );
    }
  }

  return outerStart;
}

function stitchOuterShell(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart) {
  const radialSteps = getRadialSteps(ringCount, fullCircle);
  for (let row = 0; row < lengthSteps; row += 1) {
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

function stitchMouthRim(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart) {
  const radialSteps = getRadialSteps(ringCount, fullCircle);
  const mouthInnerStart = lengthSteps * ringCount;
  const mouthOuterStart = outerStart + mouthInnerStart;

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

function addThroatReturnAndRearCap(
  vertices,
  indices,
  ringCount,
  fullCircle,
  outerStart,
  rearDiscY
) {
  const throatReturnStartTri = indices.length / 3;
  const discRimStart = vertices.length / 3;

  for (let col = 0; col < ringCount; col += 1) {
    const outerIdx = outerStart + col;
    vertices.push(
      vertices[outerIdx * 3],
      rearDiscY,
      vertices[outerIdx * 3 + 2]
    );
  }

  const radialSteps = getRadialSteps(ringCount, fullCircle);
  for (let col = 0; col < radialSteps; col += 1) {
    const col2 = fullCircle ? (col + 1) % ringCount : col + 1;
    pushTri(vertices, indices, outerStart + col, outerStart + col2, discRimStart + col);
    pushTri(vertices, indices, outerStart + col2, discRimStart + col2, discRimStart + col);
  }
  const throatReturnEndTri = indices.length / 3;

  let centerX = 0;
  let centerZ = 0;
  for (let col = 0; col < ringCount; col += 1) {
    centerX += vertices[(discRimStart + col) * 3];
    centerZ += vertices[(discRimStart + col) * 3 + 2];
  }
  centerX /= ringCount;
  centerZ /= ringCount;

  const centerIdx = vertices.length / 3;
  vertices.push(centerX, rearDiscY, centerZ);

  for (let col = 0; col < radialSteps; col += 1) {
    const col2 = fullCircle ? (col + 1) % ringCount : col + 1;
    pushTri(vertices, indices, discRimStart + col, discRimStart + col2, centerIdx);
  }
  const rearCapEndTri = indices.length / 3;

  return {
    throatReturnStartTri,
    throatReturnEndTri,
    rearCapEndTri
  };
}

function computeThroatY(vertices, ringCount) {
  let sum = 0;
  for (let col = 0; col < ringCount; col += 1) {
    sum += vertices[col * 3 + 1];
  }
  return sum / ringCount;
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
  const outerStart = appendOuterSectionOffsetShell(vertices, ringCount, lengthSteps, thickness);
  const throatY = computeThroatY(vertices, ringCount);
  const rearDiscY = throatY - thickness;

  const outerWallStartTri = indices.length / 3;
  stitchOuterShell(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart);
  const outerWallEndTri = indices.length / 3;

  stitchMouthRim(vertices, indices, ringCount, lengthSteps, fullCircle, outerStart);
  const mouthRimEndTri = indices.length / 3;

  const {
    throatReturnStartTri,
    throatReturnEndTri,
    rearCapEndTri
  } = addThroatReturnAndRearCap(
    vertices,
    indices,
    ringCount,
    fullCircle,
    outerStart,
    rearDiscY
  );

  const wallEndTri = indices.length / 3;
  if (groupInfo && wallEndTri > wallStartTri) {
    groupInfo.freestandingWall = { start: wallStartTri, end: wallEndTri };
    groupInfo.outer_wall = { start: outerWallStartTri, end: outerWallEndTri };
    groupInfo.mouth_rim = { start: outerWallEndTri, end: mouthRimEndTri };
    groupInfo.throat_return = { start: throatReturnStartTri, end: throatReturnEndTri };
    groupInfo.rear_cap = { start: throatReturnEndTri, end: rearCapEndTri };
  }
}
