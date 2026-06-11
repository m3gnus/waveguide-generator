/**
 * Unit tests for the backend viewport geometry tessellator.
 *
 * Synthetic payloads mirror the `POST /api/mesh/viewport` response shape:
 * mesher point grids (x, y, z-axial) plus enclosure profile rings.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { tessellateViewportGeometry } from '../src/geometry/viewportTessellator.js';
import { analyzeBemMeshIntegrity } from '../src/geometry/meshIntegrity.js';

const TAU = Math.PI * 2;

/** Mesher-order point grid (i-major) for a cone of given ring radii. */
function coneGridPoints(nPhi, ringRadii, ringZ) {
  const nLength = ringRadii.length - 1;
  const points = [];
  for (let i = 0; i < nPhi; i += 1) {
    const phi = (i / nPhi) * TAU;
    for (let j = 0; j <= nLength; j += 1) {
      points.push(ringRadii[j] * Math.cos(phi), ringRadii[j] * Math.sin(phi), ringZ[j]);
    }
  }
  return points;
}

function ringPoints(count, radius, z, { startAngle = 0, reverse = false } = {}) {
  const points = [];
  for (let k = 0; k < count; k += 1) {
    const step = reverse ? count - k : k;
    const phi = startAngle + (step / count) * TAU;
    points.push(radius * Math.cos(phi), radius * Math.sin(phi), z);
  }
  return points;
}

function hornPayload({ nPhi = 8, sourceShape = 0, params = {}, outer = false } = {}) {
  const ringRadii = [1, 1.5, 2];
  const ringZ = [0, 5, 10];
  const grid = {
    inner_points: coneGridPoints(nPhi, ringRadii, ringZ),
    outer_points: outer
      ? coneGridPoints(
          nPhi,
          ringRadii.map((r) => r + 0.5),
          [ringZ[0] - 0.5, ...ringZ.slice(1)]
        )
      : null,
    grid_n_phi: nPhi,
    grid_n_length: ringRadii.length - 1,
    full_circle: true,
  };
  return {
    formula: 'OSSE',
    mode: outer ? 'freestanding' : 'bare',
    params: { type: 'OSSE', sourceShape, ...params },
    grid,
    enclosure: null,
  };
}

test('horn grid is transposed into viewport vertex order with y/z swapped', () => {
  const nPhi = 4;
  const payload = hornPayload({ nPhi });
  const mesh = tessellateViewportGeometry(payload, { omitSource: true });

  const nLength = payload.grid.grid_n_length;
  assert.equal(mesh.vertices.length / 3, nPhi * (nLength + 1));
  for (let j = 0; j <= nLength; j += 1) {
    for (let i = 0; i < nPhi; i += 1) {
      const meshIdx = (j * nPhi + i) * 3;
      const gridIdx = (i * (nLength + 1) + j) * 3;
      assert.equal(mesh.vertices[meshIdx], payload.grid.inner_points[gridIdx]);
      assert.equal(mesh.vertices[meshIdx + 1], payload.grid.inner_points[gridIdx + 2]);
      assert.equal(mesh.vertices[meshIdx + 2], payload.grid.inner_points[gridIdx + 1]);
    }
  }

  assert.deepEqual(mesh.groups.horn, { start: 0, end: nPhi * nLength * 2 });
  assert.deepEqual(mesh.groups.inner_wall, mesh.groups.horn);
  assert.equal(mesh.groups.throat_disc, undefined);
});

test('throat disc fans the throat ring and honors the mesher source contract', () => {
  const flat = tessellateViewportGeometry(hornPayload({ sourceShape: 0 }));
  assert.ok(flat.groups.throat_disc);
  assert.deepEqual(flat.groups.source, flat.groups.throat_disc);
  const flatCenterY = flat.vertices[flat.vertices.length * 1 - 2];
  assert.ok(Math.abs(flatCenterY - 0) < 1e-12, `flat disc center at throat plane, got ${flatCenterY}`);

  // sourceShape 1 = rounded cap (mesher contract): OSSE cap height r0*tan(a0).
  const capped = tessellateViewportGeometry(
    hornPayload({ sourceShape: 1, params: { r0: 1, a0: 45 } })
  );
  const capCenterY = capped.vertices[capped.vertices.length - 2];
  assert.ok(Math.abs(capCenterY - 1) < 1e-9, `cap height should be tan(45°)*r0, got ${capCenterY}`);

  const concave = tessellateViewportGeometry(
    hornPayload({ sourceShape: 1, params: { r0: 1, a0: 45, sourceCurv: -1 } })
  );
  const concaveCenterY = concave.vertices[concave.vertices.length - 2];
  assert.ok(Math.abs(concaveCenterY + 1) < 1e-9, `concave cap flips sign, got ${concaveCenterY}`);
});

test('freestanding wall produces a closed oriented solid with wall groups', () => {
  const mesh = tessellateViewportGeometry(hornPayload({ outer: true }));

  for (const name of ['freestandingWall', 'outer_wall', 'mouth_rim', 'rear_cap']) {
    assert.ok(mesh.groups[name], `missing group ${name}`);
  }

  const report = analyzeBemMeshIntegrity(mesh.vertices, mesh.indices, {
    requireClosed: true,
    requireSingleComponent: true,
  });
  assert.deepEqual(report.errors, []);
});

test('enclosure rings stitch into a closed solid across differing ring sizes', () => {
  const nPhi = 12;
  const payload = hornPayload({ nPhi });
  payload.mode = 'enclosure';
  payload.enclosure = {
    mouth_points: [],
    profile_rings: [
      // Deliberately different point counts and start angles per ring.
      { role: 'front_inset', points: ringPoints(20, 4.0, 10) },
      { role: 'front_edge', points: ringPoints(24, 4.4, 9.4, { startAngle: 0.21 }) },
      { role: 'side_back_outer', points: ringPoints(24, 4.5, -4.4, { startAngle: 0.1 }) },
      { role: 'back_edge', points: ringPoints(20, 4.0, -5, { startAngle: -0.13 }) },
    ],
    bounds: { bx0: -4.5, bx1: 4.5, by0: -4.5, by1: 4.5, z_front: 10, z_back: -5, cx: 0, cy: 0 },
    plan_type: 1,
    edge_type: 1,
    edge_mm: 0.5,
    edge_depth: 0.5,
  };

  const mesh = tessellateViewportGeometry(payload);

  assert.ok(mesh.groups.enclosure);
  assert.ok(mesh.groups.enc_front);
  assert.ok(mesh.groups.enc_side);
  assert.ok(mesh.groups.enc_rear);
  assert.equal(mesh.groups.enc_edge.length, 2);
  assert.deepEqual(mesh.groups.horn_wall, mesh.groups.horn);

  const report = analyzeBemMeshIntegrity(mesh.vertices, mesh.indices, {
    requireClosed: true,
    requireSingleComponent: true,
  });
  assert.deepEqual(report.errors, []);
});

test('clockwise enclosure rings are normalized before stitching', () => {
  const payload = hornPayload({ nPhi: 8 });
  payload.enclosure = {
    mouth_points: [],
    profile_rings: [
      { role: 'front_inset', points: ringPoints(16, 4.0, 10, { reverse: true }) },
      { role: 'side_back_outer', points: ringPoints(16, 4.0, -5, { reverse: true }) },
    ],
    bounds: { bx0: -4, bx1: 4, by0: -4, by1: 4, z_front: 10, z_back: -5, cx: 0, cy: 0 },
    plan_type: 1,
    edge_type: 1,
    edge_mm: 0,
    edge_depth: 0,
  };

  const mesh = tessellateViewportGeometry(payload);
  const report = analyzeBemMeshIntegrity(mesh.vertices, mesh.indices, {
    requireClosed: true,
    requireSingleComponent: true,
  });
  assert.deepEqual(report.errors, []);
});

test('invalid grids are rejected with explicit errors', () => {
  assert.throws(
    () => tessellateViewportGeometry({ grid: { grid_n_phi: 8, grid_n_length: 2, inner_points: [1, 2, 3] } }),
    /inner_points/
  );
  assert.throws(() => tessellateViewportGeometry({ grid: null }), /point grid/);
  assert.throws(
    () => tessellateViewportGeometry({ grid: { grid_n_phi: 2, grid_n_length: 0, inner_points: [] } }),
    /invalid dimensions/
  );
});
