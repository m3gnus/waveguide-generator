/**
 * Display tessellator for hornlab-waveguide-mesher viewport geometry.
 *
 * The backend (`POST /api/mesh/viewport`) returns the mesher's canonical horn
 * point grid (plus an outer wall grid for freestanding walls) and enclosure
 * profile rings. This module turns those into render triangles, replacing the
 * in-browser profile math in src/geometry/engine/ for the 3D viewport.
 *
 * Conventions shared with the JS engine (see tests/geometry-parity.test.js):
 * - mesher grid point (i=angle, j=slice) lives at flat index i*(nLength+1)+j,
 *   with coordinates (x, y, z) = (transverse-x, transverse-y, axial-z) in mm.
 * - viewport vertex (j*nPhi + i) uses (x, axial, transverse-y), i.e. the
 *   mesher's y/z axes swapped, matching engine/mesh/horn.js vertex order.
 * - display groups: horn (+ inner_wall/horn_wall alias), freestandingWall,
 *   enclosure, throat_disc (+ source alias).
 */

import { evalParam, toRad } from './common.js';

function toFinite(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function assertGridShape(grid) {
  if (!grid || typeof grid !== 'object') {
    throw new Error('Viewport geometry payload is missing the point grid.');
  }
  const nPhi = Math.round(Number(grid.grid_n_phi));
  const nLength = Math.round(Number(grid.grid_n_length));
  if (!Number.isInteger(nPhi) || nPhi < 3 || !Number.isInteger(nLength) || nLength < 1) {
    throw new Error(
      `Viewport grid has invalid dimensions (${grid.grid_n_phi}x${grid.grid_n_length}).`
    );
  }
  const expected = nPhi * (nLength + 1) * 3;
  if (!Array.isArray(grid.inner_points) || grid.inner_points.length !== expected) {
    throw new Error(
      `Viewport grid inner_points has ${grid.inner_points?.length ?? 0} values; expected ${expected}.`
    );
  }
  if (grid.outer_points != null && grid.outer_points.length !== expected) {
    throw new Error(
      `Viewport grid outer_points has ${grid.outer_points.length} values; expected ${expected}.`
    );
  }
  return { nPhi, nLength };
}

/** Append a mesher point grid as viewport vertices in row-major (j, i) order. */
function appendGridVertices(vertices, points, nPhi, nLength) {
  const start = vertices.length / 3;
  for (let j = 0; j <= nLength; j += 1) {
    for (let i = 0; i < nPhi; i += 1) {
      const base = (i * (nLength + 1) + j) * 3;
      vertices.push(points[base], points[base + 2], points[base + 1]);
    }
  }
  return start;
}

function triangleArea2(vertices, a, b, c) {
  const ax = vertices[a * 3];
  const ay = vertices[a * 3 + 1];
  const az = vertices[a * 3 + 2];
  const abx = vertices[b * 3] - ax;
  const aby = vertices[b * 3 + 1] - ay;
  const abz = vertices[b * 3 + 2] - az;
  const acx = vertices[c * 3] - ax;
  const acy = vertices[c * 3 + 1] - ay;
  const acz = vertices[c * 3 + 2] - az;
  const nx = aby * acz - abz * acy;
  const ny = abz * acx - abx * acz;
  const nz = abx * acy - aby * acx;
  return Math.hypot(nx, ny, nz);
}

function makeTriPusher(vertices, indices) {
  return (a, b, c) => {
    if (a === b || b === c || c === a) return;
    if (triangleArea2(vertices, a, b, c) <= 1e-10) return;
    indices.push(a, b, c);
  };
}

function addHornSurface(indices, nPhi, nLength, fullCircle) {
  const radialSteps = fullCircle ? nPhi : Math.max(0, nPhi - 1);
  for (let j = 0; j < nLength; j += 1) {
    for (let i = 0; i < radialSteps; i += 1) {
      const row1 = j * nPhi;
      const row2 = (j + 1) * nPhi;
      const i2 = fullCircle ? (i + 1) % nPhi : i + 1;
      indices.push(row1 + i, row1 + i2, row2 + i2);
      indices.push(row1 + i, row2 + i2, row2 + i);
    }
  }
}

/**
 * Freestanding wall from the mesher outer grid. Outer row 0 is the rear rim
 * ring (already shifted back by the wall thickness by the mesher).
 */
function addFreestandingWall(vertices, indices, outerPoints, nPhi, nLength, fullCircle, groups) {
  const pushTri = makeTriPusher(vertices, indices);
  const wallStartTri = indices.length / 3;
  const outerStart = appendGridVertices(vertices, outerPoints, nPhi, nLength);
  const radialSteps = fullCircle ? nPhi : Math.max(0, nPhi - 1);

  const outerWallStartTri = indices.length / 3;
  for (let row = 0; row < nLength; row += 1) {
    for (let col = 0; col < radialSteps; col += 1) {
      const col2 = fullCircle ? (col + 1) % nPhi : col + 1;
      const o11 = outerStart + row * nPhi + col;
      const o12 = outerStart + row * nPhi + col2;
      const o21 = outerStart + (row + 1) * nPhi + col;
      const o22 = outerStart + (row + 1) * nPhi + col2;
      pushTri(o11, o22, o12);
      pushTri(o11, o21, o22);
    }
  }
  const outerWallEndTri = indices.length / 3;

  const mouthInnerStart = nLength * nPhi;
  const mouthOuterStart = outerStart + nLength * nPhi;
  for (let col = 0; col < radialSteps; col += 1) {
    const col2 = fullCircle ? (col + 1) % nPhi : col + 1;
    pushTri(mouthInnerStart + col, mouthInnerStart + col2, mouthOuterStart + col2);
    pushTri(mouthInnerStart + col, mouthOuterStart + col2, mouthOuterStart + col);
  }
  const mouthRimEndTri = indices.length / 3;

  const rearCapStartTri = indices.length / 3;
  let centerX = 0;
  let centerY = 0;
  let centerZ = 0;
  for (let col = 0; col < nPhi; col += 1) {
    centerX += vertices[(outerStart + col) * 3];
    centerY += vertices[(outerStart + col) * 3 + 1];
    centerZ += vertices[(outerStart + col) * 3 + 2];
  }
  const centerIdx = vertices.length / 3;
  vertices.push(centerX / nPhi, centerY / nPhi, centerZ / nPhi);
  for (let col = 0; col < radialSteps; col += 1) {
    const col2 = fullCircle ? (col + 1) % nPhi : col + 1;
    pushTri(outerStart + col, outerStart + col2, centerIdx);
  }
  const wallEndTri = indices.length / 3;

  if (wallEndTri > wallStartTri) {
    groups.freestandingWall = { start: wallStartTri, end: wallEndTri };
    groups.outer_wall = { start: outerWallStartTri, end: outerWallEndTri };
    groups.mouth_rim = { start: outerWallEndTri, end: mouthRimEndTri };
    groups.rear_cap = { start: rearCapStartTri, end: wallEndTri };
  }
}

/**
 * Unwrapped CCW angle table for a closed ring around (cx, cz) in the viewport
 * x/z plane. Entry k is the cumulative angle of vertex (startOffset + k) mod n,
 * with entry n landing exactly one turn above entry 0.
 */
function ringAngleTable(vertices, ringStart, count, cx, cz, baseAngle) {
  const angles = new Float64Array(count + 1);
  const TAU = Math.PI * 2;
  let startIndex = 0;

  if (Number.isFinite(baseAngle)) {
    // Start the ring at the vertex angularly closest to baseAngle so the two
    // rings of a zipper stitch begin near the same azimuth.
    let bestDelta = Infinity;
    for (let k = 0; k < count; k += 1) {
      const idx = ringStart + k;
      const theta = Math.atan2(vertices[idx * 3 + 2] - cz, vertices[idx * 3] - cx);
      let delta = Math.abs(theta - baseAngle) % TAU;
      if (delta > Math.PI) delta = TAU - delta;
      if (delta < bestDelta) {
        bestDelta = delta;
        startIndex = k;
      }
    }
  }

  const firstIdx = ringStart + startIndex;
  let prev = Math.atan2(vertices[firstIdx * 3 + 2] - cz, vertices[firstIdx * 3] - cx);
  if (Number.isFinite(baseAngle)) {
    // Re-base near baseAngle so both tables share one angular frame.
    prev = baseAngle + ((((prev - baseAngle + Math.PI) % TAU) + TAU) % TAU) - Math.PI;
  }
  angles[0] = prev;
  for (let k = 1; k <= count; k += 1) {
    const idx = ringStart + ((startIndex + k) % count);
    const theta = Math.atan2(vertices[idx * 3 + 2] - cz, vertices[idx * 3] - cx);
    let step = (theta - prev) % TAU;
    if (step <= 0) step += TAU;
    // Tolerate tiny backtracking from sampling noise without unwrapping a turn.
    if (step > TAU - 1e-9) step = 0;
    angles[k] = angles[k - 1] + step;
    prev = angles[k];
  }
  return { angles, startIndex };
}

/**
 * Stitch two closed CCW rings into an annulus by merging on azimuth around a
 * shared interior center. Handles rings with different vertex counts. Ring A
 * must be the ring nearer the horn mouth (or the inner ring on a coplanar
 * baffle) so the emitted winding faces outward.
 */
function zipperStitchRings(vertices, pushTri, ringA, ringB, cx, cz) {
  const { start: aStart, count: nA } = ringA;
  const { start: bStart, count: nB } = ringB;
  if (nA < 3 || nB < 3) return;

  const a = ringAngleTable(vertices, aStart, nA, cx, cz, NaN);
  // B starts at the vertex nearest A's first angle and shares A's angular frame.
  const b = ringAngleTable(vertices, bStart, nB, cx, cz, a.angles[0]);
  const bAngles = b.angles;

  const aIndexAt = (k) => aStart + ((a.startIndex + k) % nA);
  const bIndexAt = (k) => bStart + ((b.startIndex + k) % nB);

  let i = 0;
  let j = 0;
  while (i < nA || j < nB) {
    const advanceA = j >= nB || (i < nA && a.angles[i + 1] <= bAngles[j + 1]);
    if (advanceA) {
      pushTri(bIndexAt(j), aIndexAt(i), aIndexAt(i + 1));
      i += 1;
    } else {
      pushTri(bIndexAt(j), aIndexAt(i), bIndexAt(j + 1));
      j += 1;
    }
  }
}

function stitchMatchedRings(vertices, pushTri, ringA, ringB, fullCircle) {
  if (ringA.count !== ringB.count) {
    return false;
  }
  const count = ringA.count;
  const limit = fullCircle ? count : count - 1;
  for (let k = 0; k < limit; k += 1) {
    const k2 = (k + 1) % count;
    pushTri(ringB.start + k, ringA.start + k, ringA.start + k2);
    pushTri(ringB.start + k, ringA.start + k2, ringB.start + k2);
  }
  return true;
}

function normalizedViewportRingPoints(points) {
  const count = Math.floor(points.length / 3);
  const ring = [];

  let shoelace = 0;
  for (let k = 0; k < count; k += 1) {
    const k2 = (k + 1) % count;
    shoelace += points[k * 3] * points[k2 * 3 + 1] - points[k2 * 3] * points[k * 3 + 1];
  }
  const reversed = shoelace < 0;

  for (let k = 0; k < count; k += 1) {
    const src = reversed ? count - 1 - k : k;
    ring.push([points[src * 3], points[src * 3 + 2], points[src * 3 + 1]]);
  }
  return ring;
}

/**
 * Append one enclosure profile ring (mesher coords) as viewport vertices,
 * normalized to CCW order in the viewport x/z plane so the zipper stitch can
 * assume one winding direction.
 */
function appendRingVertices(vertices, points) {
  const ring = normalizedViewportRingPoints(points);
  const count = ring.length;
  const start = vertices.length / 3;

  for (let k = 0; k < count; k += 1) {
    vertices.push(ring[k][0], ring[k][1], ring[k][2]);
  }
  return { start, count };
}

function raySegmentIntersection(cx, cz, dx, dz, ax, az, bx, bz) {
  const sx = bx - ax;
  const sz = bz - az;
  const denom = dx * sz - dz * sx;
  if (Math.abs(denom) <= 1e-12) return null;

  const ox = ax - cx;
  const oz = az - cz;
  const t = (ox * sz - oz * sx) / denom;
  const u = (ox * dz - oz * dx) / denom;
  if (t < -1e-9 || u < -1e-9 || u > 1 + 1e-9) return null;
  return { t, u: Math.max(0, Math.min(1, u)) };
}

function nearestAngularRingPoint(ring, theta, cx, cz) {
  let best = ring[0] || [cx, 0, cz];
  let bestDelta = Infinity;
  for (const point of ring) {
    const pTheta = Math.atan2(point[2] - cz, point[0] - cx);
    let delta = Math.abs(pTheta - theta) % (Math.PI * 2);
    if (delta > Math.PI) delta = Math.PI * 2 - delta;
    if (delta < bestDelta) {
      bestDelta = delta;
      best = point;
    }
  }
  return best;
}

function appendRingVerticesAlignedToReference(vertices, points, referenceRing, cx, cz) {
  const ring = normalizedViewportRingPoints(points);
  if (ring.length < 3 || referenceRing.count < 3) {
    return appendRingVertices(vertices, points);
  }

  const start = vertices.length / 3;
  for (let k = 0; k < referenceRing.count; k += 1) {
    const refIdx = referenceRing.start + k;
    const dx = vertices[refIdx * 3] - cx;
    const dz = vertices[refIdx * 3 + 2] - cz;
    const dirLen = Math.hypot(dx, dz);
    if (dirLen <= 1e-12) {
      const fallback = ring[0];
      vertices.push(fallback[0], fallback[1], fallback[2]);
      continue;
    }

    let best = null;
    for (let i = 0; i < ring.length; i += 1) {
      const a = ring[i];
      const b = ring[(i + 1) % ring.length];
      const hit = raySegmentIntersection(cx, cz, dx, dz, a[0], a[2], b[0], b[2]);
      if (!hit) continue;
      if (!best || hit.t < best.t) {
        best = { ...hit, a, b };
      }
    }

    if (best) {
      const x = best.a[0] + (best.b[0] - best.a[0]) * best.u;
      const y = best.a[1] + (best.b[1] - best.a[1]) * best.u;
      const z = best.a[2] + (best.b[2] - best.a[2]) * best.u;
      vertices.push(x, y, z);
    } else {
      const fallback = nearestAngularRingPoint(ring, Math.atan2(dz, dx), cx, cz);
      vertices.push(fallback[0], fallback[1], fallback[2]);
    }
  }

  return { start, count: referenceRing.count };
}

function classifyEnclosureStitch(prevRole, nextRole) {
  if (nextRole === 'front_edge') return 'front_edge';
  if (nextRole === 'back_edge') return 'back_edge';
  if (nextRole === 'side_back_outer') return 'side';
  return prevRole === 'front_inset' ? 'side' : 'other';
}

/**
 * Enclosure from mesher profile rings: front baffle (mouth ring -> front
 * inset), swept ring stitches (front roundover, side wall, back roundover),
 * then a fanned back cap. Mirrors the display-group layout of
 * engine/mesh/enclosure.js without re-deriving any plan geometry.
 */
function addEnclosure(vertices, indices, enclosure, nPhi, nLength, fullCircle, groups) {
  const rings = Array.isArray(enclosure?.profile_rings) ? enclosure.profile_rings : [];
  if (rings.length < 2) {
    throw new Error('Viewport enclosure payload must include at least two profile rings.');
  }
  const bounds = enclosure.bounds || {};
  const boxCx = toFinite(bounds.cx, 0);
  const boxCz = toFinite(bounds.cy, 0);

  const pushTri = makeTriPusher(vertices, indices);
  const mouthRing = { start: nLength * nPhi, count: nPhi };

  let mouthCx = 0;
  let mouthCz = 0;
  for (let i = 0; i < nPhi; i += 1) {
    mouthCx += vertices[(mouthRing.start + i) * 3];
    mouthCz += vertices[(mouthRing.start + i) * 3 + 2];
  }
  mouthCx /= nPhi;
  mouthCz /= nPhi;

  const placedRings = rings.map((ring, index) => ({
    role: ring.role,
    ...(index === 0
      ? appendRingVerticesAlignedToReference(
          vertices,
          ring.points || [],
          mouthRing,
          mouthCx,
          mouthCz
        )
      : appendRingVertices(vertices, ring.points || [])),
  }));

  const enclosureStartTri = indices.length / 3;
  // Front baffle: stitched around the mouth centroid, which lies inside both
  // the mouth ring and the surrounding front inset ring.
  if (!stitchMatchedRings(vertices, pushTri, mouthRing, placedRings[0], fullCircle)) {
    zipperStitchRings(vertices, pushTri, mouthRing, placedRings[0], mouthCx, mouthCz);
  }
  const frontEndTri = indices.length / 3;

  const edgeRanges = [];
  let sideRange = null;
  for (let k = 0; k < placedRings.length - 1; k += 1) {
    const stitchStart = indices.length / 3;
    zipperStitchRings(vertices, pushTri, placedRings[k], placedRings[k + 1], boxCx, boxCz);
    const stitchEnd = indices.length / 3;
    const kind = classifyEnclosureStitch(placedRings[k].role, placedRings[k + 1].role);
    if (kind === 'side') {
      sideRange = { start: stitchStart, end: stitchEnd };
    } else if (stitchEnd > stitchStart) {
      edgeRanges.push({ start: stitchStart, end: stitchEnd });
    }
  }

  // Back cap: concentric blend rings toward the centroid, then a center fan,
  // matching the JS engine's rear cap topology.
  const rearStartTri = indices.length / 3;
  const lastRing = placedRings[placedRings.length - 1];
  let capCx = 0;
  let capCy = 0;
  let capCz = 0;
  for (let k = 0; k < lastRing.count; k += 1) {
    capCx += vertices[(lastRing.start + k) * 3];
    capCy += vertices[(lastRing.start + k) * 3 + 1];
    capCz += vertices[(lastRing.start + k) * 3 + 2];
  }
  capCx /= lastRing.count;
  capCy /= lastRing.count;
  capCz /= lastRing.count;

  let prevRing = lastRing;
  const capSlices = 3;
  if (fullCircle) {
    for (let s = 1; s < capSlices; s += 1) {
      const blend = s / capSlices;
      const ringStart = vertices.length / 3;
      for (let k = 0; k < lastRing.count; k += 1) {
        const x = vertices[(lastRing.start + k) * 3];
        const z = vertices[(lastRing.start + k) * 3 + 2];
        vertices.push(x + (capCx - x) * blend, capCy, z + (capCz - z) * blend);
      }
      const newRing = { start: ringStart, count: lastRing.count };
      for (let k = 0; k < lastRing.count; k += 1) {
        const k2 = (k + 1) % lastRing.count;
        pushTri(newRing.start + k, prevRing.start + k, prevRing.start + k2);
        pushTri(newRing.start + k, prevRing.start + k2, newRing.start + k2);
      }
      prevRing = newRing;
    }
  }

  const centerIdx = vertices.length / 3;
  vertices.push(capCx, capCy, capCz);
  const capLimit = fullCircle ? prevRing.count : prevRing.count - 1;
  for (let k = 0; k < capLimit; k += 1) {
    const k2 = (k + 1) % prevRing.count;
    pushTri(prevRing.start + k, prevRing.start + k2, centerIdx);
  }
  const enclosureEndTri = indices.length / 3;

  groups.enclosure = { start: enclosureStartTri, end: enclosureEndTri };
  groups.enc_front = { start: enclosureStartTri, end: frontEndTri };
  if (edgeRanges.length > 0) groups.enc_edge = edgeRanges;
  if (sideRange) groups.enc_side = sideRange;
  groups.enc_rear = { start: rearStartTri, end: enclosureEndTri };
}

/**
 * Throat source cap height, mirroring engine/mesh/source.js but with the
 * mesher parameter contract (sourceShape 1 = rounded cap, 0 = flat disc).
 */
function resolveCapHeight(vertices, ringCount, params) {
  const sourceShape = Math.round(toFinite(evalParam(params?.sourceShape, 0), 0));
  if (sourceShape !== 1) return 0;

  let centerX = 0;
  let centerZ = 0;
  for (let i = 0; i < ringCount; i += 1) {
    centerX += vertices[i * 3];
    centerZ += vertices[i * 3 + 2];
  }
  centerX /= ringCount;
  centerZ /= ringCount;

  let maxRadius = 0;
  for (let i = 0; i < ringCount; i += 1) {
    const idx = i * 3;
    maxRadius = Math.max(
      maxRadius,
      Math.hypot(vertices[idx] - centerX, vertices[idx + 2] - centerZ)
    );
  }

  const sourceRadius = toFinite(evalParam(params?.sourceRadius, 0), -1);
  let height;
  if (sourceRadius > maxRadius) {
    height = sourceRadius - Math.sqrt(Math.max(0, sourceRadius ** 2 - maxRadius ** 2));
  } else {
    const r0 = toFinite(evalParam(params?.r0 ?? maxRadius, 0), maxRadius);
    const a0 = toRad(toFinite(evalParam(params?.a0 ?? 0, 0), 0));
    const baseRadius = r0 > 0 ? r0 : maxRadius;
    const capScale = String(params?.type) === 'R-OSSE' ? 0.5 : 1;
    height = baseRadius * Math.tan(a0) * capScale;
  }

  if (!Number.isFinite(height) || height < 0) return 0;
  const sourceCurv = Math.round(toFinite(evalParam(params?.sourceCurv, 0), 0));
  return sourceCurv === -1 ? -height : height;
}

function addThroatDisc(vertices, indices, nPhi, fullCircle, params, groups) {
  const sourceStartTri = indices.length / 3;
  let centerX = 0;
  let centerY = 0;
  let centerZ = 0;
  for (let i = 0; i < nPhi; i += 1) {
    centerX += vertices[i * 3];
    centerY += vertices[i * 3 + 1];
    centerZ += vertices[i * 3 + 2];
  }
  centerX /= nPhi;
  centerY /= nPhi;
  centerZ /= nPhi;
  centerY += resolveCapHeight(vertices, nPhi, params);

  const centerIdx = vertices.length / 3;
  vertices.push(centerX, centerY, centerZ);
  const segmentCount = fullCircle ? nPhi : Math.max(0, nPhi - 1);
  for (let i = 0; i < segmentCount; i += 1) {
    const a = i;
    const b = fullCircle ? (i + 1) % nPhi : i + 1;
    indices.push(centerIdx, b, a);
  }
  const sourceEndTri = indices.length / 3;
  if (sourceEndTri > sourceStartTri) {
    groups.source = { start: sourceStartTri, end: sourceEndTri };
    groups.throat_disc = { start: sourceStartTri, end: sourceEndTri };
  }
}

/**
 * Tessellate a backend viewport geometry payload into render triangles.
 *
 * @param {object} payload `POST /api/mesh/viewport` response: mesher point
 *   grids, optional enclosure rings, and the resolved mesher params.
 * @param {object} [options]
 * @param {boolean} [options.omitSource] skip the throat disc group.
 * @returns {{vertices: number[], indices: number[], groups: object}}
 */
export function tessellateViewportGeometry(payload, options = {}) {
  const grid = payload?.grid;
  const { nPhi, nLength } = assertGridShape(grid);
  const fullCircle = grid.full_circle !== false;
  const hasEnclosure = payload?.enclosure != null;

  const vertices = [];
  const indices = [];
  const groups = {};

  appendGridVertices(vertices, grid.inner_points, nPhi, nLength);
  addHornSurface(indices, nPhi, nLength, fullCircle);
  const hornEndTri = indices.length / 3;
  groups.horn = { start: 0, end: hornEndTri };
  groups[hasEnclosure ? 'horn_wall' : 'inner_wall'] = { start: 0, end: hornEndTri };

  if (hasEnclosure) {
    addEnclosure(vertices, indices, payload.enclosure, nPhi, nLength, fullCircle, groups);
  } else if (grid.outer_points != null) {
    addFreestandingWall(vertices, indices, grid.outer_points, nPhi, nLength, fullCircle, groups);
  }

  if (options.omitSource !== true) {
    addThroatDisc(vertices, indices, nPhi, fullCircle, payload?.params || {}, groups);
  }

  return { vertices, indices, groups };
}
