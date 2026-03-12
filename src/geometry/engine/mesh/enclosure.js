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

function getRingSteps(ringSize, fullCircle) {
  return fullCircle ? ringSize : Math.max(0, ringSize - 1);
}

function clampEdgeRadius(edgeRadius, halfW, halfH) {
  if (!Number.isFinite(edgeRadius) || edgeRadius <= 0) return 0;
  const maxAllowed = Math.max(0, Math.min(halfW, halfH) - 1e-6);
  return Math.min(edgeRadius, maxAllowed);
}

function intersectRayWithShapedBox(
  angle,
  cx,
  cz,
  boxLeft,
  boxRight,
  boxBottom,
  boxTop,
  cornerRadius,
  edgeType
) {
  const cosA = Math.cos(angle);
  const sinA = Math.sin(angle);
  const EPS = 1e-12;

  let bestT = Infinity;
  let hitX = cx + cosA;
  let hitZ = cz + sinA;

  const trySegment = (x1, z1, x2, z2) => {
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
    }
  };

  const tryArc = (acx, acz, radius, startAngle, endAngle) => {
    const ox = cx - acx;
    const oz = cz - acz;
    const A = 1;
    const B = 2 * (ox * cosA + oz * sinA);
    const C = ox * ox + oz * oz - radius * radius;
    const disc = B * B - 4 * A * C;
    if (disc < 0) return;

    const sqrtDisc = Math.sqrt(disc);
    for (const t of [(-B - sqrtDisc) / (2 * A), (-B + sqrtDisc) / (2 * A)]) {
      if (t <= EPS || t >= bestT) continue;

      const px = cx + cosA * t;
      const pz = cz + sinA * t;
      let pa = Math.atan2(pz - acz, px - acx);
      let rel = pa - startAngle;
      const sweep = endAngle - startAngle;
      while (rel < -EPS) rel += Math.PI * 2;
      while (rel > Math.PI * 2 + EPS) rel -= Math.PI * 2;
      if (rel <= sweep + EPS) {
        bestT = t;
        hitX = px;
        hitZ = pz;
      }
    }
  };

  const tryChamfer = (acx, acz, radius, startAngle, endAngle) => {
    const x1 = acx + radius * Math.cos(startAngle);
    const z1 = acz + radius * Math.sin(startAngle);
    const x2 = acx + radius * Math.cos(endAngle);
    const z2 = acz + radius * Math.sin(endAngle);
    trySegment(x1, z1, x2, z2);
  };

  const halfW = (boxRight - boxLeft) / 2;
  const halfH = (boxTop - boxBottom) / 2;
  const boxCx = (boxRight + boxLeft) / 2;
  const boxCz = (boxTop + boxBottom) / 2;
  const r = clampEdgeRadius(cornerRadius, halfW, halfH);
  const useCorners = r > 0;

  const innerRight = boxRight;
  const innerLeft = boxLeft;
  const innerTop = boxTop;
  const innerBottom = boxBottom;

  trySegment(innerRight, boxCz - halfH + (useCorners ? r : 0), innerRight, boxCz + halfH - (useCorners ? r : 0));
  trySegment(boxCx + halfW - (useCorners ? r : 0), innerTop, boxCx - halfW + (useCorners ? r : 0), innerTop);
  trySegment(innerLeft, boxCz + halfH - (useCorners ? r : 0), innerLeft, boxCz - halfH + (useCorners ? r : 0));
  trySegment(boxCx - halfW + (useCorners ? r : 0), innerBottom, boxCx + halfW - (useCorners ? r : 0), innerBottom);

  if (useCorners) {
    const corners = [
      { cx: boxCx + halfW - r, cz: boxCz - halfH + r, start: -Math.PI / 2, end: 0 },
      { cx: boxCx + halfW - r, cz: boxCz + halfH - r, start: 0, end: Math.PI / 2 },
      { cx: boxCx - halfW + r, cz: boxCz + halfH - r, start: Math.PI / 2, end: Math.PI },
      { cx: boxCx - halfW + r, cz: boxCz - halfH + r, start: Math.PI, end: Math.PI * 1.5 }
    ];
    for (const corner of corners) {
      if (edgeType === 2) {
        tryChamfer(corner.cx, corner.cz, r, corner.start, corner.end);
      } else {
        tryArc(corner.cx, corner.cz, r, corner.start, corner.end);
      }
    }
  }

  return { x: hitX, z: hitZ };
}

function buildOuterContour(
  mouthAngles,
  centerX,
  centerZ,
  boxLeft,
  boxRight,
  boxBottom,
  boxTop,
  edgeRadius,
  edgeType
) {
  return mouthAngles.map((angle) => intersectRayWithShapedBox(
    angle,
    centerX,
    centerZ,
    boxLeft,
    boxRight,
    boxBottom,
    boxTop,
    edgeRadius,
    edgeType
  ));
}

function stitchRings(vertices, indices, lowerStart, upperStart, ringSize, fullCircle) {
  const ringSteps = getRingSteps(ringSize, fullCircle);
  for (let i = 0; i < ringSteps; i += 1) {
    const i2 = fullCircle ? (i + 1) % ringSize : i + 1;
    pushTri(vertices, indices, lowerStart + i, lowerStart + i2, upperStart + i);
    pushTri(vertices, indices, lowerStart + i2, upperStart + i2, upperStart + i);
  }
}

function closeRearCap(vertices, indices, ringStart, ringSize, fullCircle, backY) {
  let centerX = 0;
  let centerZ = 0;
  for (let i = 0; i < ringSize; i += 1) {
    centerX += vertices[(ringStart + i) * 3];
    centerZ += vertices[(ringStart + i) * 3 + 2];
  }
  centerX /= ringSize;
  centerZ /= ringSize;

  const centerIdx = vertices.length / 3;
  vertices.push(centerX, backY, centerZ);

  const ringSteps = getRingSteps(ringSize, fullCircle);
  for (let i = 0; i < ringSteps; i += 1) {
    const i2 = fullCircle ? (i + 1) % ringSize : i + 1;
    pushTri(vertices, indices, ringStart + i, ringStart + i2, centerIdx);
  }
}

export function addEnclosureGeometry(
  vertices,
  indices,
  params,
  verticalOffset = 0,
  quadrantInfo = null,
  groupInfo = null,
  ringCount = null,
  angleList = null
) {
  const ringSize = Number.isFinite(ringCount) && ringCount > 2
    ? ringCount
    : Math.max(3, Math.round(params.angularSegments || 0));

  const depth = Number(params.encDepth || 0);
  if (!Number.isFinite(depth) || depth <= 0) return;

  const fullCircle = !quadrantInfo || quadrantInfo.fullCircle;
  const lastRowStart = params.lengthSegments * ringSize;
  const mouthY = vertices[lastRowStart * 3 + 1];
  const backY = mouthY - depth;

  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  let centerX = 0;
  let centerZ = 0;
  for (let i = 0; i < ringSize; i += 1) {
    const idx = lastRowStart + i;
    const x = vertices[idx * 3];
    const z = vertices[idx * 3 + 2];
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minZ = Math.min(minZ, z);
    maxZ = Math.max(maxZ, z);
    centerX += x;
    centerZ += z;
  }
  centerX /= ringSize;
  centerZ /= ringSize;

  const sL = Number(params.encSpaceL ?? 25);
  const sT = Number(params.encSpaceT ?? 25);
  const sR = Number(params.encSpaceR ?? 25);
  const sB = Number(params.encSpaceB ?? 25);

  const boxLeft = minX - (Number.isFinite(sL) ? sL : 25);
  const boxRight = maxX + (Number.isFinite(sR) ? sR : 25);
  const boxTop = maxZ + (Number.isFinite(sT) ? sT : 25);
  const boxBottom = minZ - (Number.isFinite(sB) ? sB : 25);

  const edgeType = Number(params.encEdgeType) === 2 ? 2 : 1;
  const edgeRadiusRaw = Math.max(0, Number(params.encEdge || 0));
  const halfW = (boxRight - boxLeft) / 2;
  const halfH = (boxTop - boxBottom) / 2;
  const edgeRadius = clampEdgeRadius(edgeRadiusRaw, halfW, halfH);

  const mouthAngles = Array.isArray(angleList) && angleList.length === ringSize
    ? angleList
    : Array.from({ length: ringSize }, (_, i) => Math.atan2(
      vertices[(lastRowStart + i) * 3 + 2] - centerZ,
      vertices[(lastRowStart + i) * 3] - centerX
    ));

  const contour = buildOuterContour(
    mouthAngles,
    centerX,
    centerZ,
    boxLeft,
    boxRight,
    boxBottom,
    boxTop,
    edgeRadius,
    edgeType
  );

  const enclosureStartTri = indices.length / 3;

  const frontRingStart = vertices.length / 3;
  for (let i = 0; i < ringSize; i += 1) {
    vertices.push(contour[i].x, mouthY, contour[i].z);
  }

  stitchRings(vertices, indices, lastRowStart, frontRingStart, ringSize, fullCircle);
  const frontEndTri = indices.length / 3;

  const backRingStart = vertices.length / 3;
  for (let i = 0; i < ringSize; i += 1) {
    vertices.push(contour[i].x, backY, contour[i].z);
  }

  stitchRings(vertices, indices, frontRingStart, backRingStart, ringSize, fullCircle);
  const sideEndTri = indices.length / 3;

  closeRearCap(vertices, indices, backRingStart, ringSize, fullCircle, backY);
  const enclosureEndTri = indices.length / 3;

  if (groupInfo) {
    groupInfo.enclosure = { start: enclosureStartTri, end: enclosureEndTri };
    groupInfo.enc_front = { start: enclosureStartTri, end: frontEndTri };
    groupInfo.enc_side = { start: frontEndTri, end: sideEndTri };
    groupInfo.enc_rear = { start: sideEndTri, end: enclosureEndTri };
    if (edgeRadius > 0) {
      groupInfo.enc_edge = { start: frontEndTri, end: sideEndTri };
    }
  }
}
