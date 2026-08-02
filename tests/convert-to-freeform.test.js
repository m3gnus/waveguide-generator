import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  convertToFreeform,
  formatConversionReport,
} from '../src/modules/design/convertToFreeform.js';
import { prepareBackendViewportMesh } from '../src/modules/geometry/useCases.js';

function syntheticGrid(rows, { scale = 1 } = {}) {
  const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
  const nLength = rows.H.length - 1;
  const inner = [];
  for (let i = 0; i < angles.length; i += 1) {
    const source = i === 1 ? rows.V : rows.H;
    for (const [z, r] of source) {
      inner.push(r * Math.cos(angles[i]) * scale, r * Math.sin(angles[i]) * scale, z * scale);
    }
  }
  return {
    angle_list: angles,
    grid_n_phi: angles.length,
    grid_n_length: nLength,
    inner_points: inner,
    full_circle: true,
    quadrants: 1234,
    slice_map: Array.from({ length: nLength + 1 }, (_, index) => index),
  };
}

function sourceState(params = {}) {
  return {
    type: 'OSSE',
    params: { ...getDefaults('OSSE'), ...params },
  };
}

test('convertToFreeform un-scales monotone meridians and clamps rectangle morph corners', async () => {
  const grid = syntheticGrid(
    {
      H: [
        [0, 10],
        [10, 12],
        [20, 18],
        [30, 28],
        [40, 40],
      ],
      V: [
        [0, 10],
        [10, 11],
        [20, 15],
        [30, 22],
        [40, 30],
      ],
    },
    { scale: 2 }
  );
  const state = sourceState({ scale: 2, morphTarget: 1, morphCorner: 99, quadrants: 12 });
  const result = await convertToFreeform(state, {
    viewportMesh: { grid, preparedParams: { scale: 2 } },
  });

  assert.equal(result.params.scale, 2);
  assert.equal(result.params.length, 40);
  assert.equal(result.params.throatRadius, 10);
  assert.equal(result.params.mouthRadiusH, 40);
  assert.equal(result.params.mouthRadiusV, 30);
  assert.ok(Math.abs(result.params.throatAngle - 11.309932) < 1e-5);
  assert.deepEqual(result.params.crossSections, [
    { t: 0, shape: 'circle' },
    { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: 30 },
  ]);
  assert.equal(result.params.morphTarget, 0);
  assert.equal(result.params.quadrants, 12);
  assert.equal(result.report.truncatedMm, 0);
  assert.match(formatConversionReport(result.report), /^max deviation H .* mm, V .* mm$/);
});

test('convertToFreeform truncates non-monotone rollback and resamples the other mouth', async () => {
  const horizontal = Array.from({ length: 21 }, (_, index) => {
    const z = index * 2;
    return [z, 10 + 0.22 * z + 0.012 * z * z];
  });
  horizontal.push([34.9, 39]);
  const vertical = Array.from({ length: 22 }, (_, index) => {
    const z = index * 2;
    return [z, 10 + 0.16 * z + 0.008 * z * z];
  });
  const grid = syntheticGrid({
    H: horizontal,
    V: vertical,
  });
  const result = await convertToFreeform(sourceState({ morphTarget: 2 }), {
    viewportMesh: { grid, preparedParams: { scale: 1 } },
  });

  assert.equal(result.params.length, 40);
  assert.equal(result.params.mouthRadiusH, 38);
  assert.equal(result.params.mouthRadiusV, 29.2);
  assert.deepEqual(result.params.crossSections, [
    { t: 0, shape: 'circle' },
    { t: 1, shape: 'ellipse' },
  ]);
  assert.equal(result.report.truncatedMm, 34.9);
  assert.match(formatConversionReport(result.report), /rollback lip 34\.9 mm dropped$/);
});

test('convertToFreeform enforces the 62-interior-anchor decimation cap', async () => {
  const dense = Array.from({ length: 101 }, (_, index) => [
    index,
    20 + index * 0.5 + (index % 2 === 0 ? 1 : -1),
  ]);
  const grid = syntheticGrid({ H: dense, V: dense });
  const result = await convertToFreeform(sourceState(), {
    viewportMesh: { grid, preparedParams: { scale: 1 } },
    toleranceMm: 0.01,
  });

  assert.equal(result.report.anchorCountH, 62);
  assert.equal(result.report.anchorCountV, 62);
  assert.equal(result.params.interiorH.length, 62);
  assert.ok(result.report.maxDeviationMmH > 0.01);
  assert.ok(
    result.params.interiorH.every((point) => point.angleDeg === null && point.strength === null)
  );
});

test('convertToFreeform requests a full-domain smooth grid when no cached grid is available', async () => {
  const grid = syntheticGrid({
    H: [
      [0, 10],
      [20, 20],
    ],
    V: [
      [0, 10],
      [20, 18],
    ],
  });
  let requestedState;
  let requestedOptions;
  await convertToFreeform(sourceState({ quadrants: 1 }), {
    prepareViewportMesh: async (state, options) => {
      requestedState = state;
      requestedOptions = options;
      return { grid, preparedParams: { scale: 1 } };
    },
  });

  assert.equal(requestedState.params.quadrants, 1234);
  assert.equal(requestedOptions.variant, 'smooth');
});

test('prepareBackendViewportMesh passes the raw response grid through unchanged', async () => {
  const grid = syntheticGrid({
    H: [
      [0, 10],
      [20, 20],
    ],
    V: [
      [0, 10],
      [20, 18],
    ],
  });
  const payload = {
    formula: 'OSSE',
    mode: 'bare',
    params: { type: 'OSSE' },
    grid,
    enclosure: null,
    metadata: { marker: 'raw-grid' },
  };
  const mesh = await prepareBackendViewportMesh(sourceState(), {
    fetchImpl: async () => ({
      ok: true,
      json: async () => payload,
    }),
  });

  assert.equal(mesh.grid, grid);
  assert.deepEqual(mesh.metadata, { marker: 'raw-grid' });
  assert.ok(mesh.vertices.length > 0);
  assert.ok(mesh.indices.length > 0);
});
